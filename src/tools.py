"""
工具定义 + ToolHandler — Agent Loop 的工具层

定义 search_knowledge_base 工具 schema（OpenAI function calling 格式）
ToolHandler 负责工具执行、结果格式化、异常处理、相邻分块合并
"""
import json
import hashlib
import os
import time
from typing import List, Dict, Optional
from collections import defaultdict

from config import (
    HYBRID_SEARCH_ENABLED, BM25_WEIGHT,
    AGENT_CHUNK_MERGE_ENABLED, AGENT_CHUNK_MERGE_MAX_CHARS,
    RAG_CONTEXT_NEIGHBOR_RADIUS,
    DOCUMENTS_DIR,
    AGENT_SEARCH_TOP_K_MAX,
)
from src.reranker import (
    get_reranker_model,
    rerank_precomputed_results,
    search_with_optional_rerank,
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
                    "minimum": 1,
                    "maximum": 30,
                    "description": "返回结果条数，默认 3。多实体集合问题通常使用 5；若从 3 或 5 开始，后续可用 8、15、30 扩大，最大 30。",
                },
                "rerank_query_source": {
                    "type": "string",
                    "enum": ["search_query", "user_query", "custom"],
                    "default": "search_query",
                    "description": (
                        "可选的重排查询来源。search_query 使用当前检索词；"
                        "user_query 使用当前轮用户问题；custom 使用 rerank_query。"
                        "问题独立完整时可选 user_query；追问或子问题优先使用 search_query。"
                    ),
                },
                "rerank_query": {
                    "type": "string",
                    "description": "当 rerank_query_source=custom 时使用的重排问题；否则忽略。",
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
                current["_trace_exact_matches"] = ToolHandler._trace_exact_matches(item)
                continue

            # 判断是否可合并：chunk_index 连续 且 合并不超限
            prev_idx = current.get("chunk_index", 0) + current.get("_merged_count", 1)
            curr_idx = item.get("chunk_index", -1)
            combined_len = len(current.get("content", "")) + len(item.get("content", ""))

            if curr_idx == prev_idx and combined_len <= max_chars:
                # 合并：拼接内容，保留最高分
                current["content"] = current["content"] + "\n" + item["content"]
                current["score"] = max(
                    current.get("score", 0),
                    item.get("score", 0),
                )
                if "rerank_score" in current or "rerank_score" in item:
                    current["rerank_score"] = max(
                        current.get("rerank_score", float("-inf")),
                        item.get("rerank_score", float("-inf")),
                    )
                if "rerank_rank" in current or "rerank_rank" in item:
                    current["rerank_rank"] = min(
                        current.get("rerank_rank", float("inf")),
                        item.get("rerank_rank", float("inf")),
                    )
                current["chunk_index"] = min(current["chunk_index"], curr_idx)
                current["_merged_count"] = current.get("_merged_count", 1) + 1
                current["_trace_exact_matches"] = current.get("_trace_exact_matches", []) + ToolHandler._trace_exact_matches(item)
                if "_trace_exact_content" not in current and item.get("_trace_exact_content") is not None:
                    current["_trace_exact_content"] = item["_trace_exact_content"]
                    current["_trace_exact_chunk_index"] = item.get("_trace_exact_chunk_index")
            else:
                merged.append(current)
                current = dict(item)
                current["_merged_count"] = 1
                current["_trace_exact_matches"] = ToolHandler._trace_exact_matches(item)

        if current is not None:
            merged.append(current)

    if any("rerank_rank" in item for item in merged):
        merged.sort(
            key=lambda item: (
                item.get("rerank_rank", float("inf")),
                -item.get("score", 0),
            )
        )
    else:
        merged.sort(key=lambda item: item.get("score", 0), reverse=True)
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

    def __init__(self, knowledge_base, rerank_enabled=False,
                 reranker_loader=get_reranker_model, turn_id=None,
                 user_query: Optional[str] = None):
        """
        Args:
            knowledge_base: KnowledgeBase 实例
        """
        self.kb = knowledge_base
        self.rerank_enabled = rerank_enabled
        self.reranker_loader = reranker_loader
        self.turn_id = turn_id
        self.user_query = str(user_query or "").strip()
        self.call_log: List[Dict] = []  # 工具调用日志
        self._pending_trace: Dict = {}

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

        try:
            if tool_name == "search_knowledge_base":
                result_text = self._handle_search_knowledge_base(arguments)
            else:
                result_text = f"[ERROR] 未知工具: {tool_name}"
                self._pending_trace = {"error": result_text}
        except Exception as error:
            result_text = f"[ERROR] 工具执行失败: {type(error).__name__}: {error}"
            self._pending_trace = {"error": result_text}

        duration_ms = round((time.time() - start_time) * 1000)

        # 记录日志
        log_entry = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result_preview": result_text[:200],
            "duration_ms": duration_ms,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        log_entry.update(self._pending_trace)
        log_entry.setdefault("turn_id", self.turn_id)
        log_entry["duration_ms"] = duration_ms
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
            self._pending_trace = {"query": "", "engine": "unknown", "result_count": 0, "hits": [], "error": "缺少必填参数 query"}
            return "[ERROR] 缺少必填参数 query"

        top_k = args.get("top_k", 3)
        # 放宽上限：LLM 自主增大 top_k 时有足够空间
        top_k = max(1, min(int(top_k), AGENT_SEARCH_TOP_K_MAX))

        rerank_query, rerank_query_source = self._resolve_rerank_query(args, query)

        filters = args.get("filters")
        # 规范化 filters：只保留已知字段
        if filters and isinstance(filters, dict):
            valid_filters = {}
            known_keys = {"doc_type", "mtime_after", "mtime_before", "source"}
            for k, v in filters.items():
                if k in known_keys:
                    valid_filters[k] = v
            filters = valid_filters if valid_filters else None

        self._pending_trace = {
            "query": query,
            "retrieval_query": query,
            "rerank_query": rerank_query,
            "rerank_query_source": rerank_query_source,
            "filters": filters or {},
            "top_k": top_k,
            "engine": "hybrid" if HYBRID_SEARCH_ENABLED and hasattr(self.kb, "hybrid_search") else "vector",
            "rerank_enabled": bool(self.rerank_enabled),
            "result_count": 0,
            "hits": [],
        }
        try:
            if HYBRID_SEARCH_ENABLED and hasattr(self.kb, "hybrid_search"):
                search_output = search_with_optional_rerank(
                    self.kb,
                    query,
                    top_k=top_k,
                    enabled=self.rerank_enabled,
                    filters=filters,
                    model_loader=self.reranker_loader,
                    rerank_query=rerank_query,
                    return_candidates=True,
                )
                if isinstance(search_output, tuple):
                    results, rerank_candidates = search_output
                else:
                    # Keep compatibility with test doubles and legacy wrappers.
                    results = search_output
                    rerank_candidates = results
            else:
                results = self.kb.search(query, top_k=top_k, filters=filters)
                rerank_candidates = results
        except Exception as e:
            self._pending_trace.update({"result_count": 0, "hits": [], "error": f"{type(e).__name__}: {e}"})
            return f"[ERROR] 搜索执行失败: {type(e).__name__}: {str(e)}"

        # Agent 双通道：若当前混合结果全无关键词命中，追加全库 BM25 结果供 LLM 判断。
        # 先用文档频率索引预检，语料中没有查询词时不扫描全库。
        bm25_results = []
        all_bm25_zero = all(r.get("bm25_score", 0) == 0 for r in results)
        if all_bm25_zero and hasattr(self.kb, "bm25_search"):
            query_tokens = self.kb._tokenize_query(query)
            bm25_doc_freq = getattr(self.kb, "_bm25_doc_freq", {})
            if any(bm25_doc_freq.get(token, 0) > 0 for token in query_tokens):
                bm25_results = self.kb.bm25_search(
                    query,
                    top_k=top_k,
                    filters=filters,
                )

        rerank_was_applied = any("rerank_score" in item for item in results)
        bm25_rescue_in_final = []
        if bm25_results and rerank_was_applied:
            # Keep the already ranked top-k first so a rescue rerank failure
            # falls back to the exact result ordering returned above, while
            # still allowing the omitted candidate tail to compete.
            ranked_keys = {
                (
                    item.get("source"),
                    item.get("chunk_index"),
                    item.get("content"),
                )
                for item in results
            }
            full_candidate_pool = list(results)
            for item in rerank_candidates:
                item_key = (
                    item.get("source"),
                    item.get("chunk_index"),
                    item.get("content"),
                )
                if item_key not in ranked_keys:
                    full_candidate_pool.append(item)
                    ranked_keys.add(item_key)
            rerank_candidates = full_candidate_pool
            for item in bm25_results:
                rescued = dict(item)
                rescued["_bm25_rescued"] = True
                rerank_candidates.append(rescued)

            reranked_with_rescue = rerank_precomputed_results(
                rerank_query,
                rerank_candidates,
                top_k=top_k,
                enabled=True,
                model_loader=self.reranker_loader,
            )
            bm25_rescue_in_final = [
                item for item in reranked_with_rescue
                if item.get("_bm25_rescued")
            ]
            if bm25_rescue_in_final:
                results = reranked_with_rescue
                # The selected rescue result is already part of the final
                # top-k context; do not append it a second time.
                bm25_results = []

        self._mark_trace_anchors(results)
        self._mark_trace_anchors(bm25_results)

        # ── 相邻分块合并 ──
        if hasattr(self.kb, "expand_adjacent_chunks"):
            results = self.kb.expand_adjacent_chunks(
                results,
                radius=RAG_CONTEXT_NEIGHBOR_RADIUS,
            )

        if AGENT_CHUNK_MERGE_ENABLED and len(results) > 1:
            before = len(results)
            results = merge_adjacent_chunks(results, AGENT_CHUNK_MERGE_MAX_CHARS)
            after = len(results)
            if before != after:
                print(f"  🔗 分块合并: {before} → {after} 块")

        # 格式化输出
        output = format_search_results(results)
        if self.rerank_enabled and any("rerank_score" in r for r in results):
            output += (
                "\n\n[智能重排] 已启用智能重排，结果顺序以跨编码器判断为准；"
                "每条结果显示的分数仍为原混合检索分，仅供辅助参考。"
            )

        if bm25_results:
            output += (
                "\n\n---\n"
                "## 关键词匹配结果（精确命中，可能缺失上下文，请自主判断是否采用）\n"
            )
            output += format_search_results(bm25_results)

        # Adjacent chunks are context only. They must not inflate the Top-k
        # trace count or appear as independent retrieval hits.
        trace_results = [result for result in results if result.get("_trace_exact_chunk_index") is not None]
        hybrid_hits = [
            self._trace_hit(
                result,
                document_id=self._document_id_for_source(result.get("source")),
            )
            for result in trace_results
            if not result.get("_bm25_rescued")
        ]
        bm25_hits = [
            self._trace_hit(
                result,
                document_id=self._document_id_for_source(result.get("source")),
            )
            for result in trace_results
            if result.get("_bm25_rescued")
        ]
        bm25_hits.extend(
            self._trace_hit(
                result,
                document_id=self._document_id_for_source(result.get("source")),
            )
            for result in bm25_results
        )
        self._pending_trace.update({
            "engine": "reranked" if any("rerank_score" in item for item in results) else self._pending_trace.get("engine", "hybrid"),
            "result_count": len(hybrid_hits) + len(bm25_hits),
            "hits": hybrid_hits + bm25_hits,
            "channels": {"hybrid": hybrid_hits, "bm25": bm25_hits},
        })

        return output

    def _resolve_rerank_query(self, args: Dict, retrieval_query: str):
        """Resolve a safe rerank query without adding another model call."""
        source = str(args.get("rerank_query_source", "search_query")).strip().lower()
        if source == "user_query" and self.user_query:
            return self.user_query, source
        if source == "custom":
            custom_query = str(args.get("rerank_query", "")).strip()
            if custom_query:
                return custom_query, source
        return retrieval_query, "search_query"

    @staticmethod
    def _trace_exact_matches(result: Dict) -> List[Dict]:
        if result.get("_trace_exact_content") is None:
            return []
        return [{
            "chunk_index": result.get("_trace_exact_chunk_index", result.get("chunk_index", 0)),
            "content": str(result.get("_trace_exact_content", "")),
        }]

    @staticmethod
    def _mark_trace_anchors(results: List[Dict]) -> None:
        for result in results:
            result.setdefault("_trace_exact_content", str(result.get("content", "")))
            result.setdefault("_trace_exact_chunk_index", result.get("chunk_index", 0))

    @staticmethod
    def _trace_preview(content: object, max_lines: int = 5, max_chars: int = 800) -> str:
        text = str(content or "").strip()
        return "\n".join(text.splitlines()[:max_lines])[:max_chars]

    def _document_id_for_source(self, source: object) -> Optional[str]:
        if not source:
            return None
        source_text = str(source)
        metadata = getattr(self.kb, "metadata", {}) or {}
        for key in (source_text, os.path.abspath(source_text), os.path.realpath(source_text)):
            raw = metadata.get(key, {})
            if raw.get("document_id"):
                return str(raw["document_id"])
        try:
            source_path = os.path.realpath(source_text)
            root_path = os.path.realpath(DOCUMENTS_DIR)
            if os.path.commonpath([source_path, root_path]) != root_path:
                return None
            relative = os.path.relpath(source_path, root_path).replace(os.sep, "/")
            return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        except (OSError, ValueError):
            return None

    @classmethod
    def _trace_hit(cls, result: Dict, document_id: Optional[str] = None) -> Dict:
        exact_content = result.get("_trace_exact_content")
        if exact_content is None:
            exact_content = result.get("content", "")
        merged_content = str(result.get("content", ""))
        matched_index = result.get("_trace_exact_chunk_index", result.get("chunk_index", 0))
        merged_count = int(result.get("_merged_count", 1) or 1)
        return {
            "source": result.get("source", "unknown"),
            "title": result.get("title", ""),
            "doc_type": result.get("doc_type", "unknown"),
            "chunk_index": result.get("chunk_index", 0),
            "matched_chunk_index": matched_index,
            "matched_content_snippet": cls._trace_preview(exact_content),
            "content_snippet": cls._trace_preview(exact_content),
            "merged_content": merged_content,
            "merged_chunk_indices": list(range(int(result.get("chunk_index", 0)), int(result.get("chunk_index", 0)) + merged_count)),
            "document_id": document_id,
            "score": result.get("score"),
            "bm25_score": result.get("bm25_score"),
            "rerank_score": result.get("rerank_score"),
            "rerank_rank": result.get("rerank_rank"),
        }

    def get_call_log(self) -> List[Dict]:
        """获取工具调用日志"""
        return self.call_log.copy()

    def reset_log(self):
        """重置工具调用日志"""
        self.call_log.clear()
