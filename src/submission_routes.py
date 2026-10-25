"""Authenticated API for user-owned knowledge-document submissions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile

from src.submissions import SubmissionService
from src.file_processing import run_file_work
from src.errors import FileLimitError
from src.identity import require_user_id


def create_submission_router(service: SubmissionService) -> APIRouter:
    router = APIRouter()

    @router.post("", status_code=201)
    async def submit_document(
        request: Request,
        file: UploadFile = File(...),
        original_filename: str | None = Form(default=None),
    ) -> dict[str, Any]:
        user_id = require_user_id()
        try:
            filename = original_filename or file.filename or ""
            return await run_file_work(service.submit, filename, file.file, owner_user_id=user_id)
        except FileLimitError as error:
            raise HTTPException(status_code=413, detail=str(error))
        except ValueError as error:
            message = str(error)
            status_code = 429 if 'rate limit' in message else 413 if "size" in message else 400
            raise HTTPException(status_code=status_code, detail=message)
        except Exception:
            raise HTTPException(status_code=422, detail="document validation failed")

    @router.get('')
    def my_submissions(limit: int = 50, offset: int = 0):
        return service.list_owned(require_user_id(), limit=limit, offset=offset)

    @router.get("/{submission_id}/status")
    def submission_status(
        submission_id: str,
        submission_token: str | None = Header(default=None, alias="X-Submission-Token"),
    ) -> dict[str, Any]:
        try:
            return service.get_owned_status(submission_id, require_user_id())
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error))

    return router
