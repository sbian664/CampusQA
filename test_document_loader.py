#!/usr/bin/env python3
"""测试DocumentLoader的多格式加载功能"""

import os
from pathlib import Path
from src.document_loader import DocumentLoader
from config import DOCUMENTS_DIR

print("=" * 70)
print("DocumentLoader 功能测试")
print("=" * 70)

# 初始化 DocumentLoader
loader = DocumentLoader(DOCUMENTS_DIR)

print(f"\n📁 文档目录: {DOCUMENTS_DIR}")
print(f"✓ 目录存在: {os.path.exists(DOCUMENTS_DIR)}")

# 列出文件
print("\n📋 目录中的文件:")
files = loader.get_file_list(recursive=True)
for file_info in files:
    print(f"  - {file_info['filename']:<30} ({file_info['size']:>6} 字节) [{file_info['format']}]")

# 测试加载每个文件
print("\n⏳ 测试加载各格式文件...\n")

# Markdown
print("1️⃣  测试 Markdown (.md) 文件:")
try:
    md_docs = loader.load_file(os.path.join(DOCUMENTS_DIR, "python_tutorial.md"))
    print(f"   ✓ 成功加载: {len(md_docs)} 个文档")
    if md_docs:
        print(f"   内容预览: {md_docs[0].page_content[:100]}...")
except Exception as e:
    print(f"   ✗ 错误: {e}")

# TXT
print("\n2️⃣  测试 TXT (.txt) 文件:")
try:
    txt_docs = loader.load_file(os.path.join(DOCUMENTS_DIR, "ml_basics.txt"))
    print(f"   ✓ 成功加载: {len(txt_docs)} 个文档")
    if txt_docs:
        print(f"   内容预览: {txt_docs[0].page_content[:100]}...")
except Exception as e:
    print(f"   ✗ 错误: {e}")

# HTML
print("\n3️⃣  测试 HTML (.html) 文件:")
try:
    html_docs = loader.load_file(os.path.join(DOCUMENTS_DIR, "deep_learning.html"))
    print(f"   ✓ 成功加载: {len(html_docs)} 个文档")
    if html_docs:
        print(f"   内容预览: {html_docs[0].page_content[:100]}...")
except Exception as e:
    print(f"   ✗ 错误: {e}")

# 批量加载
print("\n4️⃣  测试批量加载 (递归模式):")
try:
    all_docs = loader.load_directory([".md", ".txt", ".html"], recursive=True)
    print(f"   ✓ 成功加载: {len(all_docs)} 个总文档")
    print(f"   按格式统计:")
    formats = {}
    for doc in all_docs:
        fmt = doc.metadata.get('source', '').split('.')[-1]
        formats[fmt] = formats.get(fmt, 0) + 1
    for fmt, count in sorted(formats.items()):
        print(f"     - {fmt}: {count} 个")
except Exception as e:
    print(f"   ✗ 错误: {e}")

print("\n" + "=" * 70)
print("✓ DocumentLoader 测试完成")
print("=" * 70)
