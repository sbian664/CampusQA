"""Request-scoped CrossEncoder reranking with lazy model loading."""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from config import (
    RERANKER_AVAILABLE,
    RERANKER_BATCH_SIZE,
    RERANKER_CANDIDATE_K,
    RERANKER_MAX_LENGTH,
    RERANKER_MODEL,
    RERANKER_MODEL_PATH,
    RERANKER_AUTO_DOWNLOAD,
)
from src.model_utils import ensure_local_huggingface_model


_model = None
_model_lock = threading.Lock()


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

    pairs = [(query, result.get("content", "")) for result in results]
    scores = model.predict(
        pairs,
        batch_size=RERANKER_BATCH_SIZE,
        show_progress_bar=False,
    )
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


def search_with_optional_rerank(
    knowledge_base,
    query: str,
    top_k: int,
    enabled: bool,
    filters: Optional[Dict] = None,
    candidate_k: int = RERANKER_CANDIDATE_K,
    model_loader: Callable = get_reranker_model,
) -> List[Dict]:
    use_reranker = bool(enabled and RERANKER_AVAILABLE)
    requested_k = max(1, int(top_k))
    search_k = max(requested_k, int(candidate_k)) if use_reranker else requested_k
    results = knowledge_base.hybrid_search(query, top_k=search_k, filters=filters)
    if not use_reranker:
        return results[:requested_k]

    try:
        return rerank_results(query, results, model_loader(), requested_k)
    except Exception as exc:
        print(f"⚠️  重排失败，回退混合检索排序: {type(exc).__name__}: {exc}")
        return results[:requested_k]
