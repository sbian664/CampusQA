import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.documents import Document

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
    def test_prepared_chunk_metadata_omits_missing_document_id(self):
        with TemporaryDirectory() as temp_dir:
            file_path = str(Path(temp_dir) / "guide.md")
            Path(file_path).write_text("# Guide", encoding="utf-8")
            kb = KnowledgeBase.__new__(KnowledgeBase)
            kb.loader = type("Loader", (), {"load_file": lambda self, path: [Document(page_content="# Guide", metadata={"doc_type": "markdown"})]})()
            kb.text_splitter = type("Splitter", (), {"split_documents": lambda self, docs: [Document(page_content="# Guide", metadata={})]})()
            kb.metadata = {}
            kb.embeddings_manager = type("EmbeddingStub", (), {"embed_text": lambda self, text: [0.1]})()
            kb._enrich_chunk_text = lambda text, metadata: text

            prepared = kb._prepare_document_update(file_path, 1)

            self.assertNotIn("document_id", prepared["chunk_metadatas"][0])

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

    def test_mixed_alphanumeric_identifiers_remain_distinct_tokens(self):
        tokens_a = KnowledgeBase._tokenize("normalized_code: AIAA6091A")
        tokens_b = KnowledgeBase._tokenize("normalized_code: AIAA6091B")

        self.assertIn("aiaa6091a", tokens_a)
        self.assertIn("aiaa6091b", tokens_b)
        self.assertNotIn("aiaa6091b", tokens_a)
        self.assertNotIn("aiaa6091a", tokens_b)

    def test_quoted_multi_part_entity_matches_document_text_in_bm25(self):
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb._bm25_corpus = ["办公室：E1 L2"]
        kb._bm25_avgdl = 4
        kb._bm25_doc_freq = {"e1 l2": 1}

        query = '"E1 L2" 办公室'
        score = kb._bm25_score(
            query,
            "办公室：E1 L2",
            query_tokens=KnowledgeBase._tokenize_query(query),
        )

        self.assertGreater(score, 0)

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
