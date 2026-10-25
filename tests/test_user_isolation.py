from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server


def test_business_routes_reject_anonymous_requests():
    client = TestClient(server.app)
    for method, path in [
        ('get', '/api/sessions'), ('get', '/api/session/unknown'),
        ('delete', '/api/session/unknown'), ('delete', '/api/sessions/unknown'),
        ('get', '/api/cost/unknown'), ('get', '/api/session/unknown/tool-log'),
        ('post', '/api/chat'), ('post', '/api/chat/runs'),
        ('get', '/api/chat/runs/unknown'), ('get', '/api/chat/runs/unknown/events'),
        ('post', '/api/chat/runs/unknown/cancel'),
        ('post', '/api/attachments/parse'), ('post', '/api/kb/search'),
        ('get', '/api/llm-config'), ('put', '/api/llm-config'),
        ('post', '/api/llm-config/test'), ('get', '/api/submissions'),
        ('post', '/api/submissions'), ('get', '/api/submissions/old/status'),
    ]:
        assert getattr(client, method)(path).status_code == 401, path
    assert client.get('/api/health').status_code == 200


def test_openapi_describes_user_bearer_boundary():
    schema = server.app.openapi()
    assert schema['paths']['/api/chat']['post']['security'] == [{'UserBearer': []}]
    assert 'security' not in schema['paths']['/api/health']['get']


def test_migrations_repeat_without_claiming_or_losing_legacy_data(identities, tmp_path):
    import io
    from src.identity import UserStore
    from src.submissions import SubmissionService
    from src.session import Session
    users, a, b = identities
    settings = dict(db_path=users.db_path, submissions_dir=tmp_path/'legacy', documents_dir=tmp_path/'docs', kb_provider=lambda: None, supported_formats={'.txt'})
    service = SubmissionService(**settings)
    item = service.submit('legacy.txt', io.BytesIO(b'legacy content'))
    # Model the old deployed schema, then apply the constructor migration twice.
    with users.connect() as db:
        db.execute('DROP INDEX idx_submissions_owner')
        db.execute('ALTER TABLE knowledge_submissions DROP COLUMN owner_user_id')
    for _ in range(2):
        service = SubmissionService(**settings)
        assert service.list_admin()['items'][0]['submission_id'] == item['submission_id']
        assert service.list_admin()['items'][0]['owner_user_id'] is None
        assert service.list_owned(a['user_id'])['items'] == []
        assert UserStore(users.db_path).authenticate(a['token']) == a['user_id']
    owned = Session(owner_user_id=a['user_id'])
    owned.save()
    with pytest.raises(AttributeError):
        owned.owner_user_id = b['user_id']
    impostor = Session(owned.session_id, owner_user_id=b['user_id'])
    with pytest.raises(Exception, match='归属不可变更'):
        impostor.save()


@pytest.fixture
def identities(tmp_path, monkeypatch):
    from src.identity import UserStore
    from src.personal_llm_config import PersonalLLMConfigStore
    from cryptography.fernet import Fernet
    store = UserStore(tmp_path / 'control.sqlite3')
    monkeypatch.setattr(server, '_user_store', store)
    monkeypatch.setattr(server, '_personal_llm_store', PersonalLLMConfigStore(store, Fernet.generate_key()))
    monkeypatch.setattr('src.session.CACHE_DIR', str(tmp_path / 'sessions'))
    a = store.login('app', 'openid-a')
    b = store.login('app', 'openid-b')
    return store, a, b


def auth(login):
    return {'Authorization': 'Bearer ' + login['token']}


def test_identity_stability_expiry_revocation_and_token_hashes(identities):
    store, a, b = identities
    assert store.login('app', 'openid-a')['user_id'] == a['user_id']
    assert store.login('other-app', 'openid-a')['user_id'] != a['user_id']
    assert a['user_id'] != b['user_id']
    assert store.authenticate(a['token']) == a['user_id']
    with store.connect() as db:
        db.execute('UPDATE user_tokens SET expires_at=0 WHERE token_hash=?', (store.hash_token(a['token']),))
    assert store.authenticate(a['token']) is None
    store.revoke(b['token'])
    assert store.authenticate(b['token']) is None
    assert a['token'].encode() not in store.db_path.read_bytes()


def test_login_never_trusts_client_identity(identities):
    _, a, _ = identities
    client = TestClient(server.app)
    with patch.object(server, 'exchange_wechat_code', return_value=('app', 'openid-a')):
        reply = client.post('/api/auth/wechat', json={'code': 'one-time', 'user_id': 'forged'})
    assert reply.status_code == 200
    assert reply.json()['user_id'] == a['user_id']
    assert 'session_key' not in reply.text
    assert client.get('/api/auth/me', headers=auth(reply.json())).json() == {'user_id': a['user_id']}
    assert client.post('/api/auth/logout', headers=auth(reply.json())).status_code == 200
    assert client.get('/api/auth/me', headers=auth(reply.json())).status_code == 401


def test_all_session_operations_hide_foreign_missing_and_legacy(identities):
    _, a, b = identities
    from src.session import Session
    owned = Session(owner_user_id=a['user_id'])
    owned.add_message('user', 'private')
    owned.save()
    legacy = Session()
    legacy.save()
    client = TestClient(server.app)
    assert client.get('/api/session/' + owned.session_id, headers=auth(a)).status_code == 200
    assert [s['session_id'] for s in client.get('/api/sessions', headers=auth(a)).json()['sessions']] == [owned.session_id]
    assert client.get('/api/sessions', headers=auth(b)).json()['sessions'] == []
    for sid in [owned.session_id, legacy.session_id, 'nonexistent']:
        for method, path, payload in [
            ('get', f'/api/session/{sid}', None),
            ('delete', f'/api/session/{sid}', None),
            ('delete', f'/api/sessions/{sid}', None),
            ('delete', f'/api/session/{sid}/message/0', None),
            ('get', f'/api/cost/{sid}', None),
            ('get', f'/api/session/{sid}/tool-log', None),
            ('post', '/api/session/load', {'session_id': sid}),
            ('post', '/api/chat', {'session_id': sid, 'message': 'steal', 'reroll': True}),
        ]:
            assert getattr(client, method)(path, headers=auth(b), **({'json': payload} if payload else {})).status_code == 404, path
    restored = Session(owned.session_id)
    restored.load()
    assert restored.messages[0]['content'] == 'private'
    assert restored.owner_user_id == a['user_id']


def test_busy_foreign_sessions_still_return_404(identities):
    import uuid
    from src.session import Session
    _, a, b = identities
    owned = Session(owner_user_id=a['user_id'])
    owned.save()
    client = TestClient(server.app)
    sid = owned.session_id
    with Session.coordinate(sid):
        for method, path, body in [
            ('get', f'/api/session/{sid}', None),
            ('delete', f'/api/session/{sid}', None),
            ('delete', f'/api/sessions/{sid}', None),
            ('delete', f'/api/session/{sid}/message/0', None),
            ('get', f'/api/cost/{sid}', None),
            ('get', f'/api/session/{sid}/tool-log', None),
            ('post', '/api/session/load', {'session_id': sid}),
            ('post', '/api/chat', {'session_id': sid, 'message': 'x'}),
            ('post', '/api/chat/runs', {'session_id': sid, 'message': 'x', 'run_id': str(uuid.uuid4())}),
        ]:
            response = client.request(method, path, headers=auth(b), json=body)
            assert response.status_code == 404, (path, response.text)
    with Session.coordinate(sid):
        assert client.post('/api/chat/runs', headers=auth(a), json={
            'session_id': sid, 'message': 'x', 'run_id': str(uuid.uuid4())}).status_code == 409


def test_owned_run_creation_retry_remains_idempotent_while_session_locked(identities, tmp_path, monkeypatch):
    import threading
    import uuid
    from src.chat_runs import ChatRunManager
    from src.session import Session
    _, a, _ = identities
    session = Session(owner_user_id=a['user_id'])
    session.save()
    started, release = threading.Event(), threading.Event()
    def worker(payload, emit, cancel):
        with Session.coordinate(session.session_id):
            started.set()
            release.wait(5)
        return {'session_id': session.session_id, 'response': 'answer'}
    manager = ChatRunManager(tmp_path/'busy-runs.sqlite3', worker=worker)
    monkeypatch.setattr(server, 'chat_runs', manager)
    monkeypatch.setattr(server, 'get_chatbot', lambda config=None: type('Bot', (), {'agent_mode': True})())
    client = TestClient(server.app)
    payload = {'session_id': session.session_id, 'message': 'x', 'run_id': str(uuid.uuid4())}
    try:
        assert client.post('/api/chat/runs', headers=auth(a), json=payload).status_code == 202
        assert started.wait(2)
        retry = client.post('/api/chat/runs', headers=auth(a), json=payload)
        assert retry.status_code == 202
        assert retry.json()['created'] is False
    finally:
        release.set()
        manager.shutdown()


CONFIG = {'provider': 'deepseek', 'model': 'deepseek-chat', 'base_url': 'https://api.deepseek.com/v1', 'api_key': 'personal-secret-a'}


def test_personal_keys_are_encrypted_scoped_and_never_fallback(identities):
    store, a, b = identities
    personal = server._personal_llm_store
    default = {'provider': 'deepseek', 'model': 'server', 'base_url': CONFIG['base_url'], 'api_key': 'server-secret'}
    assert personal.effective(a['user_id'], default) == default
    public = personal.save(a['user_id'], {'source': 'personal', **CONFIG})
    assert public['has_api_key'] and 'api_key' not in public
    assert b'personal-secret-a' not in store.db_path.read_bytes()
    assert personal.effective(b['user_id'], default) == default
    assert personal.resolve(a['user_id'], {**CONFIG, 'api_key': ''})['api_key'] == CONFIG['api_key']
    for change in [{'provider': 'custom'}, {'base_url': 'https://api.openai.com/v1'}]:
        with pytest.raises(ValueError):
            personal.resolve(a['user_id'], {**CONFIG, **change, 'api_key': ''})
    with pytest.raises(ValueError):
        personal.resolve(b['user_id'], {**CONFIG, 'api_key': ''})
    with pytest.raises(ValueError):
        personal.save(a['user_id'], {'source': 'personal', 'provider': 'local', 'model': 'x', 'base_url': 'http://localhost:11434', 'api_key': ''})
    for provider in ['LOCAL', ' local ']:
        with pytest.raises(ValueError):
            personal.resolve(a['user_id'], {'provider': provider, 'model': 'x', 'base_url': 'http://localhost:11434', 'api_key': ''})
    with store.connect() as db:
        db.execute("UPDATE user_llm_configs SET encrypted_api_key='broken' WHERE user_id=?", (a['user_id'],))
    with pytest.raises(ValueError):
        personal.effective(a['user_id'], default)
    assert personal.delete(a['user_id'])['source'] == 'server'
    assert personal.effective(a['user_id'], default) == default


def test_personal_config_endpoints_and_admin_boundary(identities):
    _, a, b = identities
    client = TestClient(server.app)
    assert client.put('/api/llm-config', headers=auth(a), json={'source': 'personal', **CONFIG}).status_code == 200
    assert client.get('/api/llm-config', headers=auth(b)).json()['source'] == 'server'
    assert 'personal-secret' not in client.get('/api/llm-config', headers=auth(a)).text
    assert client.post('/api/mode/toggle', headers=auth(a)).status_code == 401
    assert client.get('/api/admin/sessions', headers=auth(a)).status_code == 401
    assert client.delete('/api/llm-config', headers=auth(a)).json()['source'] == 'server'


def test_owned_submissions_ignore_old_receipts(identities, tmp_path, monkeypatch):
    store, a, b = identities
    from src.submissions import SubmissionService
    service = SubmissionService(db_path=store.db_path, submissions_dir=tmp_path/'staging', documents_dir=tmp_path/'docs', kb_provider=lambda: None, supported_formats={'.txt'}, rate_limit_count=1)
    with patch.object(server._submission_service, 'submit', service.submit), \
         patch.object(server._submission_service, 'list_owned', service.list_owned), \
         patch.object(server._submission_service, 'get_owned_status', service.get_owned_status):
        client = TestClient(server.app)
        item = client.post('/api/submissions', headers=auth(a), files={'file': ('a.txt', b'a')}).json()
        sid = item['submission_id']
        assert client.get('/api/submissions', headers=auth(b)).json()['items'] == []
        assert client.get('/api/submissions', headers=auth(a)).json()['items'][0]['submission_id'] == sid
        assert client.get(f'/api/submissions/{sid}/status', headers={**auth(b), 'X-Submission-Token': item.get('access_token', '')}).status_code == 404
        assert client.get(f'/api/submissions/{sid}/status', headers=auth(a)).status_code == 200
        assert client.post('/api/submissions', headers=auth(a), files={'file': ('c.txt', b'c')}).status_code == 429
        assert client.post('/api/submissions', headers=auth(b), files={'file': ('b.txt', b'b')}).status_code == 201


def test_run_ids_are_private_and_remain_private_after_restart(identities, tmp_path, monkeypatch):
    import uuid
    from src.chat_runs import ChatRunManager
    _, a, b = identities
    manager = ChatRunManager(tmp_path/'runs.sqlite3', worker=lambda payload, emit, cancel: {'response': 'secret', 'session_id': payload['session_id']})
    monkeypatch.setattr(server, 'chat_runs', manager)
    monkeypatch.setattr(server, 'get_chatbot', lambda config=None: type('Bot', (), {'agent_mode': True})())
    client = TestClient(server.app)
    run_id = str(uuid.uuid4())
    payload = {'run_id': run_id, 'message': 'hello'}
    created = client.post('/api/chat/runs', headers=auth(a), json=payload)
    assert created.status_code == 202
    manager.wait_for_run(run_id)
    for method, suffix in [('get', ''), ('get', '/events'), ('post', '/cancel')]:
        assert getattr(client, method)('/api/chat/runs/'+run_id+suffix, headers=auth(b)).status_code == 404
    assert client.post('/api/chat/runs', headers=auth(b), json=payload).status_code == 404
    assert client.post('/api/chat/runs', headers=auth(a), json=payload).status_code == 202
    manager.shutdown()
    restored = ChatRunManager(tmp_path/'runs.sqlite3', worker=lambda *args: None)
    monkeypatch.setattr(server, 'chat_runs', restored)
    assert client.get('/api/chat/runs/'+run_id, headers=auth(b)).status_code == 404
    assert client.get('/api/chat/runs/'+run_id, headers=auth(a)).json()['result']['response'] == 'secret'
    assert b'api_key' not in (tmp_path/'runs.sqlite3').read_bytes()
    restored.shutdown()


def test_parallel_calls_use_request_snapshot_for_body_router_and_title(identities, monkeypatch):
    _, a, b = identities
    personal = server._personal_llm_store
    personal.save(a['user_id'], {'source': 'personal', **CONFIG})
    personal.save(b['user_id'], {'source': 'personal', **CONFIG, 'model': 'b', 'api_key': 'key-b'})
    seen = []
    class Bot:
        agent_mode = False
        client = type('Client', (), {})()
        def __init__(self, config):
            self.config = config
        def chat_with_rag(self, message, history, **kwargs):
            seen.append(('body', message, self.config.copy()))
            return self.config['model']
    monkeypatch.setattr(server, 'get_chatbot', lambda config=None: Bot(config))
    monkeypatch.setattr(server, '_generate_session_title', lambda message, factory, config: seen.append(('title', message, config.copy())) or 'title')
    def send(login):
        return TestClient(server.app).post('/api/chat', headers=auth(login), json={'message': login['user_id']})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(send, [a, b]))
    assert all(result.status_code == 200 for result in results)
    for kind, message, config in seen:
        assert config['api_key'] == (CONFIG['api_key'] if message == a['user_id'] else 'key-b')
    assert len([entry for entry in seen if entry[0] == 'body']) == 2
    # The router must also get a fresh client, never a personal-key global singleton.
    with patch.object(server, 'create_context_router') as create:
        server.get_context_router(CONFIG)
        assert create.call_args.kwargs['llm_config'] == CONFIG


def test_wechat_failure_modes_do_not_disclose_secrets(monkeypatch):
    from src.identity import exchange_wechat_code
    from fastapi import HTTPException
    import requests
    monkeypatch.delenv('WECHAT_APP_ID', raising=False)
    with pytest.raises(HTTPException) as error:
        exchange_wechat_code('x')
    assert error.value.status_code == 503
    monkeypatch.setenv('WECHAT_APP_ID', 'app')
    monkeypatch.setenv('WECHAT_APP_SECRET', 'wechat-secret')
    with patch('src.identity.requests.get', side_effect=requests.Timeout('wechat-secret')):
        with pytest.raises(HTTPException) as error:
            exchange_wechat_code('x')
        assert error.value.status_code == 502
        assert 'wechat-secret' not in error.value.detail
    with patch('src.identity.requests.get') as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {'errcode': 40029, 'errmsg': 'wechat-secret'}
        with pytest.raises(HTTPException) as error:
            exchange_wechat_code('x')
        assert error.value.status_code == 401
        get.return_value.json.return_value = []
        with pytest.raises(HTTPException) as error:
            exchange_wechat_code('x')
        assert error.value.status_code == 502


def test_upstream_redirects_and_errors_never_forward_or_disclose_keys(capsys):
    import requests
    from src.llm_client import OpenAICompatibleClient
    client = OpenAICompatibleClient('deepseek', CONFIG)
    with patch('src.llm_client.requests.post') as post:
        post.return_value.status_code = 302
        with pytest.raises(Exception):
            client.send_message([{'role': 'user', 'content': 'x'}])
        assert post.call_args.kwargs['allow_redirects'] is False
    with patch('src.llm_client.requests.post', side_effect=requests.HTTPError('personal-secret-a')):
        result = client.send_message_with_tools([{'role': 'user', 'content': 'x'}])
    assert 'personal-secret-a' not in result.content
    assert 'personal-secret-a' not in capsys.readouterr().out


def test_real_clients_keep_keys_for_concurrent_agent_router_and_title(identities, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    _, a, b = identities
    for login, key in [(a, CONFIG['api_key']), (b, 'key-b')]:
        server._personal_llm_store.save(login['user_id'], {'source': 'personal', **CONFIG, 'api_key': key})
    monkeypatch.setattr(server, 'get_kb', lambda: object())
    monkeypatch.setattr(server, '_chatbot', SimpleNamespace(agent_mode=True))
    monkeypatch.setattr(server._admin_config_service, 'runtime_config', lambda: {'max_tokens': 500, 'bm25_weight': .5})
    seen = []
    seen_lock = threading.Lock()
    title_executor = ThreadPoolExecutor(2)
    monkeypatch.setattr(server, '_title_executor', title_executor)

    def post(url, *, json: dict, headers, **kwargs):
        messages = json['messages']
        if json.get('response_format'):
            category = 'router'
            query = __import__('json').loads(messages[-1]['content'])['current_query']
            text = __import__('json').dumps({'route': 'follow_up', 'rewritten_query': query, 'reason': 'follow up'})
        elif json.get('max_tokens') == 24:
            category = 'title'
            text = 'title'
        else:
            category = 'agent'
            text = 'answer'
        with seen_lock:
            seen.append((category, headers['Authorization']))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
            json=lambda: {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}], 'usage': {}})

    with patch('src.llm_client.requests.post', side_effect=post):
        def send(login):
            client = TestClient(server.app)
            first = client.post('/api/chat', headers=auth(login), json={'message': 'first'})
            assert first.status_code == 200
            return client.post('/api/chat', headers=auth(login), json={
                'message': 'follow up', 'session_id': first.json()['session_id'], 'context_router_enabled': True})
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(send, [a, b]))
        title_executor.shutdown(wait=True)
    assert all(response.status_code == 200 for response in results)
    for key in [CONFIG['api_key'], 'key-b']:
        assert seen.count(('title', 'Bearer '+key)) == 1
        assert seen.count(('router', 'Bearer '+key)) == 1
        assert seen.count(('agent', 'Bearer '+key)) == 2
