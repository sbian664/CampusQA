"""Offline-only regressions for current DeepSeek non-thinking support."""
import os
import runpy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

import config
from src.admin.config import AdminConfigService
from src.llm_client import OpenAICompatibleClient
from src.user_llm_config import UserLLMConfigStore


def client(provider='deepseek', model='deepseek-flash', base='https://api.deepseek.com/v1'):
    return OpenAICompatibleClient(provider, {'api_key': 'synthetic-test-only', 'model': model, 'base_url': base})


def service(store):
    return AdminConfigService(llm_store=store, control_store=None, mode_provider=lambda: {}, mode_toggle=lambda: {})


def test_default_model_is_current():
    with patch.dict(os.environ, {}, clear=True), patch('dotenv.load_dotenv', return_value=False):
        assert runpy.run_path(config.__file__)['DEEPSEEK_MODEL'] == 'deepseek-flash'


def test_explicit_model_environment_override_is_preserved():
    with patch.dict(os.environ, {'MODEL_NAME': 'existing-explicit-model'}, clear=True), patch('dotenv.load_dotenv', return_value=False):
        assert runpy.run_path(config.__file__)['DEEPSEEK_MODEL'] == 'existing-explicit-model'


@pytest.mark.parametrize('model', ['deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'])
@pytest.mark.parametrize('base', ['https://api.deepseek.com/v1', 'https://API.DEEPSEEK.COM/v1', 'https://api.deepseek.com./v1'])
def test_supported_deepseek_payload_explicitly_disables_thinking(model, base):
    c = client(model=model, base=base)
    assert c._build_payload([])['thinking'] == {'type': 'disabled'}
    assert c._build_payload([], tools=[{'type': 'function'}])['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('provider,model,base', [
    ('openai', 'deepseek-flash', 'https://api.deepseek.com/v1'),
    ('custom', 'deepseek-flash', 'https://api.deepseek.com/v1'),
    ('deepseek', 'deepseek-flash', 'https://api.openai.com/v1'),
    ('deepseek', 'deepseek-flash', 'https://api.deepseek.com.example.test/v1'),
    ('deepseek', 'deepseek-chat', 'https://api.deepseek.com/v1'),
    ('deepseek', 'deepseek-reasoner', 'https://api.deepseek.com/v1'),
])
def test_unrelated_or_saved_legacy_models_are_not_rewritten(provider, model, base):
    payload = client(provider, model, base)._build_payload([])
    assert 'thinking' not in payload
    assert payload['model'] == model


class Response:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 1}}
    def iter_lines(self, **kwargs):
        yield b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}'
        yield b'data: [DONE]'
    def close(self):
        pass


@pytest.mark.parametrize('transport', ['text', 'tools', 'stream'])
def test_all_transports_use_non_thinking_payload(monkeypatch, transport):
    post = Mock(return_value=Response())
    monkeypatch.setattr('src.llm_client.requests.post', post)
    c = client()
    if transport == 'stream':
        assert c.stream_message([], lambda text: None, Event()) == 'OK'
    elif transport == 'tools':
        assert c.send_message_with_tools([]).content == 'OK'
    else:
        assert c.send_message([]) == 'OK'
    assert post.call_count == 1
    assert post.call_args.kwargs['json']['thinking'] == {'type': 'disabled'}
    assert post.call_args.kwargs['allow_redirects'] is False


@pytest.mark.parametrize('failure', ['timeout', '429', '503'])
def test_admin_probe_makes_only_one_attempt(monkeypatch, tmp_path, failure):
    if failure == 'timeout':
        error = requests.Timeout('synthetic timeout')
    else:
        response = requests.Response()
        response.status_code = int(failure)
        error = requests.HTTPError('synthetic error', response=response)
    post = Mock(side_effect=error)
    monkeypatch.setattr('src.llm_client.requests.post', post)
    monkeypatch.setattr('src.llm_client.time.sleep', lambda _: None)
    store = UserLLMConfigStore(path=tmp_path/'llm.json', default_config={
        'provider': 'deepseek', 'api_key': 'synthetic-test-only', 'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com/v1'})
    with pytest.raises(RuntimeError):
        service(store).test_llm({})
    assert post.call_count == 1
    assert not store.path.exists()
    payload = post.call_args.kwargs['json']
    assert payload['max_tokens'] == 8
    assert 'max_attempts' not in payload


@pytest.mark.parametrize('reply', [None, '', '  \n\t', 123])
def test_probe_rejects_missing_or_blank_text_without_saving(monkeypatch, reply):
    store = SimpleNamespace(resolve=Mock(return_value={}), update=Mock())
    fake = SimpleNamespace(send_message=Mock(return_value=reply))
    monkeypatch.setattr('src.admin.config.create_llm_client', lambda **_: fake)
    with pytest.raises(RuntimeError, match='有效文本'):
        service(store).test_llm({})
    store.update.assert_not_called()
    assert fake.send_message.call_args.kwargs['max_attempts'] == 1


def test_successful_probe_is_not_saved_and_preserves_existing_model(monkeypatch, tmp_path):
    store = UserLLMConfigStore(path=tmp_path/'llm.json', default_config={
        'provider': 'deepseek', 'api_key': 'synthetic-test-only', 'model': 'deepseek-chat', 'base_url': 'https://api.deepseek.com/v1'})
    store.save(store.get())
    before = store.path.read_bytes()
    post = Mock(return_value=Response())
    monkeypatch.setattr('src.llm_client.requests.post', post)
    assert service(store).test_llm({'model': 'deepseek-flash'}) == {'status': 'ok'}
    assert store.path.read_bytes() == before
    assert store.get()['model'] == 'deepseek-chat'
    assert post.call_count == 1


def test_general_chat_retains_existing_retry_policy(monkeypatch):
    monkeypatch.setattr('src.llm_client.DEEPSEEK_MAX_RETRIES', 3)
    monkeypatch.setattr('src.llm_client.time.sleep', lambda _: None)
    post = Mock(side_effect=requests.Timeout('synthetic timeout'))
    monkeypatch.setattr('src.llm_client.requests.post', post)
    with pytest.raises(RuntimeError):
        client().send_message([])
    assert post.call_count == 3
