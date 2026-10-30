from typing import Literal

from fastapi import APIRouter, Path, Query

from backend.services.quran_service import (
    get_default_translation,
    get_surahs,
    get_surah,
    get_editions,
    get_translated_surah
)

router = APIRouter(
    prefix="/api/v1/quran",
    tags=["Quran"]
)


@router.get("/sources")
def source_registry():
    from backend.ai.source_registry import registry_metadata
    return {"sources": registry_metadata(), "religious_correctness_verified": False}


@router.get("/surahs")
async def list_surahs():
    return await get_surahs()


@router.get("/surahs/{number}")
async def surah_details(
    number: int = Path(ge=1, le=114)
):
    return await get_surah(number)


@router.get("/translations/{language}")
async def translations(
    language: Literal["uz", "en", "ru"]
):
    return await get_editions(language)


@router.get("/surahs/{number}/read")
async def read_surah(
    number: int = Path(ge=1, le=114),
    language: Literal["uz", "en", "ru"] = Query(...),
    edition: str = Query(...)
):
    return await get_translated_surah(
        number,
        language,
        edition
    )


@router.get("/surahs/{number}/default")
async def default_translation(
    number: int = Path(ge=1, le=114),
    language: Literal["uz", "en", "ru"] = Query(default="en")
):
    return await get_default_translation(number, language)

