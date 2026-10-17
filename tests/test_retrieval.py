import unittest

from app.retrieval import BM25Index, excerpt, focus_source


class SourceRegressionTests(unittest.TestCase):
    def test_empty_document_filter_never_searches_all_sources(self):
        index = BM25Index([{"id": "chunk", "document_id": "course-a", "text": "网络协议", "ordinal": 0}])
        self.assertTrue(index.search("网络"))
        self.assertEqual(index.search("网络", document_ids=set()), [])

    def test_search_page_and_excerpt_follow_the_actual_match(self):
        context = {"chunk_id": "legacy-multi-page", "page": 1, "text": "intro\nFOCUSPHRASE",
                   "lines": [{"page": 1, "text": "introduction", "bbox": [10, 10, 90, 20]},
                             {"page": 2, "text": "FOCUSPHRASE is on the second page", "bbox": [10, 90, 200, 105]}]}
        focused = focus_source(context, "FOCUSPHRASE")
        self.assertEqual(focused["page"], 2)
        self.assertEqual({line["page"] for line in focused["lines"]}, {2})
        self.assertNotIn("introduction", focused["text"])
        self.assertEqual(context["page"], 1)  # The cached index isn't mutated.

    def test_excerpt_prefers_the_whole_phrase_over_scattered_tokens(self):
        text = "网络单独出现。" + "无关内容。" * 50 + "网络体系结构是分层设计的说明。" + "其他内容。" * 50
        self.assertIn("网络体系结构", excerpt(text, "网络体系结构", limit=75))
        self.assertNotIn("网络单独出现", excerpt(text, "网络体系结构", limit=75))

    def test_nonpaged_sources_keep_the_full_context(self):
        context = {"page": None, "text": "正文", "lines": [{"page": None, "text": "正文", "bbox": None}]}
        self.assertEqual(focus_source(context, "正文"), context)


if __name__ == "__main__":
    unittest.main()
