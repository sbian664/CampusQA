import json
from copy import deepcopy
from unittest.mock import patch

from src.agent_finalization import build_final_answer_messages
from src.chatbot import Chatbot
from src.tool_context import compact_document_message


def catalog_message(count=60):
    return {'role': 'tool', 'name': 'find_documents', 'tool_call_id': 'catalog', 'content': json.dumps({
        'total': count + 20,
        'returned_count': count,
        'items': [{'document_id': f'doc-{i}', 'filename': f'guide_{i}.txt', 'source': f'guides/guide_{i}.txt',
                   'title': f'Guide {i}', 'doc_type': 'text', 'mtime': 12345, 'chunk_count': 1}
                  for i in range(count)],
        'scope': 'active_indexed_documents', 'next_cursor': 'unchanged-cursor', 'excluded_documents': 0,
    }, ensure_ascii=False)}


def unpack_rows(data):
    return [dict(zip(data['item_columns'], row)) for row in data['items']]


def trim_at_budget(messages, budget):
    bot = Chatbot.__new__(Chatbot)
    bot.max_tokens = 40
    with patch('src.chatbot.AGENT_MODEL_MAX_CONTEXT', budget + 168), patch('src.chatbot.AGENT_CONTEXT_RATIO', 1):
        return bot._trim_context_if_needed(messages)


def test_lossless_catalog_encoding_retains_every_value_and_is_idempotent():
    message = catalog_message()
    original = json.loads(message['content'])
    assert compact_document_message(message) == 'find_documents'
    data = json.loads(message['content'])
    assert unpack_rows(data) == original['items']
    assert data['total'] == original['total'] and data['next_cursor'] == original['next_cursor']
    assert len(message['content']) < len(json.dumps(original))
    first = message['content']
    compact_document_message(message)
    assert message['content'] == first


def test_catalog_survives_budget_pressure_while_ordinary_excerpts_shrink_first():
    message = catalog_message()
    original = json.loads(message['content'])
    compact = deepcopy(message)
    compact_document_message(compact)
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'list documents'},
                message, {'role': 'tool', 'tool_call_id': 'search', 'content': 'ordinary result ' * 5000}]
    before = deepcopy(messages)
    result = trim_at_budget(messages, len(compact['content'].encode()) + 1000)
    data = json.loads(result[2]['content'])
    assert unpack_rows(data) == original['items']
    assert 'context_omitted_items' not in data
    assert len(result[3]['content']) < len(messages[3]['content'])
    assert messages == before


def test_lossless_compaction_avoids_unnecessary_loss_of_earlier_conversation():
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'earlier question'},
                {'role': 'assistant', 'content': 'earlier answer'},
                {'role': 'user', 'content': 'list documents'}, catalog_message()]
    packed = deepcopy(messages)
    compact_document_message(packed[-1])
    budget = Chatbot._estimate_tokens({'messages': packed, 'tools': []}) + 10
    result = trim_at_budget(messages, budget)
    assert any(message.get('content') == 'earlier question' for message in result)
    assert any(message.get('content') == 'earlier answer' for message in result)
    assert len(json.loads(result[-1]['content'])['items']) == 60


def test_read_body_trimming_keeps_json_offsets_and_cursor_meanings():
    body = '说明🙂' * 3000
    message = {'role': 'tool', 'name': 'read_documents', 'tool_call_id': 'read', 'content': json.dumps({
        'requested_documents': 1, 'next_cursor': 'next-page',
        'items': [{'document_id': 'doc', 'content': body, 'start_offset': 15, 'end_offset': 15 + len(body),
                   'total_chars': len(body) + 200, 'has_more_content': True}],
    }, ensure_ascii=False)}
    result = trim_at_budget([{'role': 'system', 'content': 's'}, message], 1800)
    data = json.loads(result[1]['content'])
    item = data['items'][0]
    assert item['context_omitted_chars'] == len(body) - len(item['content']) > 0
    assert item['visible_end_offset'] == 15 + len(item['content'])
    assert item['end_offset'] == 15 + len(body) and data['next_cursor'] == 'next-page'
    final = build_final_answer_messages(result)
    reread = trim_at_budget(final, 1400)
    data2 = json.loads(reread[1]['content'].split('\n', 1)[1])
    assert data2['items'][0]['end_offset'] == item['end_offset']
    assert data2['next_cursor'] == 'next-page'


def test_catalog_that_still_cannot_fit_omits_whole_rows_and_preserves_count():
    message = catalog_message(100)
    result = trim_at_budget([{'role': 'system', 'content': 's'}, message], 1800)
    data = json.loads(result[1]['content'])
    assert data['total'] == 120 and data['next_cursor'] == 'unchanged-cursor'
    assert data['context_omitted_items'] == 100 - len(data['items']) > 0
    assert all(len(row) == len(data['item_columns']) for row in data['items'])
