"""Authenticated admin routes shared by the web console."""

from __future__ import annotations

import os
import inspect
import mimetypes
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .control_store import AdminControlStore
from .service import AdminService
from src.submissions import SubmissionService


ADMIN_SESSION_COOKIE = "campusqa_admin_session"
CSRF_HEADER = "X-CSRF-Token"


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    rerank_enabled: bool = True
    filters: Optional[Dict[str, Any]] = None


class AdminDocumentEditRequest(BaseModel):
    content: str


class AdminDocumentRenameRequest(BaseModel):
    filename: str


class AdminLLMConfigRequest(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key: str = ""


class AdminRuntimeConfigRequest(BaseModel):
    agent_mode: Optional[bool] = None
    reranker_enabled: Optional[bool] = None
    context_router_enabled: Optional[bool] = None
    top_k: Optional[int] = None
    bm25_weight: Optional[float] = None
    max_tokens: Optional[int] = None


class FeedbackRequest(BaseModel):
    quality: str
    note: str = ""


class SubmissionRejectRequest(BaseModel):
    reason: str


def admin_session_dependency(store: AdminControlStore):
    def require_admin(request: Request) -> Dict[str, Any]:
        session = store.get_session(request.cookies.get(ADMIN_SESSION_COOKIE))
        if not session:
            raise HTTPException(status_code=401, detail="管理员登录已失效或尚未登录")
        request.state.admin_username = session["username"]
        return session
    return require_admin


def admin_csrf_dependency(store: AdminControlStore):
    require_admin = admin_session_dependency(store)

    def require_csrf(
        request: Request,
        csrf_token: str | None = Header(default=None, alias=CSRF_HEADER),
    ) -> Dict[str, Any]:
        session = require_admin(request)
        if not store.verify_csrf(request.cookies.get(ADMIN_SESSION_COOKIE), csrf_token):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        return session
    return require_csrf


def create_admin_router(
    store: AdminControlStore,
    service: Optional[AdminService] = None,
    search_provider: Any = None,
    job_manager: Any = None,
    document_service: Any = None,
    config_service: Any = None,
    submission_service: SubmissionService | None = None,
) -> APIRouter:
    router = APIRouter()

    require_admin = admin_session_dependency(store)
    require_csrf = admin_csrf_dependency(store)

    @router.post("/auth/login")
    def login(payload: LoginRequest, response: Response, request: Request):
        if not store.login_allowed(payload.username):
            store.record_audit(
                actor=payload.username or "unknown",
                action="auth.login",
                target="admin",
                status="rate_limited",
                request_id=getattr(request.state, "request_id", None),
            )
            raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
        if not store.authenticate(payload.username, payload.password):
            store.record_login_failure(payload.username)
            store.record_audit(
                actor=payload.username or "unknown",
                action="auth.login",
                target="admin",
                status="failure",
                request_id=getattr(request.state, "request_id", None),
                details={"reason": "invalid_credentials"},
            )
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        store.clear_login_failures(payload.username)
        session = store.create_session(payload.username)
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            session["session_token"],
            max_age=store.session_ttl_seconds,
            httponly=True,
            secure=store.cookie_secure,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            "campusqa_admin_csrf",
            session["csrf_token"],
            max_age=store.session_ttl_seconds,
            secure=store.cookie_secure,
            httponly=False,
            samesite="lax",
            path="/",
        )
        store.record_audit(
            actor=payload.username,
            action="auth.login",
            target="admin",
            status="success",
            request_id=getattr(request.state, "request_id", None),
        )
        return {
            "username": payload.username,
            "csrf_token": session["csrf_token"],
            "expires_at": session["expires_at"],
        }

    @router.get("/auth/me")
    def me(session: Dict[str, Any] = Depends(require_admin)):
        return {"username": session["username"], "expires_at": session["expires_at"]}

    @router.post("/auth/logout")
    def logout(response: Response, request: Request, session: Dict[str, Any] = Depends(require_csrf)):
        store.revoke_session(request.cookies.get(ADMIN_SESSION_COOKIE))
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
        response.delete_cookie("campusqa_admin_csrf", path="/")
        store.record_audit(
            actor=session["username"], action="auth.logout", target="admin", status="success",
            request_id=getattr(request.state, "request_id", None),
        )
        return {"status": "ok"}

    @router.get("/audit-logs")
    def audit_logs(
        limit: int = 50,
        offset: int = 0,
        session: Dict[str, Any] = Depends(require_admin),
    ):
        return {"items": store.list_audit(limit=limit, offset=offset)}

    @router.get("/metrics/summary")
    def metrics_summary(session: Dict[str, Any] = Depends(require_admin)):
        return store.metrics_summary()

    @router.get("/errors")
    def errors(limit: int = 50, session: Dict[str, Any] = Depends(require_admin)):
        return {"items": store.list_errors(limit=limit)}

    @router.get("/overview")
    def overview(session: Dict[str, Any] = Depends(require_admin)):
        if service is None:
            raise HTTPException(status_code=503, detail="总览服务未配置")
        return service.overview()

    @router.get("/sessions")
    def sessions(
        limit: int = 20,
        offset: int = 0,
        query: Optional[str] = None,
        session: Dict[str, Any] = Depends(require_admin),
    ):
        if service is None:
            raise HTTPException(status_code=503, detail="会话服务未配置")
        return service.sessions(limit=limit, offset=offset, query=query)

    @router.get("/sessions/{session_id}")
    def session_detail(session_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if service is None:
            raise HTTPException(status_code=503, detail="会话服务未配置")
        try:
            return service.session_detail(session_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))

    @router.get("/sessions/{session_id}/trace")
    def session_trace(session_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if service is None:
            raise HTTPException(status_code=503, detail="会话服务未配置")
        try:
            detail = service.session_detail(session_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        return {
            "session_id": detail["session_id"],
            "trace": detail["trace"],
            "tool_call_log": detail["tool_call_log"],
        }

    @router.post("/sessions/{session_id}/feedback")
    def session_feedback(session_id: str, payload: FeedbackRequest, request: Request,
                          session: Dict[str, Any] = Depends(require_csrf)):
        if payload.quality not in {"correct", "partial", "incorrect", "missing"}:
            raise HTTPException(status_code=400, detail="质量标签无效")
        if not session_id or len(session_id) > 160 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for char in session_id):
            raise HTTPException(status_code=400, detail="非法会话 ID")
        result = store.save_feedback(session_id, payload.quality, payload.note, session["username"])
        store.record_audit(actor=session["username"], action="session.feedback", target=session_id, status="success",
                           request_id=getattr(request.state, "request_id", None), details={"quality": payload.quality})
        return result

    @router.post("/kb/search")
    def kb_search(
        payload: AdminSearchRequest,
        session: Dict[str, Any] = Depends(require_csrf),
    ):
        if search_provider is None:
            raise HTTPException(status_code=503, detail="检索服务未配置")
        query = payload.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="检索词不能为空")
        return search_provider(query, max(1, min(payload.top_k, 20)), payload.rerank_enabled, payload.filters or {})

    @router.get("/llm-config")
    def llm_config(session: Dict[str, Any] = Depends(require_admin)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        return config_service.public_llm()

    @router.put("/llm-config")
    def update_llm_config(payload: AdminLLMConfigRequest, request: Request,
                          session: Dict[str, Any] = Depends(require_csrf)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        try:
            result = config_service.update_llm(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(actor=session["username"], action="llm_config.update", target="runtime", status="success",
                           request_id=getattr(request.state, "request_id", None), details={"provider": payload.provider, "model": payload.model})
        return {"status": "saved", **result}

    @router.post("/llm-config/test")
    def test_llm_config(payload: AdminLLMConfigRequest, request: Request,
                        session: Dict[str, Any] = Depends(require_csrf)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        try:
            result = config_service.test_llm(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
        except ValueError as error:
            store.record_audit(actor=session["username"], action="llm_config.test", target="runtime", status="failure",
                               request_id=getattr(request.state, "request_id", None), details={"reason": str(error)})
            raise HTTPException(status_code=400, detail=str(error))
        except Exception as error:
            store.record_audit(actor=session["username"], action="llm_config.test", target="runtime", status="failure",
                               request_id=getattr(request.state, "request_id", None), details={"reason": type(error).__name__})
            raise HTTPException(status_code=502, detail=f"模型连接失败: {error}")
        store.record_audit(actor=session["username"], action="llm_config.test", target="runtime", status="success",
                           request_id=getattr(request.state, "request_id", None))
        return result

    @router.get("/runtime-config")
    def runtime_config(session: Dict[str, Any] = Depends(require_admin)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        return config_service.runtime_config()

    @router.put("/runtime-config")
    def update_runtime_config(payload: AdminRuntimeConfigRequest, request: Request,
                              session: Dict[str, Any] = Depends(require_csrf)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        values = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
        try:
            result = config_service.update_runtime(values)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(actor=session["username"], action="runtime_config.update", target="runtime", status="success",
                           request_id=getattr(request.state, "request_id", None), details={"fields": list(values)})
        return result

    @router.post("/mode/toggle")
    def toggle_mode(request: Request, session: Dict[str, Any] = Depends(require_csrf)):
        if config_service is None:
            raise HTTPException(status_code=503, detail="配置服务未配置")
        result = config_service.toggle_mode()
        store.record_audit(actor=session["username"], action="mode.toggle", target="runtime", status="success",
                           request_id=getattr(request.state, "request_id", None), details={"agent_mode": result.get("agent_mode")})
        return result

    @router.get("/submissions")
    def submissions(
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        session: Dict[str, Any] = Depends(require_admin),
    ):
        if submission_service is None:
            raise HTTPException(status_code=503, detail="投稿服务未配置")
        return submission_service.list_admin(status=status, limit=limit, offset=offset)

    @router.get("/submissions/summary")
    def submission_summary(session: Dict[str, Any] = Depends(require_admin)):
        if submission_service is None:
            raise HTTPException(status_code=503, detail="投稿服务未配置")
        return submission_service.summary()

    @router.get("/submissions/{submission_id}")
    def submission_detail(submission_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if submission_service is None:
            raise HTTPException(status_code=503, detail="投稿服务未配置")
        try:
            result = submission_service.get_admin(submission_id)
            if job_manager is not None and result.get("job_id"):
                result["job"] = job_manager.get(result["job_id"])
            return result
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error))

    def _queue_submission(submission_id: str, session: Dict[str, Any], *, retry: bool) -> Dict[str, Any]:
        if submission_service is None or job_manager is None:
            raise HTTPException(status_code=503, detail="投稿任务服务未配置")
        try:
            submission_service.mark_importing(submission_id, allow_failed=retry)
            job_id = job_manager.submit(
                "publish_submission",
                lambda progress: _publish_submission_job(submission_id, progress),
                target_id=submission_id,
            )
            submission_service.attach_job(submission_id, job_id)
        except (LookupError, ValueError) as error:
            if "cannot be imported" in str(error):
                raise HTTPException(status_code=409, detail=str(error))
            raise HTTPException(status_code=404, detail=str(error))
        except RuntimeError as error:
            submission_service.reset_pending(submission_id)
            raise HTTPException(status_code=409, detail=str(error))
        store.record_audit(
            actor=session["username"],
            action="submission.retry" if retry else "submission.approve",
            target=submission_id,
            status="accepted",
            details={"job_id": job_id},
        )
        return {"submission_id": submission_id, "job_id": job_id, "status": "queued"}

    def _publish_submission_job(submission_id: str, progress: Any) -> Dict[str, Any]:
        progress(10, "正在准备投稿文件")
        result = submission_service.publish(submission_id)
        progress(95, "正在保存知识库索引")
        return result

    @router.post("/submissions/{submission_id}/approve", status_code=202)
    def approve_submission(submission_id: str, session: Dict[str, Any] = Depends(require_csrf)):
        return _queue_submission(submission_id, session, retry=False)

    @router.post("/submissions/{submission_id}/reject")
    def reject_submission(
        submission_id: str,
        payload: SubmissionRejectRequest,
        session: Dict[str, Any] = Depends(require_csrf),
    ):
        if submission_service is None:
            raise HTTPException(status_code=503, detail="投稿服务未配置")
        try:
            result = submission_service.reject(submission_id, payload.reason)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(
            actor=session["username"], action="submission.reject", target=submission_id,
            status="success", details={"reason": payload.reason[:200]},
        )
        return result

    @router.post("/submissions/{submission_id}/retry", status_code=202)
    def retry_submission(submission_id: str, session: Dict[str, Any] = Depends(require_csrf)):
        return _queue_submission(submission_id, session, retry=True)

    def submit_job(kind: str, session: Dict[str, Any]) -> Dict[str, Any]:
        if job_manager is None:
            raise HTTPException(status_code=503, detail="任务服务未配置")
        try:
            job_id = job_manager.submit(kind, lambda progress: _run_job(kind, progress))
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error))
        store.record_audit(
            actor=session["username"],
            action=f"kb.{kind}",
            target="knowledge_base",
            status="accepted",
            details={"job_id": job_id},
        )
        return {"job_id": job_id, "status": "queued"}

    def _run_job(kind: str, progress: Any) -> Any:
        # The application wiring supplies the actual KB operation through this
        # callback attribute. Keeping the route independent makes it testable.
        runner = getattr(job_manager, "run_operation", None)
        if runner is None:
            progress(100, "任务已接收，等待应用执行")
            return None
        return runner(kind, progress)

    @router.post("/kb/jobs/scan", status_code=202)
    def start_scan(session: Dict[str, Any] = Depends(require_csrf)):
        return submit_job("scan", session)

    @router.post("/kb/jobs/rebuild", status_code=202)
    def start_rebuild(session: Dict[str, Any] = Depends(require_csrf)):
        return submit_job("rebuild", session)

    @router.get("/kb/jobs")
    def jobs(limit: int = 50, offset: int = 0, session: Dict[str, Any] = Depends(require_admin)):
        if job_manager is None:
            raise HTTPException(status_code=503, detail="任务服务未配置")
        return {"items": job_manager.list(limit=max(1, min(limit, 100)), offset=max(0, offset))}

    @router.get("/kb/jobs/{job_id}")
    def job_detail(job_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if job_manager is None:
            raise HTTPException(status_code=503, detail="任务服务未配置")
        job = job_manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

    @router.get("/kb/documents")
    def documents(limit: int = 50, offset: int = 0, query: Optional[str] = None,
                  session: Dict[str, Any] = Depends(require_admin)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        if hasattr(document_service, "list_documents_page"):
            return document_service.list_documents_page(limit=safe_limit, offset=safe_offset, query=query)
        items = document_service.list_documents(limit=safe_limit, offset=safe_offset, query=query)
        return {"items": items, "total": None, "limit": safe_limit, "offset": safe_offset}

    @router.get("/kb/documents/{document_id}")
    def document_detail(document_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            return document_service.get_document(document_id)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))

    @router.get("/kb/documents/{document_id}/download")
    def download_document(document_id: str, session: Dict[str, Any] = Depends(require_admin)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            path = document_service.get_document_path(document_id)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        store.record_audit(actor=session["username"], action="kb.document.download", target=document_id, status="success")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, filename=path.name, media_type=media_type)

    @router.patch("/kb/documents/{document_id}")
    def rename_document(document_id: str, payload: AdminDocumentRenameRequest,
                        session: Dict[str, Any] = Depends(require_csrf)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            result = document_service.rename(document_id, payload.filename)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(actor=session["username"], action="kb.document.rename", target=document_id, status="success")
        return result

    @router.put("/kb/documents/{document_id}")
    def edit_document(document_id: str, payload: AdminDocumentEditRequest,
                      session: Dict[str, Any] = Depends(require_csrf)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            result = document_service.update_content(document_id, payload.content)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(actor=session["username"], action="kb.document.edit", target=document_id, status="success")
        return result

    @router.post("/kb/documents/upload", status_code=201)
    async def upload_document(file: UploadFile = File(...), session: Dict[str, Any] = Depends(require_csrf)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            result = document_service.upload(file.filename or "", file.file)
            if inspect.isawaitable(result):
                result = await result
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"上传失败: {error}")
        store.record_audit(actor=session["username"], action="kb.document.upload", target=result.get("document_id", "document"), status="success")
        return result

    @router.delete("/kb/documents/{document_id}")
    def delete_document(document_id: str, session: Dict[str, Any] = Depends(require_csrf)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            result = document_service.delete_document(document_id)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        store.record_audit(actor=session["username"], action="kb.document.delete", target=document_id, status="success")
        return result

    @router.post("/kb/documents/{document_id}/replace", status_code=202)
    async def replace_document(document_id: str, file: UploadFile = File(...), session: Dict[str, Any] = Depends(require_csrf)):
        if document_service is None:
            raise HTTPException(status_code=503, detail="文档服务未配置")
        try:
            result = document_service.replace(document_id, file.filename or "", file.file)
            if inspect.isawaitable(result):
                result = await result
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        store.record_audit(actor=session["username"], action="kb.document.replace", target=document_id, status="accepted")
        return result

    return router


def create_default_admin_store(data_dir: str) -> AdminControlStore:
    ttl = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "28800"))
    secure = os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
    return AdminControlStore(
        os.path.join(data_dir, "admin_control.db"),
        session_ttl_seconds=ttl,
        cookie_secure=secure,
    )
