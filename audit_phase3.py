#!/usr/bin/env python3
"""
Phase 3 代码审查脚本 - 检查所有潜在问题
"""

import sys
import ast
import traceback

print("=" * 80)
print("Phase 3 代码质量审查")
print("=" * 80)

# 需要检查的文件
files_to_check = [
    "src/document_loader.py",
    "src/embeddings_manager.py",
    "src/knowledge_base.py",
]

# ============ 第一步：语法检查 ============
print("\n[步骤 1] 语法检查")
print("-" * 80)

syntax_issues = []
for filepath in files_to_check:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        ast.parse(code)
        print(f"✓ {filepath}: 语法正常")
    except SyntaxError as e:
        syntax_issues.append((filepath, e))
        print(f"✗ {filepath}: {e}")

if syntax_issues:
    print(f"\n⚠️  发现 {len(syntax_issues)} 个语法错误")
    sys.exit(1)

# ============ 第二步：导入检查 ============
print("\n[步骤 2] 导入检查")
print("-" * 80)

import_errors = []
try:
    print("✓ 导入 DocumentLoader...")
    from src.document_loader import DocumentLoader
    print(f"  - 成功: {DocumentLoader}")
except Exception as e:
    import_errors.append(("DocumentLoader", e))
    print(f"  ✗ 失败: {e}")

try:
    print("✓ 导入 EmbeddingsManager...")
    from src.embeddings_manager import EmbeddingsManager
    print(f"  - 成功: {EmbeddingsManager}")
except Exception as e:
    import_errors.append(("EmbeddingsManager", e))
    print(f"  ✗ 失败: {e}")

try:
    print("✓ 导入 KnowledgeBase...")
    from src.knowledge_base import KnowledgeBase
    print(f"  - 成功: {KnowledgeBase}")
except Exception as e:
    import_errors.append(("KnowledgeBase", e))
    print(f"  ✗ 失败: {e}")

if import_errors:
    print(f"\n⚠️  发现 {len(import_errors)} 个导入错误")
    for name, error in import_errors:
        print(f"\n{name}:")
        traceback.print_exc()

# ============ 第三步：检查关键方法签名 ============
print("\n[步骤 3] 关键方法和属性检查")
print("-" * 80)

from src.document_loader import DocumentLoader
from src.embeddings_manager import EmbeddingsManager
from src.knowledge_base import KnowledgeBase

checks = [
    ("DocumentLoader", DocumentLoader, [
        "load_file",
        "load_directory",
        "get_file_list",
    ]),
    ("EmbeddingsManager", EmbeddingsManager, [
        "embed_text",
        "embed_batch",
        "get_embedding_dimension",
        "save_cache",
        "clear_cache",
    ]),
    ("KnowledgeBase", KnowledgeBase, [
        "load_documents_from_dir",
        "search",
        "get_statistics",
        "rebuild_index",
    ]),
]

missing_methods = []
for class_name, cls, methods in checks:
    print(f"\n{class_name}:")
    for method_name in methods:
        if hasattr(cls, method_name):
            print(f"  ✓ {method_name}")
        else:
            print(f"  ✗ {method_name} - 缺失")
            missing_methods.append((class_name, method_name))

if missing_methods:
    print(f"\n⚠️  缺失 {len(missing_methods)} 个方法")

# ============ 第四步：配置检查 ============
print("\n[步骤 4] 配置参数检查")
print("-" * 80)

from config import (
    SUPPORTED_FORMATS,
    KB_EMBEDDINGS_PROVIDER,
    EMBEDDINGS_MODEL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    CHROMA_DB_PATH,
    DOCUMENTS_DIR,
)

print(f"✓ SUPPORTED_FORMATS: {SUPPORTED_FORMATS}")
print(f"✓ KB_EMBEDDINGS_PROVIDER: {KB_EMBEDDINGS_PROVIDER}")
print(f"✓ EMBEDDINGS_MODEL: {EMBEDDINGS_MODEL}")
print(f"✓ CHUNK_SIZE: {CHUNK_SIZE}")
print(f"✓ CHUNK_OVERLAP: {CHUNK_OVERLAP}")
print(f"✓ CHROMA_DB_PATH: {CHROMA_DB_PATH}")
print(f"✓ DOCUMENTS_DIR: {DOCUMENTS_DIR}")

# ============ 第五步：初始化测试 ============
print("\n[步骤 5] 对象初始化测试")
print("-" * 80)

init_errors = []

try:
    print("✓ 初始化 DocumentLoader...")
    loader = DocumentLoader()
    print(f"  - 成功: {loader}")
except Exception as e:
    init_errors.append(("DocumentLoader", e))
    print(f"  ✗ 失败: {e}")

try:
    print("✓ 初始化 EmbeddingsManager...")
    em = EmbeddingsManager()
    print(f"  - 成功: {em}")
except Exception as e:
    init_errors.append(("EmbeddingsManager", e))
    print(f"  ✗ 失败: {e}")
    traceback.print_exc()

print("\n" + "=" * 80)
if init_errors or import_errors or missing_methods or syntax_issues:
    print("❌ 审查完成 - 发现问题")
    print(f"  - 语法错误: {len(syntax_issues)}")
    print(f"  - 导入错误: {len(import_errors)}")
    print(f"  - 缺失方法: {len(missing_methods)}")
    print(f"  - 初始化错误: {len(init_errors)}")
    sys.exit(1)
else:
    print("✅ 审查完成 - 所有检查通过")
    print("=" * 80)
