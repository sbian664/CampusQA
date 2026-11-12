"""Startup must prewarm sequentially without downloads or populating score cache."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import src.reranker as reranker
import src.startup_warmup as startup


@pytest.fixture
def model_runtime(monkeypatch):
    model = SimpleNamespace(predict=Mock(return_value=[0.2]), _campusqa_backend='torch')
    ensure = Mock(return_value='/local/model')
    load = Mock(return_value=model)
    monkeypatch.setattr(startup, 'RERANKER_AVAILABLE', True)
    monkeypatch.setattr(reranker, 'ensure_local_reranker_model', ensure)
    monkeypatch.setattr(reranker, 'get_reranker_model', load)
    monkeypatch.setattr(reranker, '_score_cache', reranker.RerankScoreCache(16))
    return model, ensure, load


def test_lazy_mode_does_not_load(model_runtime):
    model, ensure, load = model_runtime
    assert startup.prepare_reranker_startup('lazy')['status'] == 'lazy'
    ensure.assert_not_called()
    load.assert_not_called()
    model.predict.assert_not_called()


def test_disabled_reranker_does_not_load(monkeypatch, model_runtime):
    monkeypatch.setattr(startup, 'RERANKER_AVAILABLE', False)
    assert startup.prepare_reranker_startup('warmup')['status'] == 'disabled'
    model_runtime[2].assert_not_called()


def test_invalid_mode_is_explicit_error(model_runtime):
    with pytest.raises(ValueError, match='STARTUP_MODE'):
        startup.prepare_reranker_startup('eager-download')
    model_runtime[2].assert_not_called()


def test_warmup_uses_local_model_and_one_pair_without_filling_cache(model_runtime):
    model, ensure, load = model_runtime
    report = startup.prepare_reranker_startup('warmup')
    ensure.assert_called_once_with(auto_download=False)
    load.assert_called_once()
    assert report['status'] == 'warmed'
    assert report['backend'] == 'torch'
    assert report['load_ms'] >= 0 and report['warmup_ms'] >= 0
    args, kwargs = model.predict.call_args
    assert len(args[0]) == 1 and kwargs['batch_size'] == 1
    assert kwargs['show_progress_bar'] is False
    assert not reranker._score_cache._entries


def test_missing_local_model_fails_without_loading(model_runtime):
    model_runtime[1].side_effect = FileNotFoundError('not present')
    with pytest.raises(FileNotFoundError):
        startup.prepare_reranker_startup('warmup')
    model_runtime[2].assert_not_called()


@pytest.mark.parametrize('scores', [[], [float('nan')], [float('inf')], [0.2, 0.3]])
def test_bad_predictions_fail_startup(model_runtime, scores):
    model_runtime[0].predict.return_value = scores
    with pytest.raises(ValueError, match='warmup'):
        startup.prepare_reranker_startup('warmup')


def test_predict_failure_is_not_silently_changed_to_lazy(model_runtime):
    model_runtime[0].predict.side_effect = RuntimeError('allocation failed')
    with pytest.raises(RuntimeError, match='allocation failed'):
        startup.prepare_reranker_startup('warmup')


def test_linux_memory_snapshot_reports_current_and_high_water(tmp_path):
    path = tmp_path / 'status'
    path.write_text('Name:\tpython\nVmRSS:\t131072 kB\nVmHWM:\t262144 kB\n')
    snapshot = startup.process_memory_snapshot(path)
    assert snapshot['rss_mib'] == 128.0
    assert snapshot['peak_rss_mib'] == 256.0


def test_lifespan_waits_for_index_then_warmup_before_ready(monkeypatch):
    import server
    order = []
    kb = SimpleNamespace(index_state=SimpleNamespace(status='ready'),
                         embeddings_manager=SimpleNamespace(get_embedding_dimension=lambda: 384))
    monkeypatch.setattr(server, 'get_kb', lambda: order.append('embedding') or kb)
    async def file_work(func, current_kb):
        assert current_kb is kb
        order.append('index')
    monkeypatch.setattr(server, 'run_file_work', file_work)
    monkeypatch.setattr(server, 'get_chatbot', lambda: order.append('chatbot'))
    monkeypatch.setattr(server, 'prepare_reranker_startup',
                        lambda: order.append('warmup') or {'status': 'warmed'})
    monkeypatch.setattr(server, '_admin_job_manager', SimpleNamespace(shutdown=lambda: order.append('shutdown')))
    app = SimpleNamespace(state=SimpleNamespace())
    async def run():
        async with server._lifespan(app):
            assert app.state.reranker_startup['status'] == 'warmed'
            order.append('ready')
    asyncio.run(run())
    assert order == ['embedding', 'index', 'chatbot', 'warmup', 'ready', 'shutdown']


def test_warmup_failure_prevents_ready(monkeypatch):
    import server
    kb = SimpleNamespace(index_state=SimpleNamespace(status='maintenance'),
                         embeddings_manager=SimpleNamespace(get_embedding_dimension=lambda: 384))
    monkeypatch.setattr(server, 'get_kb', lambda: kb)
    monkeypatch.setattr(server, 'get_chatbot', lambda: None)
    monkeypatch.setattr(server, 'prepare_reranker_startup', Mock(side_effect=RuntimeError('warmup failed')))
    entered = []
    async def run():
        async with server._lifespan(SimpleNamespace(state=SimpleNamespace())):
            entered.append(True)
    with pytest.raises(RuntimeError, match='warmup failed'):
        asyncio.run(run())
    assert not entered
