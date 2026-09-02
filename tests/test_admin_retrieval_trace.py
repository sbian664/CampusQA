import unittest

from src.tools import ToolHandler


class FakeKnowledgeBase:
    def hybrid_search(self, query, top_k, filters=None):
        return [{
            "source": "courses.md",
            "title": "课程注册",
            "doc_type": "markdown",
            "chunk_index": 2,
            "content": "注册窗口开放时间为 9 月 1 日。",
            "score": 0.81,
            "bm25_score": 0.42,
            "metadata": {"section_path": "注册 > 时间"},
        }]

    def _tokenize_query(self, query):
        return set()


class RetrievalTraceTests(unittest.TestCase):
    def test_search_call_log_contains_bounded_structured_hits(self):
        handler = ToolHandler(FakeKnowledgeBase(), rerank_enabled=False, turn_id="turn-1")

        handler.execute("search_knowledge_base", {"query": "课程注册", "top_k": 3})
        entry = handler.get_call_log()[0]

        self.assertEqual(entry["turn_id"], "turn-1")
        self.assertEqual(entry["query"], "课程注册")
        self.assertEqual(entry["engine"], "hybrid")
        self.assertEqual(entry["result_count"], 1)
        self.assertEqual(entry["hits"][0]["source"], "courses.md")
        self.assertIn("注册窗口", entry["hits"][0]["content_snippet"])
        self.assertLessEqual(len(entry["hits"][0]["content_snippet"]), 800)


if __name__ == "__main__":
    unittest.main()
