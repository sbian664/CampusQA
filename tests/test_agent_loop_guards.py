import unittest

import src.chatbot as chatbot_module
from src.chatbot import AgentLoopState, Chatbot
from src.llm_client import LLMResponse


class InvalidToolClient:
    def __init__(self):
        self.tool_enabled_calls = 0
        self.total_calls = 0

    def send_message_with_tools(self, messages, tools=None, **kwargs):
        self.total_calls += 1
        if tools is None:
            return LLMResponse(content="最终回答", finish_reason="stop")
        self.tool_enabled_calls += 1
        if self.tool_enabled_calls <= 3:
            return LLMResponse(
                content=None,
                tool_calls=[{
                    "id": f"bad-{self.tool_enabled_calls}",
                    "type": "function",
                    "function": {"name": "", "arguments": {}},
                }],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="意外继续", finish_reason="stop")


class AgentLoopGuardTests(unittest.TestCase):
    def test_same_query_can_escalate_when_top_k_increases(self):
        past_searches = [{"query": "DSA 学域 staff", "top_k": 3, "filters": {}}]

        self.assertFalse(
            Chatbot._is_duplicate_search(
                "DSA 学域 staff", 5, {}, past_searches
            )
        )

    def test_same_query_is_duplicate_when_top_k_does_not_increase(self):
        past_searches = [{"query": "DSA 学域 staff", "top_k": 5, "filters": {}}]

        self.assertTrue(
            Chatbot._is_duplicate_search(
                "DSA 学域 staff", 5, {}, past_searches
            )
        )

        self.assertTrue(
            Chatbot._is_duplicate_search(
                "DSA 学域 staff", 3, {}, past_searches
            )
        )

    def test_same_retrieval_query_can_use_a_different_rerank_query(self):
        past_searches = [{
            "query": "课程 关键词",
            "top_k": 3,
            "filters": {},
            "rerank_query_source": "search_query",
            "rerank_query": "课程 关键词",
        }]

        self.assertFalse(
            Chatbot._is_duplicate_search(
                "课程 关键词",
                3,
                {},
                past_searches,
                "user_query",
                "完整的当前用户问题",
            )
        )
    def test_invalid_tool_calls_consume_llm_round_budget(self):
        original_limit = chatbot_module.AGENT_MAX_LLM_ROUNDS
        chatbot_module.AGENT_MAX_LLM_ROUNDS = 2
        client = InvalidToolClient()
        chatbot = Chatbot.__new__(Chatbot)
        chatbot.client = client
        chatbot.kb = object()

        try:
            result = chatbot.agent_chat("查询测试", [], rerank_enabled=False)
        finally:
            chatbot_module.AGENT_MAX_LLM_ROUNDS = original_limit

        self.assertEqual(result.finish_reason, "max_rounds")
        self.assertEqual(client.tool_enabled_calls, 2)
        self.assertEqual(client.total_calls, 3)


if __name__ == "__main__":
    unittest.main()
