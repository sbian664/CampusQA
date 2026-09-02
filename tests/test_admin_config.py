import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router


class AdminConfigRouteTests(unittest.TestCase):
    def test_config_is_protected_and_key_is_not_returned(self):
        temp_dir = TemporaryDirectory()
        store = AdminControlStore(Path(temp_dir.name) / "admin.db", username="admin", password_hash=hash_password("secret"))

        class FakeConfig:
            def public_llm(self):
                return {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-a...-key", "has_api_key": True}

            def update_llm(self, payload):
                self.updated = payload
                return self.public_llm()

            def test_llm(self, payload):
                return {"status": "ok"}

            def runtime_config(self):
                return {"agent_mode": True, "reranker_enabled": None, "context_router_enabled": None}

            def update_runtime(self, payload):
                self.runtime = payload
                return self.runtime_config()

            def toggle_mode(self):
                return {"agent_mode": False, "mode_name": "一步式 RAG"}

        config = FakeConfig()
        app = FastAPI()
        app.include_router(create_admin_router(store, config_service=config), prefix="/api/admin")
        client = TestClient(app)

        self.assertEqual(client.get("/api/admin/llm-config").status_code, 401)
        login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
        csrf = login.json()["csrf_token"]
        response = client.get("/api/admin/llm-config")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sk-real-secret", response.text)
        self.assertTrue(response.json()["has_api_key"])

        updated = client.put(
            "/api/admin/llm-config",
            json={"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "api_key": ""},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(config.updated["api_key"], "")


if __name__ == "__main__":
    unittest.main()
