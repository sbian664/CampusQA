import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router


class AdminMetricsTests(unittest.TestCase):
    def test_metrics_and_errors_are_protected_and_return_collected_state(self):
        temp_dir = TemporaryDirectory()
        store = AdminControlStore(Path(temp_dir.name) / "admin.db", username="admin", password_hash=hash_password("secret"))
        store.record_metric("api_requests", 3, duration_ms=12)
        store.record_error(request_id="req-1", path="/api/admin/overview", status_code=500, detail="safe error")
        app = FastAPI()
        app.include_router(create_admin_router(store), prefix="/api/admin")
        client = TestClient(app)
        self.assertEqual(client.get("/api/admin/metrics/summary").status_code, 401)
        login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
        summary = client.get("/api/admin/metrics/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["api_requests"], 3)
        errors = client.get("/api/admin/errors")
        self.assertEqual(errors.status_code, 200)
        self.assertEqual(errors.json()["items"][0]["request_id"], "req-1")
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
