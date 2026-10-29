"""Online, single-process index generations with a durable active pointer.

Each active read pins a KnowledgeBase object for its entire retrieval operation.
Only the short registration/switch operations synchronize with document writes;
building a shadow version leaves the active version available to readers.
"""
import hashlib
import json
import math
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from config import KB_METADATA_FILE, VECTOR_STORE
from src.errors import IndexRebuildError, IndexWriteConflict
from src.index_state import IndexState
from src.knowledge_base import DocumentIndexFailure, DocumentIndexResult, KnowledgeBase
from src.storage import atomic_json


class VersionAccess:
    def __init__(self, manager, core):
        self.manager, self.core = manager, core
        self.local = threading.local()
        self.writer_lock = core.writer_lock

    @property
    def status(self):
        return self.core.status

    @property
    def data(self):
        return self.core.data

    def set(self, status, reason=None):
        return self.core.set(status, reason)

    @contextmanager
    def read(self):
        with self.core.read():
            with self.pin():
                yield

    @contextmanager
    def pin(self):
        # Administrative catalog remains readable during repair. Its caller
        # already holds the document mutation lock.
        depth = getattr(self.local, 'depth', 0)
        if not depth:
            with self.manager.lock:
                current = self.manager.active
                self.local.kb = current
                self.manager.readers[id(current)] = self.manager.readers.get(id(current), 0) + 1
        self.local.depth = depth + 1
        try:
            yield
        finally:
            self.local.depth -= 1
            if not depth:
                with self.manager.lock:
                    current = self.local.kb
                    count = self.manager.readers[id(current)] - 1
                    if count:
                        self.manager.readers[id(current)] = count
                    else:
                        del self.manager.readers[id(current)]
                        self.manager._collect_retired(current)
                    del self.local.kb

    @contextmanager
    def write(self):
        with self.core.writer_lock:
            if self.manager.building:
                raise IndexWriteConflict('索引正在重建，暂时不能修改文档')
            with self.core.write():
                yield


class IndexVersionManager:
    """A stable facade; callers and cached chatbots never cache the active KB."""

    def __init__(self, legacy=None, *, root=None, factory=KnowledgeBase, on_switch=None):
        self.factory = factory
        self.root = Path(root or Path(KB_METADATA_FILE).parent / 'index_versions')
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / 'registry.json'
        self.lock = threading.RLock()
        self.readers = {}
        self.retired = {}
        self.on_switch = on_switch or (lambda: None)
        self.building = False
        self.registry = {'active': 'legacy', 'previous': None,
                         'previous_manifest': None, 'building': None}
        load_error = None
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding='utf-8'))
                self._validate_registry(data)
                self.registry.update(data)
            except Exception as error:
                load_error = error
        elif any(item.is_dir() and len(item.name) == 32
                 and all(c in '0123456789abcdef' for c in item.name)
                 for item in self.root.iterdir()):
            # Missing pointer is not a first install if managed versions exist.
            load_error = RuntimeError('索引版本指针缺失')

        # Once a managed version is active, do not open the old legacy store
        # during normal startup: it may be stale, missing, or intentionally in
        # repair, and opening it must not change the active version's state.
        active_id = self.registry['active']
        if legacy is None and active_id != 'legacy' and load_error is None:
            try:
                engine = self.registry.get('store_type', VECTOR_STORE)
                if engine != VECTOR_STORE:
                    raise ValueError('索引后端与配置不一致')
                self._check_files(active_id, engine)
                self.active = factory(vector_store=engine, storage_dir=self.root / active_id, strict=True)
                self._validate(self.active)
                self.core = IndexState(Path(KB_METADATA_FILE).with_name('index_state.json'))
                self.versions = {active_id: self.active}
            except Exception as error:
                load_error = error
                self.active = factory()
                self.core = self.active.index_state
                self.versions = {'legacy': self.active}
        else:
            self.active = legacy or factory()
            self.core = self.active.index_state
            self.versions = {'legacy': self.active}
        self.active._index_version = active_id if active_id in self.versions else 'legacy'
        self.access = VersionAccess(self, self.core)
        self.active.index_state = self.access
        if load_error is not None:
            self.core.set('repair_required', f'索引版本状态损坏: {type(load_error).__name__}')
            return
        if active_id == 'legacy' and self.core.status == 'ready':
            try:
                self._validate(self.active)
            except Exception as error:
                self.core.set('repair_required', f'当前索引校验失败: {type(error).__name__}')
        if active_id != 'legacy' and active_id not in self.versions:
            try:
                self.active = self._open(active_id)
                self.versions[active_id] = self.active
            except Exception as error:
                self.core.set('repair_required', f'索引版本状态损坏: {type(error).__name__}')
                return
        interrupted = self.registry.get('building')
        if interrupted:
            self.registry['building'] = None
            self.registry['last_failure'] = '上次索引重建中断，仍使用原活动版本'
            if interrupted not in {active_id, self.registry.get('previous'), 'legacy'}:
                shutil.rmtree(self.root / interrupted, ignore_errors=True)
        self.registry.setdefault('store_type', self.active.store_type)
        self._save_registry()

    @staticmethod
    def _validate_registry(data):
        if not isinstance(data, dict):
            raise ValueError('invalid index registry')
        for key in ('active', 'previous', 'building'):
            value = data.get(key)
            if value is not None and value != 'legacy':
                if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError('invalid index version')
        if not data.get('active'):
            raise ValueError('missing active version')

    @property
    def index_state(self):
        return self.access

    @property
    def active_version(self):
        kb = getattr(self.access.local, 'kb', None) or self.active
        return kb._index_version

    def pinned(self):
        return self.access.pin()

    def __getattr__(self, name):
        local = self.access.local
        kb = getattr(local, 'kb', None) or self.active
        return getattr(kb, name)

    def _save_registry(self):
        atomic_json(self.registry_path, self.registry)

    def _check_files(self, version, engine):
        path = self.root / version
        if not (path / 'metadata.json').is_file() or not (path / 'metadata_chunks.json').is_file():
            raise RuntimeError('索引版本元数据或分块快照缺失')
        if engine == 'faiss':
            if not (path / 'faiss_index.bin').is_file() or not (path / 'faiss_index.bin.meta').is_file():
                raise RuntimeError('Faiss 索引文件缺失')
        elif not (path / 'chroma' / 'chroma.sqlite3').is_file():
            raise RuntimeError('Chroma 索引文件缺失')

    def _open(self, version):
        if version == 'legacy':
            if version not in self.versions:
                state = IndexState(self.root / 'legacy_validation.json')
                kb = self.factory(vector_store=self.active.store_type,
                                  embeddings_manager=self.active.embeddings_manager, index_state=state)
                if state.status != 'ready':
                    raise RuntimeError('旧索引需要修复，不能回退')
                self._validate(kb)
                kb.index_state = self.access
                kb._index_version = 'legacy'
                self.versions['legacy'] = kb
            return self.versions['legacy']
        path = self.root / version
        self._check_files(version, self.active.store_type)
        kb = self.factory(vector_store=self.active.store_type, storage_dir=path,
                          embeddings_manager=self.active.embeddings_manager, strict=True)
        self._validate(kb)
        kb.index_state = self.access
        kb._index_version = version
        return kb

    @staticmethod
    def _validate(kb):
        all_data = kb.store.get_all()
        ids = list(all_data.get('ids', []))
        docs = list(all_data.get('documents', []))
        metas = list(all_data.get('metadatas', []))
        expected = [cid for meta in kb.metadata.values() for cid in meta.get('chunk_ids', [])]
        if (len(ids) != kb.store.count() or len(set(ids)) != len(ids)
                or len(docs) != len(ids) or len(metas) != len(ids)
                or len(expected) != len(set(expected)) or set(expected) != set(ids)
                or set(kb._chunk_texts) != set(ids)
                or any(kb._chunk_texts[cid] != doc for cid, doc in zip(ids, docs))):
            raise RuntimeError('向量、文档元数据与分块快照不一致')
        if ids:
            records = kb.store.get_records(ids[:1])
            vectors = records.get('embeddings', [])
            dimension = kb.embeddings_manager.get_embedding_dimension()
            if len(vectors) != 1 or len(vectors[0]) != dimension or not all(math.isfinite(float(x)) for x in vectors[0]):
                raise RuntimeError('索引向量维度无效')

    def _manifest(self):
        root = Path(self.active.loader.base_dir).resolve()
        records = {}
        for item in self.active.loader.get_file_list():
            path = Path(item['path']).resolve()
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            stat = path.stat()
            records[relative] = {'sha256': digest.hexdigest(), 'size': stat.st_size,
                                 'mtime_ns': stat.st_mtime_ns}
        return dict(sorted(records.items()))

    def status(self):
        with self.lock:
            previous = self.registry.get('previous')
            building = self.building
            expected = self.registry.get('previous_manifest')
            active = self.registry['active']
            failure = self.registry.get('last_failure')
        try:
            available = bool(previous and not building and expected is not None and self._manifest() == expected)
        except (OSError, ValueError):
            available = False
        return {'active': active, 'previous': previous, 'building': building,
                'rollback_available': available, 'index_state': self.access.status,
                'last_failure': failure}

    def _collect_retired(self, kb):
        path = self.retired.pop(id(kb), None)
        if path:
            shutil.rmtree(path, ignore_errors=True)

    def _retire(self, version):
        if not version or version in ('legacy', self.registry['active'], self.registry.get('previous')):
            return
        kb = self.versions.pop(version, None)
        path = self.root / version
        if kb is not None and self.readers.get(id(kb)):
            self.retired[id(kb)] = path
        else:
            shutil.rmtree(path, ignore_errors=True)

    def rebuild_index(self):
        version = uuid.uuid4().hex
        shadow_path = self.root / version
        paths, phase = [], 'prepare'
        with self.core.writer_lock, self.lock:
            if self.building:
                raise IndexWriteConflict('索引正在重建')
            self.building = True
            self.registry['building'] = version
            try:
                self._save_registry()
            except Exception:
                self.registry['building'] = None
                self.building = False
                raise
        try:
            before = self._manifest()
            paths = [item['path'] for item in self.active.loader.get_file_list()]
            identities = {path: {'document_id': item.get('document_id') or hashlib.sha256(
                os.path.relpath(path, self.active.loader.base_dir).replace('\\', '/').encode()).hexdigest()[:24]}
                for path, item in self.active.metadata.items()}
            shadow_path.mkdir(parents=True, exist_ok=False)
            shadow = self.factory(vector_store=self.active.store_type, storage_dir=shadow_path,
                                  embeddings_manager=self.active.embeddings_manager)
            shadow.metadata.update(identities)
            phase = 'index'
            result = shadow.index_documents(paths, max_workers=1)
            shadow.metadata = {path: item for path, item in shadow.metadata.items() if path in paths}
            phase = 'persist'
            shadow._persist_index()
            summary = shadow.index_result_summary(result, len(paths))
            if result.failures:
                self.registry['last_failure'] = '部分文档重建失败，仍使用原活动版本'
                return summary
            phase = 'validate'
            self._validate(shadow)
            if self._manifest() != before:
                raise RuntimeError('重建期间源文件发生变化，拒绝切换')
            with self.core.writer_lock, self.lock:
                old_previous = self.registry.get('previous')
                old_active = self.registry['active']
                previous = old_active if self.core.status == 'ready' else None
                updated = dict(self.registry, active=version, previous=previous,
                               previous_manifest=before if previous else None,
                               building=None, last_failure=None)
                # Persist first; a crash after this point reopens the committed
                # version on startup. The in-memory pointer changes atomically.
                atomic_json(self.registry_path, updated)
                self.registry = updated
                shadow.index_state = self.access
                shadow._index_version = version
                self.active = shadow
                self.versions[version] = shadow
                if self.core.status != 'ready':
                    self.core.set('ready')
                self._retire(old_previous)
            self.on_switch()
            return summary
        except Exception as error:
            self.registry['last_failure'] = f'重建失败: {type(error).__name__}: {error}'
            summary = self.active.index_result_summary(DocumentIndexResult(failures=[
                DocumentIndexFailure(path, str(error), phase) for path in paths]), len(paths))
            summary['operation_error'] = {'stage': phase, 'reason': str(error)}
            raise IndexRebuildError(str(error), summary) from error
        finally:
            with self.core.writer_lock, self.lock:
                if self.registry.get('building') == version:
                    self.registry['building'] = None
                self.building = False
                self._save_registry()
                if self.registry['active'] != version:
                    shutil.rmtree(shadow_path, ignore_errors=True)

    def rollback(self):
        with self.core.writer_lock, self.lock:
            if self.building:
                raise IndexWriteConflict('索引正在重建，暂不能回退')
            previous = self.registry.get('previous')
            if not previous or self.registry.get('previous_manifest') is None:
                raise IndexWriteConflict('没有可回退的旧索引')
            if self._manifest() != self.registry['previous_manifest']:
                raise IndexWriteConflict('文档已变化，请重新构建索引，不能回退')
            try:
                target = self.versions.get(previous)
                if target is None:
                    target = self._open(previous)
                self._validate(target)
            except Exception as error:
                raise IndexWriteConflict('旧索引无法使用，请重新构建索引') from error
            # Check once more immediately before the durable pointer update.
            if self._manifest() != self.registry['previous_manifest']:
                raise IndexWriteConflict('文档已变化，请重新构建索引，不能回退')
            old_active = self.registry['active']
            updated = dict(self.registry, active=previous, previous=old_active, last_failure=None)
            atomic_json(self.registry_path, updated)
            self.registry = updated
            target.index_state = self.access
            self.active = target
            self.versions[previous] = target
            if self.core.status != 'ready':
                self.core.set('ready')
        self.on_switch()
        return self.status()
