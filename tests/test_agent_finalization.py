import unittest

from config import AGENT_SYSTEM_PROMPT
from src.chatbot import AgentLoopState, Chatbot
from src.llm_client import LLMResponse
from server import ChatRequest, is_context_router_enabled
from src.agent_finalization import (
    build_final_answer_messages,
    sanitize_tool_protocol_text,
)


class AgentFinalizationTests(unittest.TestCase):
    def test_context_router_is_opt_in_per_request(self):
        self.assertFalse(is_context_router_enabled(ChatRequest(message="hello")))
        self.assertTrue(is_context_router_enabled(
            ChatRequest(message="hello", context_router_enabled=True)
        ))

    def test_agent_prompt_defines_budget_exhaustion_behavior(self):
        self.assertIn("达到检索轮次上限", AGENT_SYSTEM_PROMPT)
        self.assertIn("禁止调用任何工具", AGENT_SYSTEM_PROMPT)

    def test_chatbot_forced_call_uses_tool_free_final_context(self):
        captured = {}

        class CaptureClient:
            def send_message_with_tools(self, messages, tools=None, **kwargs):
                captured["messages"] = messages
                captured["tools"] = tools
                return LLMResponse(content="最终回答", finish_reason="stop")

        chatbot = Chatbot.__new__(Chatbot)
        chatbot.client = CaptureClient()
        state = AgentLoopState()

        Chatbot._call_llm_forced_text(chatbot, [
            {"role": "system", "content": "agent prompt"},
            {"role": "assistant", "content": "<|DSML|>toolcalls", "tool_calls": []},
            {"role": "tool", "content": "已有检索结果"},
        ], state)

        self.assertIsNone(captured["tools"])
        history = "\n".join(str(message) for message in captured["messages"][:-1])
        final_instruction = str(captured["messages"][-1])
        self.assertIn("禁止调用任何工具", final_instruction)
        self.assertNotIn("tool_calls", history)
        self.assertNotIn("DSML", history)

    def test_final_context_explicitly_stops_tools_and_keeps_search_results(self):
        messages = [
            {"role": "system", "content": "agent prompt"},
            {"role": "user", "content": "请查找 DSA 学域信息"},
            {
                "role": "assistant",
                "content": "我再检索一次\n<| | DSML | | toolcalls>",
                "tool_calls": [{"function": {"name": "search_knowledge_base"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "search_knowledge_base",
                "content": "[结果 1] DSA 学域介绍",
            },
        ]

        final_messages = build_final_answer_messages(messages)
        history_text = "\n".join(str(message) for message in final_messages[:-1])
        final_text = str(final_messages[-1])

        self.assertIn("禁止调用任何工具", final_text)
        self.assertIn("[结果 1] DSA 学域介绍", history_text)
        self.assertNotIn("DSML", history_text)
        self.assertNotIn("tool_calls", history_text)

    def test_protocol_text_is_removed_without_dropping_prefix(self):
        raw = "已经完成检索。&lt;| | DSML | | toolcalls&gt;\n&lt;| | invoke&gt;"

        self.assertEqual(sanitize_tool_protocol_text(raw), "已经完成检索。")


if __name__ == "__main__":
    unittest.main()
