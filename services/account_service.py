from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import json
import os
import time
from threading import Condition, Lock
from typing import Any
from datetime import datetime

from curl_cffi.requests import Session

from services.config import DATA_DIR, config
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.proxy_service import proxy_settings
from services.storage.base import StorageBackend
from services.state_store import load_json_state, save_json_state
from utils.helper import anonymize_token


class AccountService:
    ACCOUNT_TYPE_MAP = {
        "free": "Free",
        "plus": "Plus",
        "prolite": "ProLite",
        "pro_lite": "ProLite",
        "team": "Team",
        "pro": "Pro",
        "personal": "Plus",
        "business": "Team",
        "enterprise": "Team",
    }

    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self._lock = Lock()
        self._refresh_progress_lock = Lock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._accounts = self._load_accounts()
        self._refresh_progress: dict[str, dict[str, Any]] = {}
        self._public_compact_cache: list[dict] | None = None
        self._image_inflight: dict[str, int] = {}
        self._image_cooldown_until: dict[str, float] = {}
        self._image_remote_refresh_ttl_seconds = 600
        self._account_refresh_batch_size = self._read_int_env("CHATGPT2API_ACCOUNT_REFRESH_BATCH_SIZE", 200, 1, 500)
        self._account_refresh_max_workers = self._read_int_env("CHATGPT2API_ACCOUNT_REFRESH_MAX_WORKERS", 24, 1, 64)
        self._account_refresh_batch_pause_seconds = self._read_float_env(
            "CHATGPT2API_ACCOUNT_REFRESH_BATCH_PAUSE_SECONDS",
            0.1,
            0.0,
            10.0,
        )
        self._invalid_tokens_path = DATA_DIR / "invalid_image_tokens.json"
        self._invalid_tokens = self._load_invalid_tokens()

    @staticmethod
    def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(str(os.getenv(name, "")).strip() or default)
        except Exception:
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _read_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(str(os.getenv(name, "")).strip() or default)
        except Exception:
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _now_text() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _clean_token(value: Any) -> str:
        return str(value or "").strip()

    def _clean_tokens(self, tokens: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen = set()
        for token in tokens:
            value = self._clean_token(token)
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned

    def _find_account_index(self, access_token: str) -> int:
        for index, item in enumerate(self._accounts):
            if self._clean_token(item.get("access_token")) == access_token:
                return index
        return -1

    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        status = str(account.get("status") or "").strip()
        if status in {"禁用", "限流", "异常"}:
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        return int(account.get("quota") or 0) > 0

    def _decode_access_token_payload(self, access_token: str) -> dict[str, Any]:
        parts = self._clean_token(access_token).split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
            data = json.loads(decoded.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict[str, Any]:
        parts = str(token or "").strip().split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
            data = json.loads(decoded.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize_source_type(value: object) -> str:
        return str(value or "web").strip().lower() or "web"

    @classmethod
    def _account_matches_source_type(cls, account: dict, source_type: str | None = None) -> bool:
        if not source_type:
            return True
        return cls._normalize_source_type(account.get("source_type")) == cls._normalize_source_type(source_type)

    def _normalize_account_type(self, value: Any) -> str | None:
        return self.ACCOUNT_TYPE_MAP.get(self._clean_token(value).lower())

    def _account_matches_any_plan_type(self, account: dict, plan_types: set[str] | tuple[str, ...] | None = None) -> bool:
        if not plan_types:
            return True
        normalized_account = self._normalize_account_type(account.get("type"))
        normalized_plans = {
            normalized
            for plan_type in plan_types
            if (normalized := self._normalize_account_type(plan_type))
        }
        return bool(normalized_account and normalized_account in normalized_plans)

    def _account_matches_plan_type(self, account: dict, plan_type: str | None = None) -> bool:
        if not plan_type:
            return True
        normalized_account = self._normalize_account_type(account.get("type"))
        normalized_plan = self._normalize_account_type(plan_type)
        return bool(normalized_account and normalized_plan and normalized_account == normalized_plan)

    def _search_account_type(self, value: Any) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = self._clean_token(key).lower()
                if any(flag in key_text for flag in ("plan", "type", "subscription", "workspace", "tier")):
                    matched = self._normalize_account_type(item)
                    if matched:
                        return matched
                    matched = self._search_account_type(item)
                    if matched:
                        return matched
            return None
        if isinstance(value, list):
            for item in value:
                matched = self._search_account_type(item)
                if matched:
                    return matched
            return None
        return None

    def _detect_account_type(self, access_token: str, me_payload: Any, init_payload: Any) -> str:
        token_payload = self._decode_access_token_payload(access_token)

        auth_payload = token_payload.get("https://api.openai.com/auth")
        print("检测账户类型响应", auth_payload)
        if isinstance(auth_payload, dict):
            matched = self._normalize_account_type(auth_payload.get("chatgpt_plan_type"))
            if matched:
                return matched

        for payload in (me_payload, init_payload, token_payload):
            matched = self._search_account_type(payload)
            if matched:
                return matched

        return "Free"

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = self._clean_token(item.get("access_token") or item.get("accessToken"))
        if not access_token:
            return None
        normalized = dict(item)
        normalized.pop("accessToken", None)
        normalized["access_token"] = access_token
        if self._clean_token(normalized.get("type")).lower() == "codex":
            normalized["export_type"] = "codex"
            normalized.pop("type", None)
        source_type = normalized.get("source_type")
        if not source_type and self._clean_token(normalized.get("export_type")).lower() == "codex":
            source_type = "codex"
        normalized["source_type"] = self._normalize_source_type(source_type)
        normalized["type"] = self._clean_token(normalized.get("type")) or "Free"
        normalized["status"] = self._clean_token(normalized.get("status")) or "正常"
        normalized["quota"] = int(normalized.get("quota") if normalized.get("quota") is not None else 0)
        if normalized["quota"] < 0:
            normalized["quota"] = 0
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = self._clean_token(normalized.get("email")) or None
        normalized["user_id"] = self._clean_token(normalized.get("user_id")) or None
        normalized["account_id"] = self._clean_token(normalized.get("account_id")) or None
        normalized["export_type"] = self._clean_token(normalized.get("export_type")) or None
        limits_progress = normalized.get("limits_progress")
        normalized["limits_progress"] = limits_progress if isinstance(limits_progress, list) else []
        normalized["default_model_slug"] = self._clean_token(normalized.get("default_model_slug")) or None
        normalized["restore_at"] = self._clean_token(normalized.get("restore_at")) or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        normalized["last_remote_checked_at"] = self._clean_token(normalized.get("last_remote_checked_at")) or None
        normalized["refresh_token"] = self._clean_token(normalized.get("refresh_token")) or None
        normalized["id_token"] = self._clean_token(normalized.get("id_token")) or None
        normalized["last_token_refresh_at"] = self._clean_token(normalized.get("last_token_refresh_at")) or None
        normalized["last_token_refresh_error"] = self._clean_token(normalized.get("last_token_refresh_error")) or None
        normalized["last_token_refresh_error_at"] = self._clean_token(normalized.get("last_token_refresh_error_at")) or None
        return normalized

    @staticmethod
    def _extract_quota_and_restore_at(limits_progress: list[Any]) -> tuple[int, str | None, bool]:
        quota = 0
        restore_at = None
        for item in limits_progress:
            if not isinstance(item, dict) or item.get("feature_name") != "image_gen":
                continue
            quota = int(item.get("remaining") or 0)
            restore_at = str(item.get("reset_after") or "").strip() or None
            return quota, restore_at, False
        return quota, restore_at, True

    def _load_accounts(self) -> list[dict]:
        accounts = self.storage.load_accounts()
        return [normalized for item in accounts if (normalized := self._normalize_account(item)) is not None]

    def _save_accounts(self) -> None:
        self.storage.save_accounts(self._accounts)
        self._public_compact_cache = None

    def _save_account(self, account: dict) -> None:
        self.storage.save_account(account)
        self._public_compact_cache = None

    def _load_invalid_tokens(self) -> set[str]:
        raw = load_json_state("invalid_image_tokens", {})
        items = raw.get("tokens") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return set()
        return {self._clean_token(item) for item in items if self._clean_token(item)}

    def _save_invalid_tokens_locked(self) -> None:
        try:
            save_json_state("invalid_image_tokens", {"tokens": sorted(self._invalid_tokens)})
        except Exception as exc:
            print(f"[account-invalid-cache] save failed: {exc}")

    def mark_invalid_image_token(self, access_token: str, reason: str = "") -> None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return
        with self._image_slot_condition:
            if access_token not in self._invalid_tokens:
                self._invalid_tokens.add(access_token)
                self._save_invalid_tokens_locked()
            self._image_inflight.pop(access_token, None)
            self._image_cooldown_until.pop(access_token, None)
            self._image_slot_condition.notify_all()
        if config.auto_remove_invalid_accounts:
            self.remove_invalid_token(access_token, reason or "image_stream")

    def _build_remote_headers(self, access_token: str) -> tuple[dict[str, str], str]:
        account = self.get_account(access_token) or {}
        user_agent = self._clean_token(account.get("user-agent") or account.get("user_agent"))
        impersonate = self._clean_token(account.get("impersonate")) or "edge101"
        headers = {
            "authorization": f"Bearer {access_token}",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json",
            "oai-language": "zh-CN",
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": user_agent
                          or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "sec-ch-ua": self._clean_token(account.get("sec-ch-ua"))
                         or '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": self._clean_token(account.get("sec-ch-ua-mobile")) or "?0",
            "sec-ch-ua-platform": self._clean_token(account.get("sec-ch-ua-platform")) or '"Windows"',
        }
        device_id = self._clean_token(account.get("oai-device-id") or account.get("oai_device_id"))
        session_id = self._clean_token(account.get("oai-session-id") or account.get("oai_session_id"))
        if device_id:
            headers["oai-device-id"] = device_id
        if session_id:
            headers["oai-session-id"] = session_id
        return headers, impersonate

    def _public_items(self, accounts: list[dict]) -> list[dict]:
        return [
            {
                "id": hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16],
                "access_token": access_token,
                "type": account.get("type") or "Free",
                "status": account.get("status") or "正常",
                "quota": account.get("quota") if account.get("quota") is not None else 0,
                "imageQuotaUnknown": bool(account.get("image_quota_unknown")),
                "email": account.get("email"),
                "user_id": account.get("user_id"),
                "source_type": account.get("source_type") or "web",
                "export_type": account.get("export_type"),
                "account_id": account.get("account_id"),
                "limits_progress": account.get("limits_progress") or [],
                "default_model_slug": account.get("default_model_slug"),
                "restoreAt": account.get("restore_at"),
                "success": int(account.get("success") or 0),
                "fail": int(account.get("fail") or 0),
                "lastUsedAt": account.get("last_used_at"),
                "lastRemoteCheckedAt": account.get("last_remote_checked_at"),
            }
            for account in accounts
            if (access_token := self._clean_token(account.get("access_token")))
        ]

    def _public_items_compact(self, accounts: list[dict]) -> list[dict]:
        items: list[dict] = []
        for account in accounts:
            access_token = self._clean_token(account.get("access_token"))
            if not access_token:
                continue
            items.append({
                "id": hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16],
                "token_preview": anonymize_token(access_token),
                "type": account.get("type") or "Free",
                "status": account.get("status") or "正常",
                "quota": account.get("quota") if account.get("quota") is not None else 0,
                "imageQuotaUnknown": bool(account.get("image_quota_unknown")),
                "email": account.get("email"),
                "user_id": account.get("user_id"),
                "source_type": account.get("source_type") or "web",
                "export_type": account.get("export_type"),
                "account_id": account.get("account_id"),
                "default_model_slug": account.get("default_model_slug"),
                "restoreAt": account.get("restore_at"),
                "success": int(account.get("success") or 0),
                "fail": int(account.get("fail") or 0),
                "lastUsedAt": account.get("last_used_at"),
                "lastRemoteCheckedAt": account.get("last_remote_checked_at"),
            })
        return items

    def _token_by_id_locked(self, account_id: str) -> str:
        target = self._clean_token(account_id)
        if not target:
            return ""
        for account in self._accounts:
            access_token = self._clean_token(account.get("access_token"))
            if access_token and hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16] == target:
                return access_token
        return ""

    def tokens_for_ids(self, ids: list[str]) -> list[str]:
        cleaned_ids = self._clean_tokens(ids)
        if not cleaned_ids:
            return []
        with self._lock:
            tokens: list[str] = []
            for account_id in cleaned_ids:
                token = self._token_by_id_locked(account_id)
                if token:
                    tokens.append(token)
            return tokens

    def list_token_items(self, ids: list[str] | None = None) -> list[dict[str, str]]:
        with self._lock:
            items = []
            target_ids = set(self._clean_tokens(ids or []))
            for account in self._accounts:
                access_token = self._clean_token(account.get("access_token"))
                if not access_token:
                    continue
                account_id = hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16]
                if target_ids and account_id not in target_ids:
                    continue
                items.append({"id": account_id, "access_token": access_token})
            return items

    def list_tokens(self) -> list[str]:
        with self._lock:
            return [token for item in self._accounts if (token := self._clean_token(item.get("access_token")))]

    @staticmethod
    def _cooldown_seconds_for_error(error: str) -> int:
        lower = str(error or "").lower()
        if "401" in lower or "unauthorized" in lower or "invalid access token" in lower:
            return 0
        if "429" in lower or "rate limit" in lower or "限流" in lower:
            return 20 * 60
        if (
                "cloudflare" in lower
                or "error 520" in lower
                or "error 522" in lower
                or "error 524" in lower
                or "bad gateway" in lower
                or "gateway timeout" in lower
                or "502" in lower
                or "503" in lower
                or "504" in lower
        ):
            return 5 * 60
        if "timeout" in lower or "timed out" in lower or "connection" in lower:
            return 3 * 60
        return 60

    def _candidate_account_sort_key(self, account: dict, now: float) -> tuple[int, float, int, float, str]:
        checked_at = self._account_remote_checked_timestamp(account)
        last_used_at = self._account_last_used_timestamp(account)
        success_count = int(account.get("success") or 0)
        token = self._clean_token(account.get("access_token"))
        # Prefer accounts recently verified by /backend-api/me.  This avoids spending
        # every image request walking thousands of stale imported tokens before reaching
        # known-good accounts.
        if checked_at > 0 and now - checked_at < self._image_remote_refresh_ttl_seconds:
            group = 0
        elif success_count > 0:
            group = 1
        elif checked_at > 0:
            group = 2
        else:
            group = 3
        return (group, last_used_at, -success_count, -checked_at, token)

    def _list_ready_candidate_tokens(
        self,
        excluded_tokens: set[str] | None = None,
        plan_type: str | None = None,
        source_type: str | None = None,
        plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        excluded = {self._clean_token(token) for token in (excluded_tokens or set()) if self._clean_token(token)}
        now = time.time()
        candidates: list[tuple[tuple[int, float, int, float, str], str]] = []
        for item in self._accounts:
            if (
                not self._is_image_account_available(item)
                or not self._account_matches_plan_type(item, plan_type)
                or not self._account_matches_any_plan_type(item, plan_types)
                or not self._account_matches_source_type(item, source_type)
            ):
                continue
            token = self._clean_token(item.get("access_token"))
            if (
                not token
                or token in excluded
                or token in self._invalid_tokens
                or float(self._image_cooldown_until.get(token, 0.0)) > now
            ):
                continue
            candidates.append((self._candidate_account_sort_key(item, now), token))
        candidates.sort(key=lambda row: row[0])
        return [token for _, token in candidates]

    def _list_available_candidate_tokens(
        self,
        excluded_tokens: set[str] | None = None,
        plan_type: str | None = None,
        source_type: str | None = None,
        plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        max_concurrency = max(1, int(getattr(config, "image_per_account_concurrency", 1) or 1))
        return [
            token
            for token in self._list_ready_candidate_tokens(excluded_tokens, plan_type, source_type, plan_types)
            if int(self._image_inflight.get(token, 0)) < max_concurrency
        ]

    def _pick_next_candidate_token(
        self,
        excluded_tokens: set[str] | None = None,
        plan_type: str | None = None,
        source_type: str | None = None,
        plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> str:
        excluded = {self._clean_token(token) for token in (excluded_tokens or set()) if self._clean_token(token)}
        wait_started = time.time()
        with self._image_slot_condition:
            while True:
                if not self._list_ready_candidate_tokens(excluded, plan_type, source_type, plan_types):
                    raise RuntimeError(
                        f"no available {plan_type or source_type or ''} image quota".replace("  ", " ").strip()
                        if plan_type or source_type else "no available image quota"
                    )
                tokens = self._list_available_candidate_tokens(excluded, plan_type, source_type, plan_types)
                if tokens:
                    access_token = tokens[0]
                    self._index += 1
                    self._image_inflight[access_token] = int(self._image_inflight.get(access_token, 0)) + 1
                    return access_token
                if time.time() - wait_started >= 30:
                    raise RuntimeError("waiting for image account slot timed out")
                self._image_slot_condition.wait(timeout=1.0)

    def release_image_slot(self, access_token: str) -> None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return
        with self._image_slot_condition:
            current_inflight = int(self._image_inflight.get(access_token, 0))
            if current_inflight <= 1:
                self._image_inflight.pop(access_token, None)
            else:
                self._image_inflight[access_token] = current_inflight - 1
            self._image_slot_condition.notify_all()

    def release_all_image_slots(self) -> None:
        with self._image_slot_condition:
            self._image_inflight.clear()
            self._image_slot_condition.notify_all()

    def cooldown_image_token(self, access_token: str, error: str = "", seconds: int | None = None) -> None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return
        cooldown_seconds = max(0, int(seconds if seconds is not None else self._cooldown_seconds_for_error(error)))
        with self._image_slot_condition:
            if cooldown_seconds > 0:
                self._image_cooldown_until[access_token] = time.time() + cooldown_seconds
            self._image_slot_condition.notify_all()

    def image_pool_stats(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            return {
                "inflight": sum(max(0, int(value or 0)) for value in self._image_inflight.values()),
                "inflight_accounts": sum(1 for value in self._image_inflight.values() if int(value or 0) > 0),
                "cooldown_accounts": sum(1 for value in self._image_cooldown_until.values() if float(value or 0) > now),
                "invalid_cached_accounts": len(self._invalid_tokens),
                "per_account_concurrency": max(1, int(getattr(config, "image_per_account_concurrency", 1) or 1)),
            }


    def _account_needs_remote_refresh(self, account: dict | None) -> bool:
        if not isinstance(account, dict):
            return True
        checked = self._account_remote_checked_timestamp(account)
        if checked <= 0:
            return True
        return time.time() - checked >= self._image_remote_refresh_ttl_seconds

    def _account_remote_checked_timestamp(self, account: dict | None) -> float:
        if not isinstance(account, dict):
            return 0.0
        value = self._clean_token(account.get("last_remote_checked_at"))
        if not value:
            return 0.0
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:26], fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    def _account_last_used_timestamp(self, account: dict | None) -> float:
        if not isinstance(account, dict):
            return 0.0
        value = self._clean_token(account.get("last_used_at"))
        if not value:
            return 0.0
        try:
            return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return 0.0

    def refresh_account_state(self, access_token: str, *, force: bool = False) -> dict | None:
        token_ref = anonymize_token(access_token)
        if not force:
            current = self.get_account(access_token)
            if current and not self._account_needs_remote_refresh(current):
                return current
        try:
            remote_info = self.fetch_remote_info(access_token)
        except Exception as exc:
            message = str(exc)
            print(f"[account-available] refresh token={token_ref} fail {message}")
            if "/backend-api/me failed: HTTP 401" in message:
                self.mark_invalid_image_token(access_token, "refresh_account_state")
                if config.auto_remove_invalid_accounts:
                    return None
                return self.update_account(
                    access_token,
                    {
                        "status": "异常",
                        "quota": 0,
                    },
                )
            return None
        return self.update_account(
            access_token,
            {
                **remote_info,
                "last_remote_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    def get_available_access_token(
        self,
        plan_type: str | None = None,
        source_type: str | None = None,
        plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> str:
        attempted_tokens: set[str] = set()
        while True:
            access_token = self._pick_next_candidate_token(
                excluded_tokens=attempted_tokens,
                plan_type=plan_type,
                source_type=source_type,
                plan_types=plan_types,
            )
            attempted_tokens.add(access_token)
            token_ref = anonymize_token(access_token)
            account = self.refresh_account_state(access_token)
            if (
                self._is_image_account_available(account or {})
                and self._account_matches_plan_type(account or {}, plan_type)
                and self._account_matches_any_plan_type(account or {}, plan_types)
                and self._account_matches_source_type(account or {}, source_type)
            ):
                return access_token
            self.release_image_slot(access_token)
            self.cooldown_image_token(access_token, "remote account refresh failed", seconds=60)
            print(
                f"[account-available] skip token={token_ref} "
                f"quota={account.get('quota') if account else 'unknown'} "
                f"status={account.get('status') if account else 'unknown'}"
            )

    def get_text_access_token(self) -> str:
        with self._lock:
            for account in self._accounts:
                status = self._clean_token(account.get("status"))
                if status not in {"禁用", "异常"}:
                    return self._clean_token(account.get("access_token"))
        return ""

    def remove_invalid_token(self, access_token: str, event: str) -> bool:
        if not config.auto_remove_invalid_accounts:
            return False
        removed = self.remove_token(access_token)
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除异常账号", {"source": event, "token": anonymize_token(access_token)})
        return removed

    def next_token(self) -> str:
        return self.get_available_access_token()

    def has_available_account(self) -> bool:
        with self._lock:
            now = time.time()
            return any(
                self._is_image_account_available(item)
                and (token := self._clean_token(item.get("access_token")))
                and token not in self._invalid_tokens
                and float(self._image_cooldown_until.get(token, 0.0)) <= now
                for item in self._accounts
            )

    def get_account(self, access_token: str) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        with self._lock:
            index = self._find_account_index(access_token)
            if index >= 0:
                return dict(self._accounts[index])
        return None

    def list_accounts(self, compact: bool = False) -> list[dict]:
        with self._lock:
            if compact:
                if self._public_compact_cache is None:
                    self._public_compact_cache = self._public_items_compact(self._accounts)
                return [dict(item) for item in self._public_compact_cache]
            return self._public_items(self._accounts)

    def list_accounts_page(
            self,
            *,
            compact: bool = True,
            query: str = "",
            account_type: str = "",
            status: str = "",
            page: int = 1,
            page_size: int = 10,
    ) -> dict[str, Any]:
        with self._lock:
            if compact:
                if self._public_compact_cache is None:
                    self._public_compact_cache = self._public_items_compact(self._accounts)
                items = [dict(item) for item in self._public_compact_cache]
            else:
                items = self._public_items(self._accounts)

        normalized_query = self._clean_token(query).lower()
        normalized_type = self._clean_token(account_type)
        normalized_status = self._clean_token(status)

        filtered: list[dict] = []
        for item in items:
            email = self._clean_token(item.get("email")).lower()
            current_type = self._clean_token(item.get("type"))
            current_status = self._clean_token(item.get("status"))
            if normalized_query and normalized_query not in email:
                continue
            if normalized_type and normalized_type != "all" and current_type != normalized_type:
                continue
            if normalized_status and normalized_status != "all" and current_status != normalized_status:
                continue
            filtered.append(item)

        safe_page_size = max(1, min(int(page_size or 10), 200))
        safe_page = max(1, int(page or 1))
        total = len(filtered)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        page_items = filtered[start:end]

        return {
            "items": page_items,
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        }

    def account_summary(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._accounts)
            active_items = [item for item in self._accounts if item.get("status") == "正常"]
            available_unlimited = any(str(item.get("type") or "") in {"Pro", "ProLite"} for item in active_items)
            available_unknown = any(bool(item.get("image_quota_unknown")) for item in active_items)
            available_quota = sum(max(0, int(item.get("quota") or 0)) for item in active_items)
            return {
                "total": total,
                "active": len(active_items),
                "limited": sum(1 for item in self._accounts if item.get("status") == "限流"),
                "abnormal": sum(1 for item in self._accounts if item.get("status") == "异常"),
                "disabled": sum(1 for item in self._accounts if item.get("status") == "禁用"),
                "available_quota": available_quota,
                "available_unlimited": available_unlimited,
                "available_unknown": available_unknown,
            }

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts
                if item.get("status") == "限流"
                   and (token := self._clean_token(item.get("access_token")))
            ]

    def remove_marked_invalid_accounts(self, *, event: str = "invalid_account_sweeper") -> dict[str, Any]:
        if not config.auto_remove_invalid_accounts:
            return {"removed": 0, "items": self.list_accounts(compact=True)}
        with self._lock:
            tokens = [
                token
                for item in self._accounts
                if item.get("status") == "异常"
                   and (token := self._clean_token(item.get("access_token")))
            ]
        result = self.delete_accounts(tokens)
        removed = int(result.get("removed") or 0)
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除历史异常账号", {"source": event, "removed": removed})
        return result

    def _account_payload_token(self, item: dict[str, Any]) -> str:
        return self._clean_token(item.get("access_token") or item.get("accessToken"))

    def _prepare_account_payload(self, item: dict[str, Any]) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = self._account_payload_token(item)
        if not access_token:
            return None
        payload = dict(item)
        payload.pop("accessToken", None)
        payload["access_token"] = access_token
        if self._clean_token(payload.get("type")).lower() == "codex":
            payload["export_type"] = "codex"
            payload["source_type"] = "codex"
            payload.pop("type", None)
        if self._clean_token(payload.get("export_type")).lower() == "codex":
            payload["source_type"] = "codex"
        if payload.get("plan_type") and not payload.get("type"):
            payload["type"] = self._clean_token(payload.get("plan_type"))
        payload["source_type"] = self._normalize_source_type(payload.get("source_type"))
        return payload

    def add_account_items(self, items: list[dict]) -> dict:
        payloads = [
            payload
            for item in items
            if (payload := self._prepare_account_payload(item)) is not None
        ]
        if not payloads:
            return {"added": 0, "skipped": 0, "items": self.list_accounts(compact=True)}
        with self._lock:
            indexed = {self._clean_token(item.get("access_token")): dict(item) for item in self._accounts}
            added = 0
            skipped = 0
            invalid_cache_changed = False
            for payload in payloads:
                access_token = self._account_payload_token(payload)
                if access_token in self._invalid_tokens:
                    self._invalid_tokens.discard(access_token)
                    invalid_cache_changed = True
                current = indexed.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account({**current, **payload, "access_token": access_token})
                if account is not None:
                    indexed[access_token] = account
            self._accounts = list(indexed.values())
            if invalid_cache_changed:
                self._save_invalid_tokens_locked()
            self._save_accounts()
            items_out = self._public_items_compact(self._accounts)
            log_service.add(LOG_TYPE_ACCOUNT, f"导入 {added} 个账号，跳过 {skipped} 个", {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items_out}

    def add_accounts(self, tokens: list[str], source_type: str = "web") -> dict:
        cleaned_tokens = self._clean_tokens(tokens)
        if not cleaned_tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts(compact=True)}

        with self._lock:
            indexed = {self._clean_token(item.get("access_token")): dict(item) for item in self._accounts}
            added = 0
            skipped = 0
            invalid_cache_changed = False
            for access_token in cleaned_tokens:
                if access_token in self._invalid_tokens:
                    self._invalid_tokens.discard(access_token)
                    invalid_cache_changed = True
                current = indexed.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account(
                    {
                        **current,
                        "access_token": access_token,
                        "source_type": current.get("source_type") or self._normalize_source_type(source_type),
                        "type": str(current.get("type") or "Free"),
                    }
                )
                if account is not None:
                    indexed[access_token] = account
            self._accounts = list(indexed.values())
            if invalid_cache_changed:
                self._save_invalid_tokens_locked()
            self._save_accounts()
            items = self._public_items_compact(self._accounts)
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个", {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def delete_accounts(self, tokens: list[str]) -> dict:
        target_set = set(self._clean_tokens(tokens))
        if not target_set:
            return {"removed": 0, "items": self.list_accounts(compact=True)}
        with self._lock:
            before = len(self._accounts)
            self._accounts = [item for item in self._accounts if
                              self._clean_token(item.get("access_token")) not in target_set]
            removed = before - len(self._accounts)
            if self._accounts:
                self._index %= len(self._accounts)
            else:
                self._index = 0
            if removed:
                for token in target_set:
                    self._image_inflight.pop(token, None)
                    self._image_cooldown_until.pop(token, None)
                    self._invalid_tokens.discard(token)
                self._save_invalid_tokens_locked()
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = self._public_items_compact(self._accounts)
        return {"removed": removed, "items": items}

    def remove_token(self, access_token: str) -> bool:
        return bool(self.delete_accounts([access_token])["removed"])

    def delete_accounts_by_ids(self, ids: list[str]) -> dict:
        return self.delete_accounts(self.tokens_for_ids(ids))

    def update_account_by_id(self, account_id: str, updates: dict) -> dict | None:
        tokens = self.tokens_for_ids([account_id])
        if not tokens:
            return None
        return self.update_account(tokens[0], updates)

    def update_account(self, access_token: str, updates: dict) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        with self._lock:
            index = self._find_account_index(access_token)
            if index < 0:
                return None
            account = self._normalize_account({**self._accounts[index], **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                del self._accounts[index]
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[index] = account
            self._save_account(account)
            log_service.add(LOG_TYPE_ACCOUNT, "更新账号", {"token": anonymize_token(access_token), "status": account.get("status")})
            return dict(account)
        return None

    def mark_image_result(self, access_token: str, success: bool) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        self.release_image_slot(access_token)
        with self._lock:
            index = self._find_account_index(access_token)
            if index < 0:
                return None
            next_item = dict(self._accounts[index])
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_quota_unknown = bool(next_item.get("image_quota_unknown"))
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
                if not image_quota_unknown:
                    next_item["quota"] = max(0, int(next_item.get("quota") or 0) - 1)
                if not image_quota_unknown and next_item["quota"] == 0:
                    next_item["status"] = "限流"
                    next_item["restore_at"] = next_item.get("restore_at") or None
                elif next_item.get("status") == "限流":
                    next_item["status"] = "正常"
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                del self._accounts[index]
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[index] = account
            self._save_account(account)
            return dict(account)
        return None

    @staticmethod
    def _extract_chatgpt_account_info(payload: dict[str, Any]) -> dict[str, Any]:
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        default_entry = payload.get("default") if isinstance(payload, dict) else None
        account_id = ""
        account_user_id = ""
        plan_type = ""
        if isinstance(default_entry, dict):
            account = default_entry.get("account") if isinstance(default_entry.get("account"), dict) else {}
            account_id = str(account.get("account_id") or "").strip()
            account_user_id = str(account.get("account_user_id") or "").strip()
            plan_type = str(account.get("plan_type") or "").strip()
        if not account_id and isinstance(accounts, dict):
            for key, entry in accounts.items():
                if isinstance(entry, dict):
                    account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
                    account_id = str(account.get("account_id") or key or "").strip()
                    account_user_id = str(account.get("account_user_id") or "").strip()
                    plan_type = str(account.get("plan_type") or "").strip()
                    if account_id:
                        break
        return {
            "chatgpt_account_id": account_id,
            "chatgpt_account_user_id": account_user_id,
            "chatgpt_plan_type": plan_type,
        }

    def fetch_remote_info(self, access_token: str) -> dict[str, Any]:
        access_token = self._clean_token(access_token)
        if not access_token:
            raise ValueError("access_token is required")

        headers, impersonate = self._build_remote_headers(access_token)
        token_ref = anonymize_token(access_token)
        print(f"[account-refresh] start {token_ref}")
        session = Session(**proxy_settings.build_session_kwargs(impersonate=impersonate, verify=True))
        session.headers.update(headers)
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                me_future = executor.submit(
                    session.get,
                    "https://chatgpt.com/backend-api/me",
                    headers={
                        "x-openai-target-path": "/backend-api/me",
                        "x-openai-target-route": "/backend-api/me",
                    },
                    timeout=20,
                )
                init_future = executor.submit(
                    session.post,
                    "https://chatgpt.com/backend-api/conversation/init",
                    json={
                        "gizmo_id": None,
                        "requested_default_model": None,
                        "conversation_id": None,
                        "timezone_offset_min": -480,
                    },
                    timeout=20,
                )
                account_future = executor.submit(
                    session.get,
                    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                    headers={
                        "x-openai-target-path": "/backend-api/accounts/check/v4-2023-04-27",
                        "x-openai-target-route": "/backend-api/accounts/check/v4-2023-04-27",
                    },
                    timeout=20,
                )

                me_response = me_future.result()
                init_response = init_future.result()
                account_response = account_future.result()

            if me_response.status_code != 200:
                raise RuntimeError(f"/backend-api/me failed: HTTP {me_response.status_code}")
            me_payload = me_response.json()

            if init_response.status_code != 200:
                raise RuntimeError(f"/backend-api/conversation/init failed: HTTP {init_response.status_code}")
            init_payload = init_response.json()

            account_payload: dict[str, Any] = {}
            if account_response.status_code == 200:
                try:
                    raw_account_payload = account_response.json()
                    if isinstance(raw_account_payload, dict):
                        account_payload = raw_account_payload
                except Exception:
                    account_payload = {}

            limits_progress = init_payload.get("limits_progress")
            if not isinstance(limits_progress, list):
                limits_progress = []

            account_type = self._detect_account_type(access_token, me_payload, init_payload)
            quota, restore_at, image_quota_unknown = self._extract_quota_and_restore_at(limits_progress)
            status = "正常" if image_quota_unknown and account_type != "Free" else ("限流" if quota == 0 else "正常")

            account_info = self._extract_chatgpt_account_info(account_payload)
            result = {
                "email": me_payload.get("email"),
                "user_id": me_payload.get("id"),
                "chatgpt_account_id": account_info.get("chatgpt_account_id"),
                "chatgpt_account_user_id": account_info.get("chatgpt_account_user_id"),
                "chatgpt_plan_type": account_info.get("chatgpt_plan_type"),
                "type": account_type,
                "quota": quota,
                "image_quota_unknown": image_quota_unknown,
                "limits_progress": limits_progress,
                "default_model_slug": init_payload.get("default_model_slug"),
                "restore_at": restore_at,
                "status": status,
            }
            print(
                "[account-refresh] ok",
                token_ref,
                f"quota={result.get('quota')}",
                f"restore_at={result.get('restore_at')}",
            )
            return result
        finally:
            session.close()

    def _jwt_exp(self, access_token: str) -> int:
        try:
            return int(self._decode_jwt_payload(access_token).get("exp") or 0)
        except (TypeError, ValueError):
            return 0

    def _token_needs_refresh(self, access_token: str, *, force: bool = False) -> bool:
        if force:
            return True
        exp = self._jwt_exp(access_token)
        if exp <= 0:
            return False
        return exp - int(time.time()) <= 24 * 60 * 60

    @staticmethod
    def _parse_iso_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None

    def list_expiring_access_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for account in self._accounts
                if self._clean_token(account.get("refresh_token"))
                and (token := self._clean_token(account.get("access_token")))
                and self._token_needs_refresh(token)
            ]

    def list_refresh_token_keepalive_tokens(self) -> list[str]:
        now = time.time()
        due: list[tuple[float, str]] = []
        with self._lock:
            for account in self._accounts:
                token = self._clean_token(account.get("access_token"))
                if not token or not self._clean_token(account.get("refresh_token")) or account.get("status") == "禁用":
                    continue
                last_error = self._parse_iso_datetime(account.get("last_token_refresh_error_at"))
                if last_error and now - last_error.timestamp() < 6 * 60 * 60:
                    continue
                last_refresh = self._parse_iso_datetime(account.get("last_token_refresh_at"))
                anchor = last_refresh.timestamp() if last_refresh else 0
                if anchor <= 0:
                    exp = self._jwt_exp(token)
                    anchor = float(exp) if exp > 0 else 0.0
                if anchor <= 0 or now - anchor >= 3 * 24 * 60 * 60:
                    due.append((anchor, token))
        due.sort(key=lambda item: item[0])
        return [token for _, token in due[:3]]

    def keepalive_refresh_tokens(self, access_tokens: list[str]) -> dict[str, Any]:
        # Full OAuth refresh is only available in upstream account_service; keep this
        # conservative no-op so the watcher does not crash on deployments that retain
        # duan's customized account refresh path.
        return {"refreshed": 0, "errors": [], "items": self.list_accounts(compact=True)}

    def _refresh_progress_account_stats(self) -> dict[str, Any]:
        with self._lock:
            status_counts: dict[str, int] = {}
            total_quota = 0
            for account in self._accounts:
                status = self._clean_token(account.get("status")) or "未知"
                status_counts[status] = status_counts.get(status, 0) + 1
                try:
                    total_quota += max(0, int(account.get("quota") or 0))
                except Exception:
                    continue
        return {"status_counts": status_counts, "total_quota": total_quota}

    def _cleanup_refresh_progress_locked(self) -> None:
        now = time.time()
        expired_ids: list[str] = []
        for progress_id, progress in self._refresh_progress.items():
            updated_at = float(progress.get("_updated_ts") or progress.get("_created_ts") or 0.0)
            if updated_at and now - updated_at > 2 * 60 * 60:
                expired_ids.append(progress_id)
        for progress_id in expired_ids:
            self._refresh_progress.pop(progress_id, None)
        if len(self._refresh_progress) <= 64:
            return
        ordered = sorted(
            self._refresh_progress.items(),
            key=lambda item: float(item[1].get("_updated_ts") or item[1].get("_created_ts") or 0.0),
        )
        for progress_id, _ in ordered[: len(self._refresh_progress) - 64]:
            self._refresh_progress.pop(progress_id, None)

    def begin_refresh_progress(self, progress_id: str, total: int) -> tuple[str, bool]:
        progress_id = self._clean_token(progress_id) or hashlib.sha1(str(time.time()).encode("utf-8")).hexdigest()[:16]
        safe_total = max(0, int(total or 0))
        now = time.time()
        now_text = self._now_text()
        batch_size = max(1, min(self._account_refresh_batch_size, safe_total or self._account_refresh_batch_size))
        total_batches = (safe_total + batch_size - 1) // batch_size if safe_total else 0
        stats = self._refresh_progress_account_stats()
        with self._refresh_progress_lock:
            self._cleanup_refresh_progress_locked()
            existing = self._refresh_progress.get(progress_id)
            if existing and not existing.get("done"):
                return progress_id, False
            for active_id, active_progress in self._refresh_progress.items():
                if not active_progress.get("done"):
                    return active_id, False
            self._refresh_progress[progress_id] = {
                "id": progress_id,
                "total": safe_total,
                "processed": 0,
                "started": 0,
                "running": 0,
                "queued": safe_total,
                "done": False,
                "error": None,
                "created_at": now_text,
                "updated_at": now_text,
                "finished_at": None,
                "batch_size": batch_size,
                "current_batch": 0,
                "total_batches": total_batches,
                "max_workers": min(self._account_refresh_max_workers, batch_size, safe_total) if safe_total else 0,
                "status_counts": stats["status_counts"],
                "total_quota": stats["total_quota"],
                "last_started_token": "",
                "last_finished_token": "",
                "result": None,
                "_created_ts": now,
                "_updated_ts": now,
            }
        return progress_id, True

    def _update_refresh_progress(self, progress_id: str | None, **updates: Any) -> None:
        progress_id = self._clean_token(progress_id)
        if not progress_id:
            return
        now = time.time()
        updates["updated_at"] = self._now_text()
        updates["_updated_ts"] = now
        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            if progress is None:
                return
            progress.update(updates)

    def _refresh_progress_stats(self, progress_id: str | None) -> None:
        progress_id = self._clean_token(progress_id)
        if not progress_id:
            return
        stats = self._refresh_progress_account_stats()
        self._update_refresh_progress(
            progress_id,
            status_counts=stats["status_counts"],
            total_quota=stats["total_quota"],
        )

    def finish_refresh_progress(
            self,
            progress_id: str | None,
            result: dict[str, Any] | None = None,
            error: str | None = None,
    ) -> None:
        progress_id = self._clean_token(progress_id)
        if not progress_id:
            return
        stats = self._refresh_progress_account_stats()
        now = time.time()
        now_text = self._now_text()
        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            if progress is None:
                return
            total = int(progress.get("total") or 0)
            processed = int(progress.get("processed") or 0)
            progress.update({
                "processed": max(processed, total if result is not None and error is None else processed),
                "running": 0,
                "queued": 0,
                "done": True,
                "error": error,
                "result": result,
                "finished_at": now_text,
                "updated_at": now_text,
                "status_counts": stats["status_counts"],
                "total_quota": stats["total_quota"],
                "_updated_ts": now,
            })

    def get_refresh_progress(self, progress_id: str) -> dict[str, Any] | None:
        progress_id = self._clean_token(progress_id)
        if not progress_id:
            return None
        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            if progress is None:
                return None
            return {key: value for key, value in progress.items() if not key.startswith("_")}

    def refresh_accounts(self, access_tokens: list[str], progress_id: str | None = None) -> dict[str, Any]:
        cleaned_tokens = self._clean_tokens(access_tokens)
        progress_id = self._clean_token(progress_id)
        if progress_id and self.get_refresh_progress(progress_id) is None:
            progress_id, _ = self.begin_refresh_progress(progress_id, len(cleaned_tokens))
        if not cleaned_tokens:
            result = {"refreshed": 0, "errors": [], "items": self.list_accounts(compact=True)}
            self.finish_refresh_progress(progress_id, result=result)
            return result

        refreshed = 0
        errors: list[dict[str, str]] = []
        total = len(cleaned_tokens)
        batch_size = max(1, min(self._account_refresh_batch_size, total))
        max_workers = max(1, min(self._account_refresh_max_workers, batch_size))
        total_batches = (total + batch_size - 1) // batch_size
        processed = 0
        started = 0

        try:
            print(
                f"[account-refresh] queue total={total} batch_size={batch_size} "
                f"workers={max_workers} progress={progress_id or '-'}"
            )
            self._update_refresh_progress(
                progress_id,
                total=total,
                queued=total,
                batch_size=batch_size,
                total_batches=total_batches,
                max_workers=max_workers,
            )
            for batch_no, batch_start in enumerate(range(0, total, batch_size), start=1):
                batch = cleaned_tokens[batch_start: batch_start + batch_size]
                batch_workers = max(1, min(max_workers, len(batch)))
                batch_done = 0
                self._update_refresh_progress(
                    progress_id,
                    current_batch=batch_no,
                    running=0,
                    queued=max(0, total - started),
                )
                print(
                    f"[account-refresh] batch {batch_no}/{total_batches} "
                    f"size={len(batch)} workers={batch_workers}"
                )
                with ThreadPoolExecutor(max_workers=batch_workers) as executor:
                    future_map = {}
                    for access_token in batch:
                        future_map[executor.submit(self.fetch_remote_info, access_token)] = access_token
                        started += 1
                        self._update_refresh_progress(
                            progress_id,
                            started=started,
                            running=min(batch_workers, len(future_map) - batch_done),
                            queued=max(0, total - started),
                            last_started_token=anonymize_token(access_token),
                        )
                    for future in as_completed(future_map):
                        access_token = future_map[future]
                        try:
                            remote_info = future.result()
                            if self.update_account(
                                access_token,
                                {
                                    **remote_info,
                                    "last_remote_checked_at": self._now_text(),
                                },
                            ) is not None:
                                refreshed += 1
                        except Exception as exc:
                            message = str(exc)
                            print(f"[account-refresh] fail {anonymize_token(access_token)} {message}")
                            if "/backend-api/me failed: HTTP 401" in message:
                                self.mark_invalid_image_token(access_token, "refresh_accounts")
                                if not config.auto_remove_invalid_accounts:
                                    self.update_account(access_token, {"status": "异常", "quota": 0})
                                message = "检测到封号"
                            errors.append({"access_token": access_token, "error": message})
                        finally:
                            processed += 1
                            batch_done += 1
                            self._update_refresh_progress(
                                progress_id,
                                processed=processed,
                                running=max(0, min(batch_workers, len(future_map) - batch_done)),
                                queued=max(0, total - started),
                                last_finished_token=anonymize_token(access_token),
                            )
                self._refresh_progress_stats(progress_id)
                if batch_no < total_batches and self._account_refresh_batch_pause_seconds > 0:
                    time.sleep(self._account_refresh_batch_pause_seconds)

            print(
                f"[account-refresh] done refreshed={refreshed} errors={len(errors)} "
                f"workers={max_workers} batch_size={batch_size}"
            )
            result = {
                "refreshed": refreshed,
                "errors": errors,
                "items": self.list_accounts(compact=True),
            }
            self.finish_refresh_progress(progress_id, result=result)
            return result
        except Exception as exc:
            self.finish_refresh_progress(progress_id, error=str(exc))
            raise


account_service = AccountService(config.get_storage_backend())
