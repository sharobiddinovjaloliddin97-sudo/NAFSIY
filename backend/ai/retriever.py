import asyncio
import re
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.ai.references import quran_reference
from backend.ai.source_registry import REGISTRY, arabic_edition, content_digest, permitted, translation_editions


QURAN_API = "https://api.alquran.cloud/v1"
HADITH_API = "https://ummahapi.com/api/hadith"
ALLOWED_COLLECTIONS = {
    "bukhari", "muslim", "abudawud", "tirmidhi", "ibnmajah",
    "nasai", "malik", "nawawi", "qudsi", "dehlawi",
}
MAX_SOURCES = 8
MAX_RESULTS_PER_QUERY = 3
MAX_TEXT_LENGTH = 6000


def hadith_reference(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([a-z0-9-]+):([1-9]\d{0,5}(?:\.\d{1,3})?)", value.strip())
    if not match or match[1] not in ALLOWED_COLLECTIONS or float(match[2]) > 100000:
        return None
    number = match[2].rstrip("0").rstrip(".") if "." in match[2] else match[2]
    return f"{match[1]}:{number}"


class SearchPlan(BaseModel):
    language: str = Field(pattern=r"^(ar|uz|ru|en)$")
    quran_queries: list[str] = Field(default_factory=list, max_length=3)
    hadith_queries: list[str] = Field(default_factory=list, max_length=3)
    quran_references: list[str] = Field(default_factory=list, max_length=3)
    hadith_references: list[str] = Field(default_factory=list, max_length=3)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("quran_queries", "hadith_queries")
    @classmethod
    def queries(cls, values):
        result = []
        seen = set()
        for value in values:
            query = " ".join(value.split())
            if not 2 <= len(query) <= 80:
                raise ValueError("Search query must contain 2 to 80 characters")
            if query.casefold() not in seen:
                result.append(query)
                seen.add(query.casefold())
        return result

    @field_validator("quran_references", "hadith_references")
    @classmethod
    def references(cls, values, info):
        normalize = quran_reference if info.field_name == "quran_references" else hadith_reference
        result = []
        for value in values:
            reference = normalize(value)
            if reference is None:
                raise ValueError("Invalid source reference")
            if reference not in result:
                result.append(reference)
        return result


def data_of(value):
    return value.get("data", value) if isinstance(value, dict) else value


def text_of(value):
    return value if isinstance(value, str) and value.strip() else ""


def source(kind, reference, provider, arabic="", translation="", grade="", *, edition=None):
    original, translated = text_of(arabic), text_of(translation)
    record = {
        "type": kind, "reference": reference, "provider": provider, "verified": False,
        "arabic": original[:MAX_TEXT_LENGTH], "translation": translated[:MAX_TEXT_LENGTH],
        "translation_language": "en" if translated else "", "grade": text_of(grade)[:200],
        "translation_edition": (edition or "en.itani") if kind == "quran" and translated else "",
        "arabic_edition": arabic_edition() if kind == "quran" and original else "",
        "numbering_system": "hafs-kufan" if kind == "quran" else "ummahapi-provider-numbering-unspecified",
        "verification_status": "reference_resolved" if kind == "quran" else "provider_reported",
        "grading_status": "not_applicable" if kind == "quran" else "grading_not_verified",
        "grade_authority": "", "metadata_conflicts": [],
        "truncated": len(original) > MAX_TEXT_LENGTH or len(translated) > MAX_TEXT_LENGTH,
    }
    return identify(record)


def identify(record):
    edition = record.get("translation_edition") or record.get("arabic_edition") or "ummah-hadith"
    entry = REGISTRY.get(edition)
    record["registry_id"] = edition
    record["license_state"] = entry.license_state.value if entry else "permission_pending"
    record["attribution"] = entry.attribution if entry else "UmmahAPI; edition not established"
    arabic_entry = REGISTRY.get(record.get("arabic_edition"))
    record["arabic_attribution"] = arabic_entry.attribution if arabic_entry else ""
    record["translation_attribution"] = entry.attribution if entry and record.get("translation") else ""
    record["license_url"] = entry.license_url if entry else ""
    record["source_id"] = f"{record['provider']}:{edition}:{record['numbering_system']}:{record['reference']}"
    record["content_sha256"] = content_digest(record)
    return record


async def fetch_data(client, semaphore, url, params=None):
    try:
        async with semaphore:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is False:
            return None
        if "code" in payload and payload["code"] != 200:
            return None
        return data_of(payload)
    except (httpx.HTTPError, ValueError):
        return None


def verse_reference(item):
    if not isinstance(item, dict) or not isinstance(item.get("surah"), dict):
        return None
    number, ayah = item["surah"].get("number"), item.get("numberInSurah")
    if type(number) is not int or type(ayah) is not int:
        return None
    return quran_reference(f"{number}:{ayah}")


async def quran_exact(client, semaphore, reference):
    if quran_reference(reference) != reference:
        return []
    arabic = arabic_edition()
    editions = [arabic, *(entry.id for entry in translation_editions())]
    data = await fetch_data(client, semaphore,
                            f"{QURAN_API}/ayah/{reference}/editions/{','.join(editions)}")
    if not isinstance(data, list):
        return []
    texts = {}
    names = {}
    for item in data:
        if verse_reference(item) != reference or not isinstance(item.get("edition"), dict):
            continue
        edition = item["edition"].get("identifier")
        if edition in editions:
            text = text_of(item.get("text"))
            if edition in texts and texts[edition] != text:
                return []  # Conflicting payloads cannot establish one canonical record.
            texts[edition] = text
            names = {"surah_name": text_of(item["surah"].get("englishName"))[:120],
                     "surah_name_arabic": text_of(item["surah"].get("name"))[:120]}
    if not any(texts.values()):
        return []
    translations = [entry for entry in translation_editions() if texts.get(entry.id)]
    records = []
    for entry in translations or [None]:
        record = source("quran", reference, "AlQuran Cloud", texts.get(arabic, ""),
                        texts.get(entry.id, "") if entry else "", edition=entry.id if entry else None)
        record["translation_language"] = entry.language if entry else ""
        records.append({**identify(record), **names})
    return records


async def quran_search(client, semaphore, query, edition="en.itani"):
    if not permitted(edition):
        return []
    requested_edition = edition
    data = await fetch_data(client, semaphore, f"{QURAN_API}/search/{quote(query, safe='')}/all/{edition}")
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return []
    candidates = {}
    for item in data["matches"]:
        reference = verse_reference(item)
        if reference is None or not text_of(item.get("text")):
            continue
        edition = item.get("edition")
        if not isinstance(edition, dict) or edition.get("identifier") != requested_edition:
            continue
        candidates.setdefault(reference, source("quran", reference, "AlQuran Cloud", translation=item["text"], edition=edition["identifier"]))
        if len(candidates) == MAX_RESULTS_PER_QUERY:
            break
    results = await asyncio.gather(*(quran_exact(client, semaphore, reference) for reference in candidates))
    return [record for enriched, fallback in zip(results, candidates.values()) for record in (enriched or [fallback])]


def normalize_hadith(item, expected=None):
    if not isinstance(item, dict):
        return None
    collection = item.get("collection") or item.get("collection_key")
    number = next((item[key] for key in ("hadithnumber", "hadith_number", "hadithNumber", "number")
                   if item.get(key) is not None), None)
    if number is None and isinstance(item.get("id"), str) and isinstance(collection, str):
        match = re.fullmatch(re.escape(collection) + r"-([1-9]\d*(?:\.\d+)?)", item["id"])
        number = match[1] if match else None
    if isinstance(number, bool) or not isinstance(number, (str, int, float)):
        return None
    reference = hadith_reference(f"{collection}:{number}")
    if reference is None or (expected is not None and reference != expected):
        return None
    arabic = next((text_of(item.get(key)) for key in ("arabic", "hadith_arabic", "hadithArabic", "arabic_text")
                   if text_of(item.get(key))), "")
    english = next((text_of(item.get(key)) for key in ("english", "hadith_english", "hadithEnglish", "english_text", "text")
                    if text_of(item.get(key))), "")
    if not arabic and not english:
        return None
    record = source("hadith", reference, "UmmahAPI", arabic, english, item.get("grade", ""))
    record["collection"] = collection
    record["hadith_number"] = reference.split(":", 1)[1]
    record["numbering_system"] = text_of(item.get("numbering_system"))[:120] or "ummahapi-provider-numbering-unspecified"
    # The live adapter exposes a bare grade, no named grading authority/edition.
    # Retain it as reported metadata, never authenticate it on the model's behalf.
    record["grade_authority"] = text_of(item.get("grade_authority"))[:120]
    identities = [hadith_reference(f"{collection}:{item[key]}") for key in
                  ("hadithnumber", "hadith_number", "hadithNumber", "number") if item.get(key) is not None]
    collections = {item[key] for key in ("collection", "collection_key") if isinstance(item.get(key), str)}
    if len(set(identities)) > 1 or len(collections) > 1:
        record["metadata_conflicts"].append("conflicting_identity")
    if item.get("grades") and record["grade"]:
        record["metadata_conflicts"].append("multiple_grading_metadata")
    for label, fields in (("arabic", ("arabic", "hadith_arabic", "hadithArabic", "arabic_text")),
                          ("translation", ("english", "hadith_english", "hadithEnglish", "english_text", "text"))):
        if len({text_of(item.get(field)) for field in fields if text_of(item.get(field))}) > 1:
            record["metadata_conflicts"].append("conflicting_" + label)
    record["reported_grades"] = [{"grade": text_of(row.get("grade"))[:200],
                                  "authority": text_of(row.get("name"))[:120]}
                                 for row in (item.get("grades") or [])[:10] if isinstance(row, dict)] if isinstance(item.get("grades"), list) else []
    if record["metadata_conflicts"]:
        record["verification_status"] = "metadata_conflict"
    return identify(record)


async def hadith_exact(client, semaphore, reference):
    if not permitted("ummah-hadith") or hadith_reference(reference) != reference:
        return []
    collection, number = reference.split(":")
    data = await fetch_data(client, semaphore, f"{HADITH_API}/{collection}/{number}")
    record = normalize_hadith(data, expected=reference)
    return [record] if record else []


async def hadith_search(client, semaphore, query):
    if not permitted("ummah-hadith"):
        return []
    data = await fetch_data(client, semaphore, f"{HADITH_API}/search", {"q": query, "limit": MAX_RESULTS_PER_QUERY})
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = next((data[key] for key in ("hadiths", "results", "items", "matches")
                        if isinstance(data.get(key), list)), [])
    else:
        entries = []
    result = []
    for item in entries:
        record = normalize_hadith(item)
        if record:
            result.append(record)
        if len(result) == MAX_RESULTS_PER_QUERY:
            break
    return result


async def retrieve_sources(plan: SearchPlan | dict):
    plan = plan if isinstance(plan, SearchPlan) else SearchPlan.model_validate(plan)
    semaphore = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=8.0, limits=httpx.Limits(max_connections=6)) as client:
        jobs = [quran_exact(client, semaphore, ref) for ref in plan.quran_references]
        jobs += [hadith_exact(client, semaphore, ref) for ref in plan.hadith_references]
        for index in range(max(len(plan.quran_queries), len(plan.hadith_queries))):
            if index < len(plan.quran_queries):
                jobs.append(quran_search(client, semaphore, plan.quran_queries[index]))
            if index < len(plan.hadith_queries):
                jobs.append(hadith_search(client, semaphore, plan.hadith_queries[index]))
        if not jobs:
            return []
        tasks = [asyncio.create_task(job) for job in jobs]
        try:
            done, _ = await asyncio.wait(tasks, timeout=25)
            collected = []
            for task in tasks:
                if task in done and not task.cancelled() and task.exception() is None:
                    collected.append(task.result())
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    unique = {}
    for records in collected:
        for record in records:
            key = (record["type"], record["reference"], record.get("numbering_system"), record.get("translation_edition"))
            if key not in unique and len(unique) < MAX_SOURCES:
                unique[key] = record
            elif key in unique:
                conflicts = [field for field in ("arabic", "translation", "grade", "grade_authority")
                             if unique[key].get(field) and record.get(field) and unique[key][field] != record[field]]
                if conflicts:
                    unique[key]["metadata_conflicts"] = sorted(set(unique[key].get("metadata_conflicts", []) + conflicts))
                    unique[key]["verification_status"] = "metadata_conflict"
                for field in ("arabic", "translation", "translation_language", "translation_edition",
                              "grade", "surah_name", "surah_name_arabic"):
                    if not unique[key].get(field) and record.get(field):
                        unique[key][field] = record[field]
                unique[key]["truncated"] = unique[key]["truncated"] or record["truncated"]
    return [identify(record) for record in unique.values()]
