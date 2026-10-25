import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
import server
from src.llm_client import OpenAICompatibleClient


def test_auxiliary_model_payload_enforces_budget_before_api():
    client = OpenAICompatibleClient(config={'api_key': 'test', 'model': 'test'})
    with patch('src.llm_client.AGENT_MODEL_MAX_CONTEXT', 300), patch('src.llm_client.AGENT_CONTEXT_RATIO', .8):
        with pytest.raises(Exception, match='缩短'):
            client._build_payload([{'role': 'user', 'content': 'x' * 500}], max_tokens=24)


def test_chat_request_snapshot_is_stable_and_trace_contains_no_body_or_key(tmp_path, caplog, user_client):
    captured = []
    settings = {'max_tokens': 321, 'bm25_weight': .6}
    class Bot:
        agent_mode = False
        client = SimpleNamespace(supports_tools=False, last_usage=None)
        def chat_with_rag(self, *args, **kwargs):
            captured.append((self.max_tokens, self.bm25_weight))
            settings.update(max_tokens=777, bm25_weight=.2)
            return 'answer'
    with patch('src.session.CACHE_DIR', str(tmp_path)), patch('server.get_chatbot', return_value=Bot()), patch.object(server._admin_config_service, 'runtime_config', side_effect=lambda: dict(settings)), patch('server._generate_session_title', return_value='title'):
        caplog.set_level('INFO', logger='uvicorn.error')
        client = user_client
        for _ in range(2):
            response = client.post('/api/chat', json={'message': 'private body secret-key'})
            assert response.status_code == 200
            assert response.json()['effective_mode'] == 'rag'
        assert captured == [(321, .6), (777, .2)]
        entries = [json.loads(record.message.removeprefix('chat_request ')) for record in caplog.records if record.message.startswith('chat_request ')]
        assert len(entries) == 2
        assert entries[0]['effective_mode'] == 'rag'
        assert entries[0]['usage'] is None
        assert entries[0]['outcome'] == 'success'
        assert entries[0]['request_id'] and entries[0]['duration_ms'] >= 0
        assert 'private body' not in caplog.text
        assert 'secret-key' not in caplog.text
