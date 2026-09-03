"""Anonymous knowledge-document submissions and publication workflow."""

from __future__ import annotations

import hashlib
import hmac
from html import unescape
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, Callable, Iterator, Optional

class SubmissionStatus:
    PENDING = "pending"
    REJECTED = "rejected"
    IMPORTING = "importing"
    PUBLISHED = "published"
    FAILED = "failed"


class SubmissionService:
    """Persist submissions separately from the live knowledge-base directory."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        connection: sqlite3.Connection | None = None,
        submissions_dir: str | Path,
        documents_dir: str | Path,
        kb_provider: Callable[[], Any],
        supported_formats: set[str],
        max_file_size: int = 20 * 1024 * 1024,
        rate_limit_count: int = 5,
        rate_limit_window_seconds: int = 3600,
        rate_limit_salt: str = "campusqa-submissions",
        mutation_lock: RLock | None = None,
    ) -> None:
        if connection is None and db_path is None:
            raise ValueError("db_path or connection is required")
        self._connection = connection
        self.db_path = Path(db_path) if db_path is not None else None
        self.submissions_dir = Path(submissions_dir).resolve()
        self.documents_dir = Path(documents_dir).resolve()
        self.kb_provider = kb_provider
        self.supported_formats = {item.lower() for item in supported_formats}
        self.max_file_size = max(1, int(max_file_size))
        self.rate_limit_count = max(1, int(rate_limit_count))
        self.rate_limit_window_seconds = max(1, int(rate_limit_window_seconds))
        self.rate_limit_salt = rate_limit_salt
        self.mutation_lock = mutation_lock or RLock()
        self.submissions_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._recovery_document_paths: list[Path] = []
        self._initialize()
        self._recover_interrupted_imports()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection
        owns_connection = connection is None
        if connection is None:
            connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if owns_connection:
                connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_submissions (
                    submission_id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    staging_relpath TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    access_token_hash TEXT NOT NULL,
                    client_key_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    error_message TEXT,
                    published_filename TEXT,
                    published_document_id TEXT,
                    job_id TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    published_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_submissions_status_created
                    ON knowledge_submissions(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_submissions_hash_status
                    ON knowledge_submissions(content_hash, status);
                CREATE INDEX IF NOT EXISTS idx_submissions_client_created
                    ON knowledge_submissions(client_key_hash, created_at DESC);
                """
            )

    def _recover_interrupted_imports(self) -> None:
        """Make submissions left in-flight by a process restart retryable."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_submissions WHERE status = ?",
                (SubmissionStatus.IMPORTING,),
            ).fetchall()
            connection.execute(
                """
                UPDATE knowledge_submissions
                SET status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE status = ?
                """,
                (
                    SubmissionStatus.FAILED,
                    "服务重启导致投稿发布任务中断",
                    self._now(),
                    SubmissionStatus.IMPORTING,
                ),
            )
        for row in rows:
            published_path = self.documents_dir / self._published_filename(row)
            self._recovery_document_paths.append(published_path)
            published_path.unlink(missing_ok=True)

    def cleanup_recovered_indexes(self) -> None:
        """Remove index entries for publications interrupted before DB commit."""
        with self.mutation_lock:
            if not self._recovery_document_paths:
                return
            remove_document = getattr(self.kb_provider(), "delete_document", None)
            if callable(remove_document):
                remaining = []
                for path in self._recovery_document_paths:
                    try:
                        remove_document(str(path))
                    except Exception:
                        remaining.append(path)
                self._recovery_document_paths = remaining
            else:
                self._recovery_document_paths.clear()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _client_hash(self, client_key: str) -> str:
        return self._hash(f"{self.rate_limit_salt}:{client_key or 'unknown'}")

    def _staging_path(self, row: sqlite3.Row | dict[str, Any]) -> Path:
        path = (self.submissions_dir / str(row["staging_relpath"])).resolve()
        if os.path.commonpath([str(self.submissions_dir), str(path)]) != str(self.submissions_dir):
            raise RuntimeError("invalid submission storage path")
        return path

    @staticmethod
    def _row_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        return dict(row)

    def _public_projection(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = self._row_dict(row)
        return {
            "submission_id": item["submission_id"],
            "status": item["status"],
            "original_filename": item["original_filename"],
            "file_size": item["file_size"],
            "extension": item["extension"],
            "rejection_reason": item["rejection_reason"],
            "error_message": item["error_message"],
            "created_at": item["created_at"],
            "reviewed_at": item["reviewed_at"],
            "published_at": item["published_at"],
        }

    def _public_status_projection(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = self._row_dict(row)
        return {
            "submission_id": item["submission_id"],
            "status": item["status"],
            "created_at": item["created_at"],
            "reviewed_at": item["reviewed_at"],
            "published_at": item["published_at"],
            "rejection_reason": item["rejection_reason"],
        }

    def _admin_projection(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = self._row_dict(row)
        item.pop("access_token_hash", None)
        item.pop("client_key_hash", None)
        item["staging_file_exists"] = self._staging_path(row).is_file()
        return item

    @staticmethod
    def _validate_content(extension: str, data: bytes) -> None:
        if extension in {".txt", ".md", ".html"}:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("document is not valid UTF-8") from error
            if extension == ".html":
                visible_text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
                visible_text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", visible_text, flags=re.IGNORECASE | re.DOTALL)
                visible_text = unescape(re.sub(r"<[^>]+>", " ", visible_text))
                if not visible_text.strip():
                    raise ValueError("document has no readable content")
            elif not text.strip():
                raise ValueError("document has no readable content")
        elif extension == ".pdf" and not data.startswith(b"%PDF"):
            raise ValueError("document is not a valid PDF")

    def submit(self, filename: str, stream: BinaryIO, *, client_key: str = "unknown") -> dict[str, Any]:
        if not filename or filename != os.path.basename(filename) or any(char in filename for char in "/\\"):
            raise ValueError("invalid filename")
        extension = Path(filename).suffix.lower()
        if extension not in self.supported_formats:
            raise ValueError(f"unsupported format: {extension}")

        data = stream.read(self.max_file_size + 1)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data:
            raise ValueError("empty file")
        if len(data) > self.max_file_size:
            raise ValueError("file size exceeds limit")

        content_hash = hashlib.sha256(data).hexdigest()
        client_hash = self._client_hash(client_key)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=self.rate_limit_window_seconds)).isoformat()

        submission_id = uuid.uuid4().hex
        access_token = secrets.token_urlsafe(32)
        submission_dir = self.submissions_dir / submission_id
        staging_path = submission_dir / f"original{extension}"
        submission_dir.mkdir(parents=True, exist_ok=False)
        try:
            staging_path.write_bytes(data)
            self._validate_content(extension, data)

            with self.mutation_lock, self._connect() as connection:
                if connection.execute(
                    "SELECT COUNT(*) FROM knowledge_submissions WHERE client_key_hash = ? AND created_at >= ?",
                    (client_hash, cutoff),
                ).fetchone()[0] >= self.rate_limit_count:
                    raise ValueError("submission rate limit exceeded")
                duplicate = connection.execute(
                    """
                    SELECT 1 FROM knowledge_submissions
                    WHERE content_hash = ? AND status IN (?, ?, ?, ?)
                    LIMIT 1
                    """,
                    (
                        content_hash,
                        SubmissionStatus.PENDING,
                        SubmissionStatus.IMPORTING,
                        SubmissionStatus.FAILED,
                        SubmissionStatus.PUBLISHED,
                    ),
                ).fetchone()
                if duplicate:
                    raise ValueError("duplicate submission")
                connection.execute(
                    """
                    INSERT INTO knowledge_submissions(
                        submission_id, original_filename, extension, staging_relpath,
                        content_hash, access_token_hash, client_key_hash, file_size,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        filename,
                        extension,
                        f"{submission_id}/original{extension}",
                        content_hash,
                        self._hash(access_token),
                        client_hash,
                        len(data),
                        SubmissionStatus.PENDING,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM knowledge_submissions WHERE submission_id = ?",
                    (submission_id,),
                ).fetchone()
            result = self._public_projection(row)
            result["access_token"] = access_token
            return result
        except Exception:
            shutil.rmtree(submission_dir, ignore_errors=True)
            raise

    def get_public_status(self, submission_id: str, access_token: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        if not row:
            raise LookupError("submission not found")
        if not access_token or not hmac.compare_digest(row["access_token_hash"], self._hash(access_token)):
            raise PermissionError("invalid submission token")
        return self._public_status_projection(row)

    def list_admin(self, *, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        query = "SELECT * FROM knowledge_submissions"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            total_query = "SELECT COUNT(*) FROM knowledge_submissions" + (" WHERE status = ?" if status else "")
            total = connection.execute(total_query, [status] if status else []).fetchone()[0]
        return {"items": [self._admin_projection(row) for row in rows], "limit": limit, "offset": offset, "total": total}

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM knowledge_submissions GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in (SubmissionStatus.PENDING, SubmissionStatus.REJECTED, SubmissionStatus.IMPORTING, SubmissionStatus.PUBLISHED, SubmissionStatus.FAILED)}
        counts.update({row["status"]: row["count"] for row in rows})
        return {"counts": counts, "pending": counts[SubmissionStatus.PENDING], "total": sum(counts.values())}

    def get_admin(self, submission_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        if not row:
            raise LookupError("submission not found")
        result = self._admin_projection(row)
        path = self._staging_path(row)
        try:
            from src.document_loader import DocumentLoader

            docs = DocumentLoader(base_dir=str(path.parent)).load_file(str(path))
            result["content"] = "\n\n".join(str(doc.page_content) for doc in docs)[:20000]
        except Exception as error:
            result["content"] = ""
            result["content_error"] = f"{type(error).__name__}: {error}"
        return result

    def mark_importing(self, submission_id: str, *, allow_failed: bool = False) -> dict[str, Any]:
        allowed = [SubmissionStatus.PENDING]
        if allow_failed:
            allowed.append(SubmissionStatus.FAILED)
        placeholders = ", ".join("?" for _ in allowed)
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE knowledge_submissions SET status = ?, error_message = NULL, reviewed_at = COALESCE(reviewed_at, ?), updated_at = ? WHERE submission_id = ? AND status IN ({placeholders})",
                [SubmissionStatus.IMPORTING, now, now, submission_id, *allowed],
            )
            if cursor.rowcount == 0:
                row = connection.execute("SELECT status FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
                if not row:
                    raise LookupError("submission not found")
                raise ValueError(f"submission cannot be imported from status {row['status']}")
            row = connection.execute("SELECT * FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
        return self._admin_projection(row)

    def attach_job(self, submission_id: str, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE knowledge_submissions SET job_id = ?, updated_at = ? WHERE submission_id = ?",
                (job_id, self._now(), submission_id),
            )

    def reset_pending(self, submission_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE knowledge_submissions SET status = ?, updated_at = ? WHERE submission_id = ? AND status = ?",
                (SubmissionStatus.PENDING, self._now(), submission_id, SubmissionStatus.IMPORTING),
            )

    def reject(self, submission_id: str, reason: str) -> dict[str, Any]:
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("rejection reason is required")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_submissions
                SET status = ?, rejection_reason = ?, reviewed_at = ?, updated_at = ?
                WHERE submission_id = ? AND status = ?
                """,
                (SubmissionStatus.REJECTED, reason, self._now(), self._now(), submission_id, SubmissionStatus.PENDING),
            )
            if cursor.rowcount == 0:
                row = connection.execute("SELECT status FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
                if not row:
                    raise LookupError("submission not found")
                raise ValueError(f"submission cannot be rejected from status {row['status']}")
            row = connection.execute("SELECT * FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
        return self._admin_projection(row)

    def _published_filename(self, row: sqlite3.Row) -> str:
        stem = Path(row["original_filename"]).stem
        stem = re.sub(r"[^\w-]+", "_", stem, flags=re.UNICODE).strip("_")[:60] or "document"
        return f"submission_{row['submission_id']}_{stem}{row['extension']}"

    @staticmethod
    def _document_id(filename: str) -> str:
        return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]

    def publish(self, submission_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
        if not row:
            raise LookupError("submission not found")
        if row["status"] == SubmissionStatus.PUBLISHED:
            return self._admin_projection(row)
        if row["status"] not in {SubmissionStatus.PENDING, SubmissionStatus.IMPORTING, SubmissionStatus.FAILED}:
            raise ValueError(f"submission cannot be published from status {row['status']}")

        staging_path = self._staging_path(row)
        if not staging_path.is_file():
            self._mark_failed(submission_id, "staging file not found")
            raise FileNotFoundError("staging file not found")
        published_filename = self._published_filename(row)
        published_path = self.documents_dir / published_filename
        published_path.unlink(missing_ok=True)

        kb = self.kb_provider()
        try:
            with self.mutation_lock:
                shutil.copyfile(staging_path, published_path)
                try:
                    kb.index_document(str(published_path))
                except Exception:
                    remove_document = getattr(kb, "delete_document", None)
                    if callable(remove_document):
                        try:
                            remove_document(str(published_path))
                        except Exception:
                            pass
                    raise
            now = self._now()
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_submissions
                    SET status = ?, published_filename = ?, published_document_id = ?,
                        published_at = ?, error_message = NULL, updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (SubmissionStatus.PUBLISHED, published_filename, self._document_id(published_filename), now, now, submission_id),
                )
                row = connection.execute("SELECT * FROM knowledge_submissions WHERE submission_id = ?", (submission_id,)).fetchone()
            return self._admin_projection(row)
        except Exception as error:
            with self.mutation_lock:
                remove_document = getattr(kb, "delete_document", None)
                if callable(remove_document):
                    try:
                        remove_document(str(published_path))
                    except Exception:
                        pass
            published_path.unlink(missing_ok=True)
            self._mark_failed(submission_id, str(error))
            raise

    def _mark_failed(self, submission_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE knowledge_submissions SET status = ?, error_message = ?, updated_at = ? WHERE submission_id = ?",
                (SubmissionStatus.FAILED, message[:1000], self._now(), submission_id),
            )
