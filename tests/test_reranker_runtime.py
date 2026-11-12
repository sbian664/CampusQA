"""Deterministic concurrency, backend and failure regressions; no model downloads."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading

import pytest
import src.reranker as reranker
from src.inference_runtime import InferenceGate, RerankMetrics, RerankerBusy, collect_rerank_metrics
from src.reranker_backend import validate_onnx_artifact, load_backend


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(reranker, "_score_cache", reranker.RerankScoreCache(128))
    monkeypatch.setattr(reranker, "_inference_gate", InferenceGate(1, 4, 2))
    monkeypatch.setattr(reranker, "RERANKER_AVAILABLE", True)


class Model:
    max_length = 512
    def __init__(self):
        self.calls = []
    def predict(self, pairs, **kwargs):
        self.calls.append(list(pairs))
        return [float(len(content)) for _, content in pairs]


def run(model, query="Q"):
    with collect_rerank_metrics() as metrics:
        ranked = reranker.rerank_results(query, [{"content": "abc"}], model, 1)
    return ranked, metrics.to_dict()


def test_overlapping_identical_requests_share_prediction_after_wait(monkeypatch):
    first_predicting, allow_completion, second_waiting = (threading.Event() for _ in range(3))
    class Gate(InferenceGate):
        @contextmanager
        def slot(self, metrics):
            if first_predicting.is_set():
                second_waiting.set()
            with super().slot(metrics):
                yield
    class BlockingModel(Model):
        def predict(self, pairs, **kwargs):
            first_predicting.set()
            assert allow_completion.wait(3)
            return super().predict(pairs, **kwargs)
    monkeypatch.setattr(reranker, "_inference_gate", Gate(1, 4, 2))
    model = BlockingModel()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(run, model)
        assert first_predicting.wait(2)
        second = pool.submit(run, model)
        assert second_waiting.wait(2)
        allow_completion.set()
        a, b = first.result(3), second.result(3)
    assert a[0] == b[0] and len(model.calls) == 1
    assert a[1]["predicted_pairs"] == 1
    assert b[1]["predicted_pairs"] == 0
    assert b[1]["cache_hits_after_wait"] == 1
    assert b[1]["queue_ms"] >= 0


def test_cache_hit_bypasses_occupied_gate(monkeypatch):
    gate = InferenceGate(1, 0, 0)
    monkeypatch.setattr(reranker, "_inference_gate", gate)
    model = Model()
    run(model)
    with gate.slot(RerankMetrics()):
        _, metrics = run(model)
    assert metrics["cache_hits"] == 1 and metrics["queue_ms"] == 0
    assert len(model.calls) == 1


@pytest.mark.parametrize("waiting, reason", [(0, "queue_full"), (1, "queue_timeout")])
def test_overload_falls_back_and_releases_admission(monkeypatch, waiting, reason):
    gate = InferenceGate(1, waiting, 0)
    monkeypatch.setattr(reranker, "_inference_gate", gate)
    model, results = Model(), [{"content": "abc"}]
    with gate.slot(RerankMetrics()), collect_rerank_metrics() as metrics:
        assert reranker.rerank_precomputed_results("Q", results, 1, True, lambda: model) == results
        assert metrics.fallback_reason == reason
    assert run(model)[1]["predicted_pairs"] == 1


def test_predict_exception_does_not_leak_gate_or_cache():
    model = Model()
    with patch.object(model, "predict", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            run(model)
    assert run(model)[1]["predicted_pairs"] == 1


@pytest.mark.parametrize("bad", [[float("nan")], [float("inf")], []])
def test_malformed_output_is_not_cached(bad):
    model = Model()
    with patch.object(model, "predict", return_value=bad):
        with pytest.raises(ValueError):
            run(model)
    assert run(model)[1]["predicted_pairs"] == 1


def test_max_length_change_invalidates_same_instance_cache():
    model = Model()
    run(model)
    model.max_length = 256
    assert run(model)[1]["predicted_pairs"] == 1
    assert len(model.calls) == 2


def test_metrics_aggregate_primary_and_rescue():
    model = Model()
    with collect_rerank_metrics() as metrics:
        reranker.rerank_results("Q", [{"content": "a"}, {"content": "b"}], model, 1)
        reranker.rerank_results("Q", [{"content": "a"}, {"content": "b"}, {"content": "new"}], model, 1)
    assert metrics.calls == 2 and metrics.pairs == 5
    assert metrics.cache_hits == 2 and metrics.predicted_pairs == 3
    assert metrics.predict_calls == 2


def test_empty_results_do_not_load_model():
    loader = Mock(side_effect=AssertionError("must not load"))
    assert reranker.rerank_precomputed_results("Q", [], 3, True, loader) == []
    kb = SimpleNamespace(hybrid_search=lambda *a, **kw: [])
    assert reranker.search_with_optional_rerank(kb, "Q", 3, True, model_loader=loader) == []
    loader.assert_not_called()


def test_model_load_failure_has_retry_cooldown(monkeypatch):
    for key, value in [("_model", None), ("_load_failed", False), ("_load_retry_at", 0),
                       ("RERANKER_LOAD_RETRY_SECONDS", 30)]:
        monkeypatch.setattr(reranker, key, value)
    with patch.object(reranker, "ensure_local_reranker_model", return_value="/model"), \
         patch.object(reranker, "load_backend", side_effect=RuntimeError("bad model")) as load:
        with pytest.raises(RuntimeError, match="bad model"):
            reranker.get_reranker_model()
        with pytest.raises(RuntimeError, match="cooldown"):
            reranker.get_reranker_model()
        assert load.call_count == 1


def test_onnx_requires_explicit_local_file_and_rejects_escape(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx/model.onnx").write_bytes(b"stub")
    assert validate_onnx_artifact(str(tmp_path), "onnx/model.onnx").is_file()
    for name in ("../model.onnx", "/tmp/model.onnx", "model.bin"):
        with pytest.raises(ValueError):
            validate_onnx_artifact(str(tmp_path), name)
    with pytest.raises(FileNotFoundError):
        validate_onnx_artifact(str(tmp_path), "onnx/missing.onnx")


def test_onnx_loading_never_exports_or_downloads(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.onnx").write_bytes(b"stub")
    constructor = Mock(return_value=SimpleNamespace())
    session = object()
    with patch.dict("sys.modules", {"sentence_transformers": SimpleNamespace(CrossEncoder=constructor)}), \
         patch("src.reranker_backend.configure_torch_threads"), \
         patch("src.reranker_backend.onnx_session_options", return_value=session):
        result = load_backend(str(tmp_path), backend="onnx", file_name="model.onnx",
                              device="cpu", max_length=512, threads=2)
    args = constructor.call_args.kwargs
    assert args["local_files_only"] is True
    assert args["model_kwargs"]["export"] is False
    assert args["model_kwargs"]["provider"] == "CPUExecutionProvider"
    assert args["model_kwargs"]["session_options"] is session
    assert result._campusqa_backend == "onnx"


def test_torch_thread_configuration_is_once_and_process_wide(monkeypatch):
    import src.inference_runtime as runtime
    fake = SimpleNamespace(set_num_threads=Mock(), set_num_interop_threads=Mock(),
                           get_num_threads=lambda: 2, get_num_interop_threads=lambda: 1)
    monkeypatch.setattr(runtime, "_torch_configured", False)
    with patch.dict("sys.modules", {"torch": fake}):
        assert runtime.configure_torch_threads()["torch_threads"] == 2
        runtime.configure_torch_threads()
    fake.set_num_threads.assert_called_once()
    fake.set_num_interop_threads.assert_called_once()


def test_late_interop_configuration_reports_actual_value(monkeypatch, caplog):
    import src.inference_runtime as runtime
    fake = SimpleNamespace(set_num_threads=Mock(),
                           set_num_interop_threads=Mock(side_effect=RuntimeError("already started")),
                           get_num_threads=lambda: 2, get_num_interop_threads=lambda: 8)
    monkeypatch.setattr(runtime, "_torch_configured", False)
    with patch.dict("sys.modules", {"torch": fake}):
        assert runtime.configure_torch_threads()["torch_interop_threads"] == 8
    assert "already initialized" in caplog.text
