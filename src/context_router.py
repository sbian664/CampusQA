"""多轮问答上下文路由与独立问题改写。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Protocol

from src.llm_client import LLMClient, create_llm_client


ROUTER_SYSTEM_PROMPT = """你是 CampusQA 的对话上下文路由器。
判断当前问题能否脱离历史独立理解，并在需要时把它改写成独立、完整的检索问题。

规则：
- standalone：当前问题自身已包含完整实体和意图；不得把历史实体带入。
- follow_up：当前问题包含指代、省略、纠正或承接，必须利用历史才能完整理解。
- 只使用历史消除歧义，不得添加历史和当前问题都没有的信息。
- 保留课程代码、人名、地点、时间和限定条件。

只输出 JSON：
{"route":"standalone|follow_up","rewritten_query":"完整问题","reason":"简短原因"}
"""


@dataclass(frozen=True)
class ContextRoute:
    route: str
    rewritten_query: str
    selected_history: List[Dict]
    reason: str


class ContextRouteModel(Protocol):
    def classify_and_rewrite(
        self, current_query: str, history: List[Dict]
    ) -> Dict:
        """返回 route、rewritten_query 和 reason。"""


class LLMContextRouteModel:
    """基于任意 LLMClient 的路由模型适配器。"""

    def __init__(self, client: LLMClient):
        self.client = client

    def classify_and_rewrite(
        self, current_query: str, history: List[Dict]
    ) -> Dict:
        payload = {
            "history": history,
            "current_query": current_query,
        }
        text = self.client.send_message(
            [
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]).strip()
        result = json.loads(cleaned)
        if not isinstance(result, dict):
            raise ValueError("路由模型未返回 JSON 对象")
        return result


class ContextRouter:
    """选择相关历史，并将追问改写为独立问题。"""

    def __init__(self, model: ContextRouteModel, history_exchanges: int = 2):
        self.model = model
        self.history_exchanges = max(1, history_exchanges)

    def route(self, current_query: str, history: List[Dict]) -> ContextRoute:
        if not history:
            return ContextRoute(
                route="standalone",
                rewritten_query=current_query,
                selected_history=[],
                reason="首轮问题无需继承上下文",
            )

        model_history = self._recent_history(history, self.history_exchanges)
        fallback_history = self._recent_history(history, 1)
        try:
            decision = self.model.classify_and_rewrite(current_query, model_history)
            route = decision.get("route")
            rewritten_query = str(decision.get("rewritten_query", "")).strip()
            reason = str(decision.get("reason", "")).strip()
            if route not in {"standalone", "follow_up"} or not rewritten_query:
                raise ValueError("路由模型返回了无效决策")
            return ContextRoute(
                route=route,
                rewritten_query=rewritten_query,
                selected_history=fallback_history if route == "follow_up" else [],
                reason=reason,
            )
        except Exception as error:
            return ContextRoute(
                route="ambiguous",
                rewritten_query=current_query,
                selected_history=fallback_history,
                reason=f"路由失败，保守保留最近一轮: {error}",
            )

    @staticmethod
    def _recent_history(history: List[Dict], exchanges: int) -> List[Dict]:
        return list(history[-(exchanges * 2):])


def create_context_router(
    provider: str = "deepseek", history_exchanges: int = 2, llm_config=None
) -> ContextRouter:
    """创建路由器；provider 可切换为现有的 local 客户端。"""
    client = create_llm_client(provider, config=llm_config)
    return ContextRouter(
        LLMContextRouteModel(client),
        history_exchanges=history_exchanges,
    )
