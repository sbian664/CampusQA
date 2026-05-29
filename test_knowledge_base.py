#!/usr/bin/env python3
"""测试 KnowledgeBase 的加载与检索功能"""

from src.knowledge_base import KnowledgeBase

print("=" * 70)
print("KnowledgeBase 功能测试")
print("=" * 70)

kb = KnowledgeBase(embeddings_provider="local")

print("\n📂 开始加载文档到知识库...")
updated = kb.load_documents_from_dir()
print(f"✓ 新增/更新文档数: {updated}")

stats = kb.get_statistics()
print("\n📊 知识库统计:")
print(f"  文件数: {stats['total_files']}")
print(f"  块数: {stats['total_chunks']}")
print(f"  体积: {stats['total_size_mb']} MB")
print(f"  向量维度: {stats['embeddings_dim']}")
print(f"  缓存条目: {stats['cache_size']}")

query = "机器学习"
print(f"\n🔍 搜索示例: {query}")
results = kb.search(query, top_k=3)
for idx, item in enumerate(results, 1):
    print(f"  {idx}. 来源: {item['source']}")
    print(f"     chunk_index: {item['chunk_index']}, score: {item['score']:.4f}")
    print(f"     内容片段: {item['content'][:120]}...")

print("\n" + "=" * 70)
print("✓ KnowledgeBase 测试完成")
print("=" * 70)
