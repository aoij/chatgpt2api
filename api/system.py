from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from api.support import (
    authenticate_admin_password,
    create_admin_session_token,
    require_admin,
    require_identity,
    resolve_image_base_url,
)
from services.backup_service import BackupError, backup_service
from services.auth_service import auth_service
from services.config import config
from services.image_service import add_log_image_thumbnails, build_image_download, build_images_zip, delete_images, list_images, warm_image_download_cache
from services.image_task_service import image_task_service
from services.image_tags_service import delete_tag, get_all_tags, set_tags
from services.log_service import log_service
from services.proxy_service import test_proxy


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProxyTestRequest(BaseModel):
    url: str = ""


class ProxyUpdateRequest(BaseModel):
    enabled: bool | None = None
    url: str | None = None


class ImageDeleteRequest(BaseModel):
    paths: list[str] = []
    start_date: str = ""
    end_date: str = ""
    uploader: str = ""
    all_matching: bool = False

class ImageTagsRequest(BaseModel):
    path: str
    tags: list[str]

class LogDeleteRequest(BaseModel):
    ids: list[str] = []
class BackupDeleteRequest(BaseModel):
    key: str = ""


class ImageDownloadRequest(BaseModel):
    paths: list[str] = []
    start_date: str = ""
    end_date: str = ""
    uploader: str = ""
    all_matching: bool = False


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


def _session_payload(identity: dict[str, object]) -> dict[str, object]:
    item = auth_service.get_public_key(str(identity.get("id") or ""))
    if item is None:
        item = identity
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "username": item.get("username"),
        "role": item.get("role"),
        "enabled": item.get("enabled", True),
        "quota": item.get("quota"),
        "open_id": item.get("open_id"),
        "avatar_url": item.get("avatar_url"),
        "selected_account_id": item.get("selected_account_id"),
        "auth_mode": identity.get("auth_mode", "key"),
        "scope": identity.get("scope", "full"),
    }


def _effective_image_uploader(identity: dict[str, object], uploader: str = "") -> str:
    if identity.get("role") == "admin":
        return uploader.strip()
    return str(identity.get("id") or identity.get("subject_id") or "").strip()


def _require_download_identity(authorization: str | None, download_token: str = "") -> dict[str, object]:
    """Allow direct browser downloads without buffering blobs in the web UI.

    Normal API calls still use the Authorization header.  Direct `<a download>`
    clicks cannot attach headers, so the front end can pass the current token as
    a short-lived same-origin download query parameter.
    """
    token = str(download_token or "").strip()
    return require_identity(authorization or (f"Bearer {token}" if token else None))


def _download_response(payload: dict[str, object]) -> Response:
    filename = str(payload.get("filename") or "download")
    file_path = payload.get("file_path")
    if file_path:
        return FileResponse(
            path=str(file_path),
            media_type=str(payload.get("media_type") or "application/octet-stream"),
            filename=filename,
            headers={
                "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )
    content = payload.get("content") or b""
    if not isinstance(content, bytes):
        content = bytes(content)
    return Response(
        content=content,
        media_type=str(payload.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    async def login(body: LoginRequest | None = None, authorization: str | None = Header(default=None)):
        session_key = ""
        if body is not None and (body.username or body.password):
            candidate_username = str(body.username or "").strip()
            expected_admin_username = str(config.admin_username or "").strip()
            if expected_admin_username and candidate_username == expected_admin_username:
                identity = authenticate_admin_password(candidate_username, body.password)
                if identity is None:
                    raise HTTPException(status_code=401, detail={"error": "username or password is invalid"})
                session_key = create_admin_session_token(str(identity.get("name") or candidate_username))
            else:
                identity = auth_service.authenticate_password(candidate_username, str(body.password or ""))
                if identity is None:
                    raise HTTPException(status_code=401, detail={"error": "username or password is invalid"})
                session_key = str(identity.get("link_token") or identity.get("key") or "").strip()
        else:
            identity = require_identity(authorization)
        result = {
            "ok": True,
            "version": app_version,
            "role": identity.get("role"),
            "subject_id": identity.get("id"),
            "name": identity.get("name"),
            "quota": identity.get("quota"),
            "auth_mode": identity.get("auth_mode", "key"),
            "scope": identity.get("scope", "full"),
        }
        if session_key:
            result["key"] = session_key
        return result

    @router.get("/auth/session")
    async def get_session(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return _session_payload(identity)

    @router.get("/api/auth/me")
    async def get_me(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return _session_payload(identity)

    @router.get("/api/public/config")
    async def get_public_config():
        return {
            "site_name": config.site_name,
            "page_title": config.page_title,
            "image_page_title": config.image_page_title,
            "image_page_subtitle": config.image_page_subtitle,
            "image_batch_limit": config.image_account_concurrency,
        }

    @router.get("/version")
    async def get_version():
        return {"version": app_version}

    @router.get("/api/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"config": config.get()}

    @router.post("/api/settings")
    async def save_settings(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        updated = config.update(body.model_dump(mode="python"))
        image_task_service.reload_runtime_settings()
        return {"config": updated}

    @router.get("/api/images")
    async def get_images(
        request: Request,
        start_date: str = "",
        end_date: str = "",
        uploader: str = "",
        limit: int = 200,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return list_images(
            resolve_image_base_url(request),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            uploader=_effective_image_uploader(identity, uploader),
            limit=limit,
            offset=offset,
        )

    @router.post("/api/images/delete")
    async def delete_images_endpoint(body: ImageDeleteRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        result = delete_images(
            body.paths,
            start_date=body.start_date.strip(),
            end_date=body.end_date.strip(),
            uploader=_effective_image_uploader(identity, body.uploader),
            all_matching=body.all_matching,
        )
        if body.paths:
            try:
                from api.image_tasks import _notify_turing_delete_sync
                await run_in_threadpool(_notify_turing_delete_sync, {"paths": body.paths})
            except Exception:
                pass
        return result

    @router.get("/api/images/download")
    async def download_image(
        background_tasks: BackgroundTasks,
        path: str = "",
        paths: list[str] = Query(default=[]),
        start_date: str = "",
        end_date: str = "",
        uploader: str = "",
        all_matching: bool = False,
        download_token: str = "",
        authorization: str | None = Header(default=None),
    ):
        identity = _require_download_identity(authorization, download_token)
        effective_uploader = _effective_image_uploader(identity, uploader)
        if paths or all_matching:
            payload = build_images_zip(
                paths,
                uploader=effective_uploader,
                start_date=start_date.strip(),
                end_date=end_date.strip(),
                all_matching=all_matching,
            )
        else:
            payload = build_image_download(
                path,
                uploader=effective_uploader,
            )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "images not found"})
        background_tasks.add_task(warm_image_download_cache, paths or ([path] if path else []))
        return _download_response(payload)

    @router.post("/api/images/download")
    async def download_images(body: ImageDownloadRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = build_images_zip(
            body.paths,
            uploader=_effective_image_uploader(identity, body.uploader),
            start_date=body.start_date.strip(),
            end_date=body.end_date.strip(),
            all_matching=body.all_matching,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "images not found"})
        return _download_response(payload)

    @router.get("/api/logs")
    async def get_logs(request: Request, type: str = "", start_date: str = "", end_date: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": add_log_image_thumbnails(log_service.list(type=type.strip(), start_date=start_date.strip(), end_date=end_date.strip()), resolve_image_base_url(request))}

    @router.post("/api/logs/delete")
    async def delete_logs(body: LogDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return log_service.delete(body.ids)

    @router.post("/api/proxy/test")
    async def test_proxy_endpoint(body: ProxyTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        candidate = (body.url or "").strip() or config.get_proxy_settings()
        if not candidate:
            raise HTTPException(status_code=400, detail={"error": "proxy url is required"})
        return {"result": await run_in_threadpool(test_proxy, candidate)}

    @router.get("/api/proxy")
    async def get_proxy_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        proxy = config.get_proxy_settings()
        return {"proxy": {"enabled": bool(proxy), "url": proxy}}

    @router.post("/api/proxy")
    async def update_proxy_endpoint(body: ProxyUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        current = config.get_proxy_settings()
        enabled = bool(body.enabled) if body.enabled is not None else bool(current)
        url = str(body.url if body.url is not None else current).strip()
        next_proxy = url if enabled else ""
        updated = config.update({"proxy": next_proxy})
        proxy = str(updated.get("proxy") or "").strip()
        return {"proxy": {"enabled": bool(proxy), "url": proxy}}

    @router.get("/api/storage/info")
    async def get_storage_info(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        storage = config.get_storage_backend()
        return {
            "backend": storage.get_backend_info(),
            "health": storage.health_check(),
        }

    @router.post("/api/backup/test")
    async def test_backup_connection(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.test_connection)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups")
    async def get_backups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "items": await run_in_threadpool(backup_service.list_backups),
                "state": backup_service.get_status(),
                "settings": backup_service.get_settings(),
            }
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/run")
    async def run_backup_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.run_backup)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/delete")
    async def delete_backup_endpoint(body: BackupDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            await run_in_threadpool(backup_service.delete_backup, body.key)
            return {"ok": True}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/detail")
    async def get_backup_detail(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"item": await run_in_threadpool(backup_service.get_backup_detail, key)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/download")
    async def download_backup_endpoint(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = await run_in_threadpool(backup_service.download_backup, key)
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        filename = str(item.get("name") or "backup.bin")
        quoted = quote(filename)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(int(item.get("size") or 0)),
        }
        return Response(
            content=bytes(item.get("payload") or b""),
            media_type=str(item.get("content_type") or "application/octet-stream"),
            headers=headers,
        )


    @router.get("/api/images/tags")
    async def list_image_tags(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"tags": get_all_tags()}

    @router.post("/api/images/tags")
    async def update_image_tags(body: ImageTagsRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        rel = body.path.strip().lstrip("/")
        if not rel:
            raise HTTPException(status_code=400, detail={"error": "path is required"})
        tags = set_tags(rel, body.tags)
        return {"ok": True, "tags": tags}

    @router.delete("/api/images/tags/{tag}")
    async def delete_image_tag(tag: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        count = delete_tag(tag)
        return {"ok": True, "removed_from": count}

    return router
