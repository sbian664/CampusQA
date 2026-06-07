"""
工具定义 + ToolHandler — Agent Loop 的工具层

定义 search_knowledge_base 工具 schema（OpenAI function calling 格式）
ToolHandler 负责工具执行、结果格式化、异常处理
"""
import json
import time
from typing import List, Dict, Optional

from config import HYBRID_SEARCH_ENABLED, BM25_WEIGHT


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
            "也可用 filters 限定文档类型或时间范围。"
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
                        "mtime_after（修改时间晚于指定日期，格式 YYYY-MM-DD）、"
                        "mtime_before（修改时间早于指定日期）"
                    ),
                    "properties": {
                        "doc_type": {"type": "string"},
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
#  搜索结果的 LLM 可读格式化
# ═══════════════════════════════════════════════════════════

def format_search_results(results: List[Dict]) -> str:
    """将 hybrid_search 返回的 dict 列表格式化为 LLM 可读文本"""
    if not results:
        return "[NO_RESULTS] 知识库中未找到相关内容。"

    parts = []
    for i, r in enumerate(results, 1):
        source_name = r.get("source", "unknown").replace("\\", "/").split("/")[-1]
        score = r.get("score", 0)
        doc_type = r.get("doc_type", "unknown")
        title = r.get("title", "")
        content = r.get("content", "")

        # 分数等级标记
        if score >= 0.7:
            level = "★★★ 高相关"
        elif score >= 0.4:
            level = "★★☆ 中等相关"
        else:
            level = "★☆☆ 低相关"

        parts.append(
            f"[结果 {i}] {level} | 分数: {score:.3f}\n"
            f"  来源: {source_name} | 类型: {doc_type} | 标题: {title}\n"
            f"  内容: {content}"
        )

    # 汇总提示
    top_score = max(r.get("score", 0) for r in results)
    if top_score >= 0.7:
        hint = "检索结果质量高，可直接基于以上内容回答。"
    elif top_score >= 0.4:
        hint = "检索结果质量中等。若觉得信息不完整，可尝试用不同关键词或放宽条件重新检索。"
    else:
        hint = "检索结果相关性较低。建议尝试完全不同的检索策略，或直接告知用户知识库可能不包含相关信息。"

    return "\n\n".join(parts) + f"\n\n[检索评估] {hint}"


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
        执行 search_knowledge_base 工具

        Args:
            args: {"query": str, "top_k"?: int, "filters"?: dict}
        """
        query = args.get("query", "")
        if not query:
            return "[ERROR] 缺少必填参数 query"

        top_k = args.get("top_k", 3)
        # 限制 top_k 范围，防止滥用
        top_k = max(1, min(int(top_k), 10))

        filters = args.get("filters")
        # 规范化 filters：只保留已知字段
        if filters and isinstance(filters, dict):
            valid_filters = {}
            known_keys = {"doc_type", "mtime_after", "mtime_before"}
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

        return format_search_results(results)

    def get_call_log(self) -> List[Dict]:
        """获取工具调用日志"""
        return self.call_log.copy()

    def reset_log(self):
        """重置工具调用日志"""
        self.call_log.clear()
