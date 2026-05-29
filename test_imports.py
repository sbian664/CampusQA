#!/usr/bin/env python3
"""测试Stage 3模块导入"""

import sys
import time

print("=" * 60)
print("Stage 3 模块导入测试")
print("=" * 60)

# Test 1: Python 环境
print("\n✓ 测试 1: Python 环境")
print(f"  Python 版本: {sys.version}")

# Test 2: LangChain Community
try:
    print("\n⏳ 测试 2: 导入 LangChain Community...")
    start = time.time()
    from langchain_community.document_loaders import TextLoader, PyPDFLoader
    elapsed = time.time() - start
    print(f"  ✓ LangChain Community 导入成功 ({elapsed:.2f}s)")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

# Test 3: Document Loader
try:
    print("\n⏳ 测试 3: 导入 DocumentLoader...")
    start = time.time()
    from src.document_loader import DocumentLoader
    elapsed = time.time() - start
    print(f"  ✓ DocumentLoader 导入成功 ({elapsed:.2f}s)")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

# Test 4: Embeddings Manager
try:
    print("\n⏳ 测试 4: 导入 EmbeddingsManager...")
    start = time.time()
    from src.embeddings_manager import EmbeddingsManager
    elapsed = time.time() - start
    print(f"  ✓ EmbeddingsManager 导入成功 ({elapsed:.2f}s)")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

# Test 5: Knowledge Base
try:
    print("\n⏳ 测试 5: 导入 KnowledgeBase...")
    start = time.time()
    from src.knowledge_base import KnowledgeBase
    elapsed = time.time() - start
    print(f"  ✓ KnowledgeBase 导入成功 ({elapsed:.2f}s)")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✓ 所有模块导入成功！")
print("=" * 60)
