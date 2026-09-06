import unittest
from unittest.mock import patch

from src.reranker import rerank_precomputed_results, search_with_optional_rerank


class RerankerCompatibilityTests(unittest.TestCase):
    def test_legacy_precomputed_entrypoint_keeps_disabled_results(self):
        results = [{"content": "first"}, {"content": "second"}]

        self.assertEqual(
            rerank_precomputed_results(
                "query",
                results,
                top_k=1,
                enabled=False,
            ),
            results[:1],
        )

    def test_search_uses_retrieval_query_for_candidates_and_separate_query_for_reranking(self):
        class RecordingKnowledgeBase:
            def __init__(self):
                self.retrieval_query = None

            def hybrid_search(self, query, top_k, filters=None):
                self.retrieval_query = query
                return [{"content": "candidate", "score": 0.5}]

        class RecordingReranker:
            def __init__(self):
                self.pairs = None

            def predict(self, pairs, batch_size, show_progress_bar):
                self.pairs = pairs
                return [0.9]

        knowledge_base = RecordingKnowledgeBase()
        model = RecordingReranker()

        with patch("src.reranker.RERANKER_AVAILABLE", True):
            results = search_with_optional_rerank(
                knowledge_base,
                "keyword retrieval query",
                top_k=1,
                enabled=True,
                rerank_query="完整用户问题",
                model_loader=lambda: model,
            )

        self.assertEqual(knowledge_base.retrieval_query, "keyword retrieval query")
        self.assertEqual(model.pairs, [("完整用户问题", "candidate")])
        self.assertEqual(results[0]["rerank_score"], 0.9)

    def test_search_can_return_the_full_candidate_pool_for_rescue(self):
        class KnowledgeBase:
            def hybrid_search(self, query, top_k, filters=None):
                return [
                    {"content": "top candidate"},
                    {"content": "candidate retained for rescue"},
                ]

        class Model:
            def predict(self, pairs, batch_size, show_progress_bar):
                return [0.9, 0.1]

        with patch("src.reranker.RERANKER_AVAILABLE", True):
            results, candidates = search_with_optional_rerank(
                KnowledgeBase(),
                "retrieval query",
                top_k=1,
                enabled=True,
                model_loader=lambda: Model(),
                return_candidates=True,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(candidates), 2)

    def test_blank_rerank_query_falls_back_to_retrieval_query(self):
        class KnowledgeBase:
            def hybrid_search(self, query, top_k, filters=None):
                return [{"content": "candidate"}]

        class Model:
            def __init__(self):
                self.pairs = None

            def predict(self, pairs, batch_size, show_progress_bar):
                self.pairs = pairs
                return [0.9]

        model = Model()
        with patch("src.reranker.RERANKER_AVAILABLE", True):
            search_with_optional_rerank(
                KnowledgeBase(),
                "retrieval query",
                top_k=1,
                enabled=True,
                rerank_query="   ",
                model_loader=lambda: model,
            )

        self.assertEqual(model.pairs, [("retrieval query", "candidate")])


if __name__ == "__main__":
    unittest.main()
