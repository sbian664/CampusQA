from copy import deepcopy
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from src.chatbot import Chatbot, AgentLoopState
from src.llm_client import LocalModelClient, OpenAICompatibleClient, LLMResponse


def test_clients_declare_tools_capability():
    assert LocalModelClient().supports_tools is False
    assert OpenAICompatibleClient(config={'api_key': 'test', 'model': 'test'}).supports_tools is True


def test_budget_removes_complete_old_exchanges_without_mutating_history():
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'x' * 5000},
                {'role': 'assistant', 'content': 'old'}, {'role': 'user', 'content': 'current'}]
    original = deepcopy(messages)
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', 500), patch('src.chatbot.AGENT_CONTEXT_RATIO', .8):
        trimmed = bot._trim_context_if_needed(messages)
    assert [m['content'] for m in trimmed] == ['s', 'current']
    assert messages == original


def test_tool_budget_keeps_all_call_ids_and_compresses_large_result():
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'current'},
                {'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'search', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'result' * 5000}]
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', 900), patch('src.chatbot.AGENT_CONTEXT_RATIO', .8):
        trimmed = bot._trim_context_if_needed(messages)
    assert trimmed[2]['tool_calls'][0]['id'] == trimmed[3]['tool_call_id'] == 'a'
    assert len(trimmed[3]['content']) < 1000


def test_rag_budget_preserves_current_retrieval_while_removing_old_rounds():
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'old' * 500},
                {'role': 'assistant', 'content': 'old answer'},
                {'role': 'user', 'content': '[知识库检索结果]\n' + 'evidence' * 500},
                {'role': 'user', 'content': 'current'}]
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', 900), patch('src.chatbot.AGENT_CONTEXT_RATIO', .8):
        trimmed = bot._trim_context_if_needed(messages)
    assert any('evidence' in str(m.get('content')) for m in trimmed)
    assert not any(m.get('content') == 'old answer' for m in trimmed)


def test_budget_rejects_current_input_and_forced_answer_also_checks():
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    bot.client = SimpleNamespace(send_message_with_tools=lambda *a, **kw: pytest.fail('must reject before API'))
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'x' * 5000}]
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', 500), patch('src.chatbot.AGENT_CONTEXT_RATIO', .8):
        with pytest.raises(Exception, match='缩短'):
            bot._trim_context_if_needed(messages)
        with pytest.raises(Exception, match='缩短'):
            bot._call_llm_forced_text(messages, AgentLoopState())


def test_rag_passes_snapshot_tokens_and_weight():
    from src.reranker import search_with_optional_rerank
    captured = {}
    class KB:
        def hybrid_search(self, query, **kwargs):
            captured.update(kwargs)
            return []
    search_with_optional_rerank(KB(), 'test', 3, False, bm25_weight=.65)
    assert captured['bm25_weight'] == .65
    bot = Chatbot.__new__(Chatbot)
    bot.system_prompt = 's'
    bot.max_tokens = 234
    bot.kb = None
    bot.client = SimpleNamespace(send_message=lambda messages, **kw: captured.update(kw) or 'answer')
    assert bot.chat_with_rag('hi', []) == 'answer'
    assert captured['max_tokens'] == 234


def test_trim_and_client_payload_use_the_same_protocol_and_tool_budget():
    import src.llm_client as llm
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'old'},
                {'role': 'assistant', 'content': 'x' * 400}, {'role': 'user', 'content': 'current'}]
    tools = [{'type': 'function', 'function': {'name': 'search', 'description': 'x' * 350}}]
    client = OpenAICompatibleClient(config={'api_key': 'test', 'model': 'test'})
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', 900), patch('src.chatbot.AGENT_CONTEXT_RATIO', .8), patch.object(llm, 'AGENT_MODEL_MAX_CONTEXT', 900), patch.object(llm, 'AGENT_CONTEXT_RATIO', .8):
        trimmed = bot._trim_context_if_needed(messages, tools)
        payload = client._build_payload(trimmed, tools, max_tokens=40)
    assert payload['messages'][-1]['content'] == 'current'
    assert not any(m.get('content') == 'x' * 400 for m in trimmed)
