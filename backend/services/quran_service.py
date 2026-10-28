
import httpx
from fastapi import HTTPException

from backend.core.config import settings

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
    return await fetch_data(
        f"surah/{number}/quran-uthmani"
    )


async def get_editions(language: str):
    data = await fetch_data(
        f"edition/language/{language}"
    )

    return [
        item for item in data
        if item.get("type") == "translation"
        and item.get("format") == "text"
    ]


async def get_translated_surah(
    number: int,
    language: str,
    edition: str
):
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

    original = arabic["ayahs"]
    translated = translation["ayahs"]

    if len(original) != len(translated):
        raise HTTPException(
            502, "Ayah count mismatch"
        )

    ayahs = []

    for ar, tr in zip(original, translated):
        if ar["numberInSurah"] != tr["numberInSurah"]:
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

