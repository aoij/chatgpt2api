from __future__ import annotations

import json
import os
import time
import atexit
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from services.config import DATA_DIR
from services.storage.database_storage import Base, ImageConversationModel

MAX_CONVERSATIONS_PER_OWNER = 80
MAX_TURNS_PER_CONVERSATION = 80
MAX_IMAGES_PER_TURN = 20
MAX_REFERENCE_IMAGES_PER_TURN = 8
DB_QUERY_LIMIT_PER_OWNER = MAX_CONVERSATIONS_PER_OWNER * 2


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


def _pick_latest(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return incoming
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


def _database_url() -> str:
    configured = (
        os.getenv("IMAGE_CONVERSATIONS_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()
    if configured:
        return configured
    # 默认跟账号数据库共用同一个 SQLite 文件，避免继续依赖 image_conversations.json。
    return f"sqlite:///{(DATA_DIR / 'accounts.db').as_posix()}"


def _create_database_engine(database_url: str):
    url = make_url(database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
    if url.drivername.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool if url.database == ":memory:" else NullPool
    return create_engine(database_url, **kwargs)


class ImageConversationService:
    def __init__(self, path: Path, *, database_url: str | None = None, enable_database: bool = True):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._dirty = False
        self._db_available = False
        self._engine = None
        self._Session = None
        self.database_url = database_url if database_url is not None else _database_url()

        if enable_database:
            self._init_database()

        if self._db_available:
            self._migrate_legacy_json_to_db()
        else:
            self._data = self._load_from_disk()

    def _init_database(self) -> None:
        try:
            self._engine = _create_database_engine(self.database_url)
            Base.metadata.create_all(self._engine, tables=[ImageConversationModel.__table__])
            self._Session = sessionmaker(bind=self._engine)
            self._db_available = True
            print(f"[image-conversations] using database storage: {self._mask_database_url(self.database_url)}")
        except Exception as exc:
            self._db_available = False
            self._engine = None
            self._Session = None
            print(f"[image-conversations] database init failed, fallback to json: {exc}")

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    @staticmethod
    def _mask_database_url(database_url: str) -> str:
        if "://" not in database_url:
            return database_url
        try:
            protocol, rest = database_url.split("://", 1)
            if "@" in rest:
                credentials, host = rest.split("@", 1)
                if ":" in credentials:
                    username, _ = credentials.split(":", 1)
                    return f"{protocol}://{username}:****@{host}"
            return database_url
        except Exception:
            return database_url

    def _handle_db_error_locked(self, action: str, exc: Exception) -> None:
        self._db_available = False
        self._cache.clear()
        self._data = self._load_from_disk()
        print(f"[image-conversations] database {action} failed, fallback to json: {exc}")

    def _load_from_disk(self) -> dict[str, list[dict[str, Any]]]:
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

    def _save_locked(self) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps({"owners": self._data}, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
        self._dirty = False

    def _row_to_conversation(self, row: ImageConversationModel) -> dict[str, Any] | None:
        try:
            payload = json.loads(row.payload)
        except Exception:
            return None
        return _normalize_conversation(payload)

    def _load_owner_from_db_locked(self, owner: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and owner in self._cache:
            return self._cache[owner]
        if self._Session is None:
            return []
        session = self._Session()
        try:
            rows = (
                session.query(ImageConversationModel)
                .filter(ImageConversationModel.owner_id == owner)
                .order_by(ImageConversationModel.updated_at.desc())
                .limit(DB_QUERY_LIMIT_PER_OWNER)
                .all()
            )
            items = [item for item in (self._row_to_conversation(row) for row in rows) if item is not None]
            items = _sort_conversations(items)[:MAX_CONVERSATIONS_PER_OWNER]
            self._cache[owner] = items
            return items
        finally:
            session.close()

    def _replace_owner_rows_locked(self, owner: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._Session is None:
            return []
        normalized_items = _sort_conversations(items)[:MAX_CONVERSATIONS_PER_OWNER]
        session = self._Session()
        try:
            session.query(ImageConversationModel).filter(ImageConversationModel.owner_id == owner).delete()
            for item in normalized_items:
                session.add(
                    ImageConversationModel(
                        owner_id=owner,
                        conversation_id=item["id"],
                        payload=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                        created_at=_clean(item.get("createdAt"), _now_iso()),
                        updated_at=_clean(item.get("updatedAt"), _clean(item.get("createdAt"), _now_iso())),
                    )
                )
            session.commit()
            self._cache[owner] = normalized_items
            return normalized_items
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _migrate_legacy_json_to_db(self) -> None:
        legacy = self._load_from_disk()
        if not legacy:
            return
        migrated = 0
        with self._lock:
            for owner, legacy_items in legacy.items():
                try:
                    current = self._load_owner_from_db_locked(owner, refresh=True)
                    by_id = {item["id"]: item for item in current}
                    for conversation in legacy_items:
                        by_id[conversation["id"]] = _pick_latest(by_id.get(conversation["id"]), conversation)
                    next_items = self._replace_owner_rows_locked(owner, list(by_id.values()))
                    migrated += len(next_items)
                except Exception as exc:
                    self._handle_db_error_locked("migration", exc)
                    return
        print(f"[image-conversations] migrated legacy json conversations to database: {migrated}")

    def list(self, identity: dict[str, object]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        with self._lock:
            if self._db_available:
                try:
                    return list(self._load_owner_from_db_locked(owner))
                except Exception as exc:
                    self._handle_db_error_locked("list", exc)
            return list(self._data.get(owner, []))

    def save(self, identity: dict[str, object], conversation: dict[str, Any]) -> list[dict[str, Any]]:
        normalized = _normalize_conversation(conversation)
        if normalized is None:
            return self.list(identity)
        owner = _owner_key(identity)
        with self._lock:
            if self._db_available:
                try:
                    items = self._load_owner_from_db_locked(owner)
                    by_id = {item["id"]: item for item in items}
                    by_id[normalized["id"]] = _pick_latest(by_id.get(normalized["id"]), normalized)
                    return list(self._replace_owner_rows_locked(owner, list(by_id.values())))
                except Exception as exc:
                    self._handle_db_error_locked("save", exc)
            items = self._data.get(owner, [])
            by_id = {item["id"]: item for item in items}
            by_id[normalized["id"]] = _pick_latest(by_id.get(normalized["id"]), normalized)
            self._data[owner] = _sort_conversations(list(by_id.values()))[:MAX_CONVERSATIONS_PER_OWNER]
            self._dirty = True
            self._save_locked()
            return list(self._data[owner])

    def save_many(self, identity: dict[str, object], conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        normalized_items = [
            item
            for item in (_normalize_conversation(conversation) for conversation in conversations)
            if item is not None
        ]
        with self._lock:
            if self._db_available:
                try:
                    by_id = {item["id"]: item for item in self._load_owner_from_db_locked(owner)}
                    for conversation in normalized_items:
                        by_id[conversation["id"]] = _pick_latest(by_id.get(conversation["id"]), conversation)
                    return list(self._replace_owner_rows_locked(owner, list(by_id.values())))
                except Exception as exc:
                    self._handle_db_error_locked("save_many", exc)
            by_id = {item["id"]: item for item in self._data.get(owner, [])}
            for conversation in normalized_items:
                by_id[conversation["id"]] = _pick_latest(by_id.get(conversation["id"]), conversation)
            self._data[owner] = _sort_conversations(list(by_id.values()))[:MAX_CONVERSATIONS_PER_OWNER]
            self._dirty = True
            self._save_locked()
            return list(self._data[owner])

    def delete(self, identity: dict[str, object], conversation_id: str) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        normalized_id = _clean(conversation_id)
        with self._lock:
            if self._db_available:
                try:
                    items = [item for item in self._load_owner_from_db_locked(owner) if item.get("id") != normalized_id]
                    return list(self._replace_owner_rows_locked(owner, items))
                except Exception as exc:
                    self._handle_db_error_locked("delete", exc)
            self._data[owner] = [item for item in self._data.get(owner, []) if item.get("id") != normalized_id]
            self._dirty = True
            self._save_locked()
            return list(self._data[owner])

    def clear(self, identity: dict[str, object]) -> list[dict[str, Any]]:
        owner = _owner_key(identity)
        with self._lock:
            if self._db_available:
                try:
                    self._replace_owner_rows_locked(owner, [])
                    return []
                except Exception as exc:
                    self._handle_db_error_locked("clear", exc)
            self._data[owner] = []
            self._dirty = True
            self._save_locked()
            return []


image_conversation_service = ImageConversationService(DATA_DIR / "image_conversations.json")
atexit.register(image_conversation_service.close)
