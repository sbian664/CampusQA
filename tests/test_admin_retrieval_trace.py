import unittest
from unittest.mock import patch

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


class SupplementalBM25KnowledgeBase:
    def __init__(self):
        self.bm25_called = False
        self._bm25_doc_freq = {"课程": 1}

    def hybrid_search(self, query, top_k, filters=None):
        return [{
            "source": "courses.md",
            "title": "课程注册",
            "doc_type": "markdown",
            "chunk_index": 2,
            "content": "主检索结果",
            "score": 0.81,
            "bm25_score": 0,
        }]

    def _tokenize_query(self, query):
        return {"课程"}

    def bm25_search(self, query, top_k, filters=None):
        self.bm25_called = True
        return [{"source": "bm25-only.md", "chunk_index": 4, "content": "不应追加", "score": 0.7}]


class ExpandedKnowledgeBase:
    def hybrid_search(self, query, top_k, filters=None):
        return [{
            "source": "courses.md",
            "title": "课程注册",
            "doc_type": "markdown",
            "chunk_index": 2,
            "content": "真实命中",
            "score": 0.81,
            "bm25_score": 0.42,
        }]

    def expand_adjacent_chunks(self, results, radius=1):
        return results + [
            {"source": "courses.md", "chunk_index": 1, "content": "邻接上下文 1", "score": 0.81, "is_context_neighbor": True},
            {"source": "courses.md", "chunk_index": 3, "content": "邻接上下文 2", "score": 0.81, "is_context_neighbor": True},
        ]


class TopKRecordingKnowledgeBase:
    def __init__(self):
        self.requested_top_k = None

    def hybrid_search(self, query, top_k, filters=None):
        self.requested_top_k = top_k
        return [{
            "source": "courses.md",
            "chunk_index": 0,
            "content": "结果",
            "score": 0.8,
            "bm25_score": 1,
        }]


class RerankableSupplementalBM25KnowledgeBase(SupplementalBM25KnowledgeBase):
    def bm25_search(self, query, top_k, filters=None):
        self.bm25_called = True
        return [{
            "source": "bm25-only.md",
            "chunk_index": 4,
            "content": "精确关键词命中",
            "score": 0.7,
            "bm25_score": 1.0,
        }]


class RescueAwareReranker:
    def predict(self, pairs, batch_size, show_progress_bar):
        return [0.9 if "精确关键词命中" in content else 0.1 for _, content in pairs]


class RetrievalTraceTests(unittest.TestCase):
    def test_search_allows_the_thirtieth_result(self):
        kb = TopKRecordingKnowledgeBase()
        handler = ToolHandler(kb, rerank_enabled=False)

        handler.execute("search_knowledge_base", {"query": "DSA staff", "top_k": 30})

        self.assertEqual(kb.requested_top_k, 30)

    def test_search_can_rerank_with_the_current_user_query(self):
        captured = {}

        def fake_search(kb, query, top_k, enabled, filters, model_loader, rerank_query=None, return_candidates=False):
            captured.update({
                "query": query,
                "rerank_query": rerank_query,
            })
            return [{
                "source": "courses.md",
                "title": "课程",
                "doc_type": "markdown",
                "chunk_index": 0,
                "content": "结果",
                "score": 0.8,
                "rerank_score": 0.9,
            }]

        handler = ToolHandler(
            FakeKnowledgeBase(),
            rerank_enabled=True,
            user_query="完整的当前用户问题",
        )

        with patch("src.tools.search_with_optional_rerank", side_effect=fake_search):
            handler.execute(
                "search_knowledge_base",
                {
                    "query": "课程关键词",
                    "rerank_query_source": "user_query",
                },
            )

        self.assertEqual(captured["query"], "课程关键词")
        self.assertEqual(captured["rerank_query"], "完整的当前用户问题")
        entry = handler.get_call_log()[0]
        self.assertEqual(entry["retrieval_query"], "课程关键词")
        self.assertEqual(entry["rerank_query"], "完整的当前用户问题")
        self.assertEqual(entry["rerank_query_source"], "user_query")

    def test_search_can_rerank_with_a_custom_query(self):
        captured = {}

        def fake_search(kb, query, top_k, enabled, filters, model_loader, rerank_query=None, return_candidates=False):
            captured["rerank_query"] = rerank_query
            return [{
                "source": "courses.md",
                "chunk_index": 0,
                "content": "结果",
                "score": 0.8,
            }]

        handler = ToolHandler(FakeKnowledgeBase(), rerank_enabled=True)

        with patch("src.tools.search_with_optional_rerank", side_effect=fake_search):
            handler.execute(
                "search_knowledge_base",
                {
                    "query": "课程关键词",
                    "rerank_query_source": "custom",
                    "rerank_query": "判断课程先修课要求",
                },
            )

        self.assertEqual(captured["rerank_query"], "判断课程先修课要求")

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

    def test_trace_hit_keeps_exact_preview_and_merged_context(self):
        hit = ToolHandler._trace_hit({
            "source": "courses.md",
            "title": "课程注册",
            "doc_type": "markdown",
            "chunk_index": 2,
            "content": "第 2 块\n相邻上下文",
            "_trace_exact_content": "确切命中第一行\n确切命中第二行\n不应出现在预览之后",
            "_trace_exact_chunk_index": 2,
            "_merged_count": 2,
            "score": 0.81,
        }, document_id="doc-123")

        self.assertEqual(hit["matched_chunk_index"], 2)
        self.assertEqual(hit["matched_content_snippet"], "确切命中第一行\n确切命中第二行\n不应出现在预览之后")
        self.assertEqual(hit["content_snippet"], hit["matched_content_snippet"])
        self.assertEqual(hit["merged_content"], "第 2 块\n相邻上下文")
        self.assertEqual(hit["document_id"], "doc-123")

    def test_adjacent_merge_preserves_the_exact_anchor_when_neighbor_is_first(self):
        from src.tools import merge_adjacent_chunks

        merged = merge_adjacent_chunks([
            {"source": "courses.md", "chunk_index": 2, "content": "命中块", "_trace_exact_content": "命中块", "_trace_exact_chunk_index": 2, "score": 0.8},
            {"source": "courses.md", "chunk_index": 1, "content": "前置块", "score": 0.8, "is_context_neighbor": True},
        ], max_chars=100)

        hit = ToolHandler._trace_hit(merged[0])
        self.assertEqual(hit["matched_chunk_index"], 2)
        self.assertEqual(hit["matched_content_snippet"], "命中块")
        self.assertEqual(hit["merged_content"], "前置块\n命中块")

    def test_search_appends_bm25_supplemental_results_when_hybrid_has_no_keyword_hit(self):
        kb = SupplementalBM25KnowledgeBase()
        handler = ToolHandler(kb, rerank_enabled=False)

        output = handler.execute("search_knowledge_base", {"query": "课程注册", "top_k": 3})
        entry = handler.get_call_log()[0]

        self.assertTrue(kb.bm25_called)
        self.assertEqual(entry["result_count"], 2)
        self.assertEqual(len(entry["hits"]), 2)
        self.assertEqual(entry["channels"]["bm25"][0]["source"], "bm25-only.md")
        self.assertIn("关键词匹配结果", output)
        self.assertIn("bm25-only.md", output)

    def test_search_reranks_bm25_rescue_and_keeps_only_final_top_k(self):
        kb = RerankableSupplementalBM25KnowledgeBase()
        handler = ToolHandler(kb, rerank_enabled=True, reranker_loader=lambda: RescueAwareReranker())
        reranked_hybrid = [{
            "source": "hybrid.md",
            "chunk_index": 0,
            "content": "主检索结果",
            "score": 0.2,
            "bm25_score": 0,
            "rerank_score": 0.1,
            "rerank_rank": 1,
        }]

        with patch("src.tools.search_with_optional_rerank", return_value=reranked_hybrid):
            output = handler.execute("search_knowledge_base", {"query": "课程注册", "top_k": 1})

        entry = handler.get_call_log()[0]
        self.assertTrue(kb.bm25_called)
        self.assertIn("精确关键词命中", output)
        self.assertNotIn("主检索结果", output)
        self.assertNotIn("关键词匹配结果", output)
        self.assertEqual(entry["result_count"], 1)
        self.assertEqual(entry["channels"]["hybrid"], [])
        self.assertEqual(entry["channels"]["bm25"][0]["source"], "bm25-only.md")

    def test_bm25_rescue_competes_with_the_full_reranker_candidate_pool(self):
        kb = RerankableSupplementalBM25KnowledgeBase()
        seen_pairs = []

        class CapturingReranker:
            def predict(self, pairs, batch_size, show_progress_bar):
                seen_pairs.extend(pairs)
                return [
                    0.1 if "主检索结果" in content else
                    0.5 if "候选尾部" in content else
                    0.95
                    for _, content in pairs
                ]

        handler = ToolHandler(kb, rerank_enabled=True, reranker_loader=lambda: CapturingReranker())
        hybrid_top = {
            "source": "hybrid.md",
            "chunk_index": 0,
            "content": "主检索结果",
            "score": 0.2,
            "bm25_score": 0,
            "rerank_score": 0.1,
            "rerank_rank": 1,
        }
        hybrid_tail = {
            "source": "hybrid-tail.md",
            "chunk_index": 1,
            "content": "候选尾部",
            "score": 0.15,
            "bm25_score": 0,
        }

        def fake_search(*args, **kwargs):
            return [hybrid_top], [hybrid_top, hybrid_tail]

        with patch("src.tools.search_with_optional_rerank", side_effect=fake_search):
            handler.execute("search_knowledge_base", {"query": "课程注册", "top_k": 1})

        self.assertIn(("课程注册", "候选尾部"), seen_pairs)

    def test_trace_counts_only_ranked_hits_not_context_neighbors(self):
        handler = ToolHandler(ExpandedKnowledgeBase(), rerank_enabled=False)

        with patch("src.tools.AGENT_CHUNK_MERGE_ENABLED", False):
            handler.execute("search_knowledge_base", {"query": "课程注册", "top_k": 3})

        entry = handler.get_call_log()[0]
        self.assertEqual(entry["result_count"], 1)
        self.assertEqual(len(entry["hits"]), 1)
        self.assertEqual(entry["hits"][0]["matched_chunk_index"], 2)


if __name__ == "__main__":
    unittest.main()
