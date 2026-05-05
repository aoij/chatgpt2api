from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
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
from services.image_service import add_log_image_thumbnails, build_image_download, build_images_zip, delete_images, list_images
from services.image_tags_service import delete_tag, get_all_tags, set_tags
from services.log_service import log_service
from services.proxy_service import test_proxy


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProxyTestRequest(BaseModel):
    url: str = ""


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


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


def _effective_image_uploader(identity: dict[str, object], uploader: str = "") -> str:
    if identity.get("role") == "admin":
        return uploader.strip()
    return str(identity.get("id") or identity.get("subject_id") or "").strip()


def _download_response(payload: dict[str, object]) -> Response:
    filename = str(payload.get("filename") or "download")
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
            identity = authenticate_admin_password(body.username, body.password)
            if identity is None:
                raise HTTPException(status_code=401, detail={"error": "username or password is invalid"})
            session_key = create_admin_session_token(str(identity.get("name") or body.username))
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

    @router.get("/api/auth/me")
    async def get_me(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = auth_service.get_public_key(str(identity.get("id") or ""))
        if item is None:
            item = identity
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "role": item.get("role"),
            "enabled": item.get("enabled", True),
            "quota": item.get("quota"),
            "auth_mode": identity.get("auth_mode", "key"),
            "scope": identity.get("scope", "full"),
        }

    @router.get("/api/public/config")
    async def get_public_config():
        return {
            "site_name": config.site_name,
            "page_title": config.page_title,
            "image_page_title": config.image_page_title,
            "image_page_subtitle": config.image_page_subtitle,
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
        return {"config": config.update(body.model_dump(mode="python"))}

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
        return delete_images(
            body.paths,
            start_date=body.start_date.strip(),
            end_date=body.end_date.strip(),
            uploader=_effective_image_uploader(identity, body.uploader),
            all_matching=body.all_matching,
        )

    @router.get("/api/images/download")
    async def download_image(path: str = "", authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = build_image_download(
            path,
            uploader=_effective_image_uploader(identity),
        )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "image not found"})
        return _download_response(payload)

    @router.post("/api/images/download")
    async def download_images(body: ImageDownloadRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = build_images_zip(
            body.paths,
            uploader=_effective_image_uploader(identity),
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
