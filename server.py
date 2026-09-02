"""
CampusQA Web API 服务器
FastAPI 后端 —— 将现有 Chatbot / Session / KnowledgeBase 封装为 REST API
"""
import os
import json
import re
import traceback
import tempfile
import uuid
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Optional, List, Dict

import shutil
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    DATA_DIR,
    DOCUMENTS_DIR,
    AGENT_MODE_ENABLED,
    SUPPORTED_FORMATS,
    CONTEXT_ROUTER_ENABLED,
    CONTEXT_ROUTER_PROVIDER,
    CONTEXT_ROUTER_HISTORY_EXCHANGES,
    RERANKER_AVAILABLE,
)
from src.chatbot import Chatbot, AgentChatResult
from src.context_router import ContextRouter, create_context_router
from src.llm_client import create_llm_client
from src.session import Session
from src.knowledge_base import KnowledgeBase
from src.document_loader import DocumentLoader
from src.user_llm_config import UserLLMConfigStore
from src.admin.routes import (
    admin_csrf_dependency,
    admin_session_dependency,
    create_admin_router,
    create_default_admin_store,
)
from src.admin.service import AdminService, list_session_summaries, load_session_detail
from src.admin.jobs import AdminJobManager
from src.admin.documents import AdminDocumentService
from src.admin.config import AdminConfigService
from src.reranker import search_with_optional_rerank

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
    _admin_job_manager.shutdown()


app = FastAPI(title="CampusQA API", version="1.2.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "ADMIN_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_admin_control_store = create_default_admin_store(DATA_DIR)
_admin_job_manager = AdminJobManager(_admin_control_store)
_admin_document_service = AdminDocumentService(
    kb_provider=lambda: get_kb(),
    documents_dir=DOCUMENTS_DIR,
    supported_formats=set(SUPPORTED_FORMATS),
)
_admin_auth = admin_session_dependency(_admin_control_store)
_admin_csrf = admin_csrf_dependency(_admin_control_store)

# ── 全局单例 ──────────────────────────────────────────────
_kb: Optional[KnowledgeBase] = None
_chatbot: Optional[Chatbot] = None
_context_router: Optional[ContextRouter] = None
_llm_config_store = UserLLMConfigStore()
_title_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-title")


@app.middleware("http")
async def admin_request_observability(request, call_next):
    """Attach a correlation id and collect only bounded admin metrics."""
    observed_paths = {
        "/api/llm-config", "/api/llm-config/test", "/api/upload", "/api/upload-legacy",
        "/api/kb/scan", "/api/kb/rebuild", "/api/mode/toggle",
    }
    if not request.url.path.startswith("/api/admin") and request.url.path not in observed_paths and not request.url.path.startswith("/api/sessions/") and not request.url.path.startswith("/api/session/"):
        return await call_next(request)
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as error:
        duration_ms = round((time.perf_counter() - started) * 1000)
        _admin_control_store.record_metric("api_requests", duration_ms=duration_ms)
        _admin_control_store.record_metric("api_errors")
        _admin_control_store.record_error(
            request_id=request_id, path=request.url.path, status_code=500,
            detail=f"{type(error).__name__}: {error}",
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000)
    _admin_control_store.record_metric("api_requests", duration_ms=duration_ms)
    if response.status_code >= 400:
        _admin_control_store.record_metric("api_errors")
        _admin_control_store.record_error(
            request_id=request_id, path=request.url.path,
            status_code=response.status_code, detail="管理请求失败",
        )
    _admin_control_store.record_audit(
        actor=getattr(request.state, "admin_username", "anonymous"),
        action="admin.request",
        target=request.url.path,
        status=str(response.status_code),
        request_id=request_id,
        duration_ms=duration_ms,
        details={"method": request.method},
    )
    response.headers["X-Request-ID"] = request_id
    return response


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


def get_chatbot() -> Chatbot:
    global _chatbot
    llm_config = get_llm_config()
    config_signature = json.dumps(llm_config, sort_keys=True)
    if _chatbot is None or getattr(_chatbot, "_llm_config_signature", None) != config_signature:
        _chatbot = Chatbot(knowledge_base=get_kb(), llm_config=llm_config)
        _chatbot._llm_config_signature = config_signature
    return _chatbot


def get_context_router() -> ContextRouter:
    global _context_router
    llm_config = get_llm_config()
    config_signature = json.dumps(llm_config, sort_keys=True)
    if _context_router is None or getattr(_context_router, "_llm_config_signature", None) != config_signature:
        _context_router = create_context_router(
            provider=CONTEXT_ROUTER_PROVIDER,
            history_exchanges=CONTEXT_ROUTER_HISTORY_EXCHANGES,
            llm_config=llm_config,
        )
        _context_router._llm_config_signature = config_signature
    return _context_router


def get_llm_config() -> Dict[str, str]:
    return _llm_config_store.get()


def _admin_search(query: str, top_k: int, rerank_enabled: bool, filters: Dict[str, object]) -> Dict[str, object]:
    started = time.perf_counter()
    kb = get_kb()
    valid_filters = {
        key: value for key, value in (filters or {}).items()
        if key in {"doc_type", "source", "mtime_after", "mtime_before"}
    }
    vector_results = kb.search(query, top_k=top_k, filters=valid_filters or None)
    bm25_results = kb.bm25_search(query, top_k=top_k, filters=valid_filters or None) if hasattr(kb, "bm25_search") else []
    hybrid_results = kb.hybrid_search(query, top_k=top_k, filters=valid_filters or None) if hasattr(kb, "hybrid_search") else vector_results
    reranked_results = (
        search_with_optional_rerank(
            kb, query, top_k=top_k, enabled=True, filters=valid_filters or None
        )
        if rerank_enabled
        else []
    )
    return AdminService.search_response(
        query=query,
        channels={
            "vector": vector_results,
            "bm25": bm25_results,
            "hybrid": hybrid_results,
            "reranked": reranked_results,
        },
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


def _admin_mode() -> Dict[str, object]:
    chatbot = get_chatbot()
    return {
        "agent_mode": chatbot.agent_mode,
        "mode_name": "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG",
        "reranker_enabled": bool(RERANKER_AVAILABLE),
        "context_router_enabled": bool(CONTEXT_ROUTER_ENABLED),
    }


def _admin_toggle_mode() -> Dict[str, object]:
    chatbot = get_chatbot()
    chatbot.agent_mode = not chatbot.agent_mode
    return {
        "agent_mode": chatbot.agent_mode,
        "mode_name": "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG",
    }


def _admin_job_operation(kind: str, progress) -> Dict[str, object]:
    """Run one serialized KB mutation and report coarse-grained progress."""
    kb = get_kb()
    if kind == "scan":
        progress(10, "正在扫描文档目录")
        updated = kb.load_documents_from_dir(max_workers=1)
        progress(95, "正在保存索引状态")
        return {"updated_documents": updated}
    if kind == "rebuild":
        progress(10, "正在清空现有索引")
        kb.rebuild_index()
        progress(95, "正在完成索引持久化")
        return {"status": "rebuilt"}
    raise ValueError(f"不支持的索引任务: {kind}")


_admin_job_manager.run_operation = _admin_job_operation
_admin_config_service = AdminConfigService(
    llm_store=_llm_config_store,
    control_store=_admin_control_store,
    mode_provider=_admin_mode,
    mode_toggle=_admin_toggle_mode,
)


def _admin_session_detail(session_id: str) -> Dict[str, object]:
    detail = load_session_detail(Session, session_id)
    detail["feedback"] = _admin_control_store.get_feedback(session_id)
    return detail


def _admin_activity() -> Dict[str, object]:
    audits = _admin_control_store.list_audit(limit=100)
    return {
        "today_sessions": None,
        "today_questions": None,
        "today_tokens": None,
        "recent_errors": _admin_control_store.list_errors(limit=5),
        "recent_kb_operations": [
            event for event in audits if str(event.get("action", "")).startswith("kb.")
        ][:5],
    }


_admin_service = AdminService(
    health_provider=lambda: {"status": "ok", "timestamp": datetime.now().isoformat()},
    kb_provider=lambda: get_kb().get_statistics(),
    llm_provider=_llm_config_store.public_config,
    mode_provider=_admin_mode,
    sessions_provider=lambda limit, offset, query: list_session_summaries(
        Session, limit=limit, offset=offset, query=query
    ),
    session_detail_provider=_admin_session_detail,
    activity_provider=_admin_activity,
)
app.include_router(
    create_admin_router(
        _admin_control_store,
        _admin_service,
        search_provider=lambda query, top_k, rerank_enabled, filters: _admin_search(
            query, top_k, rerank_enabled, filters
        ),
        job_manager=_admin_job_manager,
        document_service=_admin_document_service,
        config_service=_admin_config_service,
    ),
    prefix="/api/admin",
)


# ── 请求 / 响应模型 ───────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    rerank_enabled: bool = True        # 请求级智能搜索开关，不修改服务器全局状态
    context_router_enabled: Optional[bool] = None  # 实验性上下文路由；未指定时使用服务默认值
    reroll: bool = False               # 重新生成：去掉末条 assistant 再生成
    edit_index: Optional[int] = None    # 编辑分支：截断到该索引，替换 user 消息


def is_context_router_enabled(request: ChatRequest) -> bool:
    """Resolve the per-request experimental switch with an environment fallback."""
    if request.context_router_enabled is None:
        return CONTEXT_ROUTER_ENABLED
    return request.context_router_enabled


class LLMConfigRequest(BaseModel):
    provider: str
    api_key: str = ""
    model: str
    base_url: str


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
    source: Optional[str] = None
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


class AttachmentParseResponse(BaseModel):
    status: str
    filename: str
    content: str
    char_count: int
    truncated: bool = False


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


def _generate_session_title(
    user_message: str,
    client_factory=create_llm_client,
    llm_config: Optional[Dict[str, str]] = None,
) -> str:
    """使用 LLM 为首轮消息生成短标题。"""
    client = (
        client_factory(config=llm_config)
        if llm_config is not None
        else client_factory()
    )
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


def _attachment_context_from_docs(filename: str, docs: List, max_chars: int = 12000) -> Dict:
    """Build bounded text context for a message attachment."""
    parts = []
    total = 0
    source_chars = 0
    for doc in docs:
        text = (getattr(doc, "page_content", "") or "").strip()
        source_chars += len(text)
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunk = text[:remaining]
        parts.append(chunk)
        total += len(chunk)

    content = "\n\n".join(parts).strip()
    return {
        "status": "ok",
        "filename": filename,
        "content": content,
        "char_count": len(content),
        "truncated": source_chars > len(content),
    }


# ── API 端点 ──────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/llm-config")
def get_llm_config_endpoint(_admin=Depends(_admin_auth)):
    """读取单用户模型配置；API Key 只返回脱敏值。"""
    return _llm_config_store.public_config()


@app.put("/api/llm-config")
def update_llm_config(request: LLMConfigRequest, _admin=Depends(_admin_csrf)):
    """保存单用户模型配置并让后续请求重新创建 LLM 客户端。"""
    global _chatbot, _context_router
    try:
        _llm_config_store.update(request.dict())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    _chatbot = None
    _context_router = None
    return {"status": "saved", **_llm_config_store.public_config()}


@app.post("/api/llm-config/test")
def test_llm_config(request: LLMConfigRequest, _admin=Depends(_admin_csrf)):
    """测试当前输入配置，不保存配置。"""
    try:
        candidate = _llm_config_store.resolve(
            request.dict(),
        )
        client = create_llm_client(config=candidate)
        client.send_message(
            [
                {"role": "system", "content": "只回复 OK。"},
                {"role": "user", "content": "连接测试"},
            ],
            max_tokens=8,
            temperature=0,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"模型连接失败: {error}")
    return {"status": "ok"}


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
            _title_executor.submit(
                _generate_session_title,
                request.message,
                create_llm_client,
                get_llm_config(),
            )
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

        routed_message = request.message
        routed_history = history
        if is_context_router_enabled(request) and history:
            try:
                context_route = get_context_router().route(request.message, history)
                routed_message = context_route.rewritten_query
                routed_history = context_route.selected_history
                print(
                    f"  🧭 上下文路由: {context_route.route}; "
                    f"history={len(routed_history)}; reason={context_route.reason}"
                )
            except Exception as error:
                print(f"  ⚠️ 上下文路由不可用，保留原始历史: {error}")

        # 路由对话
        turn_id = uuid.uuid4().hex
        if chatbot.agent_mode:
            result = chatbot.agent_chat(
                routed_message,
                routed_history,
                rerank_enabled=request.rerank_enabled,
                turn_id=turn_id,
            )
        else:
            text = chatbot.chat_with_rag(
                routed_message,
                routed_history,
                rerank_enabled=request.rerank_enabled,
            )
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
def clear_session(session_id: str, _admin=Depends(_admin_csrf)):
    """清空会话历史（不删文件）"""
    session = Session(session_id=session_id)
    session.load()
    session.clear()
    session.save()
    return {"status": "cleared", "session_id": session_id}


@app.delete("/api/sessions/{session_id}")
def delete_session_file(session_id: str, _admin=Depends(_admin_csrf)):
    """删除会话文件（从磁盘永久删除）"""
    import os as _os
    filepath = _os.path.join(DATA_DIR, "cache", f"{session_id}.json")
    if _os.path.exists(filepath):
        _os.remove(filepath)
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="会话文件不存在")


@app.delete("/api/session/{session_id}/message/{index}")
def delete_message(session_id: str, index: int, _admin=Depends(_admin_csrf)):
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
async def upload_file(file: UploadFile = File(...), _admin=Depends(_admin_csrf)):
    """上传文档到知识库"""
    # 校验扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持: {[f for f in SUPPORTED_FORMATS]}",
        )

    # 保存文件
    filename = os.path.basename(file.filename or "")
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    file_path = os.path.join(DOCUMENTS_DIR, filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 索引到知识库
    try:
        kb = get_kb()
        if kb._update_document(file_path) is False:
            raise RuntimeError("文档索引未提交")
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


@app.post("/api/attachments/parse", response_model=AttachmentParseResponse)
async def parse_message_attachment(file: UploadFile = File(...)):
    """Parse a chat attachment without adding it to the knowledge base."""
    filename = os.path.basename(file.filename or "")
    ext = os.path.splitext(filename)[1].lower()

    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {ext}. Supported: {SUPPORTED_FORMATS}",
        )

    with tempfile.TemporaryDirectory(prefix="campusqa_attachment_") as temp_dir:
        file_path = os.path.join(temp_dir, filename)
        try:
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            docs = DocumentLoader(base_dir=temp_dir).load_file(file_path)
            return _attachment_context_from_docs(filename, docs)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Attachment parse failed: {str(e)}")


@app.post("/api/upload")
async def upload_files(
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
    _admin=Depends(_admin_csrf),
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
            if kb._update_document(file_path) is False:
                raise RuntimeError("文档索引未提交")
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
    if request.source:
        filters["source"] = request.source
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
            bm25_results = kb.bm25_search(
                request.query,
                top_k=request.top_k,
                filters=filters if filters else None,
            )

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
def toggle_mode(_admin=Depends(_admin_csrf)):
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
def kb_scan(_admin=Depends(_admin_csrf)):
    """扫描文档目录，增量加载新文档"""
    kb = get_kb()
    updated = kb.load_documents_from_dir()
    return {"status": "ok", "updated_count": updated}


@app.post("/api/kb/rebuild")
def kb_rebuild(_admin=Depends(_admin_csrf)):
    """重建知识库索引"""
    kb = get_kb()
    kb.rebuild_index()
    return {"status": "ok", "message": "索引重建完成"}


# ── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("⚡ 启动中（嵌入模型将预加载）...")
    # Production is started by the service manager; reload mode creates a
    # multiprocessing reloader and can leave a model-heavy child behind.
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
