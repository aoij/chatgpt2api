from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_identity, resolve_image_base_url
from services.image_task_service import image_task_service
from services.log_service import log_service


class ImageGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    size: str | None = None


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _recover_image_results_from_logs(
    identity: dict[str, object],
    *,
    started_at: str,
    model: str = "",
    mode: str = "generate",
    count: int = 1,
    window_seconds: int = 900,
) -> dict[str, Any]:
    target_ts = _parse_timestamp(started_at)
    if target_ts <= 0:
        return {"items": []}
    normalized_model = str(model or "").strip()
    endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
    owner_id = str(identity.get("id") or "").strip()
    window = max(60, min(int(window_seconds or 900), 3600))
    limit = max(1, min(int(count or 1), 10))

    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in log_service.list(type="call", limit=1000):
        detail = item.get("detail") if isinstance(item, dict) else None
        if not isinstance(detail, dict):
            continue
        if owner_id and str(detail.get("key_id") or "").strip() != owner_id:
            continue
        if str(detail.get("endpoint") or "") != endpoint:
            continue
        if str(detail.get("status") or "") != "success":
            continue
        if normalized_model and str(detail.get("model") or "") != normalized_model:
            continue
        urls = detail.get("urls")
        if not isinstance(urls, list) or not any(isinstance(url, str) and url for url in urls):
            continue
        log_ts = _parse_timestamp(detail.get("started_at")) or _parse_timestamp(item.get("time"))
        if log_ts <= 0:
            continue
        distance = abs(log_ts - target_ts)
        if distance > window:
            continue
        data = [{"url": url} for url in urls if isinstance(url, str) and url]
        candidates.append((distance, {
            "id": f"recovered-{int(log_ts)}-{len(candidates)}",
            "status": "success",
            "mode": "edit" if endpoint.endswith("/edits") else "generate",
            "model": detail.get("model"),
            "created_at": detail.get("started_at") or item.get("time"),
            "updated_at": detail.get("ended_at") or item.get("time"),
            "data": data,
        }))
    candidates.sort(key=lambda pair: (pair[0], str(pair[1].get("updated_at") or "")))
    return {"items": [item for _, item in candidates[:limit]]}


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-tasks")
    async def list_image_tasks(
        ids: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(image_task_service.list_tasks, identity, _parse_task_ids(ids))

    @router.post("/api/image-tasks/generations")
    async def create_generation_task(
        body: ImageGenerationTaskRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_task_service.submit_generation,
                identity,
                client_task_id=body.client_task_id,
                prompt=body.prompt,
                model=body.model,
                size=body.size,
                base_url=resolve_image_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/edits")
    async def create_edit_task(
        request: Request,
        authorization: str | None = Header(default=None),
        image: list[UploadFile] | None = File(default=None),
        image_list: list[UploadFile] | None = File(default=None, alias="image[]"),
        client_task_id: str = Form(...),
        prompt: str = Form(...),
        model: str = Form(default="gpt-image-2"),
        size: str | None = Form(default=None),
    ):
        identity = require_identity(authorization)
        uploads = [*(image or []), *(image_list or [])]
        if not uploads:
            raise HTTPException(status_code=400, detail={"error": "image file is required"})
        images: list[tuple[bytes, str, str]] = []
        for upload in uploads:
            image_data = await upload.read()
            if not image_data:
                raise HTTPException(status_code=400, detail={"error": "image file is empty"})
            images.append((image_data, upload.filename or "image.png", upload.content_type or "image/png"))
        try:
            return await run_in_threadpool(
                image_task_service.submit_edit,
                identity,
                client_task_id=client_task_id,
                prompt=prompt,
                model=model,
                size=size,
                base_url=resolve_image_base_url(request),
                images=images,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


    @router.get("/api/image-tasks/recover")
    async def recover_image_task_results(
        started_at: str = Query(default=""),
        model: str = Query(default=""),
        mode: str = Query(default="generate"),
        count: int = Query(default=1, ge=1, le=10),
        window_seconds: int = Query(default=900, ge=60, le=3600),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            _recover_image_results_from_logs,
            identity,
            started_at=started_at,
            model=model,
            mode=mode,
            count=count,
            window_seconds=window_seconds,
        )

    return router
