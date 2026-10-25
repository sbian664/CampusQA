"""Authenticated integration clients; no production mock-login endpoint."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def user_client(tmp_path, monkeypatch):
    import server
    from src.identity import UserStore
    from src.personal_llm_config import PersonalLLMConfigStore
    from cryptography.fernet import Fernet
    store = UserStore(tmp_path/'control.sqlite3')
    monkeypatch.setattr(server, '_user_store', store)
    monkeypatch.setattr(server, '_personal_llm_store', PersonalLLMConfigStore(store, Fernet.generate_key()))
    monkeypatch.setattr('src.session.CACHE_DIR', str(tmp_path/'sessions'))
    user = store.login('test-app', 'test-openid')
    client = TestClient(server.app, headers={'Authorization': 'Bearer '+user['token']})
    client.user_id = user['user_id']
    return client
