import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class AdminServerMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def test_admin_routes_are_mounted_and_require_authentication(self):
        response = self.client.get("/api/admin/auth/me")

        self.assertEqual(response.status_code, 401)

    def test_public_health_route_remains_available(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_admin_response_has_request_id(self):
        response = self.client.get("/api/admin/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.headers.get("X-Request-ID"))

    def test_user_llm_config_requires_login(self):
        response = self.client.get("/api/llm-config")

        self.assertEqual(response.status_code, 401)


    def test_user_llm_config_write_and_test_require_login(self):
        invalid_payload = {
            "provider": "unsupported-provider",
            "api_key": "",
            "model": "model",
            "base_url": "https://api.example.com/v1",
        }

        save_response = self.client.put("/api/llm-config", json=invalid_payload)
        test_response = self.client.post("/api/llm-config/test", json=invalid_payload)

        self.assertEqual(save_response.status_code, 401)
        self.assertEqual(test_response.status_code, 401)

    def test_anonymous_cannot_delete_a_session(self):
        response = self.client.delete(f"/api/sessions/{uuid4().hex}")

        self.assertEqual(response.status_code, 401)

    def test_user_session_delete_rejects_unsafe_session_id(self):
        response = self.client.delete("/api/sessions/invalid%20session")

        self.assertEqual(response.status_code, 401)

    def test_anonymous_cannot_clear_a_session(self):
        with patch("server.Session") as session_class:
            session_class.return_value.load.return_value = True
            response = self.client.delete(f"/api/session/{uuid4().hex}")

        self.assertEqual(response.status_code, 401)

    def test_anonymous_cannot_delete_a_message(self):
        response = self.client.delete(f"/api/session/{uuid4().hex}/message/0")

        self.assertEqual(response.status_code, 401)

    def test_user_session_mutations_reject_unsafe_session_id(self):
        clear_response = self.client.delete("/api/session/invalid%20session")
        message_response = self.client.delete("/api/session/invalid%20session/message/0")

        self.assertEqual(clear_response.status_code, 401)
        self.assertEqual(message_response.status_code, 401)

    def test_user_cannot_toggle_global_mode(self):
        chatbot = type("ChatbotStub", (), {"agent_mode": False})()
        with patch("server.get_chatbot", return_value=chatbot):
            response = self.client.post("/api/mode/toggle")

        self.assertEqual(response.status_code, 401)

    def test_legacy_sensitive_routes_require_admin_session(self):
        self.assertEqual(self.client.post("/api/upload").status_code, 401)
        self.assertEqual(self.client.post("/api/upload-legacy").status_code, 401)
        self.assertEqual(self.client.post("/api/kb/scan").status_code, 401)
        self.assertEqual(self.client.post("/api/kb/rebuild").status_code, 401)


if __name__ == "__main__":
    unittest.main()
