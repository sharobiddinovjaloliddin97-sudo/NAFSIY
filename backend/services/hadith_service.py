
import httpx
from fastapi import HTTPException
from backend.ai.source_registry import permitted

HADITH_API = "https://ummahapi.com/api/hadith"


async def fetch_hadith(path: str, params: dict | None = None):
    if not permitted("ummah-hadith"):
        raise HTTPException(503, "Hadith translation permissions and edition provenance are pending review")
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(
                f"{HADITH_API}/{path}",
                params=params
            )
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502,
                detail="Invalid Hadith API response"
            )

        if payload.get("success") is False:
            raise HTTPException(
                status_code=502,
                detail="Hadith provider returned an error"
            )

        return {
            "provider": "UmmahAPI",
            "provider_url": "https://ummahapi.com",
            "data": payload.get("data", payload)
        }

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Hadith provider timed out"
        )

    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Hadith not found"
            )
        raise HTTPException(
            status_code=502,
            detail="Hadith provider unavailable"
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to Hadith provider"
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Invalid Hadith JSON"
        )


async def get_collections():
    return await fetch_hadith("collections")


async def get_hadith(collection: str, number: int):
    return await fetch_hadith(f"{collection}/{number}")


async def search_hadith(query: str):
    return await fetch_hadith(
        "search",
        params={"q": query}
    )

