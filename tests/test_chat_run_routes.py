import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from src.chat_runs import ChatRunManager


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


