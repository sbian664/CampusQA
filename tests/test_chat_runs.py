import json
import threading
import time
import uuid

import pytest

from src.chat_runs import ChatRunManager, RunConflict


def test_create_is_idempotent_and_rejects_different_payload(tmp_path):
    release = threading.Event()

    def worker(payload, emit, cancelled):
        release.wait(1)
        return {"response": "ok", "session_id": "session-1"}

    manager = ChatRunManager(tmp_path / "runs.sqlite3", worker=worker)
    run_id = str(uuid.uuid4())
    payload = {"message": "hello", "session_id": None}
    first = manager.create(run_id, payload)
    second = manager.create(run_id, payload)

    assert first["run_id"] == run_id
    assert second["run_id"] == run_id
    assert first["created"] is True
    assert second["created"] is False
    with pytest.raises(RunConflict):
        manager.create(run_id, {"message": "different"})
    release.set()
    manager.shutdown()


def test_rejects_a_second_active_run_for_the_same_session(tmp_path):
    release = threading.Event()

    def worker(payload, emit, cancelled):
        release.wait(1)
        return {"response": "ok"}

    manager = ChatRunManager(tmp_path / "runs.sqlite3", worker=worker)
    manager.create(str(uuid.uuid4()), {"message": "one", "session_id": "same"})
    with pytest.raises(RunConflict):
        manager.create(str(uuid.uuid4()), {"message": "two", "session_id": "same"})
    release.set()
    manager.shutdown()


def test_snapshot_replays_when_cursor_is_older_than_retained_events(tmp_path):
    release = threading.Event()

    def worker(payload, emit, cancelled):
        for index in range(4):
            emit("delta", {"text": str(index)})
        release.wait(1)
        return {"response": "0123", "session_id": "session-1"}

    manager = ChatRunManager(tmp_path / "runs.sqlite3", worker=worker, max_events=2)
    run_id = str(uuid.uuid4())
    manager.create(run_id, {"message": "hello"})
    run = manager.wait_for_run(run_id, timeout=1)
    events = manager.events_after(run_id, 0)

    assert events[0]["event"] == "snapshot"
    assert events[0]["data"]["text"] == "0123"
    assert events[0]["id"] == run["latest_event_id"]
    release.set()
    manager.shutdown()


def test_cancel_is_idempotent_and_worker_terminal_state_is_cancelled(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def worker(payload, emit, cancelled):
        started.set()
        while not cancelled.wait(0.01):
            pass
        release.wait(5)
        return {"response": "partial"}

    manager = ChatRunManager(tmp_path / "runs.sqlite3", worker=worker)
    run_id = str(uuid.uuid4())
    manager.create(run_id, {"message": "hello"})
    assert started.wait(1)

    try:
        assert manager.cancel(run_id)["status"] == "stopping"
        assert manager.cancel(run_id)["status"] == "stopping"
    finally:
        release.set()
    run = manager.wait_for_run(run_id, timeout=1)
    assert run["status"] == "cancelled"
    assert run.get("result") is None
    manager.shutdown()
