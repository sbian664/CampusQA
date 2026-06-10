"""
CampusQA Web API 服务器
FastAPI 后端 —— 将现有 Chatbot / Session / KnowledgeBase 封装为 REST API
"""
import os
import json
import traceback
from datetime import datetime
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import DATA_DIR, DOCUMENTS_DIR, AGENT_MODE_ENABLED
from src.chatbot import Chatbot, AgentChatResult
from src.session import Session
from src.knowledge_base import KnowledgeBase

# ── FastAPI 应用 ──────────────────────────────────────────
app = FastAPI(title="CampusQA API", version="0.7.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 全局单例 ──────────────────────────────────────────────
_kb: Optional[KnowledgeBase] = None
_chatbot: Optional[Chatbot] = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


def get_chatbot() -> Chatbot:
    global _chatbot
    if _chatbot is None:
        _chatbot = Chatbot(knowledge_base=get_kb())
    return _chatbot


# ── 请求 / 响应模型 ───────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    finish_reason: str = "stop"
    tool_calls: Optional[List[Dict]] = None
    usage: Optional[Dict] = None
    rounds: int = 0


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    doc_type: Optional[str] = None
    mtime_after: Optional[str] = None
    mtime_before: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    history: List[Dict]
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ── 辅助函数 ──────────────────────────────────────────────

def _load_or_create_session(session_id: Optional[str]) -> Session:
    """加载已有 Session 或创建新 Session"""
    if session_id:
        session = Session(session_id=session_id)
        session.load()
    else:
        session = Session()
        session.save()
    return session


def _agent_result_to_response(result: AgentChatResult, session_id: str) -> dict:
    """将 AgentChatResult 转为可序列化的 dict"""
    content = result.content or ""
    return {
        "response": content,
        "session_id": session_id,
        "finish_reason": result.finish_reason,
        "tool_calls": result.tool_call_log,
        "usage": result.usage,
        "rounds": result.rounds,
    }


# ── API 端点 ──────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """
    发送消息，获取 AI 回复。
    自动路由至 Agent Loop 或一步式 RAG（取决于 chatbot.agent_mode）。
    """
    try:
        session = _load_or_create_session(request.session_id)
        chatbot = get_chatbot()

        # 获取历史（纯 user/assistant 视图）
        history = session.get_history(strip_tool_details=True)

        # 路由对话
        if chatbot.agent_mode:
            result = chatbot.agent_chat(request.message, history)
        else:
            text = chatbot.chat_with_rag(request.message, history)
            result = AgentChatResult(content=text, finish_reason="stop")

        # 保存消息到会话
        session.add_message("user", request.message)

        # 如果 AI 返回了 tool_calls，也一并记录
        if result.tool_call_log:
            session.add_message("assistant", result.content,
                                tool_calls=result.tool_call_log)
            session.tool_call_log.extend(result.tool_call_log)
        else:
            session.add_message("assistant", result.content)

        if result.usage:
            session.total_prompt_tokens += result.usage.get("prompt_tokens", 0)
            session.total_completion_tokens += result.usage.get("completion_tokens", 0)

        session.save()

        return _agent_result_to_response(result, session.session_id)

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    """获取会话信息与历史"""
    session = Session(session_id=session_id)
    if not session.load():
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": session.session_id,
        "message_count": session.metadata.get("message_count", 0),
        "history": session.get_history(strip_tool_details=True),
        "created_at": session.metadata.get("created_at", ""),
        "updated_at": session.metadata.get("updated_at", ""),
    }


@app.delete("/api/session/{session_id}")
def clear_session(session_id: str):
    """清空会话历史"""
    session = Session(session_id=session_id)
    session.load()
    session.clear()
    session.save()
    return {"status": "cleared", "session_id": session_id}


@app.get("/api/kb/stats")
def kb_stats():
    """知识库统计信息"""
    kb = get_kb()
    stats = kb.get_statistics()
    return stats


@app.post("/api/kb/search")
def kb_search(request: SearchRequest):
    """搜索知识库"""
    kb = get_kb()
    filters = {}
    if request.doc_type:
        filters["doc_type"] = request.doc_type
    if request.mtime_after:
        filters["mtime_after"] = request.mtime_after
    if request.mtime_before:
        filters["mtime_before"] = request.mtime_before

    results = kb.hybrid_search(
        request.query,
        top_k=request.top_k,
        filters=filters if filters else None,
    )
    return {"query": request.query, "results": results, "count": len(results)}


# ── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("🚀 CampusQA API 启动中...")
    print(f"   Agent 模式: {'启用' if AGENT_MODE_ENABLED else '关闭'}")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
