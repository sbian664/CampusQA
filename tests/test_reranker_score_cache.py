"""Regression tests for reusing CrossEncoder scores without changing ranking."""

import unittest
from unittest.mock import patch

from src.reranker import (
    RerankScoreCache,
    rerank_results,
    rerank_precomputed_results,
    search_with_optional_rerank,
)
from src.tools import ToolHandler


class CountingModel:
    def __init__(self, scores=None):
        self.scores = scores or {}
        self.calls = []
        self.fail_next = False

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append(list(pairs))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("inference failure")
        return [self.scores.get((query, content), self.scores.get(content, 0.0))
                for query, content in pairs]

    @property
    def inferred_pairs(self):
        return [pair for call in self.calls for pair in call]


def chunk(content, index):
    return {
        "source": "test.md",
        "chunk_index": index,
        "content": content,
        "title": "Test",
        "doc_type": "markdown",
        "score": 0.7,
        "bm25_score": 0.0,
        "metadata": {"doc_total_chunks": 4},
    }


class RerankScoreCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = RerankScoreCache(max_entries=8)
        self.patch_cache = patch("src.reranker._score_cache", self.cache)
        self.patch_enabled = patch("src.reranker.RERANKER_AVAILABLE", True)
        self.patch_cache.start()
        self.patch_enabled.start()
        self.addCleanup(self.patch_cache.stop)
        self.addCleanup(self.patch_enabled.stop)

    def test_full_initial_pool_reused_with_changed_order_and_top_k(self):
        model = CountingModel({"a": 0.1, "b": 0.9, "c": 0.5})
        initial = [chunk("a", 0), chunk("b", 1), chunk("c", 2)]
        self.assertEqual(rerank_results("Q", initial, model, 1)[0]["content"], "b")
        self.assertEqual(len(model.inferred_pairs), 3)
        ranked = rerank_precomputed_results(
            "Q", list(reversed(initial)), 3, True, lambda: model
        )
        self.assertEqual([x["content"] for x in ranked], ["b", "c", "a"])
        self.assertEqual(len(model.inferred_pairs), 3)
        self.assertNotIn("rerank_score", initial[0])

    def test_only_new_rescue_pairs_inferred_and_duplicates_deduplicated(self):
        model = CountingModel({"a": 0.4, "b": 0.1, "rescue": 0.9})
        rerank_results("full question", [chunk("a", 0), chunk("b", 1)], model, 1)
        reranked = rerank_precomputed_results(
            "full question",
            [chunk("a", 0), chunk("b", 1), chunk("rescue", 2), chunk("rescue", 3)],
            2, True, lambda: model,
        )
        self.assertEqual(len(model.inferred_pairs), 3)
        self.assertEqual(model.inferred_pairs[-1], ("full question", "rescue"))
        self.assertEqual(reranked[0]["content"], "rescue")

    def test_cache_keys_use_effective_query_and_document_text(self):
        model = CountingModel({
            ("question A", "document"): 0.7,
            ("question B", "document"): 0.1,
        })
        rerank_results("question A", [chunk("document", 0)], model, 1)
        rerank_results("question A", [chunk("document", 77)], model, 1)
        rerank_results("question B", [chunk("document", 0)], model, 1)
        rerank_results("question A", [chunk("changed document", 0)], model, 1)
        self.assertEqual(model.inferred_pairs, [
            ("question A", "document"),
            ("question B", "document"),
            ("question A", "changed document"),
        ])

    def test_model_replacement_invalidates_scores(self):
        original = CountingModel({"content": 0.9})
        replaced = CountingModel({"content": -0.2})
        item = [chunk("content", 0)]
        self.assertEqual(rerank_results("Q", item, original, 1)[0]["rerank_score"], 0.9)
        self.assertEqual(rerank_results("Q", item, replaced, 1)[0]["rerank_score"], -0.2)
        self.assertEqual(len(replaced.inferred_pairs), 1)
        # Reverting to the old model must not use the replaced model's score.
        self.assertEqual(rerank_results("Q", item, original, 1)[0]["rerank_score"], 0.9)
        self.assertEqual(len(original.inferred_pairs), 2)

    def test_bounded_cache_evicts_oldest_entry(self):
        with patch("src.reranker._score_cache", RerankScoreCache(max_entries=2)):
            model = CountingModel()
            for value in ("a", "b", "c", "a"):
                rerank_results("Q", [chunk(value, 0)], model, 1)
            self.assertEqual(len(model.inferred_pairs), 4)

    def test_failed_predict_does_not_cache_partial_scores(self):
        model = CountingModel({"content": 0.5})
        model.fail_next = True
        with self.assertRaises(RuntimeError):
            rerank_results("Q", [chunk("content", 0)], model, 1)
        result = rerank_results("Q", [chunk("content", 0)], model, 1)
        self.assertEqual(result[0]["rerank_score"], 0.5)
        self.assertEqual(len(model.calls), 2)

    def test_disabled_reranker_never_loads_or_scores(self):
        initial = [chunk("a", 0), chunk("b", 1)]

        class KB:
            def hybrid_search(self, query, top_k, filters=None):
                return initial

        with patch("src.reranker.RERANKER_AVAILABLE", False):
            output = search_with_optional_rerank(
                KB(), "Q", top_k=1, enabled=True,
                model_loader=lambda: self.fail("model must not load"),
            )
        self.assertEqual(output, initial[:1])

    def test_bm25_rescue_scores_only_new_document_not_original_pool(self):
        model = CountingModel({"a": 0.8, "b": 0.3, "c": 0.2, "rescue": 0.95})

        class KB:
            metadata = {}
            _bm25_doc_freq = {"term": 1}

            def hybrid_search(self, query, top_k, filters=None, **kwargs):
                return [chunk("a", 0), chunk("b", 1), chunk("c", 2)]

            def _tokenize_query(self, query):
                return ["term"]

            def bm25_search(self, query, top_k, filters=None):
                result = chunk("rescue", 3)
                result["bm25_score"] = 1.1
                return [result]

        handler = ToolHandler(
            KB(), rerank_enabled=True, reranker_loader=lambda: model,
            user_query="full original user question",
        )
        output = handler.execute("search_knowledge_base", {
            "query": "term", "top_k": 1, "rerank_query_source": "user_query",
        })
        self.assertEqual(len(model.calls), 2)
        self.assertEqual([len(call) for call in model.calls], [3, 1])
        self.assertEqual(len(model.inferred_pairs), 4)
        self.assertTrue(all(query == "full original user question"
                            for query, _ in model.inferred_pairs))
        self.assertIn("rescue", output)
        self.assertEqual(handler.get_call_log()[0]["engine"], "reranked")

        # Same user question, different retrieval query: reuse all scores.
        handler.execute("search_knowledge_base", {
            "query": "another term", "top_k": 1,
            "rerank_query_source": "user_query",
        })
        self.assertEqual(len(model.inferred_pairs), 4)

        # A custom rewrite is not fixed across tool calls: rescore all pairs.
        handler.execute("search_knowledge_base", {
            "query": "term", "top_k": 1,
            "rerank_query_source": "custom",
            "rerank_query": "different rewritten question",
        })
        self.assertEqual(len(model.inferred_pairs), 8)


if __name__ == "__main__":
    unittest.main()
