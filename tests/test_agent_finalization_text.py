import unittest

from src.agent_finalization import sanitize_tool_protocol_text
from src.chatbot import Chatbot


class TextClient:
    def send_message(self, messages, **kwargs):
        return "回答完成 <tool_call>{\"query\": \"secret\"}</tool_call>"


class AgentFinalizationTextTests(unittest.TestCase):
    def test_xml_tool_protocol_is_removed_but_natural_invoke_is_kept(self):
        self.assertEqual(
            sanitize_tool_protocol_text(
                "请 invoke 这个动作。<tool_call>{\"query\": \"secret\"}</tool_call>"
            ),
            "请 invoke 这个动作。",
        )

    def test_plain_chat_response_is_sanitized_before_return(self):
        chatbot = Chatbot.__new__(Chatbot)
        chatbot.client = TextClient()
        chatbot.system_prompt = "test"

        self.assertEqual(Chatbot.chat(chatbot, "你好"), "回答完成")


if __name__ == "__main__":
    unittest.main()
