"""Read-only admin projections over the existing CampusQA runtime objects."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AdminService:
    def __init__(
        self,
        *,
        health_provider: Callable[[], Dict[str, Any]],
        kb_provider: Callable[[], Dict[str, Any]],
        llm_provider: Callable[[], Dict[str, Any]],
        mode_provider: Callable[[], Dict[str, Any]],
        sessions_provider: Callable[[int, int, Optional[str]], list[Dict[str, Any]]],
        session_detail_provider: Optional[Callable[[str], Dict[str, Any]]] = None,
        activity_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self.health_provider = health_provider
        self.kb_provider = kb_provider
        self.llm_provider = llm_provider
        self.mode_provider = mode_provider
        self.sessions_provider = sessions_provider
        self.session_detail_provider = session_detail_provider
        self.activity_provider = activity_provider

    def overview(self, *, session_limit: int = 10) -> Dict[str, Any]:
        health = self.health_provider() or {}
        kb = self.kb_provider() or {}
        llm = self.llm_provider() or {}
        mode = self.mode_provider() or {}
        sessions = self.sessions_provider(max(1, min(session_limit, 50)), 0, None)
        healthy = health.get("status") == "ok"
        activity = self.activity_provider() if self.activity_provider else {
            "today_sessions": None,
            "today_questions": None,
            "today_tokens": None,
            "recent_errors": [],
            "recent_kb_operations": [],
        }
        return {
            "service": {
                "status": "healthy" if healthy else "degraded",
                "checked_at": health.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            },
            "llm": {
                "provider": llm.get("provider") or "未设置",
                "model": llm.get("model") or "未设置",
                "has_api_key": bool(llm.get("api_key") or llm.get("has_api_key")),
            },
            "mode": {
                "agent_mode": bool(mode.get("agent_mode")),
                "mode_name": mode.get("mode_name") or "未知",
                "reranker_enabled": mode.get("reranker_enabled"),
                "context_router_enabled": mode.get("context_router_enabled"),
            },
            "knowledge_base": {
                "total_files": _number(kb.get("total_files")),
                "total_chunks": _number(kb.get("total_chunks")),
                "total_size_mb": float(kb.get("total_size_mb") or 0),
                "freshness": kb.get("updated_at") or "未采集",
                "store_type": kb.get("store_type") or "未知",
            },
            "sessions": {
                "items": sessions,
                "limit": max(1, min(session_limit, 50)),
            },
            "activity": activity,
        }

    def sessions(self, *, limit: int = 20, offset: int = 0, query: Optional[str] = None) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        items = self.sessions_provider(safe_limit, safe_offset, query.strip() if query else None)
        return {"items": items, "limit": safe_limit, "offset": safe_offset}

    def session_detail(self, session_id: str) -> Dict[str, Any]:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("非法会话 ID")
        if self.session_detail_provider is None:
            raise LookupError("会话详情服务未配置")
        return self.session_detail_provider(session_id)

    @staticmethod
    def trace_summary(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        entries = list(entries)
        structured = [entry for entry in entries if isinstance(entry.get("hits"), list)]
        return {
            "count": len(entries),
            "structured_entries": len(structured),
            "has_legacy_entries": len(structured) != len(entries),
        }

    @staticmethod
    def search_response(*, query: str, channels: Dict[str, list[Dict[str, Any]]], duration_ms: int) -> Dict[str, Any]:
        serialized = {
            name: [AdminService._serialize_hit(hit) for hit in hits]
            for name, hits in channels.items()
        }
        return {
            "query": query,
            "duration_ms": max(0, int(duration_ms)),
            "channels": serialized,
            "counts": {name: len(items) for name, items in serialized.items()},
        }

    @staticmethod
    def _serialize_hit(result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source": result.get("source", "unknown"),
            "title": result.get("title", ""),
            "doc_type": result.get("doc_type", "unknown"),
            "chunk_index": result.get("chunk_index", 0),
            "content_snippet": str(result.get("content", ""))[:1200],
            "score": result.get("score"),
            "bm25_score": result.get("bm25_score"),
            "rerank_score": result.get("rerank_score"),
            "rerank_rank": result.get("rerank_rank"),
        }


def list_session_summaries(session_class: Any, *, limit: int, offset: int, query: Optional[str]) -> list[Dict[str, Any]]:
    query_lower = query.lower() if query else None
    matches = []
    for filename in sorted(session_class.list_saved_sessions(), reverse=True):
        session_id = filename[:-5] if filename.endswith(".json") else filename
        session = session_class(session_id=session_id)
        try:
            if not session.load():
                continue
        except Exception:
            continue
        title = session.metadata.get("title") or session.metadata.get("summary") or "未命名会话"
        if query_lower and query_lower not in f"{session_id} {title}".lower():
            continue
        matches.append({
            "session_id": session_id,
            "title": title,
            "message_count": _number(session.metadata.get("message_count"), len(session.messages)),
            "created_at": session.metadata.get("created_at", ""),
            "updated_at": session.metadata.get("updated_at", ""),
            "trace": AdminService.trace_summary(session.tool_call_log),
        })
    return matches[offset:offset + limit]


def load_session_detail(session_class: Any, session_id: str) -> Dict[str, Any]:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("非法会话 ID")
    session = session_class(session_id=session_id)
    if not session.load():
        raise FileNotFoundError("会话不存在")
    traces = session.get_tool_call_log()
    return {
        "session_id": session.session_id,
        "title": session.metadata.get("title") or session.metadata.get("summary"),
        "message_count": _number(session.metadata.get("message_count"), len(session.messages)),
        "created_at": session.metadata.get("created_at", ""),
        "updated_at": session.metadata.get("updated_at", ""),
        "history": session.get_history(strip_tool_details=True),
        "tool_call_log": traces,
        "trace": AdminService.trace_summary(traces),
        "usage": {
            "prompt_tokens": _number(session.total_prompt_tokens),
            "completion_tokens": _number(session.total_completion_tokens),
            "total_tokens": _number(session.total_prompt_tokens) + _number(session.total_completion_tokens),
        },
    }
