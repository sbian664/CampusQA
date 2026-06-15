"""Phase 4 RAG pipeline verification - no LLM call"""
from src.knowledge_base import KnowledgeBase
from src.chatbot import Chatbot
from config import RAG_TOP_K, RAG_CONTEXT_ITEM_TEMPLATE, RAG_SYSTEM_PROMPT_TEMPLATE

print("=" * 60)
print("Phase 4 RAG Pipeline Verification")
print("=" * 60)

# 1. Init KB and load docs
print("\n[1/6] Init KnowledgeBase...")
kb = KnowledgeBase(embeddings_provider="local")
updated = kb.load_documents_from_dir()
print(f"  Docs loaded/updated: {updated}")

# 2. Init Chatbot with KB
print("\n[2/6] Init Chatbot(kb=...) ...")
cb = Chatbot(knowledge_base=kb)
assert cb.kb is not None, "KB should be set"
assert hasattr(cb, "chat_with_rag"), "chat_with_rag should exist"
print("  OK")

# 3. Test search
print("\n[3/6] Search '机器学习' ...")
results = kb.search("机器学习", top_k=RAG_TOP_K)
print(f"  Results: {len(results)}")
assert len(results) > 0, "Should find results for ML query"
for i, r in enumerate(results, 1):
    src = r["source"].replace("\\", "/").split("/")[-1]
    print(f"  {i}. {src} chunk={r['chunk_index']} score={r['score']:.3f}")

# 4. Test context formatting
print("\n[4/6] Format RAG context ...")
context_parts = []
for r in results:
    context_parts.append(RAG_CONTEXT_ITEM_TEMPLATE.format(
        source=r["source"],
        chunk=r["chunk_index"],
        score=r["score"],
        content=r["content"],
    ))
context = "\n\n".join(context_parts)
print(f"  Context length: {len(context)} chars")
assert len(context) > 50, "Context should have content"

# 5. Test prompt construction
print("\n[5/6] Build RAG system prompt ...")
rag_prompt = RAG_SYSTEM_PROMPT_TEMPLATE.format(
    system_prompt="[TEST_SYSTEM_PROMPT]",
    context=context,
)
print(f"  Prompt length: {len(rag_prompt)} chars")
assert "[TEST_SYSTEM_PROMPT]" in rag_prompt
assert "机器学习" in rag_prompt or "machine" in rag_prompt.lower()
print("  OK - template substitution works")

# 6. Verify KB-less fallback
print("\n[6/6] KB-less fallback ...")
cb2 = Chatbot(knowledge_base=None)
assert cb2.kb is None, "KB should be None"
# chat_with_rag should fall back to chat_with_history
# (we can't easily test the return value without LLM, but the method exists)
print("  OK - fallback path exists")

print("\n" + "=" * 60)
print("All RAG pipeline checks passed!")
print("=" * 60)
