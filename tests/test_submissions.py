import hashlib
import io
import sqlite3
from pathlib import Path

import pytest

from src.submissions import SubmissionService, SubmissionStatus


class FakeKnowledgeBase:
    def __init__(self, documents_dir: Path, fail=False):
        self.documents_dir = documents_dir
        self.fail = fail
        self.indexed = []
        self.deleted = []

    def index_document(self, file_path: str):
        if self.fail:
            raise RuntimeError("embedding failed")
        self.indexed.append(Path(file_path))

    def delete_document(self, file_path: str):
        self.deleted.append(Path(file_path))


def make_service(tmp_path, *, fail=False):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    kb = FakeKnowledgeBase(tmp_path / "documents")
    service = SubmissionService(
        connection=connection,
        submissions_dir=tmp_path / "submissions",
        documents_dir=tmp_path / "documents",
        kb_provider=lambda: kb,
        supported_formats={".md", ".txt", ".pdf", ".html"},
        max_file_size=1024,
        rate_limit_count=100,
        rate_limit_window_seconds=3600,
    )
    if fail:
        kb.fail = True
    return service, kb


def test_submit_stays_out_of_knowledge_base_and_returns_one_time_token(tmp_path):
    service, kb = make_service(tmp_path)

    result = service.submit("Campus Guide.md", io.BytesIO(b"# Campus Guide\nWelcome"), client_key="test")

    assert result["status"] == SubmissionStatus.PENDING
    assert result["access_token"]
    assert not kb.indexed
    assert not list((tmp_path / "documents").glob("*"))
    assert (tmp_path / "submissions" / result["submission_id"] / "original.md").exists()


def test_status_requires_the_original_access_token(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.submit("guide.txt", io.BytesIO(b"hello"), client_key="test")

    status = service.get_public_status(result["submission_id"], result["access_token"])

    assert status["status"] == SubmissionStatus.PENDING
    with pytest.raises(PermissionError):
        service.get_public_status(result["submission_id"], "wrong-token")


def test_active_duplicate_hash_is_rejected(tmp_path):
    service, _ = make_service(tmp_path)
    content = b"same content"
    service.submit("one.txt", io.BytesIO(content), client_key="test")

    with pytest.raises(ValueError, match="duplicate"):
        service.submit("two.txt", io.BytesIO(content), client_key="test")


def test_reject_keeps_submission_out_of_knowledge_base(tmp_path):
    service, kb = make_service(tmp_path)
    result = service.submit("guide.txt", io.BytesIO(b"hello"), client_key="test")

    rejected = service.reject(result["submission_id"], "来源无法确认")

    assert rejected["status"] == SubmissionStatus.REJECTED
    assert rejected["rejection_reason"] == "来源无法确认"
    assert not kb.indexed


def test_publish_indexes_unique_formal_file_and_marks_published(tmp_path):
    service, kb = make_service(tmp_path)
    result = service.submit("guide.txt", io.BytesIO(b"hello"), client_key="test")

    published = service.publish(result["submission_id"])

    assert published["status"] == SubmissionStatus.PUBLISHED
    assert len(kb.indexed) == 1
    assert kb.indexed[0].parent == tmp_path / "documents"
    assert result["submission_id"] in kb.indexed[0].name


def test_published_submission_appears_in_previously_cached_document_list(tmp_path):
    from src.admin.documents import AdminDocumentService

    service, kb = make_service(tmp_path)
    documents = AdminDocumentService(kb_provider=lambda: kb, documents_dir=str(service.documents_dir),
                                     supported_formats={'.txt'}, mutation_lock=service.mutation_lock)
    service.document_service = documents
    assert documents.list_documents_page(query='official')['total'] == 0
    submission = service.submit('official_guide.txt', io.BytesIO(b'hello'))

    service.publish(submission['submission_id'])

    page = documents.list_documents_page(query='official')
    assert page['total'] == 1
    assert submission['submission_id'] in page['items'][0]['filename']


def test_failed_publish_preserves_staging_file_and_can_be_retried(tmp_path):
    service, kb = make_service(tmp_path, fail=True)
    result = service.submit("guide.txt", io.BytesIO(b"hello"), client_key="test")

    with pytest.raises(RuntimeError, match="embedding failed"):
        service.publish(result["submission_id"])

    assert service.get_public_status(result["submission_id"], result["access_token"])["status"] == SubmissionStatus.FAILED
    assert (tmp_path / "submissions" / result["submission_id"] / "original.txt").exists()
    assert not list((tmp_path / "documents").glob("*"))

    kb.fail = False
    assert service.publish(result["submission_id"])["status"] == SubmissionStatus.PUBLISHED


def test_publish_does_not_destroy_existing_same_name_file(tmp_path):
    service, kb = make_service(tmp_path, fail=True)
    submitted = service.submit('guide.txt', io.BytesIO(b'new'))
    row = service._connection.execute('SELECT * FROM knowledge_submissions WHERE submission_id = ?', (submitted['submission_id'],)).fetchone()
    target = service.documents_dir / service._published_filename(row)
    target.write_bytes(b'original')
    with pytest.raises(Exception):
        service.publish(submitted['submission_id'])
    assert target.read_bytes() == b'original'


def test_publish_obeys_file_worker_bound_and_retrieval_barrier(tmp_path):
    import src.file_processing as processing
    from src.index_state import IndexState
    service, kb = make_service(tmp_path)
    submitted = service.submit('guide.txt', io.BytesIO(b'new'))
    kb.index_state = IndexState(tmp_path / 'state.json')
    def index(path):
        assert getattr(processing._local, 'active', False)
        assert kb.index_state.writing
    kb.index_document = index
    service.publish(submitted['submission_id'])


def test_successful_publication_is_not_reversed_by_backup_cleanup_failure(tmp_path):
    import shutil
    from unittest.mock import patch
    service, kb = make_service(tmp_path)
    submitted = service.submit('guide.txt', io.BytesIO(b'new'))
    remove = shutil.rmtree
    backups = []
    def fail(path):
        backups.append(path)
        raise OSError('backup file busy')
    try:
        with patch('src.admin.documents.shutil.rmtree', side_effect=fail):
            result = service.publish(submitted['submission_id'])
        assert result['status'] == SubmissionStatus.PUBLISHED
        assert service.get_admin(submitted['submission_id'])['status'] == SubmissionStatus.PUBLISHED
    finally:
        for backup in backups:
            remove(backup, ignore_errors=True)


def test_invalid_extension_and_size_are_rejected(tmp_path):
    service, _ = make_service(tmp_path)

    with pytest.raises(ValueError, match="format"):
        service.submit("script.exe", io.BytesIO(b"x"), client_key="test")
    with pytest.raises(ValueError, match="size"):
        service.submit("large.txt", io.BytesIO(b"x" * 1025), client_key="test")
    with pytest.raises(ValueError, match="readable"):
        service.submit("script.html", io.BytesIO(b"<script>alert(1)</script>"), client_key="test")


def test_restart_marks_interrupted_import_as_failed_and_retryable(tmp_path):
    kb = FakeKnowledgeBase(tmp_path / "documents")
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
    result = service.submit("guide.txt", io.BytesIO(b"hello"), client_key="test")
    service.mark_importing(result["submission_id"])
    formal_path = tmp_path / "documents" / f"submission_{result['submission_id']}_guide.txt"
    formal_path.write_bytes(b"hello")

    restarted = SubmissionService(
        db_path=tmp_path / "admin.db",
        submissions_dir=tmp_path / "submissions",
        documents_dir=tmp_path / "documents",
        kb_provider=lambda: kb,
        supported_formats={".md", ".txt", ".pdf", ".html"},
        max_file_size=1024,
        rate_limit_count=100,
        rate_limit_window_seconds=3600,
    )

    status = restarted.get_public_status(result["submission_id"], result["access_token"])
    assert status["status"] == SubmissionStatus.FAILED
    assert "error_message" not in status
    assert not formal_path.exists()
    restarted.cleanup_recovered_indexes()
    assert formal_path in kb.deleted
    assert restarted.mark_importing(result["submission_id"], allow_failed=True)["status"] == SubmissionStatus.IMPORTING
