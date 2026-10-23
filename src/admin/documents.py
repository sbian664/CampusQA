"""Safe document projections and mutations for the admin console."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
import tempfile
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from src.file_processing import file_operation, copy_limited, MAX_FILE_BYTES, ensure_file_size
from src.errors import FileLimitError, RepairRequired
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, Callable, Dict, Optional

EDITABLE_DOCUMENT_SUFFIXES = {".md", ".txt", ".html"}
MAX_EDITABLE_CONTENT_BYTES = MAX_FILE_BYTES

class AdminDocumentService:
    def __init__(self, *, kb_provider: Callable[[], Any], documents_dir: str, supported_formats: set[str], mutation_lock: RLock | None = None) -> None:
        self.kb_provider = kb_provider
        self.documents_dir = Path(documents_dir).resolve()
        self.supported_formats = {extension.lower() for extension in supported_formats}
        self.mutation_lock = mutation_lock or RLock()
        self._catalog_cache: Optional[list[Dict[str, Any]]] = None
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, filename: str) -> Path:
        name = os.path.basename(filename or "")
        if not name or name in {".", ".."}:
            raise ValueError("文件名不能为空")
        path = (self.documents_dir / name).resolve()
        if os.path.commonpath([str(self.documents_dir), str(path)]) != str(self.documents_dir):
            raise ValueError("非法文件名")
        if path.suffix.lower() not in self.supported_formats:
            raise ValueError(f"不支持的文件格式: {path.suffix}")
        return path

    def _document_id(self, path: Path) -> str:
        relative = path.relative_to(self.documents_dir).as_posix()
        return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]

    def _stored_document_id(self, path: Path, kb: Any) -> str:
        raw = getattr(kb, "metadata", {}).get(str(path), {})
        return raw.get("document_id") or self._document_id(path)

    def _resolve_id(self, document_id: str) -> Path:
        if not document_id or len(document_id) > 64 or any(char not in "0123456789abcdef" for char in document_id.lower()):
            raise LookupError("文档不存在")
        kb = self.kb_provider()
        for path in self.documents_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in self.supported_formats and self._stored_document_id(path, kb) == document_id:
                return path
        raise FileNotFoundError("文档不存在")

    def _metadata(self, path: Path, kb: Any = None) -> Dict[str, Any]:
        kb = kb if kb is not None else self.kb_provider()
        raw = getattr(kb, "metadata", {}).get(str(path), {})
        stat = path.stat()
        return {
            "document_id": self._stored_document_id(path, kb),
            "filename": path.name,
            "source": path.relative_to(self.documents_dir).as_posix(),
            "doc_type": path.suffix.lower().lstrip(".") or "unknown",
            "size": int(raw.get("size", stat.st_size)),
            "mtime": raw.get("mtime", stat.st_mtime),
            "chunk_count": int(raw.get("chunk_count", 0)),
            "index_status": "indexed" if raw else "not_indexed",
            "last_error": None,
            "updated_at": raw.get("updated_at"),
        }

    def _build_catalog(self) -> list[Dict[str, Any]]:
        items = []
        kb = self.kb_provider()
        for path in sorted(self.documents_dir.rglob("*"), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() in self.supported_formats:
                items.append(self._metadata(path, kb))
        return items

    def invalidate_catalog(self) -> None:
        with self.mutation_lock:
            self._catalog_cache = None

    def _all_documents(self, query: Optional[str] = None) -> list[Dict[str, Any]]:
        with self.mutation_lock:
            if self._catalog_cache is None:
                self._catalog_cache = self._build_catalog()
            items = list(self._catalog_cache)
        query_lower = query.strip().lower() if query else None
        if not query_lower:
            return items
        return [item for item in items if query_lower in f"{item['filename']} {item['source']}".lower()]

    def list_documents_page(self, *, limit: int = 50, offset: int = 0, query: Optional[str] = None) -> Dict[str, Any]:
        items = self._all_documents(query)
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        return {
            "items": items[safe_offset:safe_offset + safe_limit],
            "total": len(items),
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def list_documents(self, *, limit: int = 50, offset: int = 0, query: Optional[str] = None) -> list[Dict[str, Any]]:
        return self.list_documents_page(limit=limit, offset=offset, query=query)["items"]

    @file_operation
    def get_document(self, document_id: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        item = self._metadata(path)
        ensure_file_size(path)
        try:
            if path.suffix.lower() in EDITABLE_DOCUMENT_SUFFIXES:
                content = path.read_text(encoding="utf-8")
                item["content"] = content[:100000]
                item["content_truncated"] = len(content) > 100000
            else:
                docs = self.kb_provider().loader.load_file(str(path))
                item["content"] = "\n\n".join(str(doc.page_content) for doc in docs)[:20000]
        except Exception as error:
            item["content"] = ""
            item["content_error"] = f"{type(error).__name__}: {error}"
        return item

    def get_document_path(self, document_id: str) -> Path:
        path = self._resolve_id(document_id)
        if not path.is_file():
            raise FileNotFoundError("文档不存在")
        return path

    @staticmethod
    def _persist(kb):
        for name in ('_save_metadata', '_save_chunk_texts', '_save_store'):
            method = getattr(kb, name, None)
            if method:
                method()

    @contextmanager
    def _transaction(self, kb, paths, persist=True):
        # The lock covers file and vector snapshots, mutation, persistence and rollback.
        with self.mutation_lock:
            state = getattr(kb, 'index_state', None)
            with state.write() if state else nullcontext():
                backup_dir = Path(tempfile.mkdtemp(prefix='campusqa_rollback_'))
                rollback_failed = False
                originals = {}
                try:
                    for i, path in enumerate(paths):
                        if path.exists():
                            backup = backup_dir / str(i)
                            shutil.copyfile(path, backup)
                            originals[path] = (backup, path.stat())
                        else:
                            originals[path] = None
                except Exception:
                    self._cleanup_backup(backup_dir)
                    raise
                try:
                    snapshot = kb.snapshot_documents(paths) if hasattr(kb, 'snapshot_documents') else deepcopy(getattr(kb, 'metadata', {}))
                except Exception:
                    self._cleanup_backup(backup_dir)
                    raise
                try:
                    yield
                    if persist:
                        self._persist(kb)
                except Exception as original:
                    try:
                        for path, saved in originals.items():
                            if saved is None:
                                path.unlink(missing_ok=True)
                            else:
                                temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
                                try:
                                    shutil.copyfile(saved[0], temporary)
                                    os.replace(temporary, path)
                                finally:
                                    temporary.unlink(missing_ok=True)
                                os.utime(path, (saved[1].st_atime, saved[1].st_mtime))
                        if hasattr(kb, 'restore_documents'):
                            kb.restore_documents(snapshot)
                        else:
                            kb.metadata = snapshot
                            self._persist(kb)
                    except Exception as recovery:
                        rollback_failed = True
                        reason = f'文档回滚失败，需要修复: {type(original).__name__}: {original}; {type(recovery).__name__}: {recovery}; 恢复备份: {backup_dir}'
                        if hasattr(kb, 'mark_repair_required'):
                            kb.mark_repair_required(reason)
                        elif state:
                            state.set('repair_required', reason)
                        raise RepairRequired(reason) from recovery
                    raise
                finally:
                    self._catalog_cache = None
                    if not rollback_failed:
                        self._cleanup_backup(backup_dir)

    @staticmethod
    def _cleanup_backup(path):
        try:
            shutil.rmtree(path)
        except OSError:
            logging.getLogger(__name__).warning('临时备份清理失败，已保留: %s', path)

    @staticmethod
    def _write_bytes(path, content):
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('wb') as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _index(self, kb, path, stable_id):
        kb.metadata.setdefault(str(path), {})['document_id'] = stable_id
        if kb._update_document(str(path)) is False:
            raise RuntimeError('文档索引未提交')
        kb.metadata.setdefault(str(path), {})['document_id'] = stable_id

    def _remove_index(self, kb, path):
        if hasattr(kb, 'delete_document'):
            kb.delete_document(str(path))
        else:
            old = kb.metadata.pop(str(path), {})
            kb._remove_chunk_ids(old.get('chunk_ids', []))

    @file_operation
    def rename(self, document_id: str, filename: str) -> Dict[str, Any]:
        with self.mutation_lock:
            old_path = self._resolve_id(document_id)
            new_path = self._safe_path(filename)
            if new_path == old_path:
                raise ValueError('新文件名与当前文件名相同')
            if new_path.exists():
                raise ValueError('目标文件名已存在')
            kb = self.kb_provider()
            stable_id = self._stored_document_id(old_path, kb)
            with self._transaction(kb, [old_path, new_path]):
                os.replace(old_path, new_path)
                self._index(kb, new_path, stable_id)
                self._remove_index(kb, old_path)
            return {**self._metadata(new_path, kb), 'status': 'renamed'}

    @file_operation
    def update_content(self, document_id: str, content: str) -> Dict[str, Any]:
        with self.mutation_lock:
            path = self._resolve_id(document_id)
            if path.suffix.lower() not in EDITABLE_DOCUMENT_SUFFIXES:
                raise ValueError('当前格式不支持正文编辑，请使用替换文件')
            if not isinstance(content, str):
                raise ValueError('文档内容必须是文本')
            encoded = content.encode('utf-8')
            if len(encoded) > MAX_EDITABLE_CONTENT_BYTES:
                raise FileLimitError('文档内容不能超过 20 MB')
            kb = self.kb_provider()
            stable_id = self._stored_document_id(path, kb)
            with self._transaction(kb, [path]):
                self._write_bytes(path, encoded)
                self._index(kb, path, stable_id)
            return {**self._metadata(path, kb), 'status': 'updated'}

    @contextmanager
    def _staged(self, stream):
        # Hidden .tmp files are never discoverable as knowledge documents.
        fd, name = tempfile.mkstemp(prefix='.upload-', suffix='.tmp', dir=self.documents_dir)
        path = Path(name)
        try:
            with os.fdopen(fd, 'wb') as destination:
                copy_limited(stream, destination)
            yield path
        finally:
            path.unlink(missing_ok=True)

    @file_operation
    def upload(self, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        path = self._safe_path(filename)
        with self._staged(stream) as staged, self.mutation_lock:
            if path.exists():
                raise ValueError('文件已存在，请使用替换操作')
            kb = self.kb_provider()
            with self._transaction(kb, [path]):
                os.replace(staged, path)
                self._index(kb, path, self._document_id(path))
            return self._metadata(path, kb)

    @file_operation
    def delete_document(self, document_id: str) -> Dict[str, Any]:
        with self.mutation_lock:
            path = self._resolve_id(document_id)
            kb = self.kb_provider()
            with self._transaction(kb, [path]):
                self._remove_index(kb, path)
                path.unlink()
            return {'status': 'deleted', 'document_id': document_id}

    @file_operation
    def replace(self, document_id: str, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        new_path = self._safe_path(filename)
        with self._staged(stream) as staged, self.mutation_lock:
            old_path = self._resolve_id(document_id)
            if new_path != old_path and new_path.exists():
                raise ValueError('目标文件名已存在')
            kb = self.kb_provider()
            stable_id = self._stored_document_id(old_path, kb)
            with self._transaction(kb, list(dict.fromkeys([old_path, new_path]))):
                os.replace(staged, new_path)
                self._index(kb, new_path, stable_id)
                if old_path != new_path:
                    self._remove_index(kb, old_path)
                    old_path.unlink()
            return {'status': 'replaced', 'document_id': stable_id, 'filename': new_path.name}
