from datetime import date

from fastapi import APIRouter, Query

from backend.services.prayer_service import get_prayer_times

router = APIRouter(
    prefix="/api/v1/prayer",
    tags=["Prayer"],
)


@router.get("/times")
async def prayer_times(
    city: str = Query(default="Tashkent", min_length=2, max_length=100),
    country: str = Query(default="Uzbekistan", min_length=2, max_length=100),
    prayer_date: date | None = Query(default=None),
    method: int = Query(default=3, ge=0, le=23),
    school: int = Query(default=1, ge=0, le=1),
):
    return await get_prayer_times(
        city=city,
        country=country,
        prayer_date=prayer_date or date.today(),
        method=method,
        school=school,
    )
