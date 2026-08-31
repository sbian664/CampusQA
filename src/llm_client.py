"""
LLM 客户端 - 支持多个提供商（含 Tool Calling）
"""
import json
import time
import requests
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from config import (
    LLM_PROVIDER,
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_BASE,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT,
    DEEPSEEK_MAX_RETRIES,
    DEEPSEEK_RETRY_BACKOFF_SECONDS,
    LOCAL_MODEL_BASE,
    LOCAL_MODEL_NAME,
    MAX_TOKENS,
    TEMPERATURE,
)


@dataclass
class LLMResponse:
    """LLM 结构化响应（兼容 tool calling）"""
    content: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    usage: Optional[Dict] = None          # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
    finish_reason: str = "stop"           # "stop" | "tool_calls" | "error" | "length"


class LLMClient:
    """LLM 客户端基类"""

    def __init__(self, provider: str = None):
        self.provider = provider or LLM_PROVIDER

    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """发送消息到 LLM，返回响应文本"""
        raise NotImplementedError

    def send_message_with_tools(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None, **kwargs
    ) -> LLMResponse:
        """发送消息到 LLM（支持 tool calling），返回结构化响应"""
        raise NotImplementedError


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible chat completions client."""

    def __init__(self, provider: str = "custom", config: Optional[Dict] = None):
        super().__init__(provider)
        settings = config or {
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_API_BASE,
            "model": DEEPSEEK_MODEL,
        }
        self.api_key = str(settings.get("api_key", "")).strip()
        self.base_url = str(settings.get("base_url", "")).strip().rstrip("/")
        self.model = str(settings.get("model", "")).strip()

        if self.provider != "local" and not self.api_key:
            raise ValueError("API Key 未设置，请在设置页中配置")

    @staticmethod
    def _is_retryable_error(error: requests.exceptions.RequestException) -> bool:
        if isinstance(
            error,
            (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.SSLError,
            ),
        ):
            return True

        if isinstance(error, requests.exceptions.HTTPError):
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
            return status_code == 429 or (status_code is not None and status_code >= 500)

        return False

    def _build_payload(self, messages: List[Dict], tools: Optional[List[Dict]] = None,
                       **kwargs) -> Dict:
        """构建 API 请求 payload"""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "temperature": kwargs.get("temperature", TEMPERATURE),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        return payload

    def _call_api(self, payload: Dict) -> Dict:
        """调用 API 并返回响应 JSON"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempts = max(1, DEEPSEEK_MAX_RETRIES)
        last_error = None

        for attempt in range(attempts):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=DEEPSEEK_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as error:
                last_error = error
                if attempt == attempts - 1 or not self._is_retryable_error(error):
                    raise

                delay = DEEPSEEK_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                print(f"⚠️ {self.provider} API 瞬时错误，{delay:.1f}s 后重试 ({attempt + 1}/{attempts}): {error}")
                time.sleep(delay)

        raise last_error

    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """调用 DeepSeek API（纯文本模式）"""
        try:
            payload = self._build_payload(messages, **kwargs)
            data = self._call_api(payload)
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            raise Exception(f"{self.provider} API 调用失败: {str(e)}")

    def send_message_with_tools(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None, **kwargs
    ) -> LLMResponse:
        """调用 DeepSeek API（支持 tool calling），返回结构化 LLMResponse"""
        try:
            payload = self._build_payload(messages, tools, **kwargs)
            data = self._call_api(payload)

            choice = data["choices"][0]
            message = choice.get("message", {})
            finish = choice.get("finish_reason", "stop")
            usage = data.get("usage", {})

            content = message.get("content")
            raw_tool_calls = message.get("tool_calls")

            # 规范化 tool_calls
            tool_calls = None
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    arguments = func.get("arguments", "{}")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            pass
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": func.get("name", ""),
                            "arguments": arguments,
                        },
                    })

            if finish == "tool_calls" and tool_calls:
                mapped_reason = "tool_calls"
            elif finish == "length":
                mapped_reason = "length"
            elif finish == "stop":
                mapped_reason = "stop"
            else:
                mapped_reason = finish

            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=mapped_reason,
            )

        except requests.exceptions.RequestException as e:
            return LLMResponse(
                content=f"[API_ERROR] {self.provider} API 调用失败: {str(e)}",
                finish_reason="error",
            )
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            return LLMResponse(
                content=f"[API_ERROR] 响应解析失败: {str(e)}",
                finish_reason="error",
            )


class DeepSeekClient(OpenAICompatibleClient):
    """DeepSeek API 客户端，保留旧的默认入口。"""

    def __init__(self, config: Optional[Dict] = None):
        super().__init__("deepseek", config)


class LocalModelClient(LLMClient):
    """本地模型客户端（预留接口）"""

    def __init__(self, config: Optional[Dict] = None):
        super().__init__("local")
        settings = config or {}
        self.base_url = str(settings.get("base_url", LOCAL_MODEL_BASE)).strip().rstrip("/")
        self.model = str(settings.get("model", LOCAL_MODEL_NAME)).strip()

    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """调用本地模型 API"""
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "temperature": kwargs.get("temperature", TEMPERATURE),
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()

            data = response.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            raise Exception(f"本地模型 API 调用失败: {str(e)}")

    def send_message_with_tools(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None, **kwargs
    ) -> LLMResponse:
        """本地模型暂不支持 tool calling，降级为纯文本"""
        try:
            text = self.send_message(messages, **kwargs)
            return LLMResponse(content=text, finish_reason="stop")
        except Exception as e:
            return LLMResponse(
                content=f"[API_ERROR] 本地模型调用失败: {str(e)}",
                finish_reason="error",
            )


def create_llm_client(provider: str = None, config: Optional[Dict] = None) -> LLMClient:
    """工厂函数：根据提供商创建 LLM 客户端"""
    provider = (config or {}).get("provider") or provider or LLM_PROVIDER
    provider = provider.lower()

    if provider == "deepseek":
        return DeepSeekClient(config)
    elif provider in {"openai", "qwen", "zhipu", "custom"}:
        return OpenAICompatibleClient(provider, config)
    elif provider == "local":
        return LocalModelClient(config)
    else:
        raise ValueError(f"未支持的 LLM 提供商: {provider}")
