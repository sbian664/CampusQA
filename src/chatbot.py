"""
对话机器人 - 支持基础对话、多轮对话、RAG 检索增强、Agent Loop 自主检索
"""
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from src.llm_client import create_llm_client, LLMResponse
from src.tools import SEARCH_KB_TOOL, AGENT_TOOLS, ToolHandler
from config import (
    SYSTEM_PROMPT,
    RAG_TOP_K,
    RAG_SYSTEM_PROMPT_TEMPLATE,
    RAG_CONTEXT_ITEM_TEMPLATE,
    HYBRID_SEARCH_ENABLED,
    AGENT_MODE_ENABLED,
    AGENT_MAX_LLM_ROUNDS,
    AGENT_MAX_TOTAL_TOOL_CALLS,
    AGENT_DUPLICATE_THRESHOLD,
    AGENT_LOW_SCORE_THRESHOLD,
    AGENT_CONTEXT_RATIO,
    AGENT_MODEL_MAX_CONTEXT,
    AGENT_SYSTEM_PROMPT,
)


# ═══════════════════════════════════════════════════════════
#  Agent Loop 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentLoopState:
    """Agent Loop 运行状态追踪（所有防护的计数器）"""
    llm_rounds: int = 0
    total_tool_calls: int = 0
    past_queries: List[str] = field(default_factory=list)     # L3 重复检测
    consecutive_empty: int = 0                                  # L4 零结果熔断
    consecutive_low_score: int = 0                              # L5 低分熔断
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


@dataclass
class AgentChatResult:
    """Agent 对话返回结果"""
    content: str
    finish_reason: str       # "stop" | "max_rounds" | "max_tool_calls" | "error"
    tool_call_log: List[Dict] = field(default_factory=list)
    usage: Dict = field(default_factory=dict)
    rounds: int = 0


# ═══════════════════════════════════════════════════════════
#  Chatbot
# ═══════════════════════════════════════════════════════════

class Chatbot:
    """对话机器人 — 支持 RAG 检索增强生成 + Agent Loop 自主检索"""

    def __init__(self, llm_provider: str = None, knowledge_base=None):
        """
        初始化对话机器人

        Args:
            llm_provider: LLM 提供商 (deepseek / local)
            knowledge_base: KnowledgeBase 实例（用于 RAG 检索）
        """
        self.client = create_llm_client(llm_provider)
        self.system_prompt = SYSTEM_PROMPT
        self.kb = knowledge_base
        self.agent_mode = AGENT_MODE_ENABLED  # 运行时可切换

    # ---- 基础对话 ----

    def chat(self, user_message: str) -> str:
        """单轮对话"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = self.client.send_message(messages)
        return response

    def chat_with_history(self, user_message: str, history: List[Dict]) -> str:
        """带对话历史的多轮对话"""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        response = self.client.send_message(messages)
        return response

    # ---- RAG 检索增强（一步式，快速模式）----

    def chat_with_rag(self, user_message: str, history: List[Dict]) -> str:
        """
        带 RAG 检索增强的多轮对话（一步式检索，不做循环）

        流程：
        1. 检索知识库
        2. 格式化上下文
        3. 构建增强 prompt
        4. 调用 LLM 生成回复
        """
        if self.kb is None:
            return self.chat_with_history(user_message, history)

        if HYBRID_SEARCH_ENABLED and hasattr(self.kb, 'hybrid_search'):
            results = self.kb.hybrid_search(user_message, top_k=RAG_TOP_K)
        else:
            results = self.kb.search(user_message, top_k=RAG_TOP_K)

        if results:
            context_parts = []
            for r in results:
                context_parts.append(
                    RAG_CONTEXT_ITEM_TEMPLATE.format(
                        source=r['source'],
                        doc_type=r.get('doc_type', 'unknown'),
                        title=r.get('title', ''),
                        chunk=r['chunk_index'],
                        score=r['score'],
                        content=r['content'],
                    )
                )
            context = "\n\n".join(context_parts)
        else:
            context = "（未找到相关文档）"

        rag_system_prompt = RAG_SYSTEM_PROMPT_TEMPLATE.format(
            system_prompt=self.system_prompt,
            context=context,
        )

        messages = [{"role": "system", "content": rag_system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        response = self.client.send_message(messages)
        return response

    # ═══════════════════════════════════════════════════════════
    #  Agent Loop 自主循环检索（深度模式）
    # ═══════════════════════════════════════════════════════════

    def agent_chat(self, user_message: str, history: List[Dict]) -> AgentChatResult:
        """
        Agent Loop 自主循环检索

        LLM 可自主调用 search_knowledge_base 工具，评估检索结果，
        在判断不足时自动重新检索（换关键词/增 top_k/加过滤），直到满意或达到上限。

        多层防护：
        L1: max_llm_rounds 硬限制
        L2: max_total_tool_calls 硬限制
        L3: 重复查询检测（Jaccard 相似度）
        L4: 连续空结果熔断
        L5: 连续低分熔断
        L6: Token 预算裁剪
        """
        if self.kb is None:
            text = self.chat_with_history(user_message, history)
            return AgentChatResult(content=text, finish_reason="stop")

        state = AgentLoopState()
        handler = ToolHandler(self.kb)

        # 构建初始消息列表
        messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        while state.llm_rounds < AGENT_MAX_LLM_ROUNDS:
            # ── 预检查：L4 零结果熔断 ──
            if state.consecutive_empty >= 2:
                messages.append({
                    "role": "user",
                    "content": "[系统提示] 连续检索均返回空结果，知识库中可能不包含相关信息。"
                               "请直接基于你的知识回答，并告知用户这一情况。"
                })
                # 强制下一轮不调用工具
                response = self._call_llm_forced_text(messages, state)
                return self._build_result(response, handler, state, "empty_fuse")

            # ── 预检查：L5 低分熔断 ──
            if state.consecutive_low_score >= 3:
                messages.append({
                    "role": "user",
                    "content": "[系统提示] 连续多次检索结果相关性较低，知识库可能不包含相关信息。"
                               "请直接基于你的知识回答，并说明知识库中未找到高质量匹配。"
                })
                response = self._call_llm_forced_text(messages, state)
                return self._build_result(response, handler, state, "low_score_fuse")

            # ── 预检查：L6 Token 预算裁剪 ──
            messages = self._trim_context_if_needed(messages)

            # ── 调用 LLM（带工具定义）──
            response = self.client.send_message_with_tools(
                messages, tools=AGENT_TOOLS
            )
            self._accumulate_state_usage(state, response)

            # ── 错误处理 ──
            if response.finish_reason == "error":
                return AgentChatResult(
                    content=response.content or "[错误] LLM 调用失败",
                    finish_reason="error",
                    rounds=state.llm_rounds,
                )

            # ── 无 tool_calls → LLM 认为可以回答了 ──
            if not response.tool_calls:
                return self._build_result(response, handler, state, "stop")

            # ── 处理 tool_calls ──
            # 构建 assistant 消息（arguments 必须序列化为 JSON 字符串，否则 API 400）
            assistant_msg = {"role": "assistant"}
            if response.content:
                assistant_msg["content"] = response.content
            if response.tool_calls:
                serialized_calls = []
                for tc in response.tool_calls:
                    tc_copy = {
                        "id": tc["id"],
                        "type": tc["type"],
                        "function": dict(tc["function"]),
                    }
                    args = tc_copy["function"].get("arguments")
                    if isinstance(args, dict):
                        tc_copy["function"]["arguments"] = json.dumps(args, ensure_ascii=False)
                    serialized_calls.append(tc_copy)
                assistant_msg["tool_calls"] = serialized_calls
            messages.append(assistant_msg)

            tool_results = []
            for tc in response.tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                arguments = func.get("arguments", {})

                # L2: 累计工具调用上限
                state.total_tool_calls += 1
                if state.total_tool_calls > AGENT_MAX_TOTAL_TOOL_CALLS:
                    messages.append({
                        "role": "user",
                        "content": "[系统提示] 已达到工具调用上限，请基于现有检索结果直接回答。"
                    })
                    response_final = self._call_llm_forced_text(messages, state)
                    return self._build_result(response_final, handler, state, "max_tool_calls")

                # L3: 重复查询检测
                query = arguments.get("query", "") if isinstance(arguments, dict) else str(arguments)
                if self._is_duplicate_query(query, state.past_queries):
                    tool_result_text = (
                        f"[DUPLICATE] 你已用高度相似的查询 \"{query[:50]}...\" 搜索过。"
                        "请换不同的关键词、角度，或基于已有结果回答。"
                    )
                    print(f"  ⚠️ 重复查询已拦截: {query[:60]}")
                else:
                    state.past_queries.append(query)
                    # 执行工具
                    tool_result_text = handler.execute(tool_name, arguments)
                    self._update_fuse_counters(state, tool_result_text)
                    # 彩色日志输出
                    result_count = self._count_results(tool_result_text)
                    top_score = self._extract_top_score(tool_result_text)
                    print(f"  🔍 {tool_name}(\"{query[:50]}\") → {result_count}条, top={top_score:.2f}" if top_score is not None else f"  🔍 {tool_name}(\"{query[:50]}\")")

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                    "content": tool_result_text,
                })

            messages.extend(tool_results)
            state.llm_rounds += 1

        # ── 达到 LLM 轮次上限 ──
        final = self._call_llm_forced_text(messages, state)
        result = self._build_result(final, handler, state, "max_rounds")
        result.content += "\n\n[已达到检索轮次上限，以上回答基于现有检索结果]"
        return result

    # ═══════════════════════════════════════════════════════════
    #  Agent Loop 辅助方法
    # ═══════════════════════════════════════════════════════════

    def _call_llm_forced_text(self, messages: List[Dict], state: AgentLoopState) -> LLMResponse:
        """强制文本模式调用（不带 tools），用于熔断后获取最终回答"""
        # 临时移除 tools 相关消息确保兼容性
        response = self.client.send_message_with_tools(messages, tools=None)
        self._accumulate_state_usage(state, response)
        return response

    def _build_result(self, response: LLMResponse, handler: ToolHandler,
                      state: AgentLoopState, reason: str) -> AgentChatResult:
        """构建 AgentChatResult"""
        return AgentChatResult(
            content=response.content or "",
            finish_reason=reason,
            tool_call_log=handler.get_call_log(),
            usage={
                "prompt_tokens": state.total_prompt_tokens,
                "completion_tokens": state.total_completion_tokens,
            },
            rounds=state.llm_rounds,
        )

    def _accumulate_state_usage(self, state: AgentLoopState, response: LLMResponse):
        """累积 token 用量"""
        if response.usage:
            state.total_prompt_tokens += response.usage.get("prompt_tokens", 0)
            state.total_completion_tokens += response.usage.get("completion_tokens", 0)

    # ---- L3: 重复查询检测 ----

    @staticmethod
    def _is_duplicate_query(new_query: str, past_queries: List[str]) -> bool:
        """基于 Jaccard 相似度检测重复查询（支持中英文）"""
        if not past_queries or len(new_query) < 5:
            return False

        new_tokens = Chatbot._tokenize_query(new_query)
        if len(new_tokens) < 2:
            return False

        for past in past_queries[-3:]:
            past_tokens = Chatbot._tokenize_query(past)
            if len(past_tokens) < 2:
                continue
            intersection = new_tokens & past_tokens
            union = new_tokens | past_tokens
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard >= AGENT_DUPLICATE_THRESHOLD:
                return True
        return False

    @staticmethod
    def _tokenize_query(query: str) -> set:
        """简单分词：中文字符级 bigram + 英文按空格拆分"""
        tokens = set()
        # 英文部分按空格拆分
        parts = query.lower().split()
        for part in parts:
            # 对纯中文部分做 bigram 切分
            chinese_chars = ''.join(c for c in part if '\u4e00' <= c <= '\u9fff')
            if len(chinese_chars) >= 4:
                for i in range(len(chinese_chars) - 1):
                    tokens.add(chinese_chars[i:i+2])
            elif chinese_chars:
                tokens.add(chinese_chars)
            else:
                # 纯英文/数字
                if len(part) >= 2:
                    tokens.add(part)
        return tokens

    # ---- L4/L5: 熔断计数器 ----

    @staticmethod
    def _update_fuse_counters(state: AgentLoopState, tool_result_text: str):
        """根据工具返回结果更新熔断计数器"""
        if tool_result_text.startswith("[NO_RESULTS]"):
            state.consecutive_empty += 1
            state.consecutive_low_score += 1
        elif "[ERROR]" in tool_result_text:
            state.consecutive_empty += 1
        else:
            state.consecutive_empty = 0
            # 提取最高分判断
            top_score = Chatbot._extract_top_score(tool_result_text)
            if top_score is not None and top_score < AGENT_LOW_SCORE_THRESHOLD:
                state.consecutive_low_score += 1
            else:
                state.consecutive_low_score = 0

    @staticmethod
    def _extract_top_score(result_text: str) -> Optional[float]:
        """从格式化结果文本中提取最高分"""
        import re
        scores = re.findall(r'分数:\s*([\d.]+)', result_text)
        if scores:
            return max(float(s) for s in scores)
        return None

    @staticmethod
    def _count_results(result_text: str) -> int:
        """统计结果条数"""
        import re
        matches = re.findall(r'\[结果\s+\d+\]', result_text)
        return len(matches) if matches else (0 if "[NO_RESULTS]" in result_text else 1)

    # ---- L6: Token 预算裁剪 ----

    @staticmethod
    def _estimate_tokens(messages: List[Dict]) -> int:
        """粗略估算 token 数（~3 chars/token）"""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        total_chars += sum(
            len(json.dumps(m.get("tool_calls", []), ensure_ascii=False))
            for m in messages if "tool_calls" in m
        )
        return total_chars // 3

    def _trim_context_if_needed(self, messages: List[Dict]) -> List[Dict]:
        """L6: 若 token 预算超过阈值，裁剪旧的工具结果"""
        estimated = self._estimate_tokens(messages)
        threshold = int(AGENT_MODEL_MAX_CONTEXT * AGENT_CONTEXT_RATIO)

        if estimated <= threshold:
            return messages

        print(f"  ⚡ Token 预算告警 ({estimated}>{threshold})，裁剪旧工具结果...")

        # 保留: system prompt + 最近 3 轮 tool 结果 + 所有 user/assistant 非工具消息
        # 策略：跟踪最近的 tool 消息索引，只保留最后 9 条（3 轮 × 3 结果）
        tool_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "tool"
        ]
        keep_last_n_tools = 9  # 保留最近 3 轮

        if len(tool_indices) > keep_last_n_tools:
            indices_to_trim = set(tool_indices[:-keep_last_n_tools])
            trimmed = []
            for i, m in enumerate(messages):
                if i in indices_to_trim:
                    trimmed.append({
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": "[已压缩] 早期检索结果已裁剪以节省上下文空间。",
                    })
                else:
                    trimmed.append(m)
            return trimmed

        return messages