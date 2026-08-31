import unittest

from src.knowledge_base import (
    DocumentIndexResult,
    KnowledgeBase,
    KnowledgeRetrievalResult,
)


class FailingAddStore:
    def __init__(self):
        self.ids = ["old_0"]

    def delete(self, ids):
        self.ids = [item for item in self.ids if item not in set(ids)]

    def add(self, ids, documents, metadatas, embeddings):
        if "new_0" in ids:
            raise RuntimeError("simulated new vector write failure")
        self.ids.extend(ids)


class KnowledgeBaseCommitTests(unittest.TestCase):
    def test_legacy_index_and_retrieve_api_remains_available(self):
        self.assertIsInstance(DocumentIndexResult(), DocumentIndexResult)
        self.assertIsInstance(KnowledgeRetrievalResult(), KnowledgeRetrievalResult)
        self.assertTrue(callable(KnowledgeBase.index_document))
        self.assertTrue(callable(KnowledgeBase.index_documents))
        self.assertTrue(callable(KnowledgeBase.retrieve))

        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb.hybrid_search = lambda query, top_k, filters: [{
            "content": "semantic",
            "bm25_score": 0,
        }]
        kb.bm25_search = lambda query, top_k, filters: [{
            "content": "keyword",
            "bm25_score": 1,
        }]
        kb._tokenize_query = lambda query: ["term"]
        kb._bm25_doc_freq = {"term": 1}

        retrieved = kb.retrieve("term", top_k=1, filters={"doc_type": "text"})

        self.assertEqual(retrieved.results[0]["content"], "semantic")
        self.assertEqual(retrieved.bm25_results[0]["content"], "keyword")

    def test_failed_replacement_restores_old_vector(self):
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb.store = FailingAddStore()
        kb.metadata = {"doc.txt": {"chunk_ids": ["old_0"]}}
        kb._chunk_texts = {"old_0": "old content"}
        kb._chunk_metadata = {
            "old_0": {"source": "doc.txt", "chunk_index": 0},
        }
        kb.embeddings_manager = type(
            "EmbeddingStub",
            (),
            {"embed_text": lambda self, text: [0.1]},
        )()
        kb._enrich_chunk_text = lambda text, metadata: text

        with self.assertRaises(RuntimeError):
            kb._commit_prepared_document_update({
                "file_path": "doc.txt",
                "chunk_ids": ["new_0"],
                "chunk_texts": ["new content"],
                "chunk_metadatas": [{
                    "source": "doc.txt",
                    "chunk_index": 0,
                }],
                "chunk_vectors": [[0.2]],
                "file_mtime": 2,
                "file_size": 11,
            })

        self.assertEqual(kb.store.ids, ["old_0"])
        self.assertIn("old_0", kb._chunk_texts)


if __name__ == "__main__":
    unittest.main()
