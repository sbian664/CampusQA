from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from src.admin.documents import AdminDocumentService
from src.document_loader import DocumentLoader
from src.index_state import IndexState
from src.knowledge_base import KnowledgeBase
from src.vector_store import ChromaStore, FaissStore


def test_chroma_clear_propagates_failure_instead_of_reporting_success():
    store = ChromaStore.__new__(ChromaStore)
    def fail(**kwargs): raise OSError('index unavailable')
    store.collection = SimpleNamespace(count=lambda: 1, get=fail, delete=fail)
    with pytest.raises(OSError):
        store.clear()


def test_failed_delete_does_not_duplicate_surviving_old_faiss_chunks(tmp_path):
    with patch('src.vector_store.FAISS_INDEX_PATH', str(tmp_path / 'faiss.bin')):
        store = FaissStore(2)
        old_ids = ['hash_0', 'hash_1', 'hash_2']
        store.add(old_ids, ['old'] * 3, [{'source': 'doc.txt'}] * 3, [[1., 2.]] * 3)
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb.store = store
        kb.metadata = {'doc.txt': {'chunk_ids': old_ids}}
        kb._chunk_texts = dict.fromkeys(old_ids, 'old')
        kb._chunk_metadata = {cid: {'source': 'doc.txt'} for cid in old_ids}
        original = store.delete
        failed = False
        def fail_once(ids):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError('delete failed')
            original(ids)
        with patch.object(store, 'delete', side_effect=fail_once):
            with pytest.raises(OSError):
                kb._commit_prepared_document_update({'file_path': 'doc.txt', 'chunk_ids': ['hash_0'], 'chunk_texts': ['new'], 'chunk_metadatas': [{'source': 'doc.txt'}], 'chunk_vectors': [[2., 1.]], 'file_mtime': 1, 'file_size': 3})
        assert store.count() == 3
        assert store.get_all()['ids'] == old_ids


@pytest.mark.parametrize('backend', ['chroma', 'faiss'])
@pytest.mark.parametrize('stage', ['prepare', 'delete', 'add', 'metadata', 'chunks', 'persist'])
def test_document_failure_restores_source_metadata_and_exact_vectors(tmp_path, backend, stage):
    source = tmp_path / 'documents'
    source.mkdir()
    path = source / 'guide.txt'
    path.write_text('original')
    with patch('src.vector_store.CHROMA_DB_PATH', str(tmp_path / 'chroma')), patch('src.vector_store.FAISS_INDEX_PATH', str(tmp_path / 'faiss.bin')), patch('src.knowledge_base.KB_METADATA_FILE', str(tmp_path / 'metadata.json')):
        store = ChromaStore() if backend == 'chroma' else FaissStore(2)
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb.loader = DocumentLoader(str(source))
        kb.text_splitter = SimpleNamespace(split_documents=lambda docs: docs)
        kb.embeddings_manager = SimpleNamespace(embed_text=lambda text: [float(len(text)), .123456])
        kb.store = store; kb.store_type = backend; kb.metadata = {}; kb._chunk_texts = {}; kb._chunk_metadata = {}
        kb.index_state = IndexState(tmp_path / 'state.json')
        kb._update_document(str(path)); kb._persist_index()
        old_metadata = deepcopy(kb.metadata)
        ids = old_metadata[str(path)]['chunk_ids']
        before = store.get_records(ids)
        target, method = {'prepare': (kb, '_prepare_document_update'), 'delete': (store, 'delete'),
                          'add': (store, 'add'), 'metadata': (kb, '_save_metadata'),
                          'chunks': (kb, '_save_chunk_texts'), 'persist': (kb, '_save_store')}[stage]
        original = getattr(target, method)
        called = False
        def fault_once(*args, **kwargs):
            nonlocal called
            if not called:
                called = True
                raise OSError('injected failure')
            return original(*args, **kwargs)
        service = AdminDocumentService(kb_provider=lambda: kb, documents_dir=str(source), supported_formats={'.txt'})
        with patch.object(target, method, side_effect=fault_once):
            with pytest.raises(OSError):
                service.update_content(service._document_id(path), 'replacement content')
        after = store.get_records(ids)
        assert path.read_text() == 'original'
        assert kb.metadata == old_metadata
        assert after['ids'] == before['ids']
        assert after['documents'] == before['documents']
        assert np.allclose(after['embeddings'], before['embeddings'])
        assert store.count() == len(ids)
        assert kb.index_state.status == 'ready'


def test_rollback_failure_closes_retrieval_and_preserves_recovery_backup(tmp_path):
    import shutil
    from pathlib import Path
    from src.errors import RepairRequired
    path = tmp_path / 'guide.txt'
    path.write_text('original')
    state = IndexState(tmp_path / 'state.json')
    def fail(): raise OSError('disk unavailable')
    kb = SimpleNamespace(metadata={}, index_state=state, _update_document=lambda path: None,
                         _save_metadata=fail, _save_chunk_texts=lambda: None, _save_store=lambda: None)
    service = AdminDocumentService(kb_provider=lambda: kb, documents_dir=str(tmp_path), supported_formats={'.txt'})
    with pytest.raises(RepairRequired) as error:
        service.update_content(service._document_id(path), 'new')
    assert state.status == 'repair_required'
    assert path.read_text() == 'original'
    backup = Path(str(error.value).split('恢复备份: ')[-1])
    assert backup.is_dir()
    assert (backup / '0').read_text() == 'original'
    shutil.rmtree(backup)
