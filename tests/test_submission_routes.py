import io
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router
from src.submission_routes import create_submission_router
from src.submissions import SubmissionService, SubmissionStatus


class FakeKnowledgeBase:
    def __init__(self):
        self.indexed = []

    def index_document(self, path: str):
        self.indexed.append(Path(path))


class FakeJobs:
    def __init__(self):
        self.work = None
        self.job_id = "job-submission-1"

    def submit(self, kind, work, target_id=None):
        assert kind == "publish_submission"
        assert target_id
        self.work = work
        return self.job_id

    def get(self, job_id):
        return {
            "job_id": job_id,
            "kind": "publish_submission",
            "status": "queued",
            "progress": 0,
            "phase": "starting",
            "message": "任务已排队",
        }


def make_app(tmp_path):
    store = AdminControlStore(
        tmp_path / "admin.db",
        username="admin",
        password_hash=hash_password("secret"),
    )
    kb = FakeKnowledgeBase()
    service = SubmissionService(
        db_path=tmp_path / "admin.db",
        submissions_dir=tmp_path / "submissions",
        documents_dir=tmp_path / "documents",
        kb_provider=lambda: kb,
        supported_formats={".md", ".txt", ".pdf", ".html"},
        max_file_size=1024,
        rate_limit_count=100,
        rate_limit_window_seconds=3600,
    )
    jobs = FakeJobs()
    app = FastAPI()
    app.include_router(create_submission_router(service), prefix="/api/submissions")
    app.include_router(
        create_admin_router(store, job_manager=jobs, submission_service=service),
        prefix="/api/admin",
    )
    return TestClient(app), service, kb, jobs


def test_public_submit_and_status_require_the_receipt_token(tmp_path):
    client, _, _, _ = make_app(tmp_path)

    response = client.post(
        "/api/submissions",
        files={"file": ("guide.md", io.BytesIO(b"# Guide"), "text/markdown")},
        data={"original_filename": "Campus Guide.md"},
    )

    assert response.status_code == 201
    receipt = response.json()
    assert receipt["status"] == SubmissionStatus.PENDING
    assert receipt["original_filename"] == "Campus Guide.md"
    assert client.get(f"/api/submissions/{receipt['submission_id']}/status").status_code == 403
    status = client.get(
        f"/api/submissions/{receipt['submission_id']}/status",
        headers={"X-Submission-Token": receipt["access_token"]},
    )
    assert status.status_code == 200
    assert status.json()["status"] == SubmissionStatus.PENDING
    assert "staging_relpath" not in status.json()
    assert "access_token_hash" not in status.json()


def test_admin_approval_requires_session_and_csrf_and_queues_publish(tmp_path):
    client, service, kb, jobs = make_app(tmp_path)
    receipt = client.post(
        "/api/submissions",
        files={"file": ("guide.txt", io.BytesIO(b"hello"), "text/plain")},
    ).json()

    assert client.get("/api/admin/submissions").status_code == 401
    login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
    csrf = login.json()["csrf_token"]
    assert client.post(
        f"/api/admin/submissions/{receipt['submission_id']}/approve"
    ).status_code == 403

    queued = client.post(
        f"/api/admin/submissions/{receipt['submission_id']}/approve",
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["job_id"] == jobs.job_id
    assert service.get_public_status(receipt["submission_id"], receipt["access_token"])["status"] == SubmissionStatus.IMPORTING
    detail = client.get(f"/api/admin/submissions/{receipt['submission_id']}")
    assert detail.status_code == 200
    assert detail.json()["job"]["progress"] == 0

    jobs.work(lambda *_: None)
    published = service.get_public_status(receipt["submission_id"], receipt["access_token"])
    assert published["status"] == SubmissionStatus.PUBLISHED
    assert len(kb.indexed) == 1
