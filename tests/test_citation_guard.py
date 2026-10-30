import unittest

from backend.ai.citation_guard import filter_cited_sources, validate_citations


class CitationGuardTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"type": "quran", "reference": "2:255", "provider": "AlQuran Cloud", "verified": False},
            {"type": "hadith", "reference": "bukhari:1", "provider": "UmmahAPI", "verified": False},
        ]

    def test_valid_citations_and_only_used_sources(self):
        answer = "This verse [2:255] is referenced twice [2:255]."
        validation = validate_citations(answer, self.sources)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["cited_references"], ["2:255"])
        self.assertEqual(validation["uncited_references"], ["bukhari:1"])
        self.assertEqual(filter_cited_sources(answer, self.sources), [self.sources[0]])

    def test_unknown_citation_is_rejected_even_alongside_valid_one(self):
        result = validate_citations("Valid [2:255], unknown [2:999], unknown [muslim:12].", self.sources)
        self.assertFalse(result["valid"])
        self.assertEqual(result["invalid_references"], ["2:999", "muslim:12"])
        self.assertEqual(result["cited_references"], ["2:255"])

    def test_case_insensitive_hadith_and_duplicate_sources(self):
        self.assertTrue(validate_citations("Hadith [BUKHARI:1].", self.sources)["valid"])
        self.assertEqual(filter_cited_sources("[BUKHARI:1]", self.sources + self.sources), [self.sources[1]])

    def test_decimal_hadith_reference(self):
        sources = [{"reference": "tirmidhi:2787.2", "verified": False}]
        self.assertTrue(validate_citations("[tirmidhi:2787.2]", sources)["valid"])
        self.assertFalse(validate_citations("[tirmidhi:2787.3]", sources)["valid"])

    def test_no_citations_has_no_public_sources(self):
        self.assertTrue(validate_citations("Uncited explanation", self.sources)["valid"])
        self.assertEqual(filter_cited_sources("Uncited explanation", self.sources), [])

    def test_unknown_reference_with_empty_retrieval(self):
        self.assertFalse(validate_citations("Quote [2:255]", [None, {}, {"reference": 2}])["valid"])
        self.assertEqual(filter_cited_sources("Quote [2:255]", []), [])
