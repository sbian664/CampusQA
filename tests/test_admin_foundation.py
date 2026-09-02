import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password, verify_password
from src.admin.routes import create_admin_router


class AdminFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "admin_control.db"
        self.store = AdminControlStore(
            self.db_path,
            username="admin",
            password_hash=hash_password("correct horse battery staple"),
            session_ttl_seconds=3600,
        )
        app = FastAPI()
        app.include_router(create_admin_router(self.store), prefix="/api/admin")
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_password_is_verified_without_storing_plaintext(self):
        encoded = hash_password("secret")

        self.assertTrue(verify_password("secret", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        self.assertNotIn("secret", encoded)

    def test_login_creates_http_only_session_and_csrf_token(self):
        response = self.client.post(
            "/api/admin/auth/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "admin")
        self.assertTrue(response.json()["csrf_token"])
        self.assertNotIn("password", response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

        me = self.client.get("/api/admin/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "admin")

    def test_unauthenticated_me_and_csrf_protected_logout_are_rejected(self):
        self.assertEqual(self.client.get("/api/admin/auth/me").status_code, 401)

        login = self.client.post(
            "/api/admin/auth/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        csrf_token = login.json()["csrf_token"]

        self.assertEqual(self.client.post("/api/admin/auth/logout").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/admin/auth/logout",
                headers={"X-CSRF-Token": csrf_token},
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/admin/auth/me").status_code, 401)

    def test_audit_details_are_redacted_before_persistence(self):
        self.store.record_audit(
            actor="admin",
            action="llm_config.update",
            target="runtime",
            status="success",
            request_id="req-1",
            details={"api_key": "sk-live-secret", "password": "dont-save", "provider": "deepseek"},
        )

        event = self.store.list_audit(limit=1)[0]
        details = json.dumps(event["details"], ensure_ascii=False)
        self.assertNotIn("sk-live-secret", details)
        self.assertNotIn("dont-save", details)
        self.assertIn("deepseek", details)

    def test_repeated_login_failures_are_rate_limited(self):
        for _ in range(5):
            response = self.client.post(
                "/api/admin/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post(
            "/api/admin/auth/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        self.assertEqual(blocked.status_code, 429)


if __name__ == "__main__":
    unittest.main()
