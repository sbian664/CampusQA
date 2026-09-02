import unittest

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

    def test_legacy_sensitive_routes_require_admin_session(self):
        self.assertEqual(self.client.get("/api/llm-config").status_code, 401)
        self.assertEqual(self.client.post("/api/kb/scan").status_code, 401)
        self.assertEqual(self.client.post("/api/kb/rebuild").status_code, 401)


if __name__ == "__main__":
    unittest.main()
