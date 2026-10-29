import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from backend.api.prayer import router
from backend.services import prayer_service


class PrayerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.payload = {
            "code": 200,
            "data": {
                "timings": {
                    "Fajr": "05:17", "Sunrise": "06:50", "Dhuhr": "12:07",
                    "Asr": "15:41", "Maghrib": "17:23", "Isha": "18:50",
                    "Midnight": "00:07",
                },
                "meta": {
                    "latitude": 41.26465,
                    "longitude": 69.21627,
                    "timezone": "Asia/Tashkent",
                },
            },
        }
        self.requests = []

    async def fetch(self, payload=None, city="Tashkent", error=None):
        real_client = httpx.AsyncClient

        def handler(request):
            self.requests.append(request)
            if error:
                raise error
            return httpx.Response(200, json=payload or self.payload)

        def factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(prayer_service.httpx, "AsyncClient", side_effect=factory):
            return await prayer_service.get_prayer_times(
                city, "Uzbekistan", date(2026, 10, 29), 3, 1
            )

    async def test_normalized_response_and_tashkent_coordinates(self):
        result = await self.fetch()
        self.assertEqual(result, {
            "city": "Tashkent", "country": "Uzbekistan", "date": "2026-10-29",
            "timezone": "Asia/Tashkent", "calculation_method": 3, "school": 1,
            "times": {key: self.payload["data"]["timings"][key]
                      for key in prayer_service.REQUIRED_TIMINGS},
        })
        request = self.requests[0]
        self.assertEqual(request.url.path, "/v1/timings/29-10-2026")
        self.assertEqual(request.url.params["latitude"], "41.26465")
        self.assertEqual(request.url.params["longitude"], "69.21627")
        self.assertEqual(request.url.params["method"], "3")
        self.assertEqual(request.url.params["school"], "1")

    async def test_other_cities_keep_city_lookup(self):
        result = await self.fetch(city="Samarkand")
        self.assertEqual(result["city"], "Samarkand")
        self.assertEqual(self.requests[0].url.path, "/v1/timingsByCity/29-10-2026")
        self.assertEqual(self.requests[0].url.params["city"], "Samarkand")

    async def test_tashkent_alias(self):
        await self.fetch(city=" Toshkent ")
        self.assertEqual(self.requests[0].url.path, "/v1/timings/29-10-2026")

    async def test_reject_incomplete_response(self):
        for field in ("Fajr", "Isha"):
            payload = deepcopy(self.payload)
            del payload["data"]["timings"][field]
            with self.subTest(field=field), self.assertRaises(HTTPException) as ctx:
                await self.fetch(payload)
            self.assertEqual(ctx.exception.status_code, 502)
        payload = deepcopy(self.payload)
        payload["data"]["meta"] = None
        with self.assertRaises(HTTPException) as ctx:
            await self.fetch(payload)
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_reject_wrong_location(self):
        for field, value in (("latitude", 8.8888888), ("longitude", 7.7777777),
                             ("timezone", "Europe/London"), ("latitude", None)):
            payload = deepcopy(self.payload)
            payload["data"]["meta"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(HTTPException) as ctx:
                await self.fetch(payload)
            self.assertEqual(ctx.exception.status_code, 502)

    async def test_upstream_timeout(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.fetch(error=httpx.ReadTimeout("test timeout"))
        self.assertEqual(ctx.exception.status_code, 504)

    async def test_endpoint_contract(self):
        result = await self.fetch()
        app = FastAPI()
        app.include_router(router)
        with patch("backend.api.prayer.get_prayer_times", return_value=result) as service:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/prayer/times?prayer_date=2026-10-29")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), result)
                self.assertEqual(service.call_args.kwargs["prayer_date"], date(2026, 10, 29))
                response = await client.get("/api/v1/prayer/times?school=2")
                self.assertEqual(response.status_code, 422)
