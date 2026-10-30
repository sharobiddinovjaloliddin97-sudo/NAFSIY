import asyncio
import unittest
from dataclasses import replace
from backend.ai.source_registry import REGISTRY, LicenseState
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from backend.ai import retriever


def verse(reference, edition="en.itani", text="English source"):
    surah, ayah = map(int, reference.split(":"))
    return {"surah": {"number": surah}, "numberInSurah": ayah,
            "edition": {"identifier": edition}, "text": text}


def hadith(number=1, **values):
    return {"collection": "bukhari", "hadithnumber": number, "arabic": "Arabic source",
            "english": "English source", "grade": "Sahih", **values}


def payload(data):
    return httpx.Response(200, json={"code": 200, "data": data})


class RetrieverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Synthetic adapter fixtures only; never enables the live source registry.
        override = patch.dict(REGISTRY, {"ummah-hadith": replace(REGISTRY["ummah-hadith"], license_state=LicenseState.LICENSED)})
        override.start()
        self.addCleanup(override.stop)

    async def test_duplicate_records_keep_excerpt_and_edition_metadata(self):
        original = retriever.source("quran", "1:1", "AlQuran Cloud", arabic="Arabic source")
        translated = retriever.source("quran", "1:1", "AlQuran Cloud", translation="x" * 6001)
        with patch.object(retriever, "quran_exact", AsyncMock(return_value=[original])), patch.object(
            retriever, "quran_search", AsyncMock(return_value=[translated]),
        ):
            records = await retriever.retrieve_sources({"language": "en", "quran_references": ["1:1"],
                                                        "quran_queries": ["mercy"]})
        self.assertEqual(len(records), 2)  # Distinct edition records cannot be silently merged.
        self.assertTrue(records[1]["truncated"])
        self.assertEqual(records[1]["translation_edition"], "en.itani")
        self.assertEqual(records[0]["arabic"], "Arabic source")

    async def retrieve(self, plan, handler):
        client = httpx.AsyncClient

        def factory(**kwargs):
            return client(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(retriever.httpx, "AsyncClient", side_effect=factory):
            return await retriever.retrieve_sources(plan)

    async def test_mixed_sources_normalization_and_deduplication(self):
        def handler(request):
            path = request.url.path
            if "/search/" in path:
                return payload({"matches": [verse("1:1"), verse("2:2"), verse("2:2")]})
            if "/ayah/" in path:
                reference = path.split("/ayah/")[1].split("/")[0]
                return payload([verse(reference, "quran-uthmani", "Arabic source"), verse(reference)])
            if path.endswith("/search"):
                self.assertEqual(request.url.params["limit"], "3")
                return payload({"hadiths": [hadith(), hadith(2, arabic=None), hadith(3, collection="other")]})
            return payload(hadith())

        records = await self.retrieve({"language": "uz", "quran_references": ["1:1"],
                                       "hadith_references": ["bukhari:1"],
                                       "quran_queries": ["knowledge"], "hadith_queries": ["intention"]}, handler)
        self.assertEqual([record["reference"] for record in records], ["1:1", "bukhari:1", "2:2", "bukhari:2"])
        self.assertTrue(all(record["verified"] is False for record in records))
        self.assertTrue(all(record["translation_language"] == "en" for record in records))
        self.assertEqual(records[0]["translation_edition"], "en.itani")
        self.assertEqual(records[-1]["arabic"], "")
        self.assertEqual(records[-1]["grade"], "Sahih")

    async def test_provider_failures_keep_other_sources(self):
        for failure in ("timeout", "json", "shape", "http"):
            def handler(request):
                if request.url.host == "api.alquran.cloud":
                    if failure == "timeout":
                        raise httpx.ReadTimeout("test")
                    if failure == "json":
                        return httpx.Response(200, text="not JSON")
                    if failure == "http":
                        return httpx.Response(503)
                    return payload([None, "bad", {"surah": None}])
                return payload(hadith())
            with self.subTest(failure=failure):
                records = await self.retrieve({"language": "en", "quran_references": ["1:1"],
                                               "hadith_references": ["bukhari:1"]}, handler)
                self.assertEqual([record["reference"] for record in records], ["bukhari:1"])

    async def test_exact_reference_mismatches_are_not_relabelled(self):
        def handler(request):
            if request.url.host == "api.alquran.cloud":
                return payload([verse("2:2", "quran-uthmani", "Wrong Arabic"), verse("2:2")])
            return payload(hadith(99))
        records = await self.retrieve({"language": "en", "quran_references": ["1:1"],
                                       "hadith_references": ["bukhari:1"]}, handler)
        self.assertEqual(records, [])

    async def test_search_result_can_keep_translation_when_arabic_fetch_fails(self):
        def handler(request):
            return payload({"matches": [verse("2:255")]}) if "/search/" in request.url.path else httpx.Response(503)
        records = await self.retrieve({"language": "en", "quran_queries": ["throne"]}, handler)
        self.assertEqual(records[0]["reference"], "2:255")
        self.assertEqual(records[0]["arabic"], "")
        self.assertEqual(records[0]["translation"], "English source")

    async def test_bounded_results_include_both_providers(self):
        requests = []

        def handler(request):
            requests.append(request)
            if "/ayah/" in request.url.path:
                reference = request.url.path.split("/ayah/")[1].split("/")[0]
                return payload([verse(reference)])
            if "/search/" in request.url.path:
                index = int(request.url.path.split("/search/term")[1].split("/")[0])
                return payload({"matches": [verse(f"2:{index * 100 + i}") for i in range(1, 80)]})
            index = int(request.url.params["q"][-1])
            return payload({"hadiths": [hadith(index * 100 + i) for i in range(1, 80)]})

        records = await self.retrieve({"language": "en", "quran_queries": ["term0", "term1", "term2"],
                                       "hadith_queries": ["term0", "term1", "term2"]}, handler)
        self.assertEqual(len(records), retriever.MAX_SOURCES)
        self.assertEqual({record["type"] for record in records}, {"quran", "hadith"})
        self.assertLessEqual(len(requests), 15)

    async def test_search_keywords_cannot_change_provider_host(self):
        query = "knowledge/../../?x=y#fragment"
        requests = []

        def handler(request):
            requests.append(request)
            return payload({"matches": [], "hadiths": []})

        self.assertEqual(await self.retrieve({"language": "en", "quran_queries": [query],
                                              "hadith_queries": [query]}, handler), [])
        self.assertEqual({request.url.host for request in requests}, {"api.alquran.cloud", "ummahapi.com"})
        quran = next(request for request in requests if request.url.host == "api.alquran.cloud")
        self.assertIn(b"%2F", quran.url.raw_path)
        hadith_request = next(request for request in requests if request.url.host == "ummahapi.com")
        self.assertEqual(hadith_request.url.params["q"], query)

    async def test_slow_provider_is_cancelled_without_losing_completed_results(self):
        cancelled = asyncio.Event()
        original_wait = asyncio.wait

        async def short_wait(tasks, timeout):
            return await original_wait(tasks, timeout=0.02)

        async def handler(request):
            if request.url.host == "api.alquran.cloud":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return payload(hadith())

        with patch.object(retriever.asyncio, "wait", side_effect=short_wait):
            records = await self.retrieve({"language": "en", "quran_references": ["1:1"],
                                           "hadith_references": ["bukhari:1"]}, handler)
        self.assertTrue(cancelled.is_set())
        self.assertEqual([record["reference"] for record in records], ["bukhari:1"])

    def test_plan_validation_and_text_bounds(self):
        plan = retriever.SearchPlan(language="uz", quran_queries=["  knowledge  ", "KNOWLEDGE"],
                                     hadith_references=["tirmidhi:2787.200"])
        self.assertEqual(plan.quran_queries, ["knowledge"])
        self.assertEqual(plan.hadith_references, ["tirmidhi:2787.2"])
        for values in [{"quran_references": ["115:1"]}, {"hadith_references": ["other:1"]},
                       {"quran_queries": ["a"]}, {"quran_queries": ["word"] * 4},
                       {"quran_references": [True]}, {"quran_queries": ["x" * 81]}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                retriever.SearchPlan(language="en", **values)
        record = retriever.source("hadith", "bukhari:1", "UmmahAPI", translation="x" * 6001)
        self.assertEqual(len(record["translation"]), 6000)
        self.assertTrue(record["truncated"])
        self.assertFalse(record["verified"])
