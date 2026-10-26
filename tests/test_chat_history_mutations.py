"""Exercise real chat persistence with stub models, including run recovery reads."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import server
from src.chatbot import AgentChatResult
from src.chat_runs import ChatRunManager
from src.session import Session


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('src.session.CACHE_DIR', str(tmp_path / 'sessions'))
    return TestClient(server.app)


@pytest.fixture(params=['rag', 'agent'])
def scenario(request, client, tmp_path, monkeypatch):
    calls = []
    behavior = {'cancel': False, 'fail': False, 'error_result': False}

    def answer(message, history, **kwargs):
        calls.append((message, deepcopy(history)))
        if behavior['fail']:
            raise RuntimeError('Model request failed')
        if behavior['cancel']:
            kwargs['cancel_event'].set()
        return AgentChatResult(content='New answer', finish_reason='error' if behavior['error_result'] else 'stop')

    bot = SimpleNamespace(
        agent_mode=request.param == 'agent', client=SimpleNamespace(supports_tools=True),
        agent_chat=answer,
        chat_with_rag=lambda *args, **kwargs: answer(*args, **kwargs).content,
    )
    monkeypatch.setattr(server, 'get_chatbot', lambda *args: bot)
    monkeypatch.setattr(server, 'get_llm_config', lambda: {'provider': 'test'})
    monkeypatch.setattr(server._admin_config_service, 'runtime_config', lambda: {'max_tokens': 128, 'bm25_weight': 0.5})
    manager = ChatRunManager(tmp_path / 'runs.sqlite3', worker=server._execute_chat_run)
    monkeypatch.setattr(server, 'chat_runs', manager)

    def seed(messages):
        kwargs = {'owner_user_id': client.user_id} if hasattr(client, 'user_id') else {}
        session = Session(session_id=str(uuid4()), **kwargs)
        for message in messages:
            session.add_message(**message)
        session.save()
        return session.session_id

    def submit(session_id, transport, **payload):
        payload = {'session_id': session_id, **payload}
        if transport == 'sync':
            response = client.post('/api/chat', json=payload)
            assert response.status_code == 200, response.text
            return response.json()
        run_id = str(uuid4())
        response = client.post('/api/chat/runs', json={'run_id': run_id, **payload})
        assert response.status_code == 202, response.text
        manager.wait_for_run(run_id, timeout=3)
        snapshot = client.get(f'/api/chat/runs/{run_id}').json()
        expected_status = 'cancelled' if behavior['cancel'] else 'error' if behavior['fail'] or behavior['error_result'] else 'succeeded'
        assert snapshot['status'] == expected_status, snapshot
        return snapshot.get('result')

    def read(session_id):
        response = client.get(f'/api/session/{session_id}')
        assert response.status_code == 200, response.text
        return response.json()['history']

    yield SimpleNamespace(seed=seed, submit=submit, read=read, calls=calls, behavior=behavior, client=client)
    manager.shutdown()


@pytest.mark.parametrize('transport', ['sync', 'run'])
@pytest.mark.parametrize('question', ['Question', 'Read notes\n\nAttachments:\n- notes.txt (1 KB)\n\nPrivate document body'])
def test_repeated_reroll_replaces_one_exchange_without_repeating_the_question(scenario, transport, question):
    earlier = [{'role': 'user', 'content': 'Earlier'}, {'role': 'assistant', 'content': 'Earlier answer'}]
    session_id = scenario.seed(earlier + [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': 'Old answer'}])
    for _ in range(3):
        scenario.submit(session_id, transport, message=question, reroll=True)
        assert scenario.calls[-1] == (question, earlier), 'The current question is passed separately, once'
        assert scenario.read(session_id) == earlier + [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': 'New answer'}]


@pytest.mark.parametrize('transport', ['sync', 'run'])
def test_rewrite_uses_visible_history_index_and_discards_the_replaced_branch(scenario, transport):
    session_id = scenario.seed([
        {'role': 'user', 'content': 'First'},
        {'role': 'assistant', 'content': 'Searching', 'tool_calls': [{'id': 'call-1', 'function': {'name': 'search'}}]},
        {'role': 'tool', 'content': 'Internal result', 'tool_call_id': 'call-1'},
        {'role': 'assistant', 'content': 'First answer'},
        {'role': 'user', 'content': 'Old second question'},
        {'role': 'assistant', 'content': 'Old second answer'},
        {'role': 'user', 'content': 'Obsolete third question'},
        {'role': 'assistant', 'content': 'Obsolete third answer'},
    ])
    prefix = scenario.read(session_id)[:3]
    scenario.submit(session_id, transport, message='Rewritten second question', edit_index=3)
    assert scenario.calls[-1] == ('Rewritten second question', prefix)
    expected = prefix + [{'role': 'user', 'content': 'Rewritten second question'}, {'role': 'assistant', 'content': 'New answer'}]
    assert scenario.read(session_id) == expected
    scenario.submit(session_id, transport, message='Rewritten second question', reroll=True)
    assert scenario.read(session_id) == expected


@pytest.mark.parametrize('operation', ['reroll', 'rewrite'])
def test_cancelled_run_leaves_the_saved_exchange_unchanged_and_can_be_retried(scenario, operation):
    original = [{'role': 'user', 'content': 'Question'}, {'role': 'assistant', 'content': 'Old answer'}]
    session_id = scenario.seed(original)
    payload = {'message': 'Question', 'reroll': True} if operation == 'reroll' else {'message': 'Rewritten', 'edit_index': 0}
    scenario.behavior['cancel'] = True
    scenario.submit(session_id, 'run', **payload)
    assert scenario.read(session_id) == original
    scenario.behavior['cancel'] = False
    scenario.submit(session_id, 'run', **payload)
    assert scenario.read(session_id) == [{'role': 'user', 'content': payload['message']}, {'role': 'assistant', 'content': 'New answer'}]


@pytest.mark.parametrize('operation', ['reroll', 'rewrite'])
@pytest.mark.parametrize('transport', ['sync', 'run'])
def test_failed_generation_does_not_save_a_partially_replaced_branch(scenario, operation, transport):
    original = [{'role': 'user', 'content': 'Question'}, {'role': 'assistant', 'content': 'Old answer'}]
    session_id = scenario.seed(original)
    payload = {'message': 'Question', 'reroll': True} if operation == 'reroll' else {'message': 'Rewritten', 'edit_index': 0}
    scenario.behavior['fail'] = True
    if transport == 'sync':
        response = scenario.client.post('/api/chat', json={'session_id': session_id, **payload})
        assert response.status_code == 500
    else:
        scenario.submit(session_id, transport, **payload)
    assert scenario.read(session_id) == original
    scenario.behavior['fail'] = False
    scenario.submit(session_id, transport, **payload)
    assert scenario.read(session_id) == [{'role': 'user', 'content': payload['message']}, {'role': 'assistant', 'content': 'New answer'}]


@pytest.mark.parametrize('scenario', ['agent'], indirect=True)
@pytest.mark.parametrize('operation', ['reroll', 'rewrite'])
@pytest.mark.parametrize('transport', ['sync', 'run'])
def test_model_error_result_preserves_the_previous_answer(scenario, operation, transport):
    original = [{'role': 'user', 'content': 'Question'}, {'role': 'assistant', 'content': 'Old answer'}]
    session_id = scenario.seed(original)
    payload = {'message': 'Question', 'reroll': True} if operation == 'reroll' else {'message': 'Rewritten', 'edit_index': 0}
    scenario.behavior['error_result'] = True
    if transport == 'sync':
        response = scenario.client.post('/api/chat', json={'session_id': session_id, **payload})
        assert response.status_code == 500
    else:
        scenario.submit(session_id, transport, **payload)
    assert scenario.read(session_id) == original


@pytest.mark.parametrize('edit_index', [-1, 1, 99])
def test_invalid_rewrite_does_not_append_or_truncate_saved_messages(scenario, edit_index):
    original = [{'role': 'user', 'content': 'Question'}, {'role': 'assistant', 'content': 'Old answer'}]
    session_id = scenario.seed(original)
    response = scenario.client.post('/api/chat', json={'session_id': session_id, 'message': 'Rewritten', 'edit_index': edit_index})
    assert response.status_code == 400
    assert scenario.read(session_id) == original
    assert not scenario.calls


@pytest.mark.parametrize('payload', [
    {'message': 'Rewritten', 'edit_index': 0, 'reroll': True},
    {'message': 'Question', 'reroll': True},
])
def test_ambiguous_or_missing_reroll_target_does_not_create_a_new_turn(scenario, payload):
    session_id = scenario.seed([])
    response = scenario.client.post('/api/chat', json={'session_id': session_id, **payload})
    assert response.status_code == 400
    assert scenario.read(session_id) == []
    assert not scenario.calls


def test_reroll_does_not_deduplicate_intentional_repeated_questions(scenario):
    original = [{'role': 'user', 'content': 'Again'}, {'role': 'assistant', 'content': 'First answer'},
                {'role': 'user', 'content': 'Again'}, {'role': 'assistant', 'content': 'Second answer'}]
    session_id = scenario.seed(original)
    scenario.submit(session_id, 'run', message='Again', reroll=True)
    assert scenario.read(session_id) == original[:-1] + [{'role': 'assistant', 'content': 'New answer'}]


@pytest.mark.parametrize('index', [1, 2])
def test_delete_uses_the_same_visible_indexes_as_history_and_rewrite(scenario, index):
    session_id = scenario.seed([
        {'role': 'user', 'content': 'First'},
        {'role': 'assistant', 'content': 'First answer', 'tool_calls': [{'id': 'call-1', 'function': {'name': 'search'}}]},
        {'role': 'tool', 'content': 'Internal result', 'tool_call_id': 'call-1'},
        {'role': 'user', 'content': 'Second'},
        {'role': 'assistant', 'content': 'Second answer'},
    ])
    history = scenario.read(session_id)
    response = scenario.client.delete(f'/api/session/{session_id}/message/{index}')
    assert response.status_code == 200
    assert scenario.read(session_id) == history[:index] + history[index + 1:]
    raw = Session(session_id=session_id)
    raw.load()
    if index == 1:
        assert all(message['role'] != 'tool' for message in raw.messages)


@pytest.mark.parametrize('initial_batch', ['delta', 'empty'])
@pytest.mark.parametrize('status,event_type', [('succeeded', 'done'), ('error', 'error'), ('cancelled', 'cancelled'), ('interrupted', 'status')])
def test_stream_drains_the_terminal_event_when_the_run_finishes_between_batches(client, monkeypatch, initial_batch, status, event_type):
    batches = [
        [{'id': 1, 'event': 'delta', 'data': {'text': 'Partial'}}] if initial_batch == 'delta' else [],
        [{'id': 2, 'event': event_type, 'data': {'status': status, 'result': {'response': 'Answer'}}}],
    ]
    manager = SimpleNamespace(
        get=lambda run_id: {'status': status},
        owner=lambda run_id: getattr(client, 'user_id', None),
        wait_for_events=lambda run_id, after, timeout: batches.pop(0),
    )
    monkeypatch.setattr(server, 'chat_runs', manager)
    response = client.get(f'/api/chat/runs/{uuid4()}/events')
    assert response.status_code == 200
    assert f'event: {event_type}\n' in response.text
    assert f'"status":"{status}"' in response.text
    assert not batches, 'The final event must be read even if status is already terminal'
