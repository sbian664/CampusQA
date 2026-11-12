from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
import server
from src.session import Session
from src.chatbot import AgentChatResult


@pytest.fixture
def client(user_client):
    return user_client


def test_invalid_chat_and_load_keep_400(client):
    for path, payload in [('/api/chat', {'message': 'x', 'session_id': '../x'}),
                          ('/api/session/load', {'session_id': 'kb_metadata'})]:
        assert client.post(path, json=payload).status_code == 400


def test_conflict_covers_chat_and_all_mutations(client):
    session = Session('busy', owner_user_id=client.user_id)
    session.save()
    with Session.coordinate('busy'):
        with ThreadPoolExecutor(max_workers=1) as pool:
            for method, path, payload in [('post', '/api/chat', {'message': 'x', 'session_id': 'busy'}),
                                          ('delete', '/api/session/busy', None),
                                          ('delete', '/api/sessions/busy', None),
                                          ('delete', '/api/session/busy/message/0', None)]:
                response = pool.submit(lambda: client.request(method, path, json=payload)).result(timeout=2)
                assert response.status_code == 409


def test_chat_reports_effective_mode_and_request_id(client):
    bot = SimpleNamespace(agent_mode=True, client=SimpleNamespace(supports_tools=False),
                          chat_with_rag=lambda *a, **kw: 'rag answer')
    with patch('server.get_chatbot', return_value=bot), patch('server._generate_session_title', return_value='title'):
        response = client.post('/api/chat', json={'message': 'hello'})
    assert response.status_code == 200
    assert response.json()['effective_mode'] == 'rag'
    assert response.headers['X-Request-ID']


def test_title_reload_does_not_resurrect_deleted_session(client):
    session = Session('deleted')
    session.save()
    future = Future()
    future.set_result('title')
    session.delete()
    server._save_generated_title('deleted', future)
    assert not session.load()


def test_router_handoff_preserves_raw_question_for_rerank_policy(client):
    calls = []

    def agent_chat(message, history, **kwargs):
        calls.append((message, kwargs))
        return AgentChatResult(content='answer', finish_reason='stop')

    bot = SimpleNamespace(agent_mode=True, client=SimpleNamespace(supports_tools=True),
                          agent_chat=agent_chat)
    router = SimpleNamespace(route=lambda query, history: SimpleNamespace(
        route='follow_up', rewritten_query='What are the prerequisites for AIAA1010?',
        selected_history=history, reason='Resolve the course from history'))
    with patch('server.get_chatbot', return_value=bot), \
         patch('server.get_context_router', return_value=router), \
         patch('server._generate_session_title', return_value='title'):
        first = client.post('/api/chat', json={'message': 'Tell me about AIAA1010.'})
        assert first.status_code == 200
        second = client.post('/api/chat', json={
            'message': 'What are its prerequisites?', 'session_id': first.json()['session_id'],
            'context_router_enabled': True})
    assert second.status_code == 200
    assert calls[1][0] == 'What are the prerequisites for AIAA1010?'
    assert calls[1][1]['original_user_message'] == 'What are its prerequisites?'
