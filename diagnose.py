#!/usr/bin/env python3
"""诊断依赖问题"""

import sys

# 逐个测试导入
tests = [
    ("chromadb", "import chromadb"),
    ("langchain_text_splitters", "from langchain_text_splitters import RecursiveCharacterTextSplitter"),
    ("langchain_core", "from langchain_core.documents import Document"),
]

print("=" * 60)
print("诊断 LangChain 相关导入")
print("=" * 60)

for module_name, import_stmt in tests:
    try:
        print(f"\n✓ 测试: {module_name}")
        exec(import_stmt)
        print(f"  ✓ 成功")
    except ImportError as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        sys.exit(1)

print("\n" + "=" * 60)
print("✓ 所有导入检查通过")
print("=" * 60)
