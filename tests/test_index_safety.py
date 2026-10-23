import io
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from src.knowledge_base import KnowledgeBase, DocumentIndexResult, DocumentIndexFailure
from src.admin.documents import AdminDocumentService


def prepared_kb(tmp_path):
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb.loader = SimpleNamespace(base_dir=str(tmp_path), load_file=lambda path: [Document(page_content=Path(path).read_text())])
    kb.text_splitter = SimpleNamespace(split_documents=lambda docs: docs)
    kb.embeddings_manager = SimpleNamespace(embed_text=lambda text: [1.0])
    kb.metadata = {}
    return kb


def test_same_name_in_subdirectories_has_independent_chunk_ids(tmp_path):
    kb = prepared_kb(tmp_path)
    ids = []
    for folder in ('a', 'b'):
        path = tmp_path / folder / 'guide.txt'
        path.parent.mkdir()
        path.write_text(folder)
        ids.append(kb._prepare_document_update(str(path), 1)['chunk_ids'][0])
    assert ids[0] != ids[1]


def test_persistence_errors_propagate(tmp_path):
    kb = prepared_kb(tmp_path)
    with patch('src.knowledge_base.KB_METADATA_FILE', str(tmp_path / 'meta.json')):
        with patch('src.storage.os.replace', side_effect=OSError('disk failure')):
            with pytest.raises(OSError):
                kb._save_metadata()


def test_replace_and_edit_restore_index_after_persistence_failure(tmp_path):
    path = tmp_path / 'guide.txt'
    path.write_text('old')
    class KB:
        metadata = {str(path): {'chunk_count': 1, 'value': 'old'}}
        fail = True
        def _update_document(self, source):
            self.metadata[source] = {'chunk_count': 1, 'value': Path(source).read_text()}
        def _save_metadata(self):
            if self.fail:
                self.fail = False
                raise OSError('disk failure')
        def _save_chunk_texts(self): pass
        def _save_store(self): pass
    kb = KB()
    service = AdminDocumentService(kb_provider=lambda: kb, documents_dir=str(tmp_path), supported_formats={'.txt'})
    document_id = service._document_id(path)
    for operation in (lambda: service.replace(document_id, 'guide.txt', io.BytesIO(b'new')),
                      lambda: service.update_content(document_id, 'edited')):
        before = deepcopy(kb.metadata)
        kb.fail = True
        with pytest.raises(OSError):
            operation()
        assert path.read_text() == 'old'
        assert kb.metadata == before


def test_upload_limits_stream_before_indexing_and_cleans_temporary_files(tmp_path):
    kb = SimpleNamespace(metadata={}, _update_document=lambda path: pytest.fail('must reject before indexing'))
    service = AdminDocumentService(kb_provider=lambda: kb, documents_dir=str(tmp_path), supported_formats={'.txt'})
    with patch('src.file_processing.MAX_FILE_BYTES', 10):
        with pytest.raises(ValueError, match='20 MB'):
            service.upload('big.txt', io.BytesIO(b'x' * 11))
    assert list(tmp_path.iterdir()) == []


def test_maintenance_blocks_new_readers_and_waits_for_active_reader(tmp_path):
    from src.index_state import IndexState
    state = IndexState(tmp_path / 'state.json')
    entered = threading.Event()
    release = threading.Event()
    def read():
        with state.read():
            entered.set()
            release.wait(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        assert entered.wait(1)
        writer = pool.submit(state.begin_rebuild)
        for _ in range(100):
            if state.status == 'rebuilding':
                break
            threading.Event().wait(.005)
        with pytest.raises(Exception, match='维护'):
            with state.read(): pass
        assert not writer.done()
        release.set()
        reader.result(timeout=2)
        writer.result(timeout=2)
    assert IndexState(tmp_path / 'state.json').status == 'repair_required'


def test_job_partial_failure_is_terminal(tmp_path):
    from src.admin.control_store import AdminControlStore
    from src.admin.jobs import AdminJobManager
    store = AdminControlStore(tmp_path / 'admin.db')
    manager = AdminJobManager(store)
    job_id = manager.submit('rebuild', lambda progress: {'total': 2, 'succeeded': 1, 'failed': 1, 'failures': [{'file': 'bad.txt', 'stage': 'parse', 'reason': 'bad'}]})
    manager.futures[job_id].result(timeout=2)
    assert manager.get(job_id)['status'] == 'partial_failed'
    manager.executor.shutdown(wait=True)


def test_partial_rebuild_keeps_renamed_document_identity(tmp_path):
    from src.index_state import IndexState
    path = str(tmp_path / 'renamed.txt')
    kb = prepared_kb(tmp_path)
    kb.metadata = {path: {'document_id': 'a' * 24, 'chunk_ids': ['old']}}
    kb.index_state = IndexState(tmp_path / 'state.json')
    kb._chunk_texts = {}; kb._chunk_metadata = {}
    kb.store = SimpleNamespace(clear=lambda: None)
    kb.embeddings_manager = SimpleNamespace(cache={}, save_cache=lambda: None)
    kb.loader.get_file_list = lambda: [{'path': path}]
    kb.index_documents = lambda paths, **kw: DocumentIndexResult(failures=[DocumentIndexFailure(path, 'parse failure', 'prepare')])
    kb._persist_index = lambda: None
    assert kb.rebuild_index()['failed'] == 1
    assert kb.metadata[path]['document_id'] == 'a' * 24
    assert kb.index_state.status == 'repair_required'


def test_rebuild_exception_keeps_per_file_failure_details(tmp_path):
    from src.index_state import IndexState
    kb = prepared_kb(tmp_path)
    path = str(tmp_path / 'guide.txt')
    kb.loader.get_file_list = lambda: [{'path': path}]
    kb.index_state = IndexState(tmp_path / 'state.json')
    def fail(): raise OSError('clear failed')
    kb.store = SimpleNamespace(clear=fail)
    with pytest.raises(Exception) as error:
        kb.rebuild_index()
    assert error.value.result['total'] == 1
    assert error.value.result['failed'] == 1
    assert error.value.result['failures'][0]['stage'] == 'clear'
    assert kb.index_state.status == 'repair_required'


def test_scan_and_rebuild_take_file_slot_before_mutation_lock():
    import server
    import src.file_processing as processing
    class LockOrder:
        def __enter__(self):
            assert getattr(processing._local, 'active', False), 'must acquire file slot first'
        def __exit__(self, *args): pass
    with patch('server._kb_mutation_lock', LockOrder()), patch('server.get_kb') as get_kb:
        kb = get_kb.return_value
        kb.rebuild_index.return_value = {'failed': 0}
        kb.index_result_summary.return_value = {}
        for operation in (lambda: server._admin_job_operation('scan', lambda *args: None),
                          lambda: server._admin_job_operation('rebuild', lambda *args: None),
                          server.kb_scan, server.kb_rebuild):
            operation()


def test_local_file_parser_rejects_oversize_before_loading(tmp_path):
    from src.document_loader import DocumentLoader
    path = tmp_path / 'large.txt'
    path.write_bytes(b'x' * 11)
    with patch('src.file_processing.MAX_FILE_BYTES', 10):
        with pytest.raises(ValueError, match='20 MB'):
            DocumentLoader(str(tmp_path)).load_file(str(path))
