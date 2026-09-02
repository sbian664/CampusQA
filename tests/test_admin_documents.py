import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router


class AdminDocumentRouteTests(unittest.TestCase):
    def test_document_list_is_protected_and_upload_uses_document_service(self):
        temp_dir = TemporaryDirectory()
        store = AdminControlStore(
            Path(temp_dir.name) / "admin.db",
            username="admin",
            password_hash=hash_password("secret"),
        )

        class FakeDocuments:
            def list_documents(self, **kwargs):
                return [{"document_id": "doc-1", "filename": "guide.md"}]

            def upload(self, filename, stream):
                self.uploaded = (filename, stream.read())
                return {"document_id": "doc-2", "filename": filename}

        documents = FakeDocuments()
        app = FastAPI()
        app.include_router(
            create_admin_router(store, document_service=documents),
            prefix="/api/admin",
        )
        client = TestClient(app)

        denied = client.get("/api/admin/kb/documents")
        self.assertEqual(denied.status_code, 401)

        login = client.post("/api/admin/auth/login", json={"username": "admin", "password": "secret"})
        csrf = login.json()["csrf_token"]
        listed = client.get("/api/admin/kb/documents")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["document_id"], "doc-1")

        uploaded = client.post(
            "/api/admin/kb/documents/upload",
            files={"file": ("guide.md", b"# Guide")},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(documents.uploaded, ("guide.md", b"# Guide"))
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
