"""Enumerate indexed documents and read their indexed text without vector Top-k.

No filesystem content is opened here. IDs resolve only through the active KB
metadata; cursors bind pagination to a query and an index revision.
"""
import base64
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from config import DOCUMENTS_DIR
from src.index_state import retrieval_guard


FILTER_FIELDS = {
    "document_id", "source", "source_contains", "source_prefix",
    "filename_contains", "title_contains", "doc_type", "mtime_after", "mtime_before",
}
TYPE_ALIASES = {"md": "markdown", "txt": "text", "htm": "html"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _fold(value):
    # Course codes such as "UFUG 1106" and "ufug1106" share a spelling.
    return "".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 的整数")
    return value


def _date(value, end=False):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        stamp = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        return stamp + (86400 if end else 0)
    except ValueError:
        raise ValueError("日期必须是有效的 YYYY-MM-DD（UTC）") from None


def _cursor(kind, revision, scope, position):
    value = json.dumps([1, kind, revision, scope, position], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode()


def _position(cursor, kind, revision, scope, default):
    if cursor is None:
        return default
    try:
        if not isinstance(cursor, str) or not 0 < len(cursor) <= 2048:
            raise ValueError()
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) != 5 or value[:2] != [1, kind]:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("无效游标，请从第一页重新查询") from None
    if value[2] != revision or value[3] != scope:
        raise ValueError("目录、文档或查询条件已变化，请从第一页重新查询")
    return value[4]


class DocumentCatalog:
    def __init__(self, kb):
        self.kb = kb
        self.root = Path(getattr(getattr(kb, "loader", None), "base_dir", DOCUMENTS_DIR)).resolve()

    def _records(self):
        records, excluded = [], 0
        chunk_meta = getattr(self.kb, "_chunk_metadata", {})
        for source, raw in self.kb.metadata.items():
            try:
                path = Path(source).resolve()
                relative = path.relative_to(self.root).as_posix()
            except (ValueError, OSError):
                excluded += 1
                continue
            # Rebuild seed records have no readable indexed content yet.
            ids = raw.get("chunk_ids")
            if not isinstance(ids, list) or not ids:
                excluded += 1
                continue
            first = chunk_meta.get(ids[0], {})
            title = str(first.get("title") or path.name)
            if "/" in title or "\\" in title:
                title = path.name
            doc_type = str(first.get("doc_type") or path.suffix.lstrip(".")).lower()
            item = {
                "document_id": str(raw.get("document_id") or hashlib.sha256(relative.encode()).hexdigest()[:24]),
                "filename": path.name, "source": relative, "title": title[:200],
                "doc_type": TYPE_ALIASES.get(doc_type, doc_type),
                "mtime": raw.get("mtime"), "chunk_count": len(ids),
            }
            records.append((item, raw))
        records.sort(key=lambda pair: (pair[0]["source"].casefold(), pair[0]["source"]))
        if len({item["document_id"] for item, _ in records}) != len(records):
            raise ValueError("文档 ID 冲突，请联系管理员修复索引")
        revision = _digest([(item, raw.get("updated_at"), raw["chunk_ids"]) for item, raw in records])
        return records, revision, excluded

    @staticmethod
    def _filters(filters):
        if filters is None:
            filters = {}
        if not isinstance(filters, dict) or set(filters) - FILTER_FIELDS:
            raise ValueError("包含未知筛选字段；学分等正文字段尚不支持直接筛选")
        result = {}
        for key, value in filters.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError(f"{key} 必须是非空字符串，最长 1024 字符")
            result[key] = value.strip()
        for key in ("source", "source_prefix", "source_contains"):
            if key in result:
                result[key] = result[key].replace("\\", "/")
        for key in ("source", "source_prefix"):
            if key in result:
                path = result[key]
                if path.startswith("/") or ".." in path.split("/") or re.match(r"^[A-Za-z]:", path):
                    raise ValueError("source/source_prefix 必须使用清单返回的相对路径")
        if "doc_type" in result:
            value = result["doc_type"].lower().lstrip(".")
            result["doc_type"] = TYPE_ALIASES.get(value, value)
            if result["doc_type"] not in {"markdown", "text", "pdf", "html"}:
                raise ValueError("不支持的 doc_type")
        after = _date(result["mtime_after"]) if "mtime_after" in result else None
        before = _date(result["mtime_before"], end=True) if "mtime_before" in result else None
        if after is not None and before is not None and after >= before:
            raise ValueError("日期起点不得晚于终点")
        return result, after, before

    @retrieval_guard
    def find_documents(self, filters=None, page_size=20, cursor=None, view="list"):
        if view not in {"list", "count"}:
            raise ValueError("view 必须是 list 或 count")
        page_size = _integer(page_size, "page_size", 1, 100)
        filters, after, before = self._filters(filters)
        records, revision, excluded = self._records()
        matches = []
        for item, _ in records:
            if any(item[key] != filters[key] for key in ("document_id", "source", "doc_type") if key in filters):
                continue
            if any(_fold(filters[key]) not in _fold(item[field]) for key, field in (
                ("source_contains", "source"), ("filename_contains", "filename"), ("title_contains", "title"),
            ) if key in filters):
                continue
            if "source_prefix" in filters:
                prefix = _fold(filters["source_prefix"].rstrip("/"))
                source = _fold(item["source"])
                if source != prefix and not source.startswith(prefix + "/"):
                    continue
            mtime = item["mtime"]
            if (after is not None or before is not None) and not isinstance(mtime, (int, float)):
                continue
            if after is not None and mtime < after or before is not None and mtime >= before:
                continue
            matches.append(item)
        if view == "count":
            return {"view": "count", "total": len(matches), "items": [], "returned_count": 0,
                    "next_cursor": None, "excluded_documents": excluded, "scope": "active_indexed_documents"}
        offset = _position(cursor, "find", revision, _digest(filters), 0)
        _integer(offset, "游标位置", 0, len(matches))
        items = []
        for item in matches[offset:offset + page_size]:
            if len(json.dumps(items + [item], ensure_ascii=False).encode()) > 12000:
                break
            items.append(item)
        if offset < len(matches) and not items:
            raise ValueError("单条文档元数据过长，无法返回")
        next_offset = offset + len(items)
        return {
            "total": len(matches), "items": items, "returned_count": len(items),
            "next_cursor": _cursor("find", revision, _digest(filters), next_offset) if next_offset < len(matches) else None,
            "excluded_documents": excluded, "scope": "active_indexed_documents",
        }

    @retrieval_guard
    def read_documents(self, document_ids, max_chars=2000, cursor=None):
        if (not isinstance(document_ids, list) or not 1 <= len(document_ids) <= 10
                or any(not isinstance(value, str) or not 0 < len(value) <= 160 for value in document_ids)):
            raise ValueError("document_ids 必须包含 1 到 10 个清单返回的文档 ID")
        ids = list(dict.fromkeys(document_ids))
        max_chars = _integer(max_chars, "max_chars", 1, 8000)
        records, _, excluded = self._records()
        by_id = {item["document_id"]: (item, raw) for item, raw in records}
        documents = []
        texts = getattr(self.kb, "_chunk_texts", {})
        for document_id in ids:
            if document_id not in by_id:
                raise ValueError("文档 ID 不存在或已失效，请重新 find_documents")
            item, raw = by_id[document_id]
            if any(chunk_id not in texts for chunk_id in raw["chunk_ids"]):
                raise ValueError("索引正文不完整，请联系管理员修复；不能据此判断无相关内容")
            text = "\n\n".join(texts[chunk_id] for chunk_id in raw["chunk_ids"])
            documents.append((item, text))
        revision = _digest([(item, hashlib.sha256(text.encode()).hexdigest()) for item, text in documents])
        position = _position(cursor, "read", revision, _digest(ids), [0, 0])
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("无效读取游标")
        index = _integer(position[0], "文档位置", 0, len(documents) - 1)
        offset = _integer(position[1], "正文位置", 0, len(documents[index][1]))
        items, char_budget, byte_budget = [], max_chars, 12000
        while index < len(documents):
            item, text = documents[index]
            part = text[offset:offset + char_budget].encode()[:byte_budget].decode("utf-8", errors="ignore")
            if not part and offset < len(text):
                break
            end = offset + len(part)
            items.append({**item, "content": part, "start_offset": offset, "end_offset": end,
                          "total_chars": len(text), "has_more_content": end < len(text)})
            char_budget -= len(part)
            byte_budget -= len(part.encode())
            if end < len(text):
                offset = end
                break
            index, offset = index + 1, 0
            if not char_budget or not byte_budget:
                break
        return {
            "items": items, "requested_documents": len(ids),
            "next_cursor": _cursor("read", revision, _digest(ids), [index, offset]) if index < len(documents) else None,
            "excluded_documents": excluded,
        }
