"""Helpers for producing a clean final answer after Agent retrieval."""

from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Dict, List


FINAL_ANSWER_INSTRUCTION = (
    "检索阶段已结束。现在禁止调用任何工具，也不要输出 DSML、XML 或其他工具调用标记。"
    "请只根据已有检索结果直接回答用户；如果证据不足，请明确说明信息不足。"
    "目录清单和标题不能代替正文证据；文档总数不能当作满足正文条件的实体数。"
    "如果清单未枚举完、正文未读完、资料有排除项或结果已压缩，只报告已核验部分及覆盖限制，不得声称全部列出或给出完整统计。"
    "只输出面向用户的自然语言答案，不要描述内部检索过程。"
)

_TOOL_BLOCK_RE = re.compile(
    r"<(?:tool_call|tool_calls|function_call|invoke)\b[^>]*>"
    r".*?(?:</(?:tool_call|tool_calls|function_call|invoke)\s*>|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
_TOOL_TAG_RE = re.compile(
    r"</?(?:tool_call|tool_calls|function_call|invoke)\b[^>]*>",
    flags=re.IGNORECASE,
)
# Some compatible model endpoints emit the closing ``>`` without the final
# pipe, e.g. ``<| | DSML | | toolcalls>``.
_PIPE_PROTOCOL_RE = re.compile(r"<\|.*?(?:\|>|>)", flags=re.DOTALL)


class ProtocolSafeStream:
    """Release answer text incrementally while withholding partial protocol markers."""

    _MARKERS = ("<tool_call", "<tool_calls", "<function_call", "<invoke", "<|", "dsml")

    def __init__(self, on_delta):
        self.on_delta = on_delta
        self.raw = ""
        self.emitted = ""
        self.stopped = False

    def feed(self, chunk: str, *, final=False):
        self.raw += str(chunk or "")
        if self.stopped:
            return ""
        low = self.raw.lower()
        marker_positions = [low.find(marker) for marker in self._MARKERS if low.find(marker) >= 0]
        if marker_positions:
            boundary = min(marker_positions)
            # Natural language before a protocol block remains visible.
            candidate = sanitize_tool_protocol_text(self.raw[:boundary])
            self.stopped = True
        else:
            safe_end = len(self.raw)
            if not final:
                for marker in self._MARKERS:
                    for size in range(1, len(marker)):
                        if low.endswith(marker[:size]):
                            safe_end = min(safe_end, len(low) - size)
            candidate = sanitize_tool_protocol_text(self.raw[:safe_end])
            if final:
                candidate = sanitize_tool_protocol_text(self.raw)
        if not candidate.startswith(self.emitted):
            return ""
        delta = candidate[len(self.emitted):]
        self.emitted = candidate
        if delta:
            self.on_delta(delta)
        return delta


def stream_cleaned_answer(client, messages, on_delta, cancel_event, **kwargs):
    safe = ProtocolSafeStream(on_delta)
    client.stream_message(messages, lambda chunk: safe.feed(chunk), cancel_event, **kwargs)
    safe.feed("", final=True)
    return safe.emitted


def sanitize_tool_protocol_text(content: str | None) -> str:
    """Remove leaked tool-protocol markup while retaining any natural-language prefix."""
    text = html.unescape(str(content or "")).strip()
    if not text:
        return ""

    text = _TOOL_BLOCK_RE.sub("", text)
    text = _PIPE_PROTOCOL_RE.sub("", text)
    text = _TOOL_TAG_RE.sub("", text)

    # DSML is treated as a protocol marker; ordinary words such as
    # "invoke" in a natural-language answer remain intact.
    dsml_marker = re.search(r"\bDSML\b", text, flags=re.IGNORECASE)
    if dsml_marker:
        text = text[: dsml_marker.start()]
    return text.strip()


def build_final_answer_messages(messages: List[Dict]) -> List[Dict]:
    """Create a tool-free, protocol-free context for the final answer call."""
    cleaned: List[Dict] = []
    for message in messages:
        role = message.get("role")
        content = sanitize_tool_protocol_text(message.get("content"))

        if role == "system":
            cleaned.append({"role": "system", "content": content})
        elif role == "tool":
            if content:
                cleaned.append({
                    "role": "user",
                    "content": f"[知识库检索结果]\n{content}",
                })
        elif role in {"user", "assistant"}:
            if content:
                cleaned.append({"role": role, "content": content})

    cleaned.append({"role": "user", "content": FINAL_ANSWER_INSTRUCTION})
    return deepcopy(cleaned)
