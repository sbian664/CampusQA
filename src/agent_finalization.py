"""Helpers for producing a clean final answer after Agent retrieval."""

from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Dict, List


FINAL_ANSWER_INSTRUCTION = (
    "检索预算已经用完。现在禁止调用任何工具，也不要输出 DSML、XML 或其他工具调用标记。"
    "请只根据已有检索结果直接回答用户；如果证据不足，请明确说明信息不足。"
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
_PIPE_PROTOCOL_RE = re.compile(r"<\|.*?\|>", flags=re.DOTALL)


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
