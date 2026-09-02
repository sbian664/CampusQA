"""Small SQLite-backed store for admin sessions and operational metadata."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PBKDF2_ITERATIONS = 260_000
REDACTED = "[REDACTED]"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(value: Any, key: str = "") -> Any:
    sensitive = ("password", "api_key", "token", "cookie", "secret")
    if any(part in key.lower() for part in sensitive):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


class AdminControlStore:
    """Persist admin identity, browser sessions and audit records.

    The store deliberately keeps only hashes for passwords, session cookies and
    CSRF tokens. Runtime data lives under the backend data directory and is not
    source-controlled.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        username: Optional[str] = None,
        password_hash: Optional[str] = None,
        initial_password: Optional[str] = None,
        session_ttl_seconds: int = 28_800,
        cookie_secure: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.username = username or os.getenv("ADMIN_USERNAME", "admin")
        self.password_hash = password_hash or os.getenv("ADMIN_PASSWORD_HASH", "")
        initial = initial_password or os.getenv("ADMIN_INITIAL_PASSWORD", "")
        if not self.password_hash and initial:
            self.password_hash = hash_password(initial)
        self.session_ttl_seconds = session_ttl_seconds
        self.cookie_secure = cookie_secure
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    session_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_id TEXT,
                    duration_ms INTEGER,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_timestamp
                    ON admin_audit_logs(timestamp DESC);
                CREATE TABLE IF NOT EXISTS admin_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_admin_jobs_created
                    ON admin_jobs(created_at DESC);
                CREATE TABLE IF NOT EXISTS admin_login_attempts (
                    username TEXT PRIMARY KEY,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    first_failed_at TEXT,
                    locked_until TEXT
                );
                CREATE TABLE IF NOT EXISTS admin_settings (
                    setting_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_metrics (
                    metric_key TEXT PRIMARY KEY,
                    counter INTEGER NOT NULL DEFAULT 0,
                    total_duration_ms INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    request_id TEXT,
                    path TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_errors_timestamp
                    ON admin_errors(timestamp DESC);
                CREATE TABLE IF NOT EXISTS admin_feedback (
                    session_id TEXT PRIMARY KEY,
                    quality TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            if self.password_hash:
                now = _iso_now()
                connection.execute(
                    """
                    INSERT INTO admin_users(username, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        password_hash=excluded.password_hash,
                        updated_at=excluded.updated_at
                    """,
                    (self.username, self.password_hash, now, now),
                )

    def authenticate(self, username: str, password: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def login_allowed(self, username: str, *, max_failures: int = 5, window_seconds: int = 300) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failed_count, first_failed_at, locked_until FROM admin_login_attempts WHERE username = ?",
                (username,),
            ).fetchone()
        if not row:
            return True
        now = _utc_now()
        if row["locked_until"]:
            try:
                if datetime.fromisoformat(row["locked_until"]) > now:
                    return False
            except ValueError:
                pass
        if row["first_failed_at"]:
            try:
                if datetime.fromisoformat(row["first_failed_at"]) + timedelta(seconds=window_seconds) <= now:
                    self.clear_login_failures(username)
                    return True
            except ValueError:
                self.clear_login_failures(username)
                return True
        return int(row["failed_count"] or 0) < max_failures

    def record_login_failure(self, username: str, *, max_failures: int = 5, lock_seconds: int = 300) -> None:
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failed_count, first_failed_at FROM admin_login_attempts WHERE username = ?",
                (username,),
            ).fetchone()
            count = int(row["failed_count"] or 0) + 1 if row else 1
            first = row["first_failed_at"] if row and row["first_failed_at"] else now.isoformat()
            locked = (now + timedelta(seconds=lock_seconds)).isoformat() if count >= max_failures else None
            connection.execute(
                """
                INSERT INTO admin_login_attempts(username, failed_count, first_failed_at, locked_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    failed_count=excluded.failed_count,
                    first_failed_at=excluded.first_failed_at,
                    locked_until=excluded.locked_until
                """,
                (username, count, first, locked),
            )

    def clear_login_failures(self, username: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM admin_login_attempts WHERE username = ?", (username,))

    def create_session(self, username: str) -> Dict[str, Any]:
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        now = _utc_now()
        expires = now + timedelta(seconds=self.session_ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_sessions(
                    session_hash, username, csrf_hash, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _hash_token(session_token),
                    username,
                    _hash_token(csrf_token),
                    now.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                ),
            )
        return {"session_token": session_token, "csrf_token": csrf_token, "expires_at": expires.isoformat()}

    def get_session(self, session_token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM admin_sessions WHERE session_hash = ?",
                (_hash_token(session_token),),
            ).fetchone()
            if not row:
                return None
            try:
                expired = datetime.fromisoformat(row["expires_at"]) <= _utc_now()
            except (TypeError, ValueError):
                expired = True
            if expired:
                connection.execute("DELETE FROM admin_sessions WHERE session_hash = ?", (_hash_token(session_token),))
                return None
            connection.execute(
                "UPDATE admin_sessions SET last_seen_at = ? WHERE session_hash = ?",
                (_iso_now(), _hash_token(session_token)),
            )
        return dict(row)

    def verify_csrf(self, session_token: Optional[str], csrf_token: Optional[str]) -> bool:
        session = self.get_session(session_token)
        return bool(session and csrf_token and hmac.compare_digest(session["csrf_hash"], _hash_token(csrf_token)))

    def revoke_session(self, session_token: Optional[str]) -> None:
        if not session_token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM admin_sessions WHERE session_hash = ?", (_hash_token(session_token),))

    def record_audit(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        status: str,
        request_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_details = _redact(details or {})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_audit_logs(
                    timestamp, actor, action, target, status, request_id, duration_ms, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso_now(), actor, action, target, status, request_id, duration_ms,
                    json.dumps(safe_details, ensure_ascii=False),
                ),
            )

    def list_audit(self, limit: int = 50, offset: int = 0) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, timestamp, actor, action, target, status,
                       request_id, duration_ms, details_json
                FROM admin_audit_logs
                ORDER BY id DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except (TypeError, ValueError):
                item["details"] = {}
                item.pop("details_json", None)
            result.append(item)
        return result

    def create_job(self, kind: str) -> str:
        job_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_jobs(job_id, kind, status, created_at)
                VALUES (?, ?, 'queued', ?)
                """,
                (job_id, kind, _iso_now()),
            )
        return job_id

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "progress", "phase", "message", "result", "error", "started_at", "finished_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        columns = []
        values = []
        for key, value in updates.items():
            column = "result_json" if key == "result" else key
            columns.append(f"{column} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if key == "result" else value)
        values.append(job_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE admin_jobs SET {', '.join(columns)} WHERE job_id = ?", values)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM admin_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        except (TypeError, ValueError):
            item["result"] = None
            item.pop("result_json", None)
        return item

    def list_jobs(self, limit: int = 50, offset: int = 0) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM admin_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [job for row in rows if (job := self.get_job(row["job_id"]))]

    def mark_running_jobs_interrupted(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE admin_jobs
                SET status = 'interrupted', error = '服务重启导致任务中断', finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (_iso_now(),),
            )
            return cursor.rowcount

    def get_setting(self, key: str) -> Any:
        with self._connect() as connection:
            row = connection.execute("SELECT value_json FROM admin_settings WHERE setting_key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set_setting(self, key: str, value: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_settings(setting_key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), _iso_now()),
            )

    def record_metric(self, key: str, value: int = 1, *, duration_ms: int = 0) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_metrics(metric_key, counter, total_duration_ms, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(metric_key) DO UPDATE SET
                    counter=counter + excluded.counter,
                    total_duration_ms=total_duration_ms + excluded.total_duration_ms,
                    updated_at=excluded.updated_at
                """,
                (key, int(value), max(0, int(duration_ms)), _iso_now()),
            )

    def metrics_summary(self) -> Dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT metric_key, counter, total_duration_ms FROM admin_metrics").fetchall()
        summary = {row["metric_key"]: int(row["counter"]) for row in rows}
        requests = next((row for row in rows if row["metric_key"] == "api_requests"), None)
        summary["average_response_ms"] = round(requests["total_duration_ms"] / requests["counter"], 2) if requests and requests["counter"] else None
        return summary

    def record_error(self, *, request_id: Optional[str], path: str, status_code: int, detail: str) -> None:
        safe_detail = str(detail or "请求失败")[:500]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO admin_errors(timestamp, request_id, path, status_code, detail) VALUES (?, ?, ?, ?, ?)",
                (_iso_now(), request_id, str(path)[:300], int(status_code), safe_detail),
            )

    def list_errors(self, limit: int = 50) -> list[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp, request_id, path, status_code, detail FROM admin_errors ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_feedback(self, session_id: str, quality: str, note: str, actor: str) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_feedback(session_id, quality, note, actor, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    quality=excluded.quality, note=excluded.note,
                    actor=excluded.actor, updated_at=excluded.updated_at
                """,
                (session_id, quality, note[:2000], actor, _iso_now()),
            )
        return self.get_feedback(session_id) or {}

    def get_feedback(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_id, quality, note, actor, updated_at FROM admin_feedback WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None
