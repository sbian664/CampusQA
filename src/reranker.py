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
)


_model = None
_model_lock = threading.Lock()


def get_reranker_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            print(f"📥 加载重排模型: {RERANKER_MODEL}")
            _model = CrossEncoder(RERANKER_MODEL, max_length=RERANKER_MAX_LENGTH)
            print("✓ 重排模型已就绪")
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

    # One strongest chunk per source keeps the final context diverse.
    unique = []
    seen_sources = set()
    for item in scored:
        source = item.get("source", "")
        if source in seen_sources:
            continue
        seen_sources.add(source)
        unique.append(item)
        if len(unique) >= top_k:
            break
    return unique


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
