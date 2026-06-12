from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Literal
from urllib import request as urllib_request
from urllib.error import URLError

from services.config import config
from services.storage.base import StorageBackend

AuthRole = Literal["admin", "user"]


class ImageQuotaExceeded(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _post_turing_user_sync(sync_url: str, sync_key: str, payload: dict[str, object]) -> None:
    try:
        req = urllib_request.Request(
            sync_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Admin-Key": sync_key,
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=3) as resp:
            resp.read()
    except (OSError, URLError, TimeoutError) as exc:
        print(f"[turing-user-sync] failed key_id={payload.get('chatgptKeyId')}: {exc}")


def _notify_turing_user_sync(item: dict[str, object]) -> None:
    if not isinstance(item, dict) or item.get("role") != "user":
        return
    sync_url = str(os.getenv("CHATGPT2API_TURING_USER_SYNC_URL") or "").strip()
    if not sync_url:
        base_url = str(os.getenv("CHATGPT2API_TURING_SYNC_URL") or "").strip()
        if base_url.endswith("/delete-sync"):
            sync_url = base_url[: -len("/delete-sync")] + "/user-sync"
    sync_key = str(os.getenv("CHATGPT2API_TURING_SYNC_KEY") or "").strip()
    if not sync_url or not sync_key:
        return
    payload = {
        "chatgptKeyId": str(item.get("id") or "").strip(),
        "openId": str(item.get("open_id") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "username": str(item.get("username") or "").strip(),
        "avatarUrl": str(item.get("avatar_url") or "").strip(),
        "quota": item.get("quota"),
        "enabled": bool(item.get("enabled", True)),
    }
    Thread(target=_post_turing_user_sync, args=(sync_url, sync_key, payload), daemon=True).start()


class AuthService:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._lock = Lock()
        self._needs_save = False
        self._items = self._load()
        self._last_used_flush_at: dict[str, datetime] = {}
        if self._needs_save:
            try:
                self._save()
            except Exception:
                pass

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_quota(value: object, default: int | None = None) -> int | None:
        if value is None or value == "":
            return default
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _default_name(role: str) -> str:
        return "管理员密钥" if str(role or "").strip().lower() == "admin" else "普通用户"

    def _build_name_locked(self, value: str, *, role: str, exclude_id: str = "") -> str:
        base_name = self._clean(value) or self._default_name(role)
        used_names = {
            self._clean(item.get("name")).lower()
            for item in self._items
            if self._clean(item.get("id")) != self._clean(exclude_id)
        }
        if base_name.lower() not in used_names:
            return base_name
        if self._clean(value):
            raise ValueError("这个名称已经在使用中了")
        index = 2
        while True:
            candidate = f"{base_name} {index}"
            if candidate.lower() not in used_names:
                return candidate
            index += 1

    def _normalize_item(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        role = self._clean(raw.get("role")).lower()
        if role not in {"admin", "user"}:
            return None
        key_hash = self._clean(raw.get("key_hash"))
        if not key_hash:
            return None
        item_id = self._clean(raw.get("id")) or uuid.uuid4().hex[:12]
        name = self._clean(raw.get("name")) or self._default_name(role)
        created_at = self._clean(raw.get("created_at")) or _now_iso()
        last_used_at = self._clean(raw.get("last_used_at")) or None
        quota = self._normalize_quota(raw.get("quota"), default=None)
        link_token = self._clean(raw.get("link_token"))
        username = self._clean(raw.get("username"))
        password_hash = self._clean(raw.get("password_hash"))
        open_id = self._clean(raw.get("open_id"))
        avatar_url = self._clean(raw.get("avatar_url"))
        selected_account_id = self._clean(raw.get("selected_account_id"))
        if role == "user" and not link_token:
            link_token = f"lk-{secrets.token_urlsafe(24)}"
            self._needs_save = True
        recharge_out_trade_no = self._clean(raw.get("recharge_out_trade_no"))
        raw_key = self._clean(raw.get("raw_key"))
        return {
            "id": item_id,
            "name": name,
            "role": role,
            "key_hash": key_hash,
            **({"raw_key": raw_key} if raw_key else {}),
            "enabled": bool(raw.get("enabled", True)),
            "quota": quota,
            "link_token": link_token,
            "username": username,
            "password_hash": password_hash,
            "open_id": open_id,
            "avatar_url": avatar_url,
            "selected_account_id": selected_account_id,
            "recharge_out_trade_no": recharge_out_trade_no,
            "created_at": created_at,
            "last_used_at": last_used_at,
        }

    def _load(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_auth_keys()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_item(item)) is not None]

    def _save(self) -> None:
        self.storage.save_auth_keys(self._items)

    def _save_item(self, item: dict[str, object]) -> None:
        self.storage.save_auth_key(item)

    def _delete_item(self, key_id: str) -> None:
        self.storage.delete_auth_key(key_id)

    def _reload_locked(self) -> None:
        self._items = self._load()

    @staticmethod
    def _public_item(item: dict[str, object]) -> dict[str, object]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "role": item.get("role"),
            "enabled": bool(item.get("enabled", True)),
            "quota": item.get("quota"),
            "key": item.get("raw_key") if item.get("raw_key") else None,
            "link_token": item.get("link_token") if item.get("role") == "user" else None,
            "username": item.get("username") if item.get("role") == "user" else None,
            "open_id": item.get("open_id") if item.get("role") == "user" else None,
            "avatar_url": item.get("avatar_url") if item.get("role") == "user" else None,
            "selected_account_id": item.get("selected_account_id") if item.get("role") == "user" else None,
            "recharge_out_trade_no": item.get("recharge_out_trade_no") if item.get("role") == "user" else None,
            "created_at": item.get("created_at"),
            "last_used_at": item.get("last_used_at"),
        }

    def list_keys(self, role: AuthRole | None = None) -> list[dict[str, object]]:
        with self._lock:
            self._reload_locked()
            items = [item for item in self._items if role is None or item.get("role") == role]
            return [self._public_item(item) for item in items]

    def get_key_by_recharge_order(self, out_trade_no: object) -> dict[str, object] | None:
        normalized_order_no = self._clean(out_trade_no)
        if not normalized_order_no:
            return None
        with self._lock:
            for item in self._items:
                if item.get("role") == "user" and self._clean(item.get("recharge_out_trade_no")) == normalized_order_no:
                    return self._public_item(item)
        return None

    def get_key_by_open_id(self, open_id: object) -> dict[str, object] | None:
        normalized_open_id = self._clean(open_id)
        if not normalized_open_id:
            return None
        with self._lock:
            for item in self._items:
                if item.get("role") == "user" and self._clean(item.get("open_id")) == normalized_open_id:
                    return self._public_item(item)
        return None

    def _ensure_open_id_unique_locked(self, open_id: object, *, exclude_id: str = "") -> str:
        normalized_open_id = self._clean(open_id)
        if not normalized_open_id:
            return ""
        for item in self._items:
            if item.get("role") != "user":
                continue
            if self._clean(item.get("id")) == self._clean(exclude_id):
                continue
            if self._clean(item.get("open_id")) == normalized_open_id:
                raise ValueError("这个微信用户已经绑定到其他账号了")
        return normalized_open_id

    def _ensure_username_unique_locked(self, username: object, *, exclude_id: str = "") -> str:
        normalized_username = self._clean(username).lower()
        if not normalized_username:
            return ""
        for item in self._items:
            if item.get("role") != "user":
                continue
            if self._clean(item.get("id")) == self._clean(exclude_id):
                continue
            if self._clean(item.get("username")).lower() == normalized_username:
                raise ValueError("这个登录账号已经存在")
        return normalized_username

    def create_key(
        self,
        *,
        role: AuthRole,
        name: str = "",
        quota: int | None = None,
        recharge_out_trade_no: str = "",
        key: str = "",
        open_id: str = "",
        avatar_url: str = "",
        selected_account_id: str = "",
        username: str = "",
        password: str = "",
    ) -> tuple[dict[str, object], str]:
        normalized_quota = self._normalize_quota(quota, default=0 if role == "user" else None)
        normalized_order_no = self._clean(recharge_out_trade_no) if role == "user" else ""
        normalized_open_id = self._clean(open_id) if role == "user" else ""
        normalized_avatar_url = self._clean(avatar_url) if role == "user" else ""
        normalized_selected_account_id = self._clean(selected_account_id) if role == "user" else ""
        normalized_username = self._clean(username).lower() if role == "user" else ""
        normalized_password = self._clean(password) if role == "user" else ""
        raw_key = self._clean(key) or f"sk-{secrets.token_urlsafe(24)}"
        item_id = uuid.uuid4().hex[:12]
        item = {
            "id": item_id,
            "name": "",
            "role": role,
            "key_hash": _hash_key(raw_key),
            "raw_key": raw_key,
            "enabled": True,
            "quota": normalized_quota,
            "link_token": f"lk-{secrets.token_urlsafe(24)}" if role == "user" else "",
            "username": normalized_username,
            "password_hash": _hash_key(normalized_password) if normalized_password else "",
            "open_id": normalized_open_id,
            "avatar_url": normalized_avatar_url,
            "selected_account_id": normalized_selected_account_id,
            "recharge_out_trade_no": normalized_order_no,
            "created_at": _now_iso(),
            "last_used_at": None,
        }
        with self._lock:
            self._reload_locked()
            if normalized_order_no:
                for existing in self._items:
                    if existing.get("role") == "user" and self._clean(existing.get("recharge_out_trade_no")) == normalized_order_no:
                        return self._public_item(existing), ""
            if normalized_open_id:
                for existing in self._items:
                    if existing.get("role") != "user" or self._clean(existing.get("open_id")) != normalized_open_id:
                        continue
                    next_item = dict(existing)
                    if normalized_avatar_url:
                        next_item["avatar_url"] = normalized_avatar_url
                    if normalized_selected_account_id:
                        next_item["selected_account_id"] = normalized_selected_account_id
                    if normalized_quota is not None:
                        next_item["quota"] = normalized_quota
                    if normalized_username:
                        next_item["username"] = self._ensure_username_unique_locked(normalized_username, exclude_id=self._clean(existing.get("id")))
                    if normalized_password:
                        next_item["password_hash"] = _hash_key(normalized_password)
                    next_item["enabled"] = True
                    if self._clean(name):
                        next_item["name"] = self._build_name_locked(name, role=role, exclude_id=self._clean(existing.get("id")))
                    self._save_item(next_item)
                    self._items = [next_item if self._clean(item.get("id")) == self._clean(existing.get("id")) else item for item in self._items]
                    public_item = self._public_item(next_item)
                    _notify_turing_user_sync(public_item)
                    return public_item, ""
            if normalized_open_id:
                item["open_id"] = self._ensure_open_id_unique_locked(normalized_open_id, exclude_id=item_id)
            if normalized_username:
                item["username"] = self._ensure_username_unique_locked(normalized_username, exclude_id=item_id)
            item["name"] = self._build_name_locked(name, role=role, exclude_id=item_id)
            self._items.append(item)
            self._save_item(item)
            public_item = self._public_item(item)
            _notify_turing_user_sync(public_item)
            return public_item, raw_key

    def update_key(
        self,
        key_id: str,
        updates: dict[str, object],
        *,
        role: AuthRole | None = None,
    ) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id:
                    continue
                if role is not None and item.get("role") != role:
                    return None
                next_item = dict(item)
                next_role = "admin" if str(next_item.get("role") or "").strip().lower() == "admin" else "user"
                if "name" in updates and updates.get("name") is not None:
                    next_item["name"] = self._build_name_locked(
                        str(updates.get("name") or ""),
                        role=next_role,
                        exclude_id=normalized_id,
                    )
                if "enabled" in updates and updates.get("enabled") is not None:
                    next_item["enabled"] = bool(updates.get("enabled"))
                if "quota" in updates and updates.get("quota") is not None:
                    next_item["quota"] = self._normalize_quota(updates.get("quota"), default=0)
                if "open_id" in updates and updates.get("open_id") is not None:
                    next_item["open_id"] = self._ensure_open_id_unique_locked(updates.get("open_id"), exclude_id=normalized_id)
                if "avatar_url" in updates and updates.get("avatar_url") is not None:
                    next_item["avatar_url"] = self._clean(updates.get("avatar_url"))
                if "selected_account_id" in updates and updates.get("selected_account_id") is not None:
                    next_item["selected_account_id"] = self._clean(updates.get("selected_account_id"))
                if "key" in updates and updates.get("key") is not None:
                    next_key = self._clean(updates.get("key"))
                    if not next_key:
                        raise ValueError("新的专用密钥不能为空")
                    next_item["key_hash"] = _hash_key(next_key)
                    next_item["raw_key"] = next_key
                if "username" in updates and updates.get("username") is not None:
                    next_item["username"] = self._ensure_username_unique_locked(updates.get("username"), exclude_id=normalized_id)
                if "password" in updates and updates.get("password") is not None:
                    password = self._clean(updates.get("password"))
                    if not password:
                        raise ValueError("密码不能为空")
                    next_item["password_hash"] = _hash_key(password)
                self._items[index] = next_item
                self._save_item(next_item)
                public_item = self._public_item(next_item)
                _notify_turing_user_sync(public_item)
                return public_item
        return None

    def delete_key(self, key_id: str, *, role: AuthRole | None = None) -> bool:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return False
        with self._lock:
            self._reload_locked()
            before = len(self._items)
            self._items = [
                item
                for item in self._items
                if not (item.get("id") == normalized_id and (role is None or item.get("role") == role))
            ]
            if len(self._items) == before:
                return False
            self._delete_item(normalized_id)
            return True

    def authenticate(self, raw_key: str) -> dict[str, object] | None:
        candidate = self._clean(raw_key)
        if not candidate:
            return None
        candidate_hash = _hash_key(candidate)
        with self._lock:
            for index, item in enumerate(self._items):
                if not bool(item.get("enabled", True)):
                    continue
                stored_hash = self._clean(item.get("key_hash"))
                link_token = self._clean(item.get("link_token"))
                key_matched = bool(stored_hash) and hmac.compare_digest(stored_hash, candidate_hash)
                link_matched = bool(link_token) and hmac.compare_digest(link_token, candidate)
                if not key_matched and not link_matched:
                    continue
                next_item = dict(item)
                now = datetime.now(timezone.utc)
                next_item["last_used_at"] = now.isoformat()
                self._items[index] = next_item
                item_id = self._clean(next_item.get("id"))
                last_flush_at = self._last_used_flush_at.get(item_id)
                if last_flush_at is None or (now - last_flush_at).total_seconds() >= 60:
                    try:
                        self._save_item(next_item)
                        self._last_used_flush_at[item_id] = now
                    except Exception:
                        pass
                identity = self._public_item(next_item)
                identity["auth_mode"] = "link" if link_matched else "key"
                identity["scope"] = "image" if link_matched else "full"
                return identity
        return None

    def get_public_key(self, key_id: str) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return None
        with self._lock:
            for item in self._items:
                if item.get("id") == normalized_id:
                    return self._public_item(item)
        return None

    def authenticate_password(self, username: str, password: str) -> dict[str, object] | None:
        normalized_username = self._clean(username).lower()
        normalized_password = self._clean(password)
        if not normalized_username or not normalized_password:
            return None
        password_hash = _hash_key(normalized_password)
        with self._lock:
            for index, item in enumerate(self._items):
                if item.get("role") != "user":
                    continue
                if not bool(item.get("enabled", True)):
                    continue
                if self._clean(item.get("username")).lower() != normalized_username:
                    continue
                stored_password_hash = self._clean(item.get("password_hash"))
                if not stored_password_hash or not hmac.compare_digest(stored_password_hash, password_hash):
                    return None
                next_item = dict(item)
                now = datetime.now(timezone.utc)
                next_item["last_used_at"] = now.isoformat()
                self._items[index] = next_item
                self._save_item(next_item)
                identity = self._public_item(next_item)
                identity["auth_mode"] = "password"
                identity["scope"] = "full"
                return identity
        return None

    def reserve_image_quota(self, identity: dict[str, object], amount: int = 1) -> int:
        try:
            normalized_amount = max(1, int(amount))
        except (TypeError, ValueError):
            normalized_amount = 1
        if identity.get("role") != "user":
            return 0
        key_id = self._clean(identity.get("id"))
        if not key_id:
            raise ImageQuotaExceeded("user image quota exhausted")
        with self._lock:
            for index, item in enumerate(self._items):
                if item.get("id") != key_id:
                    continue
                if item.get("role") != "user" or not bool(item.get("enabled", True)):
                    raise ImageQuotaExceeded("user image quota exhausted")
                quota = item.get("quota")
                if quota is None:
                    return 0
                remaining = self._normalize_quota(quota, default=0) or 0
                if remaining < normalized_amount:
                    raise ImageQuotaExceeded("user image quota exhausted")
                next_item = dict(item)
                next_item["quota"] = remaining - normalized_amount
                self._items[index] = next_item
                self._save_item(next_item)
                _notify_turing_user_sync(self._public_item(next_item))
                return normalized_amount
        raise ImageQuotaExceeded("user image quota exhausted")

    def refund_image_quota_by_id(self, key_id: object, amount: int = 1) -> int:
        try:
            normalized_amount = max(1, int(amount))
        except (TypeError, ValueError):
            normalized_amount = 1
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return 0
        with self._lock:
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id or item.get("role") != "user":
                    continue
                quota = item.get("quota")
                if quota is None:
                    return 0
                remaining = self._normalize_quota(quota, default=0) or 0
                next_item = dict(item)
                next_item["quota"] = remaining + normalized_amount
                self._items[index] = next_item
                self._save_item(next_item)
                _notify_turing_user_sync(self._public_item(next_item))
                return normalized_amount
        return 0

    def refund_image_quota(self, identity: dict[str, object], amount: int = 1) -> int:
        if identity.get("role") != "user":
            return 0
        return self.refund_image_quota_by_id(identity.get("id"), amount)


auth_service = AuthService(config.get_storage_backend())
