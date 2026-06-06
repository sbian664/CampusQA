"""Phase 5 集成验证 — VectorStore + Hybrid Search + Multi-provider Embeddings"""
import sys
import os

print("=" * 60)
print("Phase 5 Integration Test")
print("=" * 60)

# ---- Test 1: VectorStore Factory ----
print("\n[Test 1] VectorStore Factory")
from src.vector_store import create_vector_store, ChromaStore, FaissStore

chroma = create_vector_store("chroma")
print(f"  ChromaStore: {type(chroma).__name__}, count={chroma.count()}")

faiss = create_vector_store("faiss", dimension=384)
print(f"  FaissStore: {type(faiss).__name__}, count={faiss.count()}")

# Test Faiss add + search
import numpy as np
dummy_emb = np.random.randn(384).tolist()
faiss.add(
    ids=["test_0", "test_1"],
    documents=["hello world", "machine learning"],
    metadatas=[{"src": "a"}, {"src": "b"}],
    embeddings=[dummy_emb, dummy_emb],
)
print(f"  Faiss after add: count={faiss.count()}")
res = faiss.search(dummy_emb, 2)
print(f"  Faiss search: returned {len(res['documents'][0])} results")
assert res['documents'][0][0] == "hello world"

faiss.clear()
assert faiss.count() == 0
print("  ✓ Faiss add/search/clear OK")

# ---- Test 2: EmbeddingsManager Multi-Provider ----
print("\n[Test 2] EmbeddingsManager Multi-Provider")
from src.embeddings_manager import EmbeddingsManager, LocalEmbeddingsProvider

em = EmbeddingsManager("local")
vec = em.embed_text("测试文本")
print(f"  Local provider: dim={em.get_embedding_dimension()}, vec[0]={vec[0]:.4f}")
assert len(vec) == 384

# Test SHA256 cache key (fix for hash collision)
key1 = em._make_cache_key("test")
key2 = em._make_cache_key("test")
key3 = em._make_cache_key("test2")
assert key1 == key2
assert key1 != key3
print(f"  Cache key: {key1[:16]}... (SHA256)")
print("  ✓ EmbeddingsManager OK")

# ---- Test 3: KnowledgeBase with Chroma ----
print("\n[Test 3] KnowledgeBase (Chroma + Hybrid Search)")
from src.knowledge_base import KnowledgeBase

kb = KnowledgeBase(embeddings_provider="local", vector_store="chroma")
kb.load_documents_from_dir()

stats = kb.get_statistics()
print(f"  Store: {stats['store_type']}, Hybrid: {stats['hybrid_search']}")
print(f"  Files: {stats['total_files']}, Chunks: {stats['total_chunks']}")
assert stats['total_files'] >= 3
assert stats['total_chunks'] >= 6

# Test vector search
results = kb.search("机器学习", top_k=3)
print(f"  Vector search: {len(results)} results")
for r in results:
    print(f"    [{r['score']:.3f}] {r['source'].split(chr(92))[-1]}")

# Test hybrid search
hybrid = kb.hybrid_search("机器学习", top_k=3)
print(f"  Hybrid search: {len(hybrid)} results")
for r in hybrid:
    print(f"    [{r['score']:.3f}] BM25={r.get('bm25_score',0):.3f} Vec={r.get('vector_score',0):.3f} | {r['source'].split(chr(92))[-1]}")
assert len(hybrid) > 0
assert 'bm25_score' in hybrid[0]
print("  ✓ Hybrid search OK")

# ---- Test 4: KnowledgeBase with Faiss ----
print("\n[Test 4] KnowledgeBase (Faiss)")
# Clean up old faiss index and isolate metadata
faiss_path = os.path.join("data", "faiss_index.bin")
meta_path = faiss_path + ".meta"
chunk_path = os.path.join("data", "cache", "kb_metadata_chunks.json")
for p in [faiss_path, meta_path, chunk_path]:
    if os.path.exists(p):
        os.remove(p)

kb2 = KnowledgeBase(embeddings_provider="local", vector_store="faiss")
# Clear any shared metadata to force re-index
kb2.metadata.clear()
kb2._chunk_texts.clear()
kb2.load_documents_from_dir()

stats2 = kb2.get_statistics()
print(f"  Store: {stats2['store_type']}, Count: {stats2['store_count']}")
assert stats2['store_type'] == "faiss"
assert stats2['store_count'] == stats2['total_chunks']

# Search
results2 = kb2.search("深度学习", top_k=2)
print(f"  Faiss search: {len(results2)} results")
for r in results2:
    print(f"    [{r['score']:.3f}] {r['source'].split(chr(92))[-1]}")

# Test persistence
kb2.store.save()
kb3 = KnowledgeBase(embeddings_provider="local", vector_store="faiss")
assert kb3.store.count() == kb2.store.count()
print(f"  Persistence: saved {kb2.store.count()}, loaded {kb3.store.count()}")
print("  ✓ Faiss persistence OK")

# Cleanup test files
for p in [faiss_path, meta_path]:
    if os.path.exists(p):
        os.remove(p)

print("\n" + "=" * 60)
print("All Phase 5 integration tests passed!")
print("=" * 60)
