import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import server

from src.batch_upload import upload_batch
from src.errors import FileLimitError, RepairRequired


def uploads(names):
    return [SimpleNamespace(filename=name, file=object()) for name in names]


def test_batch_continues_and_preserves_order_and_duplicate_names():
    called = []
    def upload(filename, stream):
        called.append(filename)
        if filename == "large.txt":
            raise FileLimitError("too large")
    result = asyncio.run(upload_batch(uploads(["same.txt", "large.txt", "same.txt"]), upload))
    assert called == ["same.txt", "large.txt", "same.txt"]
    assert [(x["index"], x["status"]) for x in result["items"]] == [(0, "uploaded"), (1, "oversize"), (2, "uploaded")]
    assert result["uploaded_count"] == 2 and result["failed_count"] == 1


def test_all_oversize_returns_413_with_results():
    def upload(filename, stream):
        raise FileLimitError("too large")
    response = asyncio.run(upload_batch(uploads(["a.txt", "b.txt"]), upload))
    assert response.status_code == 413
    assert len(json.loads(response.body)["detail"]["items"]) == 2


def test_repair_failure_stops_remaining_writes_and_reports_them():
    called = []
    def upload(filename, stream):
        called.append(filename)
        if filename == "broken.txt":
            raise RepairRequired("repair needed")
    response = asyncio.run(upload_batch(uploads(["a.txt", "broken.txt", "b.txt"]), upload))
    assert response.status_code == 500 and called == ["a.txt", "broken.txt"]
    body = json.loads(response.body)
    assert body["repair_required"] is True
    assert [x["status"] for x in body["result"]["items"]] == ["uploaded", "repair_required", "not_processed"]


def test_admin_upload_route_returns_per_file_results():
    def upload(filename, stream):
        if filename == "large.txt":
            raise FileLimitError("too large")
    server.app.dependency_overrides[server._admin_csrf] = lambda: {"username": "tester"}
    try:
        with patch.object(server, "_upload_compatible", side_effect=upload):
            response = TestClient(server.app).post("/api/upload", files=[
                ("files", ("a.txt", b"a")), ("files", ("large.txt", b"b")), ("files", ("c.txt", b"c")),
            ])
        assert response.status_code == 200
        assert [item["status"] for item in response.json()["items"]] == ["uploaded", "oversize", "uploaded"]
    finally:
        server.app.dependency_overrides.pop(server._admin_csrf, None)
