import unittest

from src.reranker import rerank_precomputed_results


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


if __name__ == "__main__":
    unittest.main()
