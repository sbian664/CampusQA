import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from src.errors import IndexRebuildError, IndexWriteConflict
from src.index_versions import IndexVersionManager
from src.knowledge_base import KnowledgeBase, DocumentIndexFailure, DocumentIndexResult


class FixedEmbedding:
    def __init__(self):
        self.cache = {}
    def get_embedding_dimension(self):
        return 2
    def embed_text(self, text):
        return [1.0, float(len(text) % 7) / 10]
    def save_cache(self):
        pass


@pytest.fixture(params=['chroma', 'faiss'])
def versioned(tmp_path, request):
    docs = tmp_path / 'documents'
    docs.mkdir()
    (docs / 'policy.txt').write_text('Original campus policy.', encoding='utf-8')
    metadata = tmp_path / 'cache' / 'metadata.json'
    with patch('src.knowledge_base.DOCUMENTS_DIR', str(docs)), \
         patch('src.knowledge_base.KB_METADATA_FILE', str(metadata)), \
         patch('src.index_versions.KB_METADATA_FILE', str(metadata)), \
         patch('src.vector_store.CHROMA_DB_PATH', str(tmp_path / 'legacy-chroma')), \
         patch('src.vector_store.FAISS_INDEX_PATH', str(tmp_path / 'legacy-faiss.bin')):
        embedding = FixedEmbedding()
        legacy = KnowledgeBase(vector_store=request.param, embeddings_manager=embedding)
        legacy.index_document(str(docs / 'policy.txt'))
        manager = IndexVersionManager(legacy, root=tmp_path / 'versions')
        yield manager, docs, tmp_path


def test_rebuild_reads_old_version_rejects_writes_and_pins_inflight(versioned):
    manager, docs, root = versioned
    entered, release = threading.Event(), threading.Event()
    real_factory = manager.factory
    def delayed_factory(*args, **kwargs):
        kb = real_factory(*args, **kwargs)
        if kwargs.get('storage_dir') and not kwargs.get('strict'):
            original = kb.index_documents
            def delayed(*args, **kwargs):
                entered.set()
                assert release.wait(10)
                return original(*args, **kwargs)
            kb.index_documents = delayed
        return kb
    manager.factory = delayed_factory
    old = manager.active
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.rebuild_index)
        assert entered.wait(10)
        assert manager.status()['building']
        with manager.index_state.read():
            assert manager.store is old.store
            with pytest.raises(IndexWriteConflict):
                with manager.index_state.write():
                    pass
            release.set()
            assert future.result(timeout=20)['failed'] == 0
            assert manager.active is not old
            assert manager.store is old.store  # pinned to entry version
        assert manager.store is manager.active.store
    assert manager.status()['previous'] == 'legacy'
    assert manager.status()['rollback_available']
    manager.rollback()
    assert manager.active is old


def test_failed_rebuild_preserves_active_and_previous(versioned):
    manager, docs, root = versioned
    assert manager.rebuild_index()['failed'] == 0
    before = manager.status()
    active = manager.active
    real_factory = manager.factory
    def failing_factory(*args, **kwargs):
        kb = real_factory(*args, **kwargs)
        if kwargs.get('storage_dir') and not kwargs.get('strict'):
            kb.index_documents = lambda paths, **kw: DocumentIndexResult(
                failures=[DocumentIndexFailure(paths[0], 'parse failed')])
        return kb
    manager.factory = failing_factory
    summary = manager.rebuild_index()
    assert summary['failed'] == 1
    assert manager.active is active
    assert manager.status()['active'] == before['active']
    assert manager.status()['previous'] == before['previous']
    assert manager.status()['index_state'] == 'ready'


def test_restart_and_changed_document_prevent_rollback(versioned):
    manager, docs, root = versioned
    manager.rebuild_index()
    active_id = manager.status()['active']
    restarted_legacy = KnowledgeBase(vector_store=manager.active.store_type,
                                     embeddings_manager=manager.active.embeddings_manager)
    restarted = IndexVersionManager(restarted_legacy, root=root / 'versions')
    assert restarted.status()['active'] == active_id
    assert restarted.active.store.count() == 1
    (docs / 'policy.txt').write_text('Changed policy.', encoding='utf-8')
    assert not restarted.status()['rollback_available']
    with pytest.raises(IndexWriteConflict, match='文档已变化'):
        restarted.rollback()
    assert restarted.status()['active'] == active_id


def test_source_changed_during_build_does_not_switch(versioned):
    manager, docs, root = versioned
    real_factory = manager.factory
    def changing_factory(*args, **kwargs):
        kb = real_factory(*args, **kwargs)
        if kwargs.get('storage_dir') and not kwargs.get('strict'):
            original = kb.index_documents
            def changing(*args, **kwargs):
                result = original(*args, **kwargs)
                (docs / 'policy.txt').write_text('Changed externally.', encoding='utf-8')
                return result
            kb.index_documents = changing
        return kb
    manager.factory = changing_factory
    with pytest.raises(IndexRebuildError, match='源文件发生变化'):
        manager.rebuild_index()
    assert manager.status()['active'] == 'legacy'
    assert manager.status()['index_state'] == 'ready'


def test_corrupt_pointer_enters_repair_state(versioned):
    manager, docs, root = versioned
    (root / 'versions' / 'registry.json').write_text('{invalid', encoding='utf-8')
    legacy = KnowledgeBase(vector_store=manager.active.store_type,
                           embeddings_manager=manager.active.embeddings_manager)
    restarted = IndexVersionManager(legacy, root=root / 'versions')
    assert restarted.index_state.status == 'repair_required'


def test_production_restart_opens_active_without_loading_legacy(versioned):
    manager, docs, root = versioned
    manager.rebuild_index()
    active_id = manager.status()['active']
    engine = manager.active.store_type
    embedding = manager.active.embeddings_manager
    # Production constructs no legacy object when a managed version is active.
    def factory(*args, **kwargs):
        assert kwargs.get('storage_dir'), 'startup must not open the stale legacy store'
        kwargs['embeddings_manager'] = embedding
        return KnowledgeBase(*args, **kwargs)
    with patch('src.index_versions.VECTOR_STORE', engine):
        restarted = IndexVersionManager(root=root / 'versions', factory=factory)
    assert restarted.status()['active'] == active_id
    assert restarted.index_state.status == 'ready'
    assert restarted.active.store.count() == 1


def test_missing_pointer_fails_closed(versioned):
    manager, docs, root = versioned
    manager.rebuild_index()
    (root / 'versions' / 'registry.json').unlink()
    legacy = KnowledgeBase(vector_store=manager.active.store_type,
                           embeddings_manager=manager.active.embeddings_manager)
    restarted = IndexVersionManager(legacy, root=root / 'versions')
    assert restarted.index_state.status == 'repair_required'


def test_disk_error_preserves_old_active(versioned):
    manager, docs, root = versioned
    old = manager.active
    original_factory = manager.factory
    def failing_factory(*args, **kwargs):
        kb = original_factory(*args, **kwargs)
        if kwargs.get('storage_dir') and not kwargs.get('strict'):
            kb._persist_index = lambda: (_ for _ in ()).throw(OSError('disk full'))
        return kb
    manager.factory = failing_factory
    with pytest.raises(IndexRebuildError):
        manager.rebuild_index()
    assert manager.active is old
    assert manager.status()['active'] == 'legacy'
    assert manager.status()['index_state'] == 'ready'


def test_version_routes_require_admin_and_csrf(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.admin.control_store import AdminControlStore, hash_password
    from src.admin.routes import create_admin_router

    state = {'active': 'new', 'previous': 'legacy', 'building': False,
             'rollback_available': True, 'index_state': 'ready'}
    store = AdminControlStore(tmp_path / 'admin.db', username='admin', password_hash=hash_password('secret'))
    app = FastAPI()
    app.include_router(create_admin_router(store, versions_provider=lambda: state,
                                          rollback_provider=lambda: state), prefix='/api/admin')
    client = TestClient(app)
    assert client.get('/api/admin/kb/versions').status_code == 401
    login = client.post('/api/admin/auth/login', json={'username': 'admin', 'password': 'secret'})
    assert client.get('/api/admin/kb/versions').json()['active'] == 'new'
    assert client.post('/api/admin/kb/versions/rollback').status_code == 403
    response = client.post('/api/admin/kb/versions/rollback',
                           headers={'X-CSRF-Token': login.json()['csrf_token']})
    assert response.status_code == 200
    assert response.json()['previous'] == 'legacy'


def test_crashed_build_restores_last_committed_version(versioned):
    manager, docs, root = versioned
    manager.rebuild_index()
    active = manager.status()['active']
    interrupted = 'f' * 32
    shadow = root / 'versions' / interrupted
    shadow.mkdir()
    registry = json.loads(manager.registry_path.read_text())
    registry['building'] = interrupted
    manager.registry_path.write_text(json.dumps(registry), encoding='utf-8')
    legacy = KnowledgeBase(vector_store=manager.active.store_type,
                           embeddings_manager=manager.active.embeddings_manager)
    restarted = IndexVersionManager(legacy, root=root / 'versions')
    assert restarted.status()['active'] == active
    assert not restarted.status()['building']
    assert not shadow.exists()
    assert restarted.status()['index_state'] == 'ready'
