import json

import pytest

from src.chatbot import Chatbot, AgentLoopState
from src.llm_client import LLMResponse
from src.tools import ToolHandler
from test_document_catalog import catalog_kb


class CatalogClient:
    def __init__(self, streaming):
        self.streaming = streaming
        self.step = 0
        self.messages = []
        self.last_usage = {'prompt_tokens': 1, 'completion_tokens': 1}

    def send_message_with_tools(self, messages, tools=None, **kwargs):
        self.messages = messages
        if tools is None:
            return LLMResponse(content='已核验首份文档内容 ab。')
        assert {'find_documents', 'read_documents', 'search_knowledge_base'} <= {tool['function']['name'] for tool in tools}
        results = [json.loads(message['content']) for message in messages if message['role'] == 'tool']
        for result in results:
            if 'item_columns' in result:
                result['items'] = [dict(zip(result['item_columns'], row)) for row in result['items']]
        actions = [
            ('find_documents', {'page_size': 1}),
            ('find_documents', {'page_size': 1, 'cursor': results[0]['next_cursor']}) if results else None,
            ('read_documents', {'document_ids': [results[0]['items'][0]['document_id']], 'max_chars': 1}) if results else None,
            ('read_documents', {'document_ids': [results[0]['items'][0]['document_id']], 'max_chars': 1,
                                'cursor': results[-1]['next_cursor']}) if len(results) >= 3 else None,
        ]
        if self.step >= len(actions):
            if not self.streaming:
                return LLMResponse(content='已核验首份文档内容 ab。')
            action = ('finish_retrieval', {})
        else:
            action = actions[self.step]
        self.step += 1
        name, arguments = action
        return LLMResponse(tool_calls=[{'id': f'call-{self.step}', 'type': 'function',
                                       'function': {'name': name, 'arguments': arguments}}])

    def stream_message(self, messages, on_delta, cancel_event, **kwargs):
        self.messages = messages
        on_delta('已核验首份文档内容 ab。')


@pytest.mark.parametrize('streaming', [False, True])
def test_agent_can_enumerate_then_read_multiple_pages_without_search_dedup(tmp_path, streaming):
    kb = catalog_kb(tmp_path, 2)
    kb._chunk_texts['id-0_0'] = 'ab'
    bot = Chatbot.__new__(Chatbot)
    bot.kb, bot.client = kb, CatalogClient(streaming)
    statuses, deltas = [], []
    result = bot.agent_chat('按目录读取首份文档', [], on_status=lambda stage, data: statuses.append((stage, data)),
                            on_delta=deltas.append if streaming else None)
    assert result.content == '已核验首份文档内容 ab。'
    assert [entry['tool_name'] for entry in result.tool_call_log] == ['find_documents'] * 2 + ['read_documents'] * 2
    assert all(not entry.get('error') for entry in result.tool_call_log)
    assert any('读取已定位文档' in data.get('query', '') for _, data in statuses)
    assert not streaming or ''.join(deltas) == result.content


def test_document_dedup_is_exact_and_success_does_not_trip_semantic_score_fuse(tmp_path):
    handler = ToolHandler(catalog_kb(tmp_path))
    state = AgentLoopState(consecutive_empty=1, consecutive_low_score=2)
    execute = lambda args: Chatbot._execute_document_tool(handler, 'find_documents', args, state, None, None)
    execute({'filters': {'filename_contains': 'missing'}})
    assert state.consecutive_empty == state.consecutive_low_score == 0
    assert execute({'filters': {'filename_contains': 'missing'}}).startswith('[DUPLICATE]')
    assert json.loads(execute({'filters': {'filename_contains': 'UFUG'}}))['total'] == 3


def test_read_tool_is_reachable_with_bad_model_arguments_without_crashing_agent(tmp_path):
    result = Chatbot._execute_document_tool(ToolHandler(catalog_kb(tmp_path)), 'read_documents', [],
                                           AgentLoopState(), None, None)
    assert result.startswith('[ERROR]')


def test_finish_in_same_batch_does_not_discard_requested_evidence(tmp_path):
    class MixedClient:
        last_usage = None

        def send_message_with_tools(self, messages, **kwargs):
            return LLMResponse(tool_calls=[
                {'id': 'read', 'type': 'function', 'function': {'name': 'read_documents', 'arguments': {'document_ids': ['id-0']}}},
                {'id': 'finish', 'type': 'function', 'function': {'name': 'finish_retrieval', 'arguments': {}}},
            ])

        def stream_message(self, messages, on_delta, cancel_event, **kwargs):
            assert any('内容0' in message.get('content', '') for message in messages)
            on_delta('有正文依据的回答')

    bot = Chatbot.__new__(Chatbot)
    bot.kb, bot.client = catalog_kb(tmp_path), MixedClient()
    result = bot.agent_chat('读取资料后回答', [], on_delta=lambda text: None)
    assert [entry['tool_name'] for entry in result.tool_call_log] == ['read_documents']
    assert result.content == '有正文依据的回答'
