"""
CampusQA Web API 服务器
FastAPI 后端 —— 将现有 Chatbot / Session / KnowledgeBase 封装为 REST API
"""
import os
import json
import re
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Optional, List, Dict

import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import DATA_DIR, DOCUMENTS_DIR, AGENT_MODE_ENABLED, SUPPORTED_FORMATS
from src.chatbot import Chatbot, AgentChatResult
from src.llm_client import create_llm_client
from src.session import Session
from src.knowledge_base import KnowledgeBase

# ── FastAPI 应用 ──────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动时预加载嵌入模型和知识库"""
    print("⚡ 预加载嵌入模型...")
    kb = get_kb()
    print(f"✓ 嵌入模型已就绪 (维度: {kb.embeddings_manager.get_embedding_dimension()})")
    print("📂 加载文档索引...")
    updated = kb.load_documents_from_dir()
    print(f"✓ 文档索引完成 (更新 {updated} 个)")
    get_chatbot()
    print("✓ Chatbot 已就绪")
    print("=" * 50)
    print("🚀 CampusQA API 已就绪")
    print(f"   模式: {'Agent 自主检索' if AGENT_MODE_ENABLED else '一步式 RAG'}")
    print(f"   后端: http://0.0.0.0:8000")
    print("=" * 50)
    yield
    # shutdown 清理（预留）


app = FastAPI(title="CampusQA API", version="1.1.3", lifespan=_lifespan)

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
_title_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-title")


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
    reroll: bool = False               # 重新生成：去掉末条 assistant 再生成
    edit_index: Optional[int] = None    # 编辑分支：截断到该索引，替换 user 消息


class ChatResponse(BaseModel):
    response: str
    session_id: str
    session_title: Optional[str] = None
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


class LoadSessionRequest(BaseModel):
    session_id: str


class ModeResponse(BaseModel):
    agent_mode: bool
    mode_name: str


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    history: List[Dict]
    created_at: str
    updated_at: str
    title: Optional[str] = None


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


def _build_title_prompt(user_message: str) -> List[Dict]:
    """构造会话标题生成 prompt。"""
    return [
        {
            "role": "system",
            "content": (
                "你是会话标题生成器。请根据用户第一条消息生成一个简短中文标题。"
                "要求：不超过 12 个汉字或 24 个字符；不要引号、句号、冒号；"
                "不要解释，只输出标题。"
            ),
        },
        {"role": "user", "content": user_message},
    ]


def _clean_session_title(raw_title: Optional[str], max_chars: int = 24) -> str:
    """清洗 LLM 返回的会话标题，确保可用于列表展示。"""
    title = (raw_title or "").strip()
    title = re.sub(r"^[\s\"'“”‘’《》【】\[\]#：:.-]+", "", title)
    title = re.sub(r"[\s\"'“”‘’《》【】\[\]。.!！?？：:.-]+$", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    if "\n" in title:
        title = title.splitlines()[0].strip()
    title = re.split(r"[，,；;。.!！?？]", title, maxsplit=1)[0].strip()
    return title[:max_chars] or "新会话"


def _generate_session_title(user_message: str, client_factory=create_llm_client) -> str:
    """使用 LLM 为首轮消息生成短标题。"""
    client = client_factory()
    title = client.send_message(
        _build_title_prompt(user_message),
        max_tokens=24,
        temperature=0.2,
    )
    return _clean_session_title(title)


def _session_title(session: Session) -> Optional[str]:
    return session.metadata.get("title") or session.metadata.get("summary")


def _apply_session_title(session: Session, title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    cleaned = _clean_session_title(title)
    session.metadata["title"] = cleaned
    session.metadata["summary"] = cleaned
    session.metadata["updated_at"] = datetime.now().isoformat()
    return cleaned


def _save_generated_title(session_id: str, future: Future) -> None:
    """后台标题生成完成后回写 session 文件；失败不影响聊天。"""
    try:
        title = future.result()
    except Exception:
        return
    if not title:
        return

    try:
        session = Session(session_id=session_id)
        if session.load() and not _session_title(session):
            _apply_session_title(session, title)
            session.save()
    except Exception:
        traceback.print_exc()


def _agent_result_to_response(
    result: AgentChatResult,
    session_id: str,
    session_title: Optional[str] = None,
) -> dict:
    """将 AgentChatResult 转为可序列化的 dict"""
    content = result.content or ""
    return {
        "response": content,
        "session_id": session_id,
        "session_title": session_title,
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
    支持 reroll（重新生成）和 edit_index（编辑分支）。
    """
    try:
        session = _load_or_create_session(request.session_id)
        chatbot = get_chatbot()
        should_generate_title = (
            not request.session_id
            and not request.reroll
            and request.edit_index is None
            and not _session_title(session)
            and len(session.messages) == 0
        )
        title_future = (
            _title_executor.submit(_generate_session_title, request.message)
            if should_generate_title else None
        )

        # ── 编辑分支：截断到指定位置 ──
        if request.edit_index is not None:
            idx = request.edit_index
            if idx < len(session.messages):
                session.messages = session.messages[:idx]
                session.metadata["message_count"] = len(session.messages)

        # ── 重新生成：去掉末条 assistant（及紧随的 tool 消息）──
        if request.reroll:
            while session.messages and session.messages[-1].get("role") != "user":
                session.messages.pop()
            session.metadata["message_count"] = len(session.messages)

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

        session_title = _session_title(session)
        if title_future and title_future.done():
            try:
                session_title = _apply_session_title(session, title_future.result())
            except Exception:
                session_title = _session_title(session)

        session.save()

        if title_future and not title_future.done():
            title_future.add_done_callback(
                lambda future, sid=session.session_id: _save_generated_title(sid, future)
            )

        return _agent_result_to_response(result, session.session_id, session_title)

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
        "title": _session_title(session),
    }


@app.delete("/api/session/{session_id}")
def clear_session(session_id: str):
    """清空会话历史（不删文件）"""
    session = Session(session_id=session_id)
    session.load()
    session.clear()
    session.save()
    return {"status": "cleared", "session_id": session_id}


@app.delete("/api/sessions/{session_id}")
def delete_session_file(session_id: str):
    """删除会话文件（从磁盘永久删除）"""
    import os as _os
    filepath = _os.path.join(DATA_DIR, "cache", f"{session_id}.json")
    if _os.path.exists(filepath):
        _os.remove(filepath)
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="会话文件不存在")


@app.delete("/api/session/{session_id}/message/{index}")
def delete_message(session_id: str, index: int):
    """删除会话中指定位置的消息"""
    session = Session(session_id=session_id)
    if not session.load():
        raise HTTPException(status_code=404, detail="会话不存在")
    if index < 0 or index >= len(session.messages):
        raise HTTPException(status_code=400, detail="消息索引越界")
    session.messages.pop(index)
    session.metadata["message_count"] = len(session.messages)
    session.metadata["updated_at"] = datetime.now().isoformat()
    session.save()
    return {"status": "deleted", "session_id": session_id, "index": index}


@app.post("/api/upload-legacy")
async def upload_file(file: UploadFile = File(...)):
    """上传文档到知识库"""
    # 校验扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持: {[f for f in SUPPORTED_FORMATS]}",
        )

    # 保存文件
    file_path = os.path.join(DOCUMENTS_DIR, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 索引到知识库
    try:
        kb = get_kb()
        kb._update_document(file_path)
        kb._save_metadata()
        kb._save_chunk_texts()
        kb._save_store()
        return {
            "status": "ok",
            "filename": file.filename,
            "message": f"文件 {file.filename} 已上传并索引",
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"索引失败: {str(e)}")


@app.post("/api/upload")
async def upload_files(
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
):
    """Upload one or more documents to the knowledge base."""
    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)

    if not upload_files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    os.makedirs(DOCUMENTS_DIR, exist_ok=True)
    kb = get_kb()
    uploaded = []
    failed = []

    for upload in upload_files:
        original_name = upload.filename or ""
        filename = os.path.basename(original_name)
        ext = os.path.splitext(filename)[1].lower()

        if not filename:
            failed.append({"filename": original_name, "error": "Missing filename"})
            continue

        if ext not in SUPPORTED_FORMATS:
            failed.append({
                "filename": filename,
                "error": f"Unsupported file format: {ext}",
            })
            continue

        file_path = os.path.join(DOCUMENTS_DIR, filename)
        try:
            with open(file_path, "wb") as f:
                shutil.copyfileobj(upload.file, f)
            kb._update_document(file_path)
            uploaded.append({"filename": filename, "status": "ok"})
        except Exception as e:
            traceback.print_exc()
            failed.append({"filename": filename, "error": str(e)})

    if uploaded:
        kb._save_metadata()
        kb._save_chunk_texts()
        kb._save_store()

    uploaded_count = len(uploaded)
    failed_count = len(failed)
    response = {
        "status": "ok" if uploaded_count else "error",
        "uploaded": uploaded,
        "failed": failed,
        "uploaded_count": uploaded_count,
        "failed_count": failed_count,
        "message": f"Uploaded {uploaded_count} file(s), {failed_count} failed",
    }

    if not uploaded:
        raise HTTPException(status_code=400, detail=response)

    return response


@app.get("/api/kb/stats")
def kb_stats():
    """知识库统计信息"""
    kb = get_kb()
    stats = kb.get_statistics()
    return stats


@app.post("/api/kb/search")
def kb_search(request: SearchRequest):
    """搜索知识库（含 BM25 双通道，前端自主展示）"""
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

    # 若语义匹配全无关键词命中，检查是否需要追加 BM25
    bm25_results = []
    all_bm25_zero = all(r.get('bm25_score', 0) == 0 for r in results)
    if all_bm25_zero and hasattr(kb, 'bm25_search'):
        qt = kb._tokenize_query(request.query)
        if any(kb._bm25_doc_freq.get(t, 0) > 0 for t in qt):
            bm25_results = kb.bm25_search(request.query, top_k=request.top_k)

    return {
        "query": request.query,
        "results": results,
        "bm25_results": bm25_results,
        "count": len(results),
        "bm25_count": len(bm25_results),
    }


# ═══════════════════════════════════════════════════════════
#  会话管理（v1.0 扩展）
# ═══════════════════════════════════════════════════════════

@app.get("/api/sessions")
def list_sessions():
    """列出所有已保存的会话"""
    files = Session.list_saved_sessions()
    sessions = []
    for f in sorted(files, reverse=True):
        sid = f.replace(".json", "")
        try:
            s = Session(session_id=sid)
            if s.load():
                sessions.append({
                    "session_id": sid,
                    "title": _session_title(s),
                    "message_count": s.metadata.get("message_count", 0),
                    "created_at": s.metadata.get("created_at", ""),
                    "updated_at": s.metadata.get("updated_at", ""),
                })
        except Exception:
            pass
    return {"sessions": sessions, "count": len(sessions)}


@app.post("/api/session/load")
def load_session(request: LoadSessionRequest):
    """加载指定会话并返回历史"""
    session = Session(session_id=request.session_id)
    if not session.load():
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": session.session_id,
        "message_count": session.metadata.get("message_count", 0),
        "history": session.get_history(strip_tool_details=True),
        "tool_call_log": session.get_tool_call_log(),
        "created_at": session.metadata.get("created_at", ""),
        "updated_at": session.metadata.get("updated_at", ""),
        "title": _session_title(session),
    }


# ═══════════════════════════════════════════════════════════
#  Agent 模式
# ═══════════════════════════════════════════════════════════

@app.get("/api/mode", response_model=ModeResponse)
def get_mode():
    """获取当前对话模式"""
    chatbot = get_chatbot()
    return {
        "agent_mode": chatbot.agent_mode,
        "mode_name": "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG",
    }


@app.post("/api/mode/toggle", response_model=ModeResponse)
def toggle_mode():
    """切换 Agent / 一步式 RAG 模式"""
    chatbot = get_chatbot()
    chatbot.agent_mode = not chatbot.agent_mode
    return {
        "agent_mode": chatbot.agent_mode,
        "mode_name": "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG",
    }


# ═══════════════════════════════════════════════════════════
#  统计与日志
# ═══════════════════════════════════════════════════════════

@app.get("/api/cost/{session_id}")
def get_cost(session_id: str):
    """获取指定会话的 Token 消耗统计"""
    session = Session(session_id=session_id)
    if not session.load():
        raise HTTPException(status_code=404, detail="会话不存在")
    total = session.total_prompt_tokens + session.total_completion_tokens
    return {
        "session_id": session_id,
        "prompt_tokens": session.total_prompt_tokens,
        "completion_tokens": session.total_completion_tokens,
        "total_tokens": total,
        "tool_calls": len(session.tool_call_log),
    }


@app.get("/api/session/{session_id}/tool-log")
def get_tool_log(session_id: str):
    """获取指定会话的工具调用日志"""
    session = Session(session_id=session_id)
    if not session.load():
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": session_id,
        "tool_call_log": session.get_tool_call_log(),
        "count": len(session.tool_call_log),
    }


# ═══════════════════════════════════════════════════════════
#  知识库管理
# ═══════════════════════════════════════════════════════════

@app.post("/api/kb/scan")
def kb_scan():
    """扫描文档目录，增量加载新文档"""
    kb = get_kb()
    updated = kb.load_documents_from_dir()
    return {"status": "ok", "updated_count": updated}


@app.post("/api/kb/rebuild")
def kb_rebuild():
    """重建知识库索引"""
    kb = get_kb()
    kb.rebuild_index()
    return {"status": "ok", "message": "索引重建完成"}


# ── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("⚡ 启动中（嵌入模型将预加载）...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
