import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.ai.references import quran_reference
from backend.ai.source_registry import content_digest, permitted


CITATION_PATTERN = re.compile(r"\[([a-z0-9-]+:\d+(?:\.\d+)?)\]", re.IGNORECASE)


def allowed_references(retrieved_sources: list[dict]) -> set[str]:
    return {
        item["reference"].strip().lower()
        for item in retrieved_sources
        if isinstance(item, dict) and isinstance(item.get("reference"), str)
    }


def cited_references(answer: str) -> set[str]:
    return {reference.lower() for reference in CITATION_PATTERN.findall(answer)}


def validate_citations(answer: str, retrieved_sources: list[dict]) -> dict:
    allowed = allowed_references(retrieved_sources)
    cited = cited_references(answer)
    invalid = sorted(cited - allowed)
    invalid = sorted(set(invalid) | {ref for ref in cited if ref[0].isdigit() and quran_reference(ref) is None})
    return {
        "valid": not invalid,
        "invalid_references": invalid,
        "cited_references": sorted(cited & allowed),
        "uncited_references": sorted(allowed - cited),
    }


def filter_cited_sources(answer: str, retrieved_sources: list[dict]) -> list[dict]:
    used = cited_references(answer)
    result = []
    seen = set()
    for item in retrieved_sources:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
            continue
        reference = item["reference"].strip().lower()
        if reference in used and reference not in seen:
            result.append(item)
            seen.add(reference)
    return result


class Quotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1, max_length=300)
    reference: str = Field(min_length=3, max_length=120)
    language: str = Field(pattern=r"^(ar|uz|en|ru)$")
    edition: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=6000)


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    explanation: str = Field(max_length=10000)
    quotations: list[Quotation] = Field(default_factory=list, max_length=4)


# Free text is commentary only. Explicit quotes, Arabic and attribution/grade
# assertions must not bypass the structured quotation channel.
PROSE_CLAIMS = re.compile(
    r'["«»“”„‟<>`]|!?\[[^\]]*\]\(|https?://|(?:^|\n)\s*>|[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]'
    r'|\b(?:sahih|saheeh|hasan|daif|da.if|authentic|inauthentic|fabricated|narrated|narrator|'
    r'saheeh|sahihdir|sahihligi|zaif|roviy|ривоят|ровий|саҳиҳ|заиф|достоверн\w*|слабый|рассказал)\b'
    r'|\b(?:Allah says|God says|Prophet said|Quran says|Alloh (?:ayt|dedi|deydi)|'
    r'Payg.ambar.*(?:dedi|degan)|Аллах сказал|Пророк сказал)'
    r'|\b(?:en\.[a-z]+|ru\.[a-z-]+|uz\.[a-z]+|Saheeh International|Sahih International|'
    r'Kuliev|Кулиев|Talal Itani|Tafsiri Hilol)\b', re.IGNORECASE)


LABELS = {
    "en": ("AI explanation — unverified; not a published translation or fatwa.", "Source quotation", "For personal rulings, consult a qualified scholar."),
    "uz": ("AI izohi — tekshirilmagan; nashr qilingan tarjima yoki fatvo emas.", "Manbadan iqtibos", "Shaxsiy hukm uchun malakali olimga murojaat qiling."),
    "ru": ("Объяснение ИИ — не проверено; это не опубликованный перевод и не фетва.", "Цитата из источника", "По личным религиозным вопросам обратитесь к квалифицированному учёному."),
    "ar": ("شرح آلي غير موثق، وليس ترجمة منشورة أو فتوى.", "نص المصدر", "للفتوى الشخصية يرجى سؤال عالم مؤهل."),
}


def safe_unicode(text):
    """Canonical Unicode equivalence only. No stripping vowels/tatweel/words."""
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n"))


def validate_grounded_answer(raw, records, language):
    try:
        answer = GroundedAnswer.model_validate_json(raw)
    except ValidationError:
        return {"valid": False, "reason": "invalid_answer_schema"}
    validation = validate_citations(answer.explanation, records)
    prose = re.sub(r"\[(?:bukhari|muslim|[a-z0-9-]+):[0-9.]+\]", "", answer.explanation, flags=re.I)
    prose = re.sub(r"\bSahih al-(?:Bukhari|Muslim)\b", "collection", prose, flags=re.I)
    if not validation["valid"] or PROSE_CLAIMS.search(prose):
        return {"valid": False, "reason": "unsupported_prose_claim"}
    if answer.explanation.strip() and not validation["cited_references"]:
        return {"valid": False, "reason": "insufficient_coverage"}
    lookup = {record.get("source_id"): record for record in records}
    used = set(validation["cited_references"])
    quotes = []
    for claim in answer.quotations:
        record = lookup.get(claim.source_id)
        if record is None or claim.reference != record["reference"]:
            return {"valid": False, "reason": "wrong_source_identity"}
        original = claim.language == "ar"
        edition = record.get("arabic_edition" if original else "translation_edition")
        expected = record.get("arabic" if original else "translation", "")
        if (claim.edition != edition or (not original and claim.language != record.get("translation_language"))
                or not expected or record.get("truncated") or record.get("metadata_conflicts")
                or not permitted(record.get("registry_id")) or (original and not permitted(edition))
                or record.get("content_sha256") != content_digest(record)
                or safe_unicode(claim.text) != safe_unicode(expected)):
            return {"valid": False, "reason": "unsupported_quotation"}
        used.add(claim.reference)
        # Render the source, never the model's normalized reconstruction.
        quotes.append((record, claim.language, edition, expected))
    if not used:
        return {"valid": False, "reason": "insufficient_coverage"}
    for ref in used:
        candidates = [record for record in records if record["reference"] == ref]
        if any(record.get("metadata_conflicts") or not permitted(record.get("registry_id"))
               or record.get("content_sha256") != content_digest(record) for record in candidates):
            return {"valid": False, "reason": "untrusted_source"}
        if len({record.get("numbering_system") for record in candidates}) > 1:
            return {"valid": False, "reason": "ambiguous_numbering"}
    label, quote_label, advice = LABELS.get(language, LABELS["en"])
    rendered = [label, answer.explanation.strip()]
    for record, lang, edition, text in quotes:
        # A fence longer than any source backtick run keeps provider text literal,
        # including malicious Markdown links/images/HTML masquerading as quotations.
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=3))
        attribution = record.get("arabic_attribution" if lang == "ar" else "translation_attribution") or record["attribution"]
        rendered.append(f"{quote_label} [{record['reference']}] ({lang}; {edition}):\n\n{fence}text\n{text}\n{fence}\n\n{attribution}")
    rendered.append(advice)
    public = []
    for record in records:
        if record["reference"] not in used:
            continue
        metadata = {key: record[key] for key in (
            "type", "reference", "provider", "source_id", "content_sha256", "arabic_edition",
            "translation_edition", "translation_language", "numbering_system", "verification_status",
            "grading_status", "license_state", "attribution", "arabic_attribution", "translation_attribution", "license_url") if key in record}
        metadata["verified"] = False  # Never equate reference/text equality with religious correctness.
        metadata["quotation_status"] = "exact_source_match" if any(q[0] is record for q in quotes) else "not_quoted"
        public.append(metadata)
    text = "\n\n".join(part for part in rendered if part)
    if len(text) > 20000:
        return {"valid": False, "reason": "answer_too_long"}
    return {"valid": True, "answer": text, "sources": public}
