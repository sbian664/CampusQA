"""Request-scoped CrossEncoder reranking with lazy model loading."""

from __future__ import annotations

import hashlib
import threading
import weakref
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple, Union

from config import (
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


_model = None
_model_lock = threading.Lock()


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

    @staticmethod
    def key(query: str, content: str) -> tuple:
        return (hashlib.sha256(query.encode("utf-8")).digest(),
                hashlib.sha256(content.encode("utf-8")).digest())

    def _check_model(self, model) -> None:
        active = self._model_ref() if self._model_ref else self._strong_model
        if active is model:
            return
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
    return ensure_local_huggingface_model(
        model_name=RERANKER_MODEL,
        model_path=RERANKER_MODEL_PATH,
        required_files=("config.json", ("model.safetensors", "pytorch_model.bin")),
        auto_download=auto_download,
        label="reranker",
    )


def get_reranker_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            model_path = ensure_local_reranker_model()
            print(f"[reranker] load local model: {model_path}")
            _model = CrossEncoder(
                model_path,
                max_length=RERANKER_MAX_LENGTH,
                local_files_only=True,
            )
            print("[reranker] model ready")
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

    # Only score missing (query, document text) pairs. The initial candidate
    # pass and a subsequent BM25 rescue thus share scores, as do later tool
    # calls in the same process with the same effective rerank query.
    pairs = [(query, result.get("content", "")) for result in results]
    keys = [_score_cache.key(q, content) for q, content in pairs]
    scores = [None] * len(keys)
    missing = OrderedDict()  # Key -> [indices], deduplicates identical chunks.
    for index, key in enumerate(keys):
        cached_score = _score_cache.get(model, key)
        if cached_score is not None:
            scores[index] = cached_score
        else:
            missing.setdefault(key, []).append(index)
    if missing:
        missing_keys = list(missing)
        missing_pairs = [pairs[missing[key][0]] for key in missing_keys]
        fresh_scores = model.predict(
            missing_pairs,
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )
        if len(fresh_scores) != len(missing_keys):
            raise ValueError("Reranker returned an unexpected number of scores")
        fresh = dict(zip(missing_keys, map(float, fresh_scores)))
        _score_cache.put_many(model, fresh)
        for key, indices in missing.items():
            for index in indices:
                scores[index] = fresh[key]
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
    if not enabled or not RERANKER_AVAILABLE:
        return results[:requested_k]

    try:
        return rerank_results(query, results, model_loader(), requested_k)
    except Exception as exc:
        print(f"鈿狅笍  閲嶆帓澶辫触锛屽洖閫€娣峰悎妫€绱㈡帓搴忥細{type(exc).__name__}: {exc}")
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
    if not use_reranker:
        ranked = results[:requested_k]
        return (ranked, results) if return_candidates else ranked

    try:
        if results and on_rerank:
            on_rerank(str(rerank_query or query))
        effective_rerank_query = str(rerank_query or "").strip() or query
        ranked = rerank_results(effective_rerank_query, results, model_loader(), requested_k)
        return (ranked, results) if return_candidates else ranked
    except Exception as exc:
        print(f"⚠️  重排失败，回退混合检索排序: {type(exc).__name__}: {exc}")
        ranked = results[:requested_k]
        return (ranked, results) if return_candidates else ranked
