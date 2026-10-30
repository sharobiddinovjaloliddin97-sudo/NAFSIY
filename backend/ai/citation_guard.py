import re


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
