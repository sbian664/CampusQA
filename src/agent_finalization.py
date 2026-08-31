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


def sanitize_tool_protocol_text(content: str | None) -> str:
    """Remove leaked tool-protocol markup while retaining any natural-language prefix."""
    text = html.unescape(str(content or "")).strip()
    if not text:
        return ""

    markers = [
        match.start()
        for pattern in (r"<\|", r"\bDSML\b", r"\btoolcalls?\b", r"\binvoke\b")
        for match in [re.search(pattern, text, flags=re.IGNORECASE)]
        if match is not None
    ]
    if markers:
        text = text[: min(markers)].rstrip()
    return text


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
