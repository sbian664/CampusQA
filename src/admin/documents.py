"""Safe document projections and mutations for the admin console."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, Callable, Dict, Optional

EDITABLE_DOCUMENT_SUFFIXES = {".md", ".txt", ".html"}
MAX_EDITABLE_CONTENT_BYTES = 5 * 1024 * 1024

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

    def _resolve_id(self, document_id: str) -> Path:
        if not document_id or len(document_id) > 64 or any(char not in "0123456789abcdef" for char in document_id.lower()):
            raise LookupError("文档不存在")
        for path in self.documents_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in self.supported_formats and self._document_id(path) == document_id:
                return path
        raise FileNotFoundError("文档不存在")

    def _metadata(self, path: Path, kb: Any = None) -> Dict[str, Any]:
        kb = kb if kb is not None else self.kb_provider()
        raw = getattr(kb, "metadata", {}).get(str(path), {})
        stat = path.stat()
        return {
            "document_id": self._document_id(path),
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

    def get_document(self, document_id: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        item = self._metadata(path)
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

    def update_content(self, document_id: str, content: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        if path.suffix.lower() not in EDITABLE_DOCUMENT_SUFFIXES:
            raise ValueError("当前格式不支持正文编辑，请使用替换文件")
        if not isinstance(content, str):
            raise ValueError("文档内容必须是文本")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_EDITABLE_CONTENT_BYTES:
            raise ValueError("文档内容不能超过 5 MB")

        old_bytes = path.read_bytes()
        old_stat = path.stat()
        kb = self.kb_provider()
        temp_path = path.with_name(f".{path.name}.admin-edit-{uuid.uuid4().hex}.tmp")
        try:
            with self.mutation_lock:
                temp_path.write_bytes(encoded)
                os.replace(temp_path, path)
                if kb._update_document(str(path)) is False:
                    raise RuntimeError("文档索引未提交")
                kb._save_metadata()
                kb._save_chunk_texts()
                kb._save_store()
        except Exception:
            temp_path.unlink(missing_ok=True)
            path.write_bytes(old_bytes)
            os.utime(path, (old_stat.st_atime, old_stat.st_mtime))
            raise
        self.invalidate_catalog()
        result = self._metadata(path)
        result["status"] = "updated"
        return result

    def upload(self, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        path = self._safe_path(filename)
        if path.exists():
            raise ValueError("文件已存在，请使用替换操作")
        kb = self.kb_provider()
        try:
            with self.mutation_lock:
                with path.open("wb") as destination:
                    shutil.copyfileobj(stream, destination)
                if kb._update_document(str(path)) is False:
                    raise RuntimeError("文档索引未提交")
                kb._save_metadata()
                kb._save_chunk_texts()
                kb._save_store()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.invalidate_catalog()
        return self._metadata(path)

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        kb = self.kb_provider()
        with self.mutation_lock:
            if hasattr(kb, "delete_document"):
                kb.delete_document(str(path))
            else:
                raw = getattr(kb, "metadata", {}).pop(str(path), {})
                for chunk_id in raw.get("chunk_ids", []):
                    kb._remove_chunk_ids([chunk_id])
                kb._save_metadata()
                kb._save_chunk_texts()
                kb._save_store()
            path.unlink()
        self.invalidate_catalog()
        return {"status": "deleted", "document_id": document_id}

    def replace(self, document_id: str, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        old_path = self._resolve_id(document_id)
        new_path = self._safe_path(filename)
        if new_path != old_path and new_path.exists():
            raise ValueError("目标文件名已存在")
        old_bytes = old_path.read_bytes()
        kb = self.kb_provider()
        try:
            with self.mutation_lock:
                with new_path.open("wb") as destination:
                    shutil.copyfileobj(stream, destination)
                if kb._update_document(str(new_path)) is False:
                    raise RuntimeError("文档索引未提交")
                if new_path != old_path:
                    if hasattr(kb, "delete_document"):
                        kb.delete_document(str(old_path))
                    else:
                        old_metadata = getattr(kb, "metadata", {}).pop(str(old_path), {})
                        for chunk_id in old_metadata.get("chunk_ids", []):
                            kb._remove_chunk_ids([chunk_id])
                    old_path.unlink()
                kb._save_metadata()
                kb._save_chunk_texts()
                kb._save_store()
        except Exception:
            if new_path == old_path:
                old_path.write_bytes(old_bytes)
            else:
                new_path.unlink(missing_ok=True)
            raise
        self.invalidate_catalog()
        return {"status": "replaced", "document_id": self._document_id(new_path), "filename": new_path.name}
