from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from services.config import DATA_DIR

MAX_CONVERSATIONS_PER_OWNER = 200
MAX_TURNS_PER_CONVERSATION = 200
MAX_IMAGES_PER_TURN = 20
MAX_REFERENCE_IMAGES_PER_TURN = 8


def _now_iso() -> str:
    return datetime.now().isoformat()


def _clean(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _owner_key(identity: dict[str, object]) -> str:
    return _clean(identity.get("id") or identity.get("subject_id"), "anonymous")


def _sort_conversations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: _timestamp(item.get("updatedAt") or item.get("createdAt")), reverse=True)


def _pick_latest(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    return incoming if _timestamp(incoming.get("updatedAt")) >= _timestamp(current.get("updatedAt")) else current


def _normalize_image(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    image_id = _clean(raw.get("id")) or _clean(raw.get("taskId"))
    if not image_id:
        return None
    status = _clean(raw.get("status"))
    if status not in {"loading", "success", "error"}:
        status = "success" if raw.get("url") or raw.get("b64_json") else "loading"
    item: dict[str, Any] = {
        "id": image_id,
        "status": status,
    }
    for key in ("taskId", "url", "b64_json", "revised_prompt", "error"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            item[key] = value
    return item


def _normalize_reference_image(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    data_url = _clean(raw.get("dataUrl"))
    if not data_url:
        return None
    return {
        "name": _clean(raw.get("name"), "reference.png"),
        "type": _clean(raw.get("type"), "image/png"),
        "dataUrl": data_url,
    }


def _normalize_turn(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    turn_id = _clean(raw.get("id")) or f"turn-{int(time.time() * 1000)}"
    created_at = _clean(raw.get("createdAt"), _now_iso())
    status = _clean(raw.get("status"))
    if status not in {"queued", "generating", "success", "error"}:
        status = "success"
    images = [
        image
        for image in (_normalize_image(item) for item in (raw.get("images") if isinstance(raw.get("images"), list) else []))
        if image is not None
    ][:MAX_IMAGES_PER_TURN]
    reference_images = [
        image
        for image in (
            _normalize_reference_image(item)
            for item in (raw.get("referenceImages") if isinstance(raw.get("referenceImages"), list) else [])
        )
        if image is not None
    ][:MAX_REFERENCE_IMAGES_PER_TURN]
    try:
        count = max(1, int(raw.get("count") or len(images) or 1))
    except (TypeError, ValueError):
        count = max(1, len(images) or 1)
    return {
        "id": turn_id,
        "prompt": _clean(raw.get("prompt")),
        "model": _clean(raw.get("model"), "gpt-image-2"),
        "mode": "edit" if raw.get("mode") == "edit" else "generate",
        "referenceImages": reference_images,
        "count": count,
        "size": _clean(raw.get("size")),
        "images": images,
        "createdAt": created_at,
        "status": status,
        **({"error": _clean(raw.get("error"))} if _clean(raw.get("error")) else {}),
    }


def _normalize_conversation(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    conversation_id = _clean(raw.get("id"))
    if not conversation_id:
        return None
    turns = [
        turn
        for turn in (_normalize_turn(item) for item in (raw.get("turns") if isinstance(raw.get("turns"), list) else []))
        if turn is not None
    ][:MAX_TURNS_PER_CONVERSATION]
    created_at = _clean(raw.get("createdAt"), turns[0]["createdAt"] if turns else _now_iso())
    updated_at = _clean(raw.get("updatedAt"), turns[-1]["createdAt"] if turns else created_at)
    return {
        "id": conversation_id,
        "title": _clean(raw.get("title"), turns[0]["prompt"][:12] if turns else "未命名对话"),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "turns": turns,
    }


class ImageConversationService:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _load_locked(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        owners = data.get("owners") if isinstance(data, dict) else None
        if not isinstance(owners, dict):
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for owner, items in owners.items():
            if not isinstance(items, list):
                continue
            normalized = [item for item in (_normalize_conversation(item) for item in items) if item is not None]
            result[_clean(owner, "anonymous")] = _sort_conversations(normalized)[:MAX_CONVERSATIONS_PER_OWNER]
        return result

    def _save_locked(self, data: dict[str, list[dict[str, Any]]]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"owners": data}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def list(self, identity: dict[str, object]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        with self._lock:
            return list(self._load_locked().get(owner, []))

    def save(self, identity: dict[str, object], conversation: dict[str, Any]) -> list[dict[str, Any]]:
        normalized = _normalize_conversation(conversation)
        if normalized is None:
            return self.list(identity)
        owner = _owner_key(identity)
        with self._lock:
            data = self._load_locked()
            items = data.get(owner, [])
            by_id = {item["id"]: item for item in items}
            current = by_id.get(normalized["id"])
            by_id[normalized["id"]] = _pick_latest(current, normalized) if current else normalized
            data[owner] = _sort_conversations(list(by_id.values()))[:MAX_CONVERSATIONS_PER_OWNER]
            self._save_locked(data)
            return list(data[owner])

    def save_many(self, identity: dict[str, object], conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        normalized_items = [
            item
            for item in (_normalize_conversation(conversation) for conversation in conversations)
            if item is not None
        ]
        with self._lock:
            data = self._load_locked()
            by_id = {item["id"]: item for item in data.get(owner, [])}
            for conversation in normalized_items:
                current = by_id.get(conversation["id"])
                by_id[conversation["id"]] = _pick_latest(current, conversation) if current else conversation
            data[owner] = _sort_conversations(list(by_id.values()))[:MAX_CONVERSATIONS_PER_OWNER]
            self._save_locked(data)
            return list(data[owner])

    def delete(self, identity: dict[str, object], conversation_id: str) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        normalized_id = _clean(conversation_id)
        with self._lock:
            data = self._load_locked()
            data[owner] = [item for item in data.get(owner, []) if item.get("id") != normalized_id]
            self._save_locked(data)
            return list(data[owner])

    def clear(self, identity: dict[str, object]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        with self._lock:
            data = self._load_locked()
            data[owner] = []
            self._save_locked(data)
            return []


image_conversation_service = ImageConversationService(DATA_DIR / "image_conversations.json")
