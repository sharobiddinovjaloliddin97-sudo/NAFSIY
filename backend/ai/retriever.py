import asyncio
import re
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


QURAN_API = "https://api.alquran.cloud/v1"
HADITH_API = "https://ummahapi.com/api/hadith"
ALLOWED_COLLECTIONS = {
    "bukhari", "muslim", "abudawud", "tirmidhi", "ibnmajah",
    "nasai", "malik", "nawawi", "qudsi", "dehlawi",
}
MAX_SOURCES = 8
MAX_RESULTS_PER_QUERY = 3
MAX_TEXT_LENGTH = 6000


def quran_reference(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([1-9]\d{0,2}):([1-9]\d{0,2})", value.strip())
    if not match or int(match[1]) > 114 or int(match[2]) > 286:
        return None
    return f"{int(match[1])}:{int(match[2])}"


def hadith_reference(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([a-z0-9-]+):([1-9]\d{0,5}(?:\.\d{1,3})?)", value.strip())
    if not match or match[1] not in ALLOWED_COLLECTIONS or float(match[2]) > 100000:
        return None
    number = match[2].rstrip("0").rstrip(".") if "." in match[2] else match[2]
    return f"{match[1]}:{number}"


class SearchPlan(BaseModel):
    language: str = Field(pattern=r"^(uz|ru|en)$")
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
    return value.strip() if isinstance(value, str) else ""


def source(kind, reference, provider, arabic="", translation="", grade=""):
    original, translated = text_of(arabic), text_of(translation)
    return {
        "type": kind, "reference": reference, "provider": provider, "verified": False,
        "arabic": original[:MAX_TEXT_LENGTH], "translation": translated[:MAX_TEXT_LENGTH],
        "translation_language": "en" if translated else "", "grade": text_of(grade)[:200],
        "translation_edition": "en.sahih" if kind == "quran" and translated else "",
        "truncated": len(original) > MAX_TEXT_LENGTH or len(translated) > MAX_TEXT_LENGTH,
    }


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
    data = await fetch_data(client, semaphore,
                            f"{QURAN_API}/ayah/{reference}/editions/quran-uthmani,en.sahih")
    if not isinstance(data, list):
        return []
    texts = {}
    names = {}
    for item in data:
        if verse_reference(item) != reference or not isinstance(item.get("edition"), dict):
            continue
        edition = item["edition"].get("identifier")
        if edition in {"quran-uthmani", "en.sahih"}:
            texts[edition] = text_of(item.get("text"))
            names = {"surah_name": text_of(item["surah"].get("englishName"))[:120],
                     "surah_name_arabic": text_of(item["surah"].get("name"))[:120]}
    if not any(texts.values()):
        return []
    return [{**source("quran", reference, "AlQuran Cloud", texts.get("quran-uthmani", ""), texts.get("en.sahih", "")), **names}]


async def quran_search(client, semaphore, query):
    data = await fetch_data(client, semaphore, f"{QURAN_API}/search/{quote(query, safe='')}/all/en.sahih")
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return []
    candidates = {}
    for item in data["matches"]:
        reference = verse_reference(item)
        if reference is None or not text_of(item.get("text")):
            continue
        edition = item.get("edition")
        if isinstance(edition, dict) and edition.get("identifier") not in {None, "en.sahih"}:
            continue
        candidates.setdefault(reference, source("quran", reference, "AlQuran Cloud", translation=item["text"]))
        if len(candidates) == MAX_RESULTS_PER_QUERY:
            break
    results = await asyncio.gather(*(quran_exact(client, semaphore, reference) for reference in candidates))
    return [enriched[0] if enriched else fallback for enriched, fallback in zip(results, candidates.values())]


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
    return source("hadith", reference, "UmmahAPI", arabic, english, item.get("grade", ""))


async def hadith_exact(client, semaphore, reference):
    collection, number = reference.split(":")
    data = await fetch_data(client, semaphore, f"{HADITH_API}/{collection}/{number}")
    record = normalize_hadith(data, expected=reference)
    return [record] if record else []


async def hadith_search(client, semaphore, query):
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
            key = (record["type"], record["reference"])
            if key not in unique and len(unique) < MAX_SOURCES:
                unique[key] = record
            elif key in unique:
                for field in ("arabic", "translation", "translation_language", "translation_edition",
                              "grade", "surah_name", "surah_name_arabic"):
                    if not unique[key].get(field) and record.get(field):
                        unique[key][field] = record[field]
                unique[key]["truncated"] = unique[key]["truncated"] or record["truncated"]
    return list(unique.values())
