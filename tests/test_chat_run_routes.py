import uuid
from contextlib import nullcontext
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from src.chat_runs import ChatRunManager


def test_chat_run_worker_copies_session_before_generating():
    class FakeSession:
        def __init__(self, session_id=None):
            self.session_id = session_id or "new-session"
            self.metadata = {}
            self.saved = False

        @classmethod
        def coordinate(cls, session_id):
            return nullcontext()

        def load(self):
            return False

        def save(self):
            self.saved = True

    class FakeRunManager:
        committed = False

        def commit(self, run_id, callback, result):
            callback()
            self.committed = True
            return True

    emitted = []
    run_manager = FakeRunManager()
    result = {"response": "hello", "session_id": "new-session", "session_title": "hello"}
    payload = {"run_id": str(uuid.uuid4()), "message": "hello"}
    with patch.object(server, "Session", FakeSession), \
         patch.object(server, "_chat_locked", return_value=result), \
         patch.object(server, "chat_runs", run_manager):
        actual = server._execute_chat_run(payload, lambda event, data: emitted.append((event, data)), Event())

    assert actual == result
    assert run_manager.committed is True
    assert any(event == "status" and data["stage"] == "saving" for event, data in emitted)


def test_run_routes_create_snapshot_cancel_and_sse(tmp_path):
    def worker(payload, emit, cancelled):
        emit("status", {"stage": "answering"})
        emit("delta", {"text": "hello"})
        return {"response": "hello", "session_id": "session-1"}

    manager = ChatRunManager(tmp_path / "runs.sqlite3", worker=worker)
    run_id = str(uuid.uuid4())
    with patch.object(server, "chat_runs", manager), TestClient(server.app) as client:
        created = client.post("/api/chat/runs", json={"run_id": run_id, "message": "hello"})
        assert created.status_code == 202
        manager.wait_for_run(run_id, timeout=3)
        snapshot = client.get(f"/api/chat/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "succeeded"
        events = client.get(f"/api/chat/runs/{run_id}/events?after=0")
        assert "event: delta" in events.text
        assert '"text":"hello"' in events.text
        assert "event: done" in events.text
        assert client.post(f"/api/chat/runs/{run_id}/cancel").json()["status"] == "succeeded"
    manager.shutdown()


