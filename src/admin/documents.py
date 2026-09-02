"""Safe document projections and mutations for the admin console."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Optional


class AdminDocumentService:
    def __init__(self, *, kb_provider: Callable[[], Any], documents_dir: str, supported_formats: set[str]) -> None:
        self.kb_provider = kb_provider
        self.documents_dir = Path(documents_dir).resolve()
        self.supported_formats = {extension.lower() for extension in supported_formats}
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
        relative = path.resolve().relative_to(self.documents_dir).as_posix()
        return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]

    def _resolve_id(self, document_id: str) -> Path:
        if not document_id or len(document_id) > 64 or any(char not in "0123456789abcdef" for char in document_id.lower()):
            raise LookupError("文档不存在")
        for path in self.documents_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in self.supported_formats and self._document_id(path) == document_id:
                return path
        raise FileNotFoundError("文档不存在")

    def _metadata(self, path: Path) -> Dict[str, Any]:
        kb = self.kb_provider()
        raw = getattr(kb, "metadata", {}).get(str(path), {})
        stat = path.stat()
        return {
            "document_id": self._document_id(path),
            "filename": path.name,
            "source": str(path),
            "doc_type": path.suffix.lower().lstrip(".") or "unknown",
            "size": int(raw.get("size", stat.st_size)),
            "mtime": raw.get("mtime", stat.st_mtime),
            "chunk_count": int(raw.get("chunk_count", 0)),
            "index_status": "indexed" if raw else "not_indexed",
            "last_error": None,
            "updated_at": raw.get("updated_at"),
        }

    def list_documents(self, *, limit: int = 50, offset: int = 0, query: Optional[str] = None) -> list[Dict[str, Any]]:
        items = []
        query_lower = query.strip().lower() if query else None
        for path in sorted(self.documents_dir.rglob("*"), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in self.supported_formats:
                continue
            item = self._metadata(path)
            if query_lower and query_lower not in f"{item['filename']} {item['source']}".lower():
                continue
            items.append(item)
        return items[max(0, offset):max(0, offset) + max(1, min(limit, 200))]

    def get_document(self, document_id: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        item = self._metadata(path)
        try:
            docs = self.kb_provider().loader.load_file(str(path))
            item["content"] = "\n\n".join(str(doc.page_content) for doc in docs)[:20000]
        except Exception as error:
            item["content"] = ""
            item["content_error"] = f"{type(error).__name__}: {error}"
        return item

    def upload(self, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        path = self._safe_path(filename)
        if path.exists():
            raise ValueError("文件已存在，请使用替换操作")
        with path.open("wb") as destination:
            shutil.copyfileobj(stream, destination)
        kb = self.kb_provider()
        try:
            if kb._update_document(str(path)) is False:
                raise RuntimeError("文档索引未提交")
            kb._save_metadata()
            kb._save_chunk_texts()
            kb._save_store()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self._metadata(path)

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        path = self._resolve_id(document_id)
        kb = self.kb_provider()
        raw = getattr(kb, "metadata", {}).pop(str(path), {})
        for chunk_id in raw.get("chunk_ids", []):
            kb._remove_chunk_ids([chunk_id])
        path.unlink()
        kb._save_metadata()
        kb._save_chunk_texts()
        kb._save_store()
        return {"status": "deleted", "document_id": document_id}

    def replace(self, document_id: str, filename: str, stream: BinaryIO) -> Dict[str, Any]:
        old_path = self._resolve_id(document_id)
        new_path = self._safe_path(filename)
        if new_path != old_path and new_path.exists():
            raise ValueError("目标文件名已存在")
        old_bytes = old_path.read_bytes()
        with new_path.open("wb") as destination:
            shutil.copyfileobj(stream, destination)
        kb = self.kb_provider()
        try:
            if kb._update_document(str(new_path)) is False:
                raise RuntimeError("文档索引未提交")
            if new_path != old_path:
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
        return {"status": "replaced", "document_id": self._document_id(new_path), "filename": new_path.name}
