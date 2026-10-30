
import httpx
from fastapi import HTTPException

from backend.core.config import settings
from backend.ai.source_registry import arabic_edition, permitted, REGISTRY
from backend.ai.references import AYAH_COUNTS

BASE_URL = "https://api.alquran.cloud/v1"

DEFAULT_EDITIONS = {
    "uz": settings.QURAN_EDITION_UZ,
    "en": settings.QURAN_EDITION_EN,
    "ru": settings.QURAN_EDITION_RU,
}


async def fetch_data(path: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}/{path}")
            response.raise_for_status()
            result = response.json()

        if (
            not isinstance(result, dict)
            or result.get("code") != 200
            or "data" not in result
        ):
            raise HTTPException(
                status_code=502,
                detail="Invalid Quran API response"
            )

        return result["data"]

    except httpx.TimeoutException:
        raise HTTPException(504, "Quran API timeout")

    except httpx.HTTPError:
        raise HTTPException(502, "Quran API unavailable")

    except ValueError:
        raise HTTPException(502, "Invalid API JSON")


async def get_surahs():
    return await fetch_data("surah")


async def get_surah(number: int):
    data = await fetch_data(
        f"surah/{number}/{arabic_edition()}"
    )
    if (not isinstance(data, dict) or data.get("number") != number
            or not isinstance(data.get("edition"), dict) or data["edition"].get("identifier") != arabic_edition()
            or not isinstance(data.get("ayahs"), list) or len(data["ayahs"]) != AYAH_COUNTS[number - 1]
            or any(not isinstance(item, dict) or item.get("numberInSurah") != index or not isinstance(item.get("text"), str)
                   for index, item in enumerate(data["ayahs"], 1))):
        raise HTTPException(502, "Quran source identity or verse boundary mismatch")
    return {**data, "arabic_edition": arabic_edition(), "verification_status": "reference_resolved"}


async def get_editions(language: str):
    data = await fetch_data(
        f"edition/language/{language}"
    )

    return [
        item for item in data
        if item.get("type") == "translation"
        and item.get("format") == "text"
        and permitted(item.get("identifier"))
    ]


async def get_translated_surah(
    number: int,
    language: str,
    edition: str
):
    entry = REGISTRY.get(edition)
    if not permitted(edition) or entry.kind != "translation" or entry.language != language:
        raise HTTPException(503, "Translation permission is pending; no approved edition for this language")
    editions = await get_editions(language)

    selected = next(
        (
            item for item in editions
            if item["identifier"] == edition
        ),
        None
    )

    if not selected:
        raise HTTPException(
            422, "Invalid translation edition"
        )

    arabic = await get_surah(number)
    translation = await fetch_data(
        f"surah/{number}/{edition}"
    )

    if (not isinstance(translation, dict) or not isinstance(translation.get("ayahs"), list)
            or not isinstance(translation.get("edition"), dict)):
        raise HTTPException(502, "Invalid translation source metadata")
    original = arabic["ayahs"]
    translated = translation["ayahs"]

    if (len(original) != AYAH_COUNTS[number - 1] or len(original) != len(translated)
            or arabic.get("number") != number or translation.get("number") != number
            or arabic.get("edition", {}).get("identifier") != arabic_edition()
            or translation.get("edition", {}).get("identifier") != edition):
        raise HTTPException(
            502, "Ayah count mismatch"
        )

    ayahs = []

    for index, (ar, tr) in enumerate(zip(original, translated), 1):
        if (not isinstance(tr, dict) or ar["numberInSurah"] != index or tr.get("numberInSurah") != index
                or not isinstance(tr.get("text"), str) or not tr["text"].strip()):
            raise HTTPException(
                502, "Ayah number mismatch"
            )

        ayahs.append({
            "number": ar["numberInSurah"],
            "arabic": ar["text"],
            "translation": tr["text"]
        })

    return {
        "surah": number,
        "name": arabic["englishName"],
        "language": language,
        "edition": edition,
        "translator": selected["englishName"],
        "arabic_edition": arabic_edition(),
        "attribution": entry.attribution,
        "license_state": entry.license_state.value,
        "license_url": entry.license_url,
        "verification_status": "reference_resolved",
        "ayahs": ayahs
    }


async def get_default_translation(number: int, language: str):
    edition = DEFAULT_EDITIONS.get(language)
    if not edition:
        raise HTTPException(
            status_code=503,
            detail=f"No approved translation for {language}"
        )
    return await get_translated_surah(number, language, edition)

