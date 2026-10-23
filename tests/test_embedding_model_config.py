import unittest

from config import EMBEDDINGS_MODEL, EMBEDDINGS_MODEL_PATH


class EmbeddingModelConfigTests(unittest.TestCase):
    def test_default_embedding_model_is_granite_multilingual_r2(self):
        self.assertEqual(
            EMBEDDINGS_MODEL,
            "ibm-granite/granite-embedding-97m-multilingual-r2",
        )
        self.assertTrue(
            EMBEDDINGS_MODEL_PATH.replace("\\", "/").endswith(
                "/data/embeddings/granite-embedding-97m-multilingual-r2"
            )
        )


if __name__ == "__main__":
    unittest.main()
