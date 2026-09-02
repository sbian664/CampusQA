"""Runtime configuration facade with explicit admin-over-environment precedence."""

from __future__ import annotations

from typing import Any, Callable, Dict

from config import (
    AGENT_MODE_ENABLED,
    BM25_WEIGHT,
    CONTEXT_ROUTER_ENABLED,
    MAX_TOKENS,
    RAG_TOP_K,
    RERANKER_AVAILABLE,
)
from src.llm_client import create_llm_client


class AdminConfigService:
    def __init__(self, *, llm_store: Any, control_store: Any, mode_provider: Callable[[], Dict[str, Any]], mode_toggle: Callable[[], Dict[str, Any]]) -> None:
        self.llm_store = llm_store
        self.control_store = control_store
        self.mode_provider = mode_provider
        self.mode_toggle_callback = mode_toggle

    def public_llm(self) -> Dict[str, Any]:
        return self.llm_store.public_config()

    def update_llm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.llm_store.update(payload)
        return self.public_llm()

    def test_llm(self, payload: Dict[str, Any]) -> Dict[str, str]:
        candidate = self.llm_store.resolve(payload)
        client = create_llm_client(config=candidate)
        client.send_message(
            [{"role": "system", "content": "只回复 OK。"}, {"role": "user", "content": "连接测试"}],
            max_tokens=8,
            temperature=0,
        )
        return {"status": "ok"}

    def runtime_config(self) -> Dict[str, Any]:
        mode = self.mode_provider() or {}
        defaults = {
            "agent_mode": bool(mode.get("agent_mode", AGENT_MODE_ENABLED)),
            "reranker_enabled": bool(RERANKER_AVAILABLE),
            "context_router_enabled": bool(CONTEXT_ROUTER_ENABLED),
            "top_k": int(RAG_TOP_K),
            "bm25_weight": float(BM25_WEIGHT),
            "max_tokens": int(MAX_TOKENS),
        }
        for key in defaults:
            value = self.control_store.get_setting(f"runtime.{key}")
            if value is not None:
                defaults[key] = value
        return {**defaults, "precedence": "admin_runtime > environment > defaults"}

    def update_runtime(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"agent_mode", "reranker_enabled", "context_router_enabled", "top_k", "bm25_weight", "max_tokens"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"不支持的运行配置: {', '.join(sorted(unknown))}")
        if "top_k" in payload and not 1 <= int(payload["top_k"]) <= 50:
            raise ValueError("top_k 必须在 1 到 50 之间")
        if "bm25_weight" in payload and not 0 <= float(payload["bm25_weight"]) <= 1:
            raise ValueError("BM25 权重必须在 0 到 1 之间")
        if "max_tokens" in payload and not 1 <= int(payload["max_tokens"]) <= 128000:
            raise ValueError("最大 Token 必须在 1 到 128000 之间")
        for key, value in payload.items():
            self.control_store.set_setting(f"runtime.{key}", value)
        return self.runtime_config()

    def toggle_mode(self) -> Dict[str, Any]:
        result = self.mode_toggle_callback()
        self.control_store.set_setting("runtime.agent_mode", bool(result.get("agent_mode")))
        return result
