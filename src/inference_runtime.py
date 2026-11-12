"""CPU inference controls. Limits are per process; run one web worker on 2c2g."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Optional

from config import INFERENCE_CPU_THREADS, INFERENCE_INTEROP_THREADS

_log = logging.getLogger(__name__)
_torch_lock = threading.Lock()
_torch_configured = False


def configure_torch_threads() -> dict:
    """Configure once, before embedding or reranker work (PyTorch is process-wide)."""
    global _torch_configured
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", str(INFERENCE_CPU_THREADS))
    os.environ.setdefault("MKL_NUM_THREADS", str(INFERENCE_CPU_THREADS))
    import torch

    with _torch_lock:
        if not _torch_configured:
            torch.set_num_threads(INFERENCE_CPU_THREADS)
            try:
                torch.set_num_interop_threads(INFERENCE_INTEROP_THREADS)
            except RuntimeError:
                # Another component may already have started parallel work.
                # Never pretend this immutable setting was successfully changed.
                _log.warning("PyTorch inter-op threads already initialized; actual=%s requested=%s",
                             torch.get_num_interop_threads(), INFERENCE_INTEROP_THREADS)
            _torch_configured = True
    return {"torch_threads": torch.get_num_threads(),
            "torch_interop_threads": torch.get_num_interop_threads()}


@dataclass
class RerankMetrics:
    calls: int = 0
    pairs: int = 0
    unique_pairs: int = 0
    cache_hits: int = 0
    cache_hits_after_wait: int = 0
    deduplicated_pairs: int = 0
    predicted_pairs: int = 0
    predict_calls: int = 0
    queue_ms: float = 0.0
    predict_ms: float = 0.0  # Tokenization + forward pass; not GPU kernel-only time.
    model_load_ms: float = 0.0
    model_wait_ms: float = 0.0
    rerank_ms: float = 0.0
    backend: str = ""
    fallback_reason: str = ""

    def to_dict(self) -> dict:
        return {key: round(value, 3) if isinstance(value, float) else value
                for key, value in asdict(self).items()}


_metrics: ContextVar[Optional[RerankMetrics]] = ContextVar("rerank_metrics", default=None)


def current_rerank_metrics() -> Optional[RerankMetrics]:
    return _metrics.get()


@contextmanager
def collect_rerank_metrics():
    """Isolate concurrent turns; aggregate primary and BM25-rescue passes."""
    value = RerankMetrics()
    token = _metrics.set(value)
    try:
        yield value
    finally:
        _metrics.reset(token)


class RerankerBusy(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class InferenceGate:
    """Bound both executing and waiting requests, without spawning worker pools.

    A timeout stops waiting only; it cannot interrupt an active native inference.
    Cache hits bypass this gate. With concurrency=1, rechecking the score cache
    after admission coalesces overlapping misses without duplicate inference.
    """
    def __init__(self, concurrency: int = 1, max_queue: int = 8, timeout: float = 30.0):
        if concurrency < 1 or max_queue < 0 or timeout < 0:
            raise ValueError("Invalid reranker concurrency/queue configuration")
        self.concurrency = concurrency
        self.max_queue = max_queue
        self.timeout = timeout
        self._capacity = threading.BoundedSemaphore(concurrency + max_queue)
        self._running = threading.BoundedSemaphore(concurrency)

    @contextmanager
    def slot(self, metrics: RerankMetrics):
        if not self._capacity.acquire(blocking=False):
            raise RerankerBusy("queue_full")
        admitted = False
        started = time.perf_counter()
        try:
            admitted = self._running.acquire(timeout=self.timeout)
            metrics.queue_ms += (time.perf_counter() - started) * 1000
            if not admitted:
                raise RerankerBusy("queue_timeout")
            yield
        finally:
            if admitted:
                self._running.release()
            self._capacity.release()
