"""
Agent Loop 单元测试 — 工具定义、防护机制、Session 扩展
"""
import pytest
import json

from src.tools import (
    SEARCH_KB_TOOL, AGENT_TOOLS, format_search_results, ToolHandler,
)
from src.chatbot import Chatbot, AgentLoopState, AgentChatResult
from src.session import Session
from src.llm_client import LLMResponse


# ═══════════════════════════════════════════════════════════
#  工具 Schema 测试
# ═══════════════════════════════════════════════════════════

class TestToolSchema:
    def test_search_kb_tool_structure(self):
        assert SEARCH_KB_TOOL["type"] == "function"
        func = SEARCH_KB_TOOL["function"]
        assert func["name"] == "search_knowledge_base"
        assert "query" in func["parameters"]["properties"]
        assert "query" in func["parameters"]["required"]
        assert "top_k" in func["parameters"]["properties"]
        assert "filters" in func["parameters"]["properties"]

    def test_agent_tools_list(self):
        assert len(AGENT_TOOLS) == 1
        assert AGENT_TOOLS[0] is SEARCH_KB_TOOL


# ═══════════════════════════════════════════════════════════
#  搜索结果格式化测试
# ═══════════════════════════════════════════════════════════

class TestFormatResults:
    def test_empty_results(self):
        result = format_search_results([])
        assert "[NO_RESULTS]" in result

    def test_high_score_results(self):
        results = [{
            "source": "docs/test.md",
            "doc_type": "markdown",
            "title": "Test Doc",
            "score": 0.92,
            "content": "This is test content.",
        }]
        fmt = format_search_results(results)
        assert "★★★" in fmt
        assert "高相关" in fmt
        assert "Test Doc" in fmt

    def test_low_score_results(self):
        results = [{
            "source": "docs/test.md",
            "doc_type": "text",
            "title": "Low",
            "score": 0.25,
            "content": "Low relevance.",
        }]
        fmt = format_search_results(results)
        assert "★☆☆" in fmt
        assert "低相关" in fmt


# ═══════════════════════════════════════════════════════════
#  重复查询检测测试（L3）
# ═══════════════════════════════════════════════════════════

class TestDuplicateDetection:
    def test_exact_duplicate(self):
        assert Chatbot._is_duplicate_query("反向传播算法", ["反向传播算法"])

    def test_different_query(self):
        assert not Chatbot._is_duplicate_query(
            "Python基础语法", ["机器学习入门教程"]
        )

    def test_short_query_skipped(self):
        assert not Chatbot._is_duplicate_query("hi", [])

    def test_empty_history(self):
        assert not Chatbot._is_duplicate_query("whatever", [])

    def test_chinese_bigram_similar(self):
        # 仅末尾多一个字的查询，bigram 重叠度高（8个bigram中7个相同）
        assert Chatbot._is_duplicate_query(
            "反向传播算法详解",
            ["反向传播算法详解版"],
        )

    def test_chinese_bigram_different(self):
        assert not Chatbot._is_duplicate_query(
            "CNN卷积神经网络",
            ["RNN循环神经网络原理"],
        )

    def test_check_only_last_3(self):
        past = ["q1", "q2", "q3", "深度学习反向传播算法详解"]
        assert Chatbot._is_duplicate_query(
            "深度学习反向传播算法详解", past
        )


# ═══════════════════════════════════════════════════════════
#  熔断计数器测试（L4/L5）
# ═══════════════════════════════════════════════════════════

class TestFuseCounters:
    def test_empty_result_increments_both(self):
        state = AgentLoopState()
        Chatbot._update_fuse_counters(state, "[NO_RESULTS] 未找到")
        assert state.consecutive_empty == 1
        assert state.consecutive_low_score == 1

    def test_two_empty_results(self):
        state = AgentLoopState()
        Chatbot._update_fuse_counters(state, "[NO_RESULTS] 未找到")
        Chatbot._update_fuse_counters(state, "[NO_RESULTS] 又没找到")
        assert state.consecutive_empty == 2

    def test_good_result_resets_empty(self):
        state = AgentLoopState()
        Chatbot._update_fuse_counters(state, "[NO_RESULTS] 未找到")
        Chatbot._update_fuse_counters(
            state, "[结果 1] ★★★ 高相关 | 分数: 0.900\n内容"
        )
        assert state.consecutive_empty == 0
        assert state.consecutive_low_score == 0

    def test_low_score_accumulates(self):
        state = AgentLoopState()
        for _ in range(3):
            Chatbot._update_fuse_counters(
                state, "[结果 1] ★☆☆ 低相关 | 分数: 0.100\n内容"
            )
        assert state.consecutive_low_score == 3

    def test_error_increments_empty(self):
        state = AgentLoopState()
        Chatbot._update_fuse_counters(state, "[ERROR] 搜索失败: timeout")
        assert state.consecutive_empty == 1


# ═══════════════════════════════════════════════════════════
#  Token 估算测试（L6）
# ═══════════════════════════════════════════════════════════

class TestTokenEstimation:
    def test_basic_estimation(self):
        msgs = [{"role": "user", "content": "hello " * 300}]
        est = Chatbot._estimate_tokens(msgs)
        assert est > 0
        # ~1800 chars / 3 ≈ 600 tokens
        assert 500 < est < 800

    def test_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": "let me search",
                "tool_calls": [{"function": {"arguments": {"query": "test"}}}],
            }
        ]
        est = Chatbot._estimate_tokens(msgs)
        assert est > 0


# ═══════════════════════════════════════════════════════════
#  AgentChatResult 测试
# ═══════════════════════════════════════════════════════════

class TestAgentChatResult:
    def test_basic_result(self):
        r = AgentChatResult(content="hello", finish_reason="stop", rounds=1)
        assert r.content == "hello"
        assert r.finish_reason == "stop"
        assert r.rounds == 1

    def test_defaults(self):
        r = AgentChatResult(content="", finish_reason="stop")
        assert r.tool_call_log == []
        assert r.usage == {}
        assert r.rounds == 0


# ═══════════════════════════════════════════════════════════
#  Session 扩展测试
# ═══════════════════════════════════════════════════════════

class TestSessionToolCalls:
    def test_add_tool_messages(self):
        session = Session()
        session.add_message(
            "assistant",
            content=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_knowledge_base", "arguments": {"query": "test"}},
            }],
        )
        session.add_message("tool", content="result", tool_call_id="call_1", name="search_knowledge_base")
        session.add_message("user", "hello")

        history = session.get_history()
        assert history[0]["role"] == "assistant"
        assert history[0]["tool_calls"][0]["function"]["name"] == "search_knowledge_base"
        assert history[1]["role"] == "tool"
        assert history[1]["content"] == "result"
        assert history[2]["role"] == "user"

    def test_strip_tool_details(self):
        session = Session()
        session.add_message("user", "hello")
        session.add_message("assistant", content=None, tool_calls=[{
            "id": "1", "type": "function",
            "function": {"name": "search_knowledge_base", "arguments": {}},
        }])
        session.add_message("tool", content="result", tool_call_id="1")
        session.add_message("assistant", content="final answer")

        history = session.get_history(strip_tool_details=True)
        # tool 消息被跳过，assistant 含 tool_calls 的被简化
        assert len(history) == 3  # user, assistant(w/tool_calls), assistant(final)
        assert "[调用了工具: search_knowledge_base]" in history[1].get("content", "")

    def test_usage_accumulation(self):
        session = Session()
        session.accumulate_usage({"prompt_tokens": 100, "completion_tokens": 50})
        session.accumulate_usage({"prompt_tokens": 200, "completion_tokens": 30})
        assert session.total_prompt_tokens == 300
        assert session.total_completion_tokens == 80

    def test_cost_summary(self):
        session = Session()
        session.accumulate_usage({"prompt_tokens": 500, "completion_tokens": 200})
        summary = session.get_cost_summary()
        assert "500" in summary
        assert "200" in summary
        assert "700" in summary

    def test_tool_call_log(self):
        session = Session()
        assert session.get_tool_call_log() == []
        session.append_tool_call_log([
            {"tool_name": "search", "query": "test", "duration_ms": 150}
        ])
        assert len(session.get_tool_call_log()) == 1


# ═══════════════════════════════════════════════════════════
#  LLMResponse 测试
# ═══════════════════════════════════════════════════════════

class TestLLMResponse:
    def test_basic_response(self):
        r = LLMResponse(content="hello world", finish_reason="stop")
        assert r.content == "hello world"
        assert r.tool_calls is None

    def test_tool_calls_response(self):
        r = LLMResponse(
            content=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_knowledge_base", "arguments": {"query": "test"}},
            }],
            finish_reason="tool_calls",
        )
        assert r.tool_calls is not None
        assert r.tool_calls[0]["function"]["name"] == "search_knowledge_base"

    def test_error_response(self):
        r = LLMResponse(content="[API_ERROR] timeout", finish_reason="error")
        assert r.finish_reason == "error"


# ═══════════════════════════════════════════════════════════
#  ToolHandler 格式/异常测试
# ═══════════════════════════════════════════════════════════

class TestToolHandler:
    def test_unknown_tool(self):
        # 不需要真实 KB，error 路径不调用 kb
        class FakeKB:
            pass
        handler = ToolHandler(FakeKB())
        result = handler.execute("unknown_tool", {})
        assert "[ERROR]" in result
        assert "unknown_tool" in result

    def test_missing_query(self):
        class FakeKB:
            pass
        handler = ToolHandler(FakeKB())
        result = handler.execute("search_knowledge_base", {})
        assert "[ERROR]" in result
        assert "query" in result.lower()

    def test_top_k_clamped(self):
        class FakeKB:
            def hybrid_search(self, query, top_k=3, filters=None, bm25_weight=None):
                return []
        handler = ToolHandler(FakeKB())
        # top_k = 100 should be clamped to 10
        result = handler.execute("search_knowledge_base", {"query": "test", "top_k": 100})
        assert "[NO_RESULTS]" in result  # returns empty results, not error


# ═══════════════════════════════════════════════════════════
#  运行入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
