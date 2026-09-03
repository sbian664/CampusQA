import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.routes import create_admin_router
from src.admin.documents import AdminDocumentService


class AdminDocumentRouteTests(unittest.TestCase):
    def test_editing_text_document_updates_source_and_index(self):
        temp_dir = TemporaryDirectory()
        root = Path(temp_dir.name)
        path = root / "guide.md"
        path.write_text("# Before", encoding="utf-8")
        calls = []

        class FakeKB:
            metadata = {}

            def _update_document(self, file_path):
                calls.append(file_path)
                self.metadata[file_path] = {"chunk_count": 1}
                return True

            def _save_metadata(self): pass
            def _save_chunk_texts(self): pass
            def _save_store(self): pass

        service = AdminDocumentService(
            kb_provider=lambda: FakeKB(),
            documents_dir=temp_dir.name,
            supported_formats={".md", ".pdf"},
        )

        result = service.update_content(service._document_id(path), "# After")

        self.assertEqual(path.read_text(encoding="utf-8"), "# After")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(calls, [str(path)])
        temp_dir.cleanup()

    def test_editing_pdf_requires_replacement_upload(self):
        temp_dir = TemporaryDirectory()
        path = Path(temp_dir.name) / "guide.pdf"
        path.write_bytes(b"pdf")

        class FakeKB:
            metadata = {}

        service = AdminDocumentService(
            kb_provider=lambda: FakeKB(),
            documents_dir=temp_dir.name,
            supported_formats={".pdf"},
        )

        with self.assertRaisesRegex(ValueError, "替换文件"):
            service.update_content(service._document_id(path), "not a pdf")
        temp_dir.cleanup()

    def test_document_page_reports_total_and_relative_source(self):
        temp_dir = TemporaryDirectory()
        root = Path(temp_dir.name)
        (root / "guide.md").write_text("# Guide", encoding="utf-8")
        (root / "policy.md").write_text("# Policy", encoding="utf-8")

        class FakeKB:
            metadata = {}

        service = AdminDocumentService(
            kb_provider=lambda: FakeKB(),
            documents_dir=temp_dir.name,
            supported_formats={".md"},
        )

        page = service.list_documents_page(limit=1, offset=1)

        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["source"], "policy.md")
        temp_dir.cleanup()

    def test_document_pages_reuse_catalog_until_mutation_invalidates_it(self):
        temp_dir = TemporaryDirectory()
        root = Path(temp_dir.name)
        (root / "guide.md").write_text("# Guide", encoding="utf-8")

        class FakeKB:
            metadata = {}

        service = AdminDocumentService(
            kb_provider=lambda: FakeKB(),
            documents_dir=temp_dir.name,
            supported_formats={".md"},
        )

        self.assertEqual(service.list_documents_page()["total"], 1)
        (root / "policy.md").write_text("# Policy", encoding="utf-8")
        self.assertEqual(service.list_documents_page()["total"], 1)
        service.invalidate_catalog()
        self.assertEqual(service.list_documents_page()["total"], 2)
        temp_dir.cleanup()

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

            def update_content(self, document_id, content):
                self.edited = (document_id, content)
                return {"document_id": document_id, "status": "updated"}

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
        edited = client.put(
            "/api/admin/kb/documents/doc-1",
            json={"content": "# Edited"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(documents.edited, ("doc-1", "# Edited"))
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
