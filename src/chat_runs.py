"""Background chat runs with durable metadata and bounded in-memory event replay."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, Optional
from uuid import UUID


class RunConflict(Exception):
    """A run ID was reused for a different request or session run is active."""


class RunNotFound(Exception):
    """No run exists for the requested ID."""


class RunCapacityError(Exception):
    """The server has reached its active run limit."""


TERMINAL_STATES = {"succeeded", "cancelled", "error", "interrupted"}


class _CancellationToken:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._response = None

    def is_set(self):
        return self._event.is_set()

    def wait(self, timeout=None):
        return self._event.wait(timeout)

    def register(self, response):
        with self._lock:
            self._response = response
            cancelled = self._event.is_set()
        if cancelled and response is not None:
            try:
                response.close()
            except Exception:
                pass

    def set(self):
        self._event.set()
        with self._lock:
            response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


class _Run:
    def __init__(self, run_id: str, session_id: Optional[str], payload_hash: str, payload: dict, expires_at=None):
        self.run_id = run_id
        self.session_id = session_id
        self.payload_hash = payload_hash
        self.payload = payload
        self.status = "queued"
        self.result = None
        self.error = None
        self.cancel_event = _CancellationToken()
        self.events = deque(maxlen=256)
        self.text = ""
        self.next_event_id = 1
        self.condition = threading.Condition()
        self.started_at = time.time()
        self.logged = False
        self.expires_at = expires_at or time.time() + 86400


class ChatRunManager:
    def __init__(self, db_path, worker: Callable, *, max_tasks=4, max_events=256,
                 ttl_seconds=86400, timeout_seconds=300):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.worker = worker
        self.max_tasks = max_tasks
        self.max_events = max_events
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._runs: Dict[str, _Run] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_tasks, thread_name_prefix="chat-run")
        self._init_db()
        self._load_runs()

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self):
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chat_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_chat_runs_session ON chat_runs(session_id, status)")
            if 'owner_user_id' not in {row[1] for row in db.execute('PRAGMA table_info(chat_runs)')}:
                db.execute('ALTER TABLE chat_runs ADD COLUMN owner_user_id TEXT')

    def _load_runs(self):
        now = time.time()
        with self._connect() as db:
            db.execute("DELETE FROM chat_runs WHERE expires_at < ? AND status IN ('succeeded', 'cancelled', 'error', 'interrupted')", (now,))
            db.execute("UPDATE chat_runs SET status='interrupted', updated_at=? WHERE status IN ('queued', 'running', 'stopping')", (now,))
            rows = db.execute("SELECT * FROM chat_runs").fetchall()
        for row in rows:
            run = _Run(row["run_id"], row["session_id"], row["payload_hash"], {})
            run.status = row["status"]
            run.expires_at = row["expires_at"]
            run.error = row["error"]
            run.result = json.loads(row["result_json"]) if row["result_json"] else None
            self._runs[run.run_id] = run
            if run.status == "succeeded":
                self._publish(run, "done", {"status": "succeeded", "result": run.result})
            elif run.status == "cancelled":
                self._publish(run, "cancelled", {"status": "cancelled"})
            elif run.status == "error":
                self._publish(run, "error", {"status": "error", "message": run.error or "generation failed"})
            elif run.status == "interrupted":
                self._publish(run, "status", {"status": "interrupted", "stage": "interrupted"})

    @staticmethod
    def _request_hash(payload):
        public_payload = {key: value for key, value in payload.items() if not key.startswith("__")}
        if "__client_session_id" in payload:
            public_payload["session_id"] = payload["__client_session_id"]
        encoded = json.dumps(public_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def create(self, run_id: str, payload: dict):
        try:
            run_id = str(UUID(str(run_id)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("run_id must be a UUID")
        if not isinstance(payload, dict):
            raise ValueError("request payload must be an object")
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        payload_hash = self._request_hash(payload)
        session_id = payload.get("session_id")
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM chat_runs WHERE expires_at < ? AND status IN ('succeeded', 'cancelled', 'error', 'interrupted')", (now,))
            for old_id, old_run in list(self._runs.items()):
                if old_run.expires_at < now and old_run.status in TERMINAL_STATES:
                    self._runs.pop(old_id, None)
            existing = db.execute("SELECT * FROM chat_runs WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                if existing['owner_user_id'] != payload.get('__owner_user_id'):
                    raise RunNotFound('run not found')
                if existing["payload_hash"] != payload_hash:
                    raise RunConflict("run_id already belongs to a different request")
                return {"run_id": run_id, "session_id": existing["session_id"], "status": existing["status"], "created": False}
            if session_id and any(run.session_id == session_id and run.status not in TERMINAL_STATES
                                   for run in self._runs.values()):
                raise RunConflict("a chat run is already active for this session")
            active = sum(1 for run in self._runs.values() if run.status not in TERMINAL_STATES)
            if active >= self.max_tasks:
                raise RunCapacityError("too many active chat runs")
            db.execute(
                "INSERT INTO chat_runs(run_id,session_id,payload_hash,status,created_at,updated_at,expires_at,owner_user_id) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, session_id, payload_hash, "queued", now, now, now + self.ttl_seconds, payload.get('__owner_user_id')),
            )
            run = _Run(run_id, session_id, payload_hash, payload)
            run.expires_at = now + self.ttl_seconds
            run.events = deque(maxlen=self.max_events)
            self._runs[run_id] = run
            self._publish(run, "status", {"status": "queued", "stage": "queued"})
            timer = threading.Timer(self.timeout_seconds, lambda: self.cancel(run_id))
            timer.daemon = True
            run.timeout_timer = timer
            timer.start()
            self._executor.submit(self._execute, run)
            return {"run_id": run_id, "session_id": session_id, "status": "queued", "created": True}

    def _persist(self, run):
        now = time.time()
        run.expires_at = now + self.ttl_seconds
        with self._connect() as db:
            db.execute("UPDATE chat_runs SET status=?, result_json=?, error=?, cancel_requested=?, updated_at=?, expires_at=? WHERE run_id=?",
                       (run.status, json.dumps(run.result, ensure_ascii=False) if run.result is not None else None,
                        run.error, int(run.cancel_event.is_set()), now, run.expires_at, run.run_id))

    def _publish(self, run, event, data):
        with run.condition:
            if event == "delta":
                run.text += str(data.get("text", ""))
            envelope = {"id": run.next_event_id, "event": event,
                        "data": {"run_id": run.run_id, **data}}
            run.next_event_id += 1
            run.events.append(envelope)
            run.condition.notify_all()
        return envelope

    @staticmethod
    def _clear_timeout(run):
        timer = getattr(run, "timeout_timer", None)
        if timer is not None:
            timer.cancel()

    def _execute(self, run):
        with self._lock:
            if run.cancel_event.is_set():
                self._finish_cancelled(run)
                return
            run.status = "running"
            self._persist(run)
        self._publish(run, "status", {"status": "running", "stage": "starting"})
        with self._lock:
            if run.cancel_event.is_set():
                self._finish_cancelled(run)
                return
            run.started = True
        try:
            result = self.worker(run.payload, lambda event, data: self._publish(run, event, data), run.cancel_event)
            with self._lock:
                if run.cancel_event.is_set():
                    self._finish_cancelled(run)
                else:
                    run.result = result
                    run.status = "succeeded"
                    self._persist(run)
                    self._publish(run, "done", {"status": "succeeded", "result": result})
                    self._clear_timeout(run)
                    self._log_terminal(run)
        except Exception as error:
            with self._lock:
                if run.cancel_event.is_set():
                    self._finish_cancelled(run)
                else:
                    run.error = str(error) or type(error).__name__
                    run.status = "error"
                    self._persist(run)
                    self._publish(run, "error", {"status": "error", "message": run.error})
                    self._clear_timeout(run)
                    self._log_terminal(run)

    def commit(self, run_id, callback, result):
        """Serialize the durable session commit against cancellation."""
        run = self._runs.get(str(run_id))
        if run is None:
            raise RunNotFound(str(run_id))
        with self._lock:
            if run.cancel_event.is_set() or run.status in TERMINAL_STATES:
                return False
            callback()
            run.committed = True
            run.commit_result = result
            run.session_id = result.get("session_id") or run.session_id
            with self._connect() as db:
                db.execute("UPDATE chat_runs SET session_id=?, updated_at=? WHERE run_id=?",
                           (run.session_id, time.time(), run.run_id))
            return True

    def recover_success(self, run_id, result):
        run = self._runs.get(str(run_id))
        if run is None:
            raise RunNotFound(str(run_id))
        with self._lock:
            run.status = "succeeded"
            run.result = result
            self._persist(run)
            self._publish(run, "done", {"status": "succeeded", "result": result})

    def remove_session_runs(self, session_id):
        with self._lock, self._connect() as db:
            for run_id, run in list(self._runs.items()):
                if run.session_id == session_id and run.status in TERMINAL_STATES:
                    self._runs.pop(run_id, None)
                    db.execute("DELETE FROM chat_runs WHERE run_id=?", (run_id,))

    def active_sessions(self):
        with self._lock:
            return [
                {"run_id": run.run_id, "session_id": run.session_id, "status": run.status}
                for run in self._runs.values()
                if run.session_id and run.status in {"queued", "running", "stopping", "interrupted"}
            ]

    def is_session_busy(self, session_id):
        with self._lock:
            return any(run.session_id == session_id and run.status in {"queued", "running", "stopping"}
                       for run in self._runs.values())

    def _finish_cancelled(self, run):
        run.result = None
        run.status = "cancelled"
        self._persist(run)
        self._publish(run, "cancelled", {"status": "cancelled"})
        self._clear_timeout(run)
        self._log_terminal(run)

    def _log_terminal(self, run):
        if run.logged:
            return
        run.logged = True
        result = run.result or {}
        logging.getLogger("uvicorn.error").info("chat_run %s", json.dumps({
            "run_id": run.run_id,
            "duration_ms": round((time.time() - run.started_at) * 1000),
            "status": run.status,
            "effective_mode": result.get("effective_mode"),
            "usage": result.get("usage"),
            "model_calls": result.get("rounds"),
        }, ensure_ascii=False, separators=(",", ":")))

    def owner(self, run_id):
        with self._connect() as db:
            row = db.execute('SELECT owner_user_id FROM chat_runs WHERE run_id=?', (str(run_id),)).fetchone()
        if row is None:
            raise RunNotFound(str(run_id))
        return row['owner_user_id']

    def get(self, run_id):
        run = self._runs.get(str(run_id))
        if run is not None:
            if run.expires_at < time.time() and run.status in TERMINAL_STATES:
                with self._lock, self._connect() as db:
                    self._runs.pop(str(run_id), None)
                    db.execute("DELETE FROM chat_runs WHERE run_id=?", (str(run_id),))
                raise RunNotFound(str(run_id))
            return self._snapshot(run)
        with self._connect() as db:
            row = db.execute("SELECT * FROM chat_runs WHERE run_id=?", (str(run_id),)).fetchone()
        if not row:
            raise RunNotFound(str(run_id))
        return {"run_id": row["run_id"], "session_id": row["session_id"], "status": row["status"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None, "error": row["error"], "text": "", "latest_event_id": 0}

    def _snapshot(self, run):
        with run.condition:
            return {"run_id": run.run_id, "session_id": run.session_id, "status": run.status,
                    "result": run.result, "error": run.error, "text": run.text,
                    "latest_event_id": run.next_event_id - 1}

    def events_after(self, run_id, after=0):
        run = self._runs.get(str(run_id))
        if run is None:
            raise RunNotFound(str(run_id))
        with run.condition:
            oldest = run.events[0]["id"] if run.events else run.next_event_id
            if after < oldest - 1:
                return [{"id": run.next_event_id - 1, "event": "snapshot", "data": {
                    "run_id": run.run_id, "status": run.status, "text": run.text,
                    "latest_event_id": run.next_event_id - 1}}]
            available = [event for event in run.events if event["id"] > after]
            if not available and run.status in TERMINAL_STATES:
                terminal_event = "done" if run.status == "succeeded" else ("cancelled" if run.status == "cancelled" else "error")
                data = {"run_id": run.run_id, "status": run.status}
                if run.status == "succeeded":
                    data["result"] = run.result
                elif run.error:
                    data["message"] = run.error
                return [{"id": max(after + 1, run.next_event_id - 1), "event": terminal_event, "data": data}]
            return available

    def wait_for_events(self, run_id, after=0, timeout=15):
        run = self._runs.get(str(run_id))
        if run is None:
            raise RunNotFound(str(run_id))
        with run.condition:
            run.condition.wait_for(lambda: run.next_event_id - 1 > after or run.status in TERMINAL_STATES, timeout=timeout)
        return self.events_after(run_id, after)

    def cancel(self, run_id):
        run = self._runs.get(str(run_id))
        if run is None:
            snapshot = self.get(run_id)
            return {"run_id": str(run_id), "status": snapshot["status"]}
        with self._lock:
            if run.status in TERMINAL_STATES:
                return {"run_id": run.run_id, "status": run.status}
            run.cancel_event.set()
            run.status = "stopping"
            self._persist(run)
            self._publish(run, "status", {"status": "stopping", "stage": "stopping"})
            if not getattr(run, "started", False):
                self._finish_cancelled(run)
            return {"run_id": run.run_id, "status": run.status}

    def wait_for_run(self, run_id, timeout=15):
        deadline = time.monotonic() + timeout
        run = self._runs.get(str(run_id))
        if run is None:
            return self.get(run_id)
        with run.condition:
            while run.status not in TERMINAL_STATES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                run.condition.wait(remaining)
        return self._snapshot(run)

    def shutdown(self):
        self._executor.shutdown(wait=True, cancel_futures=True)




