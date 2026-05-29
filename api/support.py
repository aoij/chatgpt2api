from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from threading import Event, Thread
import time

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import ImageQuotaExceeded, auth_service
from services.config import config

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"
ADMIN_SESSION_PREFIX = "adm-"
ADMIN_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "admin", "name": "管理员", "role": "admin", "auth_mode": "legacy", "scope": "full"}
    return None


def _admin_session_secret() -> str:
    return f"{config.auth_key}:{config.admin_username}:{config.admin_password}"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def authenticate_admin_password(username: str, password: str) -> dict[str, object] | None:
    expected_username = str(config.admin_username or "").strip()
    expected_password = str(config.admin_password or "").strip()
    candidate_username = str(username or "").strip()
    candidate_password = str(password or "").strip()
    if not expected_username or not expected_password:
        return None
    if not hmac.compare_digest(candidate_username, expected_username):
        return None
    if not hmac.compare_digest(candidate_password, expected_password):
        return None
    return {"id": "admin", "name": expected_username, "role": "admin", "auth_mode": "password", "scope": "full"}


def create_admin_session_token(username: str) -> str:
    payload = {
        "u": str(username or "").strip(),
        "exp": int(time.time()) + ADMIN_SESSION_TTL_SECONDS,
    }
    payload_text = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _admin_session_secret().encode("utf-8"),
        payload_text.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{ADMIN_SESSION_PREFIX}{payload_text}.{_b64url_encode(signature)}"


def _admin_session_identity(token: str) -> dict[str, object] | None:
    candidate = str(token or "").strip()
    if not candidate.startswith(ADMIN_SESSION_PREFIX):
        return None
    expected_username = str(config.admin_username or "").strip()
    expected_password = str(config.admin_password or "").strip()
    if not expected_username or not expected_password:
        return None
    raw = candidate[len(ADMIN_SESSION_PREFIX):]
    payload_text, separator, signature_text = raw.partition(".")
    if not separator or not payload_text or not signature_text:
        return None
    expected_signature = _b64url_encode(hmac.new(
        _admin_session_secret().encode("utf-8"),
        payload_text.encode("ascii"),
        hashlib.sha256,
    ).digest())
    if not hmac.compare_digest(signature_text, expected_signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_text).decode("utf-8"))
    except Exception:
        return None
    username = str(payload.get("u") or "").strip() if isinstance(payload, dict) else ""
    try:
        expires_at = int(payload.get("exp") or 0) if isinstance(payload, dict) else 0
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at < int(time.time()):
        return None
    if not hmac.compare_digest(username, expected_username):
        return None
    return {"id": "admin", "name": expected_username, "role": "admin", "auth_mode": "password_session", "scope": "full"}


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    identity = _legacy_admin_identity(token) or _admin_session_identity(token) or auth_service.authenticate(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})
    return identity


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "需要管理员权限才能执行这个操作"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if isinstance(exc, ImageQuotaExceeded) or "no available image quota" in message.lower() or "user image quota exhausted" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "user image quota exhausted"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        while not stop_event.is_set():
            try:
                if config.auto_remove_invalid_accounts:
                    removed_result = account_service.remove_marked_invalid_accounts(event="account-watcher")
                    removed = int(removed_result.get("removed") or 0)
                    if removed:
                        print(f"[account-limited-watcher] removed {removed} abnormal accounts")
                limited_tokens = account_service.list_limited_tokens()
                if limited_tokens:
                    print(f"[account-limited-watcher] checking {len(limited_tokens)} limited accounts")
                    account_service.refresh_accounts(limited_tokens)
            except Exception as exc:
                print(f"[account-limited-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="limited-account-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
