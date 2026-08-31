"""Single-user, server-side LLM configuration storage."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from config import (
    DATA_DIR,
    DEEPSEEK_API_BASE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    LLM_PROVIDER,
    LOCAL_MODEL_BASE,
    LOCAL_MODEL_NAME,
    LLM_CONFIG_ALLOWED_HOSTS,
)


SUPPORTED_PROVIDERS = {"deepseek", "openai", "qwen", "zhipu", "custom", "local"}
DEFAULT_CONFIG_PATH = Path(DATA_DIR) / "user_llm_config.json"


def default_llm_config() -> Dict[str, str]:
    provider = (LLM_PROVIDER or "deepseek").strip().lower()
    if provider == "local":
        return {
            "provider": "local",
            "api_key": "",
            "model": LOCAL_MODEL_NAME,
            "base_url": LOCAL_MODEL_BASE,
        }
    return {
        "provider": provider if provider in SUPPORTED_PROVIDERS else "deepseek",
        "api_key": DEEPSEEK_API_KEY,
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_API_BASE,
    }


def mask_api_key(api_key: str) -> str:
    value = str(api_key or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def normalize_llm_config(
    raw: Dict[str, str],
    *,
    allow_missing_api_key: bool = False,
) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("模型配置必须是 JSON 对象")
    data = raw
    provider = str(data.get("provider", "")).strip().lower()
    api_key = str(data.get("api_key", "")).strip()
    model = str(data.get("model", "")).strip()
    base_url = str(data.get("base_url", "")).strip().rstrip("/")

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("不支持的模型供应商")
    if not model or len(model) > 200:
        raise ValueError("模型名称不能为空且不能超过 200 个字符")
    parsed = urlparse(base_url)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Base URL 端口无效")
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Base URL 必须是完整的 http(s) 地址")
    if parsed.username or parsed.password:
        raise ValueError("Base URL 不得包含用户名或密码")
    if provider == "local":
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("local 供应商只允许连接本机地址")
    else:
        if parsed.scheme != "https":
            raise ValueError("非 local 供应商必须使用 HTTPS")
        if host not in set(LLM_CONFIG_ALLOWED_HOSTS):
            raise ValueError("Base URL 域名不在允许列表中")
        if port not in {None, 443}:
            raise ValueError("HTTPS Base URL 只允许使用 443 端口")
    if provider != "local" and not api_key and not allow_missing_api_key:
        raise ValueError("API Key 不能为空")

    return {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
    }


class UserLLMConfigStore:
    """Persist one server-wide model configuration for the single-user MVP."""

    def __init__(self, path: Optional[Path] = None, default_config: Optional[Dict[str, str]] = None):
        self.path = Path(path or DEFAULT_CONFIG_PATH)
        self.default_config = normalize_llm_config(
            default_config or default_llm_config(),
            allow_missing_api_key=True,
        )

    def get(self) -> Dict[str, str]:
        if not self.path.exists():
            return dict(self.default_config)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return normalize_llm_config(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return dict(self.default_config)

    def save(self, raw: Dict[str, str]) -> Dict[str, str]:
        config = normalize_llm_config(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".user_llm_config.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            # chmod is portable across the deployment target (Linux) and the
            # local development environment (Windows); the file descriptor is
            # already closed before replacement, which also avoids Windows
            # file-sharing locks during cleanup.
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return config

    def update(self, payload: Dict[str, str]) -> Dict[str, str]:
        return self.save(self.resolve(payload))

    def resolve(
        self,
        payload: Dict[str, str],
        *,
        preserve_existing_key: bool = True,
    ) -> Dict[str, str]:
        current = self.get()
        incoming = dict(payload or {})
        submitted_key = str(incoming.get("api_key", "")).strip()
        if (
            preserve_existing_key
            and (not submitted_key or submitted_key == mask_api_key(current["api_key"]))
        ):
            incoming["api_key"] = current["api_key"]
        return normalize_llm_config({
            "provider": incoming.get("provider", current["provider"]),
            "api_key": incoming["api_key"],
            "model": incoming.get("model", current["model"]),
            "base_url": incoming.get("base_url", current["base_url"]),
        })

    def public_config(self) -> Dict[str, str | bool]:
        config = self.get()
        return {
            "provider": config["provider"],
            "api_key": mask_api_key(config["api_key"]),
            "has_api_key": bool(config["api_key"]),
            "model": config["model"],
            "base_url": config["base_url"],
        }
