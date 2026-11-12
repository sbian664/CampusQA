"""Request-scoped CrossEncoder reranking with lazy model loading."""

from __future__ import annotations

import hashlib
import math
import time
import threading
import weakref
from pathlib import Path
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple, Union

from config import (
    RERANKER_BACKEND, RERANKER_DEVICE, RERANKER_ONNX_FILE,
    RERANKER_MAX_CONCURRENT, RERANKER_MAX_QUEUE, RERANKER_QUEUE_TIMEOUT_SECONDS,
    RERANKER_LOAD_RETRY_SECONDS, INFERENCE_CPU_THREADS,
    RERANKER_AVAILABLE,
    RERANKER_BATCH_SIZE,
    RERANKER_SCORE_CACHE_SIZE,
    RERANKER_CANDIDATE_K,
    RERANKER_MAX_LENGTH,
    RERANKER_MODEL,
    RERANKER_MODEL_PATH,
    RERANKER_AUTO_DOWNLOAD,
)
from src.model_utils import ensure_local_huggingface_model
from src.reranker_backend import load_backend, validate_onnx_artifact
from src.inference_runtime import (InferenceGate, RerankerBusy, RerankMetrics, current_rerank_metrics)


_model = None
_model_lock = threading.Lock()
_load_retry_at = 0.0
_load_failed = False
_inference_gate = InferenceGate(RERANKER_MAX_CONCURRENT, RERANKER_MAX_QUEUE, RERANKER_QUEUE_TIMEOUT_SECONDS)


class RerankScoreCache:
    """Bounded, in-process score cache; no raw queries or document text are retained.

    Scores depend on the actual CrossEncoder input pair, not on the retrieval
    query, user/session, candidate rank, or result metadata. Model replacement
    invalidates the cache. A lock protects LRU state, not model inference.
    """

    def __init__(self, max_entries: int = RERANKER_SCORE_CACHE_SIZE):
        self.max_entries = max(0, int(max_entries))
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self._model_ref = None
        self._strong_model = None
        self._settings = None

    @staticmethod
    def key(query: str, content: str) -> tuple:
        return (hashlib.sha256(query.encode("utf-8")).digest(),
                hashlib.sha256(content.encode("utf-8")).digest())

    def _check_model(self, model) -> None:
        active = self._model_ref() if self._model_ref else self._strong_model
        settings = (getattr(model, "max_length", RERANKER_MAX_LENGTH),
                    getattr(model, "_campusqa_backend", "torch"),
                    getattr(model, "_campusqa_artifact", ""))
        if active is model and settings == self._settings:
            return
        self._settings = settings
        self._entries.clear()
        self._strong_model = None
        try:
            self._model_ref = weakref.ref(model)
        except TypeError:
            # Rare for CrossEncoder; retain identity for non-weakrefable stubs.
            self._model_ref = None
            self._strong_model = model

    def get(self, model, key: tuple) -> Optional[float]:
        if not self.max_entries:
            return None
        with self._lock:
            self._check_model(model)
            if key not in self._entries:
                return None
            self._entries.move_to_end(key)
            return self._entries[key]

    def put_many(self, model, values: Dict[tuple, float]) -> None:
        if not self.max_entries:
            return
        with self._lock:
            self._check_model(model)
            for key, score in values.items():
                self._entries[key] = score
                self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._model_ref = None
            self._strong_model = None


_score_cache = RerankScoreCache()


def ensure_local_reranker_model(auto_download: bool = RERANKER_AUTO_DOWNLOAD) -> str:
    if RERANKER_BACKEND == "onnx":
        validate_onnx_artifact(RERANKER_MODEL_PATH, RERANKER_ONNX_FILE)
        return str(Path(RERANKER_MODEL_PATH).resolve())
    if RERANKER_BACKEND != "torch":
        raise ValueError("RERANKER_BACKEND must be torch or onnx")
    return ensure_local_huggingface_model(
        model_name=RERANKER_MODEL,
        model_path=RERANKER_MODEL_PATH,
        required_files=("config.json", ("model.safetensors", "pytorch_model.bin")),
        auto_download=auto_download,
        label="reranker",
    )


def get_reranker_model():
    global _model, _load_retry_at, _load_failed
    if _model is not None:
        return _model
    metrics = current_rerank_metrics()
    wait_started = time.perf_counter()
    with _model_lock:
        if metrics is not None:
            metrics.model_wait_ms += (time.perf_counter() - wait_started) * 1000
        if _model is not None:
            return _model
        if _load_failed and time.monotonic() < _load_retry_at:
            raise RuntimeError("Reranker load is in retry cooldown; inspect the initial load error")
        started = time.perf_counter()
        try:
            model_path = ensure_local_reranker_model()
            _model = load_backend(
                model_path, backend=RERANKER_BACKEND, file_name=RERANKER_ONNX_FILE,
                device=RERANKER_DEVICE, max_length=RERANKER_MAX_LENGTH,
                threads=INFERENCE_CPU_THREADS,
            )
            _load_failed = False
            print(f"[reranker] ready: backend={RERANKER_BACKEND}, device={RERANKER_DEVICE}, "
                  f"threads={INFERENCE_CPU_THREADS}, artifact={_model._campusqa_artifact}")
        except Exception:
            _load_failed = True
            _load_retry_at = time.monotonic() + RERANKER_LOAD_RETRY_SECONDS
            raise
        finally:
            if metrics is not None:
                metrics.model_load_ms += (time.perf_counter() - started) * 1000
    return _model


def is_reranker_loaded() -> bool:
    return _model is not None


def rerank_results(
    query: str,
    results: List[Dict],
    model,
    top_k: int,
) -> List[Dict]:
    if not results or top_k <= 0:
        return []

    metrics = current_rerank_metrics() or RerankMetrics()
    metrics.calls += 1
    metrics.backend = getattr(model, "_campusqa_backend", RERANKER_BACKEND)
    started = time.perf_counter()
    pairs = [(query, result.get("content", "")) for result in results]
    keys = [_score_cache.key(q, content) for q, content in pairs]
    positions = OrderedDict()
    for index, key in enumerate(keys):
        positions.setdefault(key, []).append(index)
    metrics.pairs += len(pairs)
    metrics.unique_pairs += len(positions)
    metrics.deduplicated_pairs += len(pairs) - len(positions)
    resolved = {}
    try:
        for key in positions:
            cached = _score_cache.get(model, key)
            if cached is not None:
                resolved[key] = cached
                metrics.cache_hits += 1
        if len(resolved) < len(positions):
            with _inference_gate.slot(metrics):
                # Another request may have filled these scores while we waited.
                # Default concurrency=1 prevents overlapping requests rescoring
                # the same uncached pair. Pure cache hits never enter the gate.
                missing = []
                for key in positions:
                    if key in resolved:
                        continue
                    cached = _score_cache.get(model, key)
                    if cached is None:
                        missing.append(key)
                    else:
                        resolved[key] = cached
                        metrics.cache_hits += 1
                        metrics.cache_hits_after_wait += 1
                if missing:
                    missing_pairs = [pairs[positions[key][0]] for key in missing]
                    metrics.predict_calls += 1
                    metrics.predicted_pairs += len(missing_pairs)
                    prediction_started = time.perf_counter()
                    try:
                        values = model.predict(missing_pairs, batch_size=RERANKER_BATCH_SIZE,
                                               show_progress_bar=False)
                    finally:
                        metrics.predict_ms += (time.perf_counter() - prediction_started) * 1000
                    if len(values) != len(missing):
                        raise ValueError("Reranker returned an unexpected number of scores")
                    fresh = dict(zip(missing, map(float, values)))
                    if not all(math.isfinite(score) for score in fresh.values()):
                        raise ValueError("Reranker returned non-finite scores")
                    _score_cache.put_many(model, fresh)
                    resolved.update(fresh)
    finally:
        metrics.rerank_ms += (time.perf_counter() - started) * 1000
    scores = [resolved[key] for key in keys]
    scored = []
    for original_rank, (result, rerank_score) in enumerate(zip(results, scores), start=1):
        item = dict(result)
        item["original_rank"] = original_rank
        item["rerank_score"] = float(rerank_score)
        scored.append(item)
    scored.sort(key=lambda item: item["rerank_score"], reverse=True)
    for rerank_rank, item in enumerate(scored, start=1):
        item["rerank_rank"] = rerank_rank

    # Keep multiple strong chunks from one source when they form a contiguous
    # passage. Context assembly is responsible for diversity after expansion.
    return scored[:top_k]


def rerank_precomputed_results(
    query: str,
    results: List[Dict],
    top_k: int,
    enabled: bool,
    model_loader: Callable = get_reranker_model,
) -> List[Dict]:
    """Rerank an already retrieved result list while preserving fallback behavior."""
    requested_k = max(1, int(top_k))
    if not results or not enabled or not RERANKER_AVAILABLE:
        return results[:requested_k]

    try:
        return rerank_results(query, results, model_loader(), requested_k)
    except Exception as exc:
        metrics = current_rerank_metrics()
        if metrics is not None:
            metrics.fallback_reason = exc.reason if isinstance(exc, RerankerBusy) else type(exc).__name__
        print(f"重排失败，回退已有排序: {type(exc).__name__}: {exc}")
        return results[:requested_k]


def search_with_optional_rerank(
    knowledge_base,
    query: str,
    top_k: int,
    enabled: bool,
    filters: Optional[Dict] = None,
    candidate_k: int = RERANKER_CANDIDATE_K,
    model_loader: Callable = get_reranker_model,
    rerank_query: Optional[str] = None,
    return_candidates: bool = False,
    bm25_weight: Optional[float] = None,
    on_rerank: Optional[Callable] = None,
) -> Union[List[Dict], Tuple[List[Dict], List[Dict]]]:
    """Retrieve with ``query`` and optionally rerank with another query.

    When ``return_candidates`` is true, return ``(ranked_results, candidates)``
    so a later rescue pass can compete against the full reranker candidate pool.
    """
    use_reranker = bool(enabled and RERANKER_AVAILABLE)
    requested_k = max(1, int(top_k))
    search_k = max(requested_k, int(candidate_k)) if use_reranker else requested_k
    weight = {} if bm25_weight is None else {"bm25_weight": bm25_weight}
    results = knowledge_base.hybrid_search(query, top_k=search_k, filters=filters, **weight)
    if not results or not use_reranker:
        ranked = results[:requested_k]
        return (ranked, results) if return_candidates else ranked

    try:
        if results and on_rerank:
            on_rerank(str(rerank_query or query))
        effective_rerank_query = str(rerank_query or "").strip() or query
        ranked = rerank_results(effective_rerank_query, results, model_loader(), requested_k)
        return (ranked, results) if return_candidates else ranked
    except Exception as exc:
        metrics = current_rerank_metrics()
        if metrics is not None:
            metrics.fallback_reason = exc.reason if isinstance(exc, RerankerBusy) else type(exc).__name__
        print(f"⚠️  重排失败，回退混合检索排序: {type(exc).__name__}: {exc}")
        ranked = results[:requested_k]
        return (ranked, results) if return_candidates else ranked
