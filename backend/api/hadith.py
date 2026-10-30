
from fastapi import APIRouter, Path, Query

from backend.services.hadith_service import (
    get_collections,
    get_hadith,
    search_hadith
)

router = APIRouter(
    prefix="/api/v1/hadith",
    tags=["Hadith"]
)


@router.get("/collections")
async def list_collections():
    return await get_collections()


@router.get("/search")
async def search(
    q: str = Query(min_length=2, max_length=200)
):
    return await search_hadith(q)


@router.get("/{collection}/{number}")
async def hadith_details(
    collection: str = Path(
        min_length=2,
        max_length=50,
        pattern=r"^[a-z0-9-]+$"
    ),
    number: int = Path(ge=1, le=100000)
):
    return await get_hadith(collection, number)
