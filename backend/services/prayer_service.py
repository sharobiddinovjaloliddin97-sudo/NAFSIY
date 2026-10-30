from datetime import date

import httpx
from fastapi import HTTPException

PRAYER_API_URL = "https://api.aladhan.com/v1"

TASHKENT_COORDINATES = (41.26465, 69.21627)
REQUIRED_TIMINGS = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")


async def get_prayer_times(
    city: str,
    country: str,
    prayer_date: date,
    method: int,
    school: int,
):
    url = (
        f"{PRAYER_API_URL}/timingsByCity/"
        f"{prayer_date.strftime('%d-%m-%Y')}"
    )
    params = {
        "city": city,
        "country": country,
        "method": method,
        "school": school,
    }
    is_tashkent = (
        city.strip().casefold() in {"tashkent", "toshkent"}
        and country.strip().casefold() in {"uzbekistan", "uz", "uzb"}
    )
    if is_tashkent:

        url = f"{PRAYER_API_URL}/timings/{prayer_date.strftime('%d-%m-%Y')}"
        params = {
            "latitude": TASHKENT_COORDINATES[0],
            "longitude": TASHKENT_COORDINATES[1],
            "timezonestring": "Asia/Tashkent",
            "method": method,
            "school": school,
        }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Prayer API timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Prayer API unavailable") from exc
    except ValueError as exc:
        raise HTTPException(502, "Invalid Prayer API JSON") from exc

    if (
        not isinstance(payload, dict)
        or payload.get("code") != 200
        or not isinstance(payload.get("data"), dict)
        or not isinstance(payload["data"].get("timings"), dict)
    ):
        raise HTTPException(502, "Invalid Prayer API response")

    data = payload["data"]
    timings = data["timings"]
    meta = data.get("meta")
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("timezone"), str)
        or not meta["timezone"]
        or any(
            not isinstance(timings.get(key), str) or not timings[key]
            for key in REQUIRED_TIMINGS
        )
    ):
        raise HTTPException(502, "Invalid Prayer API response")

    if is_tashkent:
        try:
            latitude = float(meta["latitude"])
            longitude = float(meta["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(502, "Invalid Prayer API coordinates") from exc
        if not (
            abs(latitude - TASHKENT_COORDINATES[0]) < 0.001
            and abs(longitude - TASHKENT_COORDINATES[1]) < 0.001
            and meta["timezone"] == "Asia/Tashkent"
        ):
            raise HTTPException(502, "Prayer API location mismatch")

    return {
        "city": city,
        "country": country,
        "date": prayer_date.isoformat(),
        "timezone": meta.get("timezone"),
        "calculation_method": method,
        "school": school,
        "times": {key: timings[key] for key in REQUIRED_TIMINGS},
    }
