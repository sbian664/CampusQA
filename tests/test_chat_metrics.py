from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from unittest.mock import patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router
from src.chat_metrics import ChatMetrics, record_attempt, stage_scope, turn_scope
from src.chat_runs import ChatRunManager
from src.llm_client import OpenAICompatibleClient


def test_grouped_metrics_keep_unknown_and_attribute_late_title(tmp_path):
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    # 16:01 UTC is the next calendar day in Hong Kong.
    created = datetime(2026, 10, 29, 16, 1, tzinfo=timezone.utc).timestamp()
    store.start('turn', 'session', created_at=created)
    store.start('turn', 'another', created_at=created)
    store.mode('turn', 'agent')
    store.attempt('turn', 'decision', 'decision', 'deepseek', 'model-a', created, created + 2, 'succeeded', {'prompt_tokens': 9, 'completion_tokens': 2})
    store.attempt('turn', 'answer', 'answer', 'deepseek', 'model-a', created + 2, created + 4, 'succeeded', {'prompt_tokens': 11})
    store.finish('turn', 'succeeded', ended_at=created + 5)
    # Delayed title goes to the original question's day, not completion day.
    store.attempt('turn', 'title', 'title', 'deepseek', 'model-a', created + 86400, created + 86401, 'succeeded', {'prompt_tokens': 3, 'completion_tokens': 4})
    store.attempt('turn', 'title', 'title', 'deepseek', 'model-a', created, created, 'succeeded', {'prompt_tokens': 999, 'completion_tokens': 999})
    summary = store.summary('2026-10-30', '2026-10-30')
    assert summary['timezone'] == 'Asia/Hong_Kong'
    assert summary['totals']['questions'] == 1
    assert summary['totals']['sessions'] == 1
    assert summary['totals']['calls'] == 3
    assert summary['totals']['known_input_tokens'] == 23
    assert summary['totals']['known_output_tokens'] == 6
    assert summary['totals']['unknown_usage_calls'] == 1
    assert summary['totals']['average_duration_ms'] == 5000
    assert store.summary('2026-10-30', '2026-10-30', stage='title')['totals']['calls'] == 1
    assert store.summary('2026-10-30', '2026-10-30', model='wrong')['totals']['questions'] == 0
    assert store.turn('turn')['turn']['session_id'] == 'session'


def test_actual_model_attempts_include_retry_and_never_store_secret(tmp_path):
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    store.start('turn', 'session')
    model = OpenAICompatibleClient('custom', {'api_key': 'top-secret', 'model': 'private-model', 'base_url': 'https://example.com'})
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': 'private-answer'}}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 2}}
    with turn_scope(store, 'turn'), stage_scope('router'), \
         patch('src.llm_client.requests.post', side_effect=[requests.Timeout('private-question'), Response()]), \
         patch('src.llm_client.time.sleep'):
        assert model.send_message([{'role': 'user', 'content': 'private-question'}]) == 'private-answer'
    calls = store.turn('turn')['calls']
    assert [c['status'] for c in calls] == ['failed', 'succeeded']
    assert [c['stage'] for c in calls] == ['router', 'router']
    assert calls[0]['input_tokens'] is None
    assert calls[1]['input_tokens'] == 7
    rows = str(store.turn('turn'))
    assert 'top-secret' not in rows and 'private-question' not in rows and 'private-answer' not in rows


def test_async_title_explicitly_carries_turn_scope(tmp_path):
    import server
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    store.start('turn', 'session')
    def fake_title(*args):
        record_attempt('provider', 'model', 1, 'succeeded', {'prompt_tokens': 1, 'completion_tokens': 1})
        return 'title'
    with patch.object(server, '_generate_session_title', fake_title), ThreadPoolExecutor() as pool:
        assert pool.submit(server._tracked_session_title, 'question', None, {}, (store, 'turn')).result() == 'title'
    assert store.turn('turn')['calls'][0]['stage'] == 'title'


def test_cancelled_queued_run_is_counted_and_idempotent(tmp_path):
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    ready, release = Event(), Event()
    def worker(payload, emit, cancelled):
        ready.set()
        release.wait(3)
        return {'response': 'answer'}
    manager = ChatRunManager(tmp_path / 'runs.sqlite3', worker, max_tasks=1,
        on_created=lambda run_id, session_id, now: store.start(run_id, session_id, created_at=now),
        on_terminal=lambda run_id, status: store.finish(run_id, 'failed' if status == 'error' else status))
    run_id = '80cd812d-f4d0-4bf5-b32f-c1740ac68da9'
    try:
        first = manager.create(run_id, {'session_id': 'session'})
        assert first['created'] is True and ready.wait(2)
        assert manager.create(run_id, {'session_id': 'session'})['created'] is False
        manager.cancel(run_id)
        release.set()
        assert manager.wait_for_run(run_id, timeout=3)['status'] == 'cancelled'
        assert store.turn(run_id)['turn']['status'] == 'cancelled'
        assert store.summary()['totals']['questions'] == 1
    finally:
        release.set()
        manager.shutdown()


def test_admin_metrics_requires_cookie_and_exposes_only_metadata(tmp_path):
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    store.start('turn', 'session')
    store.attempt('turn', 'call', 'answer', 'deepseek', 'model-a', 1, 2, 'succeeded', None)
    auth = AdminControlStore(tmp_path / 'admin.sqlite3', username='admin', password_hash=hash_password('secret'))
    app = FastAPI()
    app.include_router(create_admin_router(auth, chat_metrics=store), prefix='/api/admin')
    client = TestClient(app)
    assert client.get('/api/admin/metrics/chat').status_code == 401
    assert client.post('/api/admin/auth/login', json={'username': 'admin', 'password': 'secret'}).status_code == 200
    assert client.get('/api/admin/metrics/chat').json()['totals']['unknown_usage_calls'] == 1
    assert client.get('/api/admin/metrics/chat?date_from=bad').status_code == 400
    assert client.get('/api/admin/metrics/chat/turns/turn').json()['calls'][0]['input_tokens'] is None
    assert client.get('/api/admin/metrics/chat/turns/absent').status_code == 404


def test_restarted_running_turns_are_interrupted(tmp_path):
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    store.start('turn', 'session')
    store.attempt('turn', 'call', 'decision', 'provider', 'model', 1, 1, 'running', None)
    store.recover_interrupted()
    assert store.turn('turn')['turn']['status'] == 'interrupted'
    assert store.turn('turn')['calls'][0]['status'] == 'interrupted'
    assert store.summary()['totals']['unknown_usage_calls'] == 1
    store.recover_succeeded('turn')
    assert store.turn('turn')['turn']['status'] == 'succeeded'


def test_sync_chat_attributes_answer_and_background_title_to_one_turn(tmp_path, user_client):
    import server
    from types import SimpleNamespace
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    title_recorded = Event()
    model = OpenAICompatibleClient('custom', {'api_key': 'secret-key', 'model': 'model-a', 'base_url': 'https://example.com'})
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': 'answer'}}], 'usage': {'prompt_tokens': 8, 'completion_tokens': 2}}
    bot = SimpleNamespace(agent_mode=False, client=model,
        chat_with_rag=lambda *args, **kwargs: model.send_message([{'role': 'user', 'content': 'private question'}]))
    def fake_title(*args):
        record_attempt('custom', 'model-a', 1, 'succeeded', {'prompt_tokens': 3, 'completion_tokens': 1})
        title_recorded.set()
        return 'title'
    with patch.object(server, '_chat_metrics', store), patch.object(server, 'get_chatbot', return_value=bot), \
         patch.object(server, '_generate_session_title', fake_title), patch('src.llm_client.requests.post', return_value=Response()), \
         patch('src.session.CACHE_DIR', str(tmp_path)):
        response = user_client.post('/api/chat', json={'message': 'private question'})
        assert response.status_code == 200
        assert title_recorded.wait(2)
        turn_id = response.json()['turn_id']
        data = store.turn(turn_id)
        assert data['turn']['mode'] == 'rag'
        assert data['turn']['status'] == 'succeeded'
        assert {call['stage'] for call in data['calls']} == {'answer', 'title'}
        assert sum(call['input_tokens'] for call in data['calls']) == 11
        assert 'secret-key' not in str(data) and 'private question' not in str(data)
        detail = server._admin_session_detail(response.json()['session_id'])
        assert {item['turn_id'] for item in detail['history']} == {turn_id}


def test_streaming_chat_reconnect_does_not_duplicate_calls_or_turn(tmp_path, user_client):
    import server
    from types import SimpleNamespace
    store = ChatMetrics(tmp_path / 'metrics.sqlite3')
    title_recorded = Event()
    model = OpenAICompatibleClient('custom', {'api_key': 'secret-key', 'model': 'model-a', 'base_url': 'https://example.com'})
    class Response:
        def raise_for_status(self): pass
        def close(self): pass
        def iter_lines(self, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":6,"completion_tokens":2}}'
            yield b'data: [DONE]'
    def rag(message, history, **kwargs):
        return model.stream_message([{'role': 'user', 'content': message}], kwargs['on_delta'], kwargs['cancel_event'])
    bot = SimpleNamespace(agent_mode=False, client=model, chat_with_rag=rag)
    def fake_title(*args):
        record_attempt('custom', 'model-a', 1, 'succeeded', {'prompt_tokens': 3, 'completion_tokens': 1})
        title_recorded.set()
        return 'title'
    manager = ChatRunManager(tmp_path / 'runs.sqlite3', server._execute_chat_run,
        on_created=lambda run_id, session_id, now: store.start(run_id, session_id, created_at=now),
        on_terminal=lambda run_id, status: store.finish(run_id, 'failed' if status == 'error' else status))
    run_id = 'c137bd59-b8f1-4f92-b059-bb0326e08161'
    try:
        with patch.object(server, '_chat_metrics', store), patch.object(server, 'chat_runs', manager), \
             patch.object(server, 'get_chatbot', return_value=bot), patch.object(server, '_generate_session_title', fake_title), \
             patch('src.llm_client.requests.post', return_value=Response()), patch('src.session.CACHE_DIR', str(tmp_path)):
            client = user_client
            first = client.post('/api/chat/runs', json={'run_id': run_id, 'message': 'private question'})
            assert first.status_code == 202
            assert manager.wait_for_run(run_id, timeout=3)['status'] == 'succeeded'
            assert title_recorded.wait(2)
            second = client.post('/api/chat/runs', json={'run_id': run_id, 'message': 'private question'})
            assert second.status_code == 202 and second.json()['created'] is False
            assert client.get(f'/api/chat/runs/{run_id}').json()['status'] == 'succeeded'
            data = store.turn(run_id)
            assert data['turn']['status'] == 'succeeded' and data['turn']['mode'] == 'rag'
            assert {call['stage'] for call in data['calls']} == {'answer', 'title'}
            assert store.summary()['totals']['questions'] == 1
            assert len(data['calls']) == 2
    finally:
        manager.shutdown()
