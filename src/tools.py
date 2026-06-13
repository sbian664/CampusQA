"""
工具定义 + ToolHandler — Agent Loop 的工具层

定义 search_knowledge_base 工具 schema（OpenAI function calling 格式）
ToolHandler 负责工具执行、结果格式化、异常处理、相邻分块合并
"""
import json
import time
from typing import List, Dict, Optional
from collections import defaultdict

from config import (
    HYBRID_SEARCH_ENABLED, BM25_WEIGHT,
    AGENT_CHUNK_MERGE_ENABLED, AGENT_CHUNK_MERGE_MAX_CHARS,
)


# ═══════════════════════════════════════════════════════════
#  工具 Schema 定义
# ═══════════════════════════════════════════════════════════

SEARCH_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "在知识库中进行混合检索（语义理解 + 关键词匹配）。"
            "返回与查询相关的文档片段，包含来源文件和相似度分数。\n"
            "适用场景：需要从已加载的文档中查找信息时。\n"
            "不适用场景：简单寒暄、常识性问题、纯代码生成。\n"
            "检索策略建议：首次用问题的核心关键词；若结果不理想，尝试同义词或更宽泛/更具体的表述；"
            "也可用 filters 限定文档类型或时间范围。\n"
            "⚠️ 完整性提示：每条结果会标注该文档共有多少块（如 [文档共 12 块，本次展示 3 块]）。"
            "若发现检索到的块数远小于文档总块数且信息不完整，应增大 top_k 或用 filters.source 限定该文档重新检索。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询词。建议提取用户问题的核心概念，而非完整句子。",
                },
                "top_k": {
                    "type": "integer",
                    "default": 3,
                    "description": "返回结果条数，默认 3。若需更多上下文可调大至 5~8。",
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "可选元数据过滤条件，用于缩小检索范围。"
                        "支持字段: doc_type（文档类型: markdown/text/pdf/html）、"
                        "source（按源文件路径过滤，用于获取某文档的全部块）、"
                        "mtime_after（修改时间晚于指定日期，格式 YYYY-MM-DD）、"
                        "mtime_before（修改时间早于指定日期）"
                    ),
                    "properties": {
                        "doc_type": {"type": "string"},
                        "source": {"type": "string"},
                        "mtime_after": {"type": "string"},
                        "mtime_before": {"type": "string"},
                    },
                },
            },
            "required": ["query"],
        },
    },
}


# Agent Loop 可用的工具列表（目前仅一个，后续可扩展）
AGENT_TOOLS = [SEARCH_KB_TOOL]


# ═══════════════════════════════════════════════════════════
#  相邻分块合并（完整性感知）
# ═══════════════════════════════════════════════════════════

def merge_adjacent_chunks(results: List[Dict], max_chars: int = None) -> List[Dict]:
    """
    将同一文档的相邻分块合并为大块，提升上下文完整性。

    策略：
    1. 按 source 分组，组内按 chunk_index 排序
    2. 相邻 chunk（index 连续）且合并后不超 max_chars → 合并
    3. 合并后分数取最高值，chunk_index 取首块

    Args:
        results: hybrid_search/search 返回的结果列表
        max_chars: 合并后单块最大字符数

    Returns:
        合并后的结果列表
    """
    if not results or len(results) <= 1:
        return results

    if max_chars is None:
        max_chars = AGENT_CHUNK_MERGE_MAX_CHARS

    # 按 source 分组
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        groups[r.get("source", "unknown")].append(r)

    merged = []
    for source, items in groups.items():
        # 按 chunk_index 排序
        items.sort(key=lambda x: x.get("chunk_index", 0))

        current = None
        for item in items:
            if current is None:
                current = dict(item)
                current["_merged_count"] = 1
                continue

            # 判断是否可合并：chunk_index 连续 且 合并不超限
            prev_idx = current.get("chunk_index", 0) + current.get("_merged_count", 1)
            curr_idx = item.get("chunk_index", -1)
            combined_len = len(current.get("content", "")) + len(item.get("content", ""))

            if curr_idx == prev_idx and combined_len <= max_chars:
                # 合并：拼接内容，保留最高分
                current["content"] = current["content"] + "\n" + item["content"]
                current["score"] = max(current["score"], item.get("score", 0))
                current["chunk_index"] = min(current["chunk_index"], curr_idx)
                current["_merged_count"] = current.get("_merged_count", 1) + 1
            else:
                merged.append(current)
                current = dict(item)
                current["_merged_count"] = 1

        if current is not None:
            merged.append(current)

    return merged


# ═══════════════════════════════════════════════════════════
#  搜索结果的 LLM 可读格式化
# ═══════════════════════════════════════════════════════════

def format_search_results(results: List[Dict]) -> str:
    """将检索结果格式化为 LLM 可读文本（含文档完整性提示）"""
    if not results:
        return "[NO_RESULTS] 知识库中未找到相关内容。"

    # ── 先统计每个文档的可见块数 vs 总块数 ──
    doc_stats: Dict[str, Dict] = {}
    for r in results:
        source = r.get("source", "unknown")
        if source not in doc_stats:
            total = r.get("metadata", {}).get("doc_total_chunks")
            if total is None:
                # 兼容旧元数据（无 doc_total_chunks）
                total = r.get("doc_total_chunks")
            doc_stats[source] = {
                "title": r.get("title", ""),
                "total": total or "?",
                "shown": 0,
            }
        doc_stats[source]["shown"] += 1

    # ── 格式化每条结果 ──
    parts = []
    for i, r in enumerate(results, 1):
        source = r.get("source", "unknown")
        source_name = source.replace("\\", "/").split("/")[-1]
        score = r.get("score", 0)
        doc_type = r.get("doc_type", "unknown")
        title = r.get("title", "")
        content = r.get("content", "")
        merged_count = r.get("_merged_count", 1)

        # 分数等级
        if score >= 0.7:
            level = "★★★ 高相关"
        elif score >= 0.4:
            level = "★★☆ 中等相关"
        else:
            level = "★☆☆ 低相关"

        # 完整性信息
        ds = doc_stats.get(source, {})
        total = ds.get("total", "?")
        shown = ds.get("shown", 1)
        merged_hint = f"，已合并 {merged_count} 块" if merged_count > 1 else ""
        completeness = f"[文档共 {total} 块，本次检索到 {shown} 块{merged_hint}]"

        parts.append(
            f"[结果 {i}] {level} | 分数: {score:.3f} | {completeness}\n"
            f"  来源: {source_name} | 类型: {doc_type} | 标题: {title}\n"
            f"  内容: {content}"
        )

    # ── 完整性警告 ──
    warnings = []
    for source, ds in doc_stats.items():
        total = ds["total"]
        shown = ds["shown"]
        if isinstance(total, int) and total > shown:
            source_name = source.replace("\\", "/").split("/")[-1]
            warnings.append(
                f"  ⚠️ {source_name} 共 {total} 块，仅检索到 {shown} 块 "
                f"（覆盖率 {shown}/{total}）。如需完整内容，建议以 source=\"{source}\" 过滤并增大 top_k 重新检索。"
            )

    # ── 汇总评估 ──
    top_score = max(r.get("score", 0) for r in results)
    if top_score >= 0.7:
        hint = "结果高概率符合问题，可基于结果回答"
    elif top_score >= 0.4:
        hint = "检索结果质量中等。若觉得信息不完整，可尝试用不同关键词或放宽条件重新检索。"
    else:
        hint = "检索结果相关性较低。建议尝试完全不同的检索策略，或直接告知用户知识库可能不包含相关信息。"

    result_text = "\n\n".join(parts) + f"\n\n[检索评估] {hint}"
    if warnings:
        result_text += "\n\n[完整性警告]\n" + "\n".join(warnings)

    return result_text


# ═══════════════════════════════════════════════════════════
#  ToolHandler — 工具执行分发
# ═══════════════════════════════════════════════════════════

class ToolHandler:
    """工具处理器 — 接收 KnowledgeBase 实例，分发并执行工具调用"""

    def __init__(self, knowledge_base):
        """
        Args:
            knowledge_base: KnowledgeBase 实例
        """
        self.kb = knowledge_base
        self.call_log: List[Dict] = []  # 工具调用日志

    def execute(self, tool_name: str, arguments: Dict) -> str:
        """
        执行工具调用

        Args:
            tool_name: 工具名称
            arguments: 工具参数（已解析为 dict）

        Returns:
            格式化的工具执行结果文本
        """
        start_time = time.time()

        if tool_name == "search_knowledge_base":
            result_text = self._handle_search_knowledge_base(arguments)
        else:
            result_text = f"[ERROR] 未知工具: {tool_name}"

        duration_ms = round((time.time() - start_time) * 1000)

        # 记录日志
        log_entry = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result_preview": result_text[:200],
            "duration_ms": duration_ms,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.call_log.append(log_entry)

        return result_text

    def _handle_search_knowledge_base(self, args: Dict) -> str:
        """
        执行 search_knowledge_base 工具（含分块合并 + 完整性感知）

        Args:
            args: {"query": str, "top_k"?: int, "filters"?: dict}
        """
        query = args.get("query", "")
        if not query:
            return "[ERROR] 缺少必填参数 query"

        top_k = args.get("top_k", 3)
        # 放宽上限：LLM 自主增大 top_k 时有足够空间
        top_k = max(1, min(int(top_k), 20))

        filters = args.get("filters")
        # 规范化 filters：只保留已知字段
        if filters and isinstance(filters, dict):
            valid_filters = {}
            known_keys = {"doc_type", "mtime_after", "mtime_before", "source"}
            for k, v in filters.items():
                if k in known_keys:
                    valid_filters[k] = v
            filters = valid_filters if valid_filters else None

        try:
            if HYBRID_SEARCH_ENABLED and hasattr(self.kb, "hybrid_search"):
                results = self.kb.hybrid_search(query, top_k=top_k, filters=filters)
            else:
                results = self.kb.search(query, top_k=top_k, filters=filters)
        except Exception as e:
            return f"[ERROR] 搜索执行失败: {type(e).__name__}: {str(e)}"

        # Agent 双通道：若语义匹配全无关键词命中，追加 BM25 结果供 LLM 判断
        bm25_results = []
        all_bm25_zero = all(r.get('bm25_score', 0) == 0 for r in results)
        if all_bm25_zero and hasattr(self.kb, 'bm25_search'):
            qt = self.kb._tokenize_query(query)
            if any(self.kb._bm25_doc_freq.get(t, 0) > 0 for t in qt):
                bm25_results = self.kb.bm25_search(query, top_k=top_k)

        # ── 相邻分块合并 ──
        if AGENT_CHUNK_MERGE_ENABLED and len(results) > 1:
            before = len(results)
            results = merge_adjacent_chunks(results, AGENT_CHUNK_MERGE_MAX_CHARS)
            after = len(results)
            if before != after:
                print(f"  🔗 分块合并: {before} → {after} 块")

        # 格式化输出
        output = format_search_results(results)

        if bm25_results:
            output += (
                "\n\n---\n"
                "## 关键词匹配结果（精确命中，可能缺失上下文，请自主判断是否采用）\n"
            )
            output += format_search_results(bm25_results)

        return output

    def get_call_log(self) -> List[Dict]:
        """获取工具调用日志"""
        return self.call_log.copy()

    def reset_log(self):
        """重置工具调用日志"""
        self.call_log.clear()
