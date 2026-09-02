import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        store = AdminControlStore(
            Path(self.temp_dir.name) / "admin.db",
            username="admin",
            password_hash=hash_password("secret"),
        )
        app = FastAPI()
        app.include_router(
            create_admin_router(
                store,
                search_provider=lambda query, top_k, rerank_enabled, filters: {
                    "query": query,
                    "duration_ms": 3,
                    "channels": {"vector": [], "bm25": [], "hybrid": [], "reranked": []},
                    "counts": {"vector": 0, "bm25": 0, "hybrid": 0, "reranked": 0},
                },
            ),
            prefix="/api/admin",
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_requires_csrf_and_returns_structured_channels(self):
        login = self.client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
        csrf = login.json()["csrf_token"]

        denied = self.client.post("/api/admin/kb/search", json={"query": "课程"})
        self.assertEqual(denied.status_code, 403)

        response = self.client.post(
            "/api/admin/kb/search",
            json={"query": "课程", "top_k": 3, "rerank_enabled": False},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("hybrid", response.json()["channels"])

    def test_index_job_returns_accepted_job_id(self):
        class FakeJobs:
            def submit(self, kind, work):
                self.kind = kind
                self.work = work
                return "job-1"

        jobs = FakeJobs()
        self.temp_dir.cleanup()
        temp_dir = TemporaryDirectory()
        store = AdminControlStore(Path(temp_dir.name) / "admin.db", username="admin", password_hash=hash_password("secret"))
        app = FastAPI()
        app.include_router(create_admin_router(store, job_manager=jobs), prefix="/api/admin")
        client = TestClient(app)
        login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})

        response = client.post(
            "/api/admin/kb/jobs/scan",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "job-1")
        temp_dir.cleanup()

    def test_session_feedback_is_csrf_protected_and_persisted(self):
        store = AdminControlStore(Path(self.temp_dir.name) / "feedback.db", username="admin", password_hash=hash_password("secret"))
        app = FastAPI()
        app.include_router(create_admin_router(store), prefix="/api/admin")
        client = TestClient(app)
        login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
        response = client.post(
            "/api/admin/sessions/session-1/feedback",
            json={"quality": "partial", "note": "缺少课程时间"},
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["quality"], "partial")
        self.assertEqual(store.get_feedback("session-1")["note"], "缺少课程时间")


if __name__ == "__main__":
    unittest.main()
