"""Public API for anonymous knowledge-document submissions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile

from src.submissions import SubmissionService


def create_submission_router(service: SubmissionService) -> APIRouter:
    router = APIRouter()

    @router.post("", status_code=201)
    async def submit_document(
        request: Request,
        file: UploadFile = File(...),
        original_filename: str | None = Form(default=None),
    ) -> dict[str, Any]:
        client_key = request.client.host if request.client else "unknown"
        try:
            filename = original_filename or file.filename or ""
            return service.submit(filename, file.file, client_key=client_key)
        except ValueError as error:
            message = str(error)
            status_code = 413 if "size" in message else 400
            raise HTTPException(status_code=status_code, detail=message)
        except Exception:
            raise HTTPException(status_code=422, detail="document validation failed")

    @router.get("/{submission_id}/status")
    def submission_status(
        submission_id: str,
        submission_token: str | None = Header(default=None, alias="X-Submission-Token"),
    ) -> dict[str, Any]:
        try:
            return service.get_public_status(submission_id, submission_token or "")
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error))

    return router
