"""Keep document evidence valid and compact when building model context."""
import json


DOCUMENT_TOOLS = {"find_documents", "read_documents"}
EVIDENCE_PREFIX = "[知识库检索结果]\n"


def _payload(message):
    content = str(message.get("content") or "")
    prefix = EVIDENCE_PREFIX if content.startswith(EVIDENCE_PREFIX) else ""
    try:
        data = json.loads(content[len(prefix):])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    kind = message.get("name") if message.get("role") == "tool" else data.get("tool") if prefix else None
    if kind not in DOCUMENT_TOOLS:
        return None
    return prefix, kind, data


def _save(message, prefix, data):
    message["content"] = prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def compact_document_message(message):
    """Losslessly encode repeated catalog keys once, retaining every row/value."""
    payload = _payload(message)
    if payload is None:
        return None
    prefix, kind, data = payload
    data["tool"] = kind
    items = data["items"]
    if kind == "find_documents" and items and all(isinstance(item, dict) for item in items):
        columns = list(items[0])
        if all(set(item) == set(columns) for item in items):
            data["item_columns"] = columns
            data["items"] = [[item[column] for column in columns] for item in items]
    _save(message, prefix, data)
    return kind


def shrink_document_message(message):
    """Remove whole rows/body suffixes only as a last resort, marking omissions.

    Retrieval offsets, totals and cursors retain their original meaning;
    visible_end_offset describes only the body retained in this context.
    """
    payload = _payload(message)
    if payload is None:
        return False
    prefix, kind, data = payload
    if kind == "find_documents":
        if not data["items"]:
            return False
        keep = len(data["items"]) // 2
        data["context_omitted_items"] = data.get("context_omitted_items", 0) + len(data["items"]) - keep
        data["items"] = data["items"][:keep]
    else:
        candidates = [item for item in data["items"] if isinstance(item, dict) and item.get("content")]
        if not candidates:
            return False
        item = max(candidates, key=lambda value: len(value["content"]))
        body = item["content"]
        item["content"] = body[:len(body) // 2]
        item["context_omitted_chars"] = item.get("context_omitted_chars", 0) + len(body) - len(item["content"])
        item["visible_end_offset"] = item["start_offset"] + len(item["content"])
    _save(message, prefix, data)
    return True
