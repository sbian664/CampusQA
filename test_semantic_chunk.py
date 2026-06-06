"""验证语义分块效果"""
from src.text_chunker import SemanticChunker

# 直接测试分块器
with open("data/documents/ml_basics.txt", "r", encoding="utf-8") as f:
    text = f.read()

chunker = SemanticChunker(500, 50)
chunks = chunker.split_text(text)

print("=" * 60)
print("语义分块验证: ml_basics.txt")
print("=" * 60)
print(f"分块数: {len(chunks)}\n")

workflow_intact = False
for i, c in enumerate(chunks, 1):
    has_step1 = "步骤1" in c
    has_step6 = "步骤6" in c
    has_workflow = "工作流程" in c
    status = "✓ 完整" if (has_step1 and has_step6) else ("—" if not has_workflow else "✗ 截断")
    print(f"块{i} ({len(c)}字) step1={has_step1} step6={has_step6} {status}")
    if has_step1 and has_step6:
        workflow_intact = True
        # 展示标题到步骤的连续性
        for line in c.split("\n")[:3]:
            print(f"  {line.strip()}")
        print(f"  ... ({len(c)} 字完整保留)")

print()
if workflow_intact:
    print("✓ 机器学习的工作流程完整保留，未被切断！")
else:
    print("✗ 工作流程仍被截断")

# 也检查监督学习部分
print()
for i, c in enumerate(chunks, 1):
    if "监督学习 (Supervised Learning)" in c:
        has_reg = "回归" in c
        has_cls = "分类" in c
        print(f"监督学习部分: 分类={'✓' if has_cls else '✗'} 回归={'✓' if has_reg else '✗'} {'✓ 完整' if (has_reg and has_cls) else '✗'}")
        break

print("=" * 60)

