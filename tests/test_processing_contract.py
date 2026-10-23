import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import server
from src.errors import FileLimitError, KnowledgeUnavailable
from src.index_state import IndexState
from src.submission_routes import create_submission_router
from src.tools import ToolHandler


def test_submission_size_has_413():
    def reject(*args, **kwargs):
        raise FileLimitError('单文件不能超过 20 MB')
    app = FastAPI()
    app.include_router(create_submission_router(SimpleNamespace(submit=reject)), prefix='/submissions')
    assert TestClient(app).post('/submissions', files={'file': ('x.txt', b'x')}).status_code == 413


def test_maintenance_error_escapes_tool_handler(tmp_path):
    state = IndexState(tmp_path / 'state.json')
    state.set('repair_required', 'failure')
    kb = SimpleNamespace(index_state=state)
    with pytest.raises(KnowledgeUnavailable):
        ToolHandler(kb).execute('search_knowledge_base', {'query': 'x'})


def test_health_responds_on_shared_event_loop_while_parser_is_blocked():
    started = threading.Event()
    release = threading.Event()
    class Loader:
        def __init__(self, **kwargs): pass
        def load_file(self, path):
            started.set()
            release.wait(3)
            return []
    @asynccontextmanager
    async def no_startup(app):
        yield
    with patch.object(server.app.router, 'lifespan_context', no_startup), patch('server.DocumentLoader', Loader):
        with TestClient(server.app) as client, ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(client.post, '/api/attachments/parse', files={'file': ('slow.txt', b'x')})
            try:
                assert started.wait(2)
                assert pool.submit(client.get, '/api/health').result(timeout=.75).status_code == 200
            finally:
                release.set()
            assert pending.result(timeout=2).status_code == 200


def test_runtime_setting_batch_rolls_back_and_reads_one_snapshot(tmp_path):
    from src.admin.control_store import AdminControlStore
    store = AdminControlStore(tmp_path / 'control.db')
    store.set_settings({'runtime.max_tokens': 200, 'runtime.bm25_weight': .3})
    with pytest.raises(TypeError):
        store.set_settings({'runtime.max_tokens': 999, 'runtime.bm25_weight': object()})
    assert store.get_settings(['runtime.max_tokens', 'runtime.bm25_weight']) == {'runtime.max_tokens': 200, 'runtime.bm25_weight': .3}


def test_file_boundary_accepts_exact_limit_and_stops_at_limit_plus_one():
    import io
    from src.file_processing import copy_limited, MAX_FILE_BYTES
    destination = io.BytesIO()
    assert copy_limited(io.BytesIO(b'x' * MAX_FILE_BYTES), destination) == MAX_FILE_BYTES
    with pytest.raises(FileLimitError):
        copy_limited(io.BytesIO(b'x' * (MAX_FILE_BYTES + 1)), io.BytesIO())
