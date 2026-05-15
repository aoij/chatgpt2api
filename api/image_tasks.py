from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.image_inputs import parse_image_edit_request, read_image_sources
from api.support import raise_image_quota_error, require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.auth_service import ImageQuotaExceeded
from services.image_conversation_service import image_conversation_service
from services.image_task_service import image_task_service
from services.log_service import LoggedCall, log_service

RECENT_CONVERSATION_TASK_REPAIR_WINDOW_SECONDS = 24 * 60 * 60
MAX_RECENT_CONVERSATION_SYNC_ITEMS = 24


class ImageGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    size: str | None = None


class ImageConversationSaveRequest(BaseModel):
    items: list[dict[str, Any]] = []


class ImageConversationItemRequest(BaseModel):
    conversation: dict[str, Any]


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


def _sync_turn_status(turn: dict[str, Any]) -> tuple[str, str]:
    images = turn.get("images") if isinstance(turn.get("images"), list) else []
    loading = sum(1 for image in images if isinstance(image, dict) and image.get("status") == "loading")
    failed = sum(1 for image in images if isinstance(image, dict) and image.get("status") == "error")
    success = sum(1 for image in images if isinstance(image, dict) and image.get("status") == "success")
    if loading:
        return ("queued" if turn.get("status") == "queued" else "generating"), ""
    if failed:
        return "error", f"其中 {failed} 张未成功生成"
    if success:
        return "success", ""
    return "queued", ""


def _friendly_image_task_error(error: object) -> str:
    text = str(error or "").strip()
    lower = text.lower()
    if not text or text == "Network Error":
        return "网络请求失败：可能是移动网络不稳定、服务刚重启，或一次上传图片过大，请稍后重试"
    if (
        "cloudflare" in lower
        or "origin web server" in lower
        or "invalid or incomplete response" in lower
        or "proxy read timeout" in lower
        or "error 520" in lower
        or "error 522" in lower
        or "error 524" in lower
    ):
        return "上游图片服务临时返回 Cloudflare 错误，可能是节点/账号或上游服务拥堵，请稍后重试；如连续出现请切换账号/节点"
    if "curl: (28)" in lower or "operation timed out" in lower or "timed out after" in lower or "timeout" in lower:
        return "上游图片生成或下载超时，请稍后重试；如果连续出现，请减少同时生成数量或切换账号/节点"
    return text


def _is_managed_image_url(value: object) -> bool:
    return isinstance(value, str) and "/images/" in value


def _sync_conversations_with_tasks(identity: dict[str, object], items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    recent_items = items[:MAX_RECENT_CONVERSATION_SYNC_ITEMS]
    repair_cutoff = time.time() - RECENT_CONVERSATION_TASK_REPAIR_WINDOW_SECONDS
    task_ids = sorted(
        {
            str(image.get("taskId") or image.get("id") or "").strip()
            for conversation in recent_items
            if isinstance(conversation, dict)
            if (
                (
                    _parse_timestamp(conversation.get("updatedAt"))
                    or _parse_timestamp(conversation.get("createdAt"))
                ) >= repair_cutoff
            )
            for turn in (conversation.get("turns") if isinstance(conversation.get("turns"), list) else [])
            if isinstance(turn, dict)
            for image in (turn.get("images") if isinstance(turn.get("images"), list) else [])
            if isinstance(image, dict)
            and str(image.get("taskId") or image.get("id") or "").strip()
            and (
                image.get("status") != "success"
                or not (image.get("url") or image.get("b64_json"))
                or (
                    (
                        _parse_timestamp(conversation.get("updatedAt"))
                        or _parse_timestamp(conversation.get("createdAt"))
                    ) >= repair_cutoff
                    and bool(image.get("url"))
                    and not _is_managed_image_url(image.get("url"))
                )
            )
        }
    )
    if not task_ids:
        return items, False

    task_list = image_task_service.list_tasks(identity, task_ids)
    task_map = {
        str(task.get("id") or ""): task
        for task in task_list.get("items", [])
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    if not task_map:
        return items, False

    changed = False
    synced_items: list[dict[str, Any]] = []
    for index, conversation in enumerate(items):
        if not isinstance(conversation, dict):
            synced_items.append(conversation)
            continue
        if index >= MAX_RECENT_CONVERSATION_SYNC_ITEMS:
            synced_items.append(conversation)
            continue
        conversation_changed = False
        turns: list[dict[str, Any]] = []
        raw_turns = conversation.get("turns") if isinstance(conversation.get("turns"), list) else []
        for turn in raw_turns:
            if not isinstance(turn, dict):
                continue
            turn_changed = False
            images: list[dict[str, Any]] = []
            raw_images = turn.get("images") if isinstance(turn.get("images"), list) else []
            for image in raw_images:
                if not isinstance(image, dict):
                    continue
                task_id = str(image.get("taskId") or image.get("id") or "").strip()
                task = task_map.get(task_id)
                if not task:
                    images.append(image)
                    continue
                next_image = dict(image)
                next_image["taskId"] = task_id
                status = str(task.get("status") or "")
                if status == "success":
                    data = task.get("data") if isinstance(task.get("data"), list) else []
                    first = next((item for item in data if isinstance(item, dict) and (item.get("url") or item.get("b64_json"))), None)
                    if first:
                        next_image["status"] = "success"
                        if first.get("url"):
                            next_image["url"] = first.get("url")
                            next_image.pop("b64_json", None)
                        elif first.get("b64_json"):
                            next_image["b64_json"] = first.get("b64_json")
                            next_image.pop("url", None)
                        if first.get("revised_prompt"):
                            next_image["revised_prompt"] = first.get("revised_prompt")
                        next_image.pop("error", None)
                elif status == "error":
                    next_image["status"] = "error"
                    next_image["error"] = _friendly_image_task_error(task.get("error") or "生成失败")
                    next_image.pop("url", None)
                    next_image.pop("b64_json", None)
                    next_image.pop("revised_prompt", None)
                elif status in {"queued", "running"}:
                    next_image["status"] = "loading"
                    next_image.pop("error", None)
                    next_image.pop("url", None)
                    next_image.pop("b64_json", None)
                    next_image.pop("revised_prompt", None)

                if next_image != image:
                    turn_changed = True
                images.append(next_image)

            if turn_changed:
                next_turn = dict(turn)
                next_turn["images"] = images
                status, error = _sync_turn_status(next_turn)
                next_turn["status"] = status
                if error:
                    next_turn["error"] = error
                else:
                    next_turn.pop("error", None)
                turns.append(next_turn)
                conversation_changed = True
            else:
                turns.append(turn)

        if conversation_changed:
            next_conversation = dict(conversation)
            next_conversation["turns"] = turns
            next_conversation["updatedAt"] = datetime.now().isoformat(timespec="milliseconds")
            synced_items.append(next_conversation)
            changed = True
        else:
            synced_items.append(conversation)

    return synced_items, changed


def _sync_and_save_conversations(identity: dict[str, object], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    synced_items, changed = _sync_conversations_with_tasks(identity, items)
    if changed:
        return image_conversation_service.save_many(identity, synced_items)
    return synced_items


def _sync_single_conversation(identity: dict[str, object], conversation_id: str) -> dict[str, Any] | None:
    conversation = image_conversation_service.get(identity, conversation_id)
    if not isinstance(conversation, dict):
        return None
    synced_items, changed = _sync_conversations_with_tasks(identity, [conversation])
    if not synced_items:
        return None
    synced = synced_items[0] if isinstance(synced_items[0], dict) else None
    if synced is None:
        return None
    if changed:
        image_conversation_service.save(identity, synced)
    return synced


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-tasks")
    async def list_image_tasks(
        ids: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(image_task_service.list_tasks, identity, _parse_task_ids(ids))

    @router.get("/api/image-tasks/runtime")
    async def get_image_task_runtime(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(image_task_service.get_runtime_stats)

    @router.get("/api/image-conversations")
    async def list_image_conversations(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def _list() -> list[dict[str, Any]]:
            return _sync_and_save_conversations(identity, image_conversation_service.list(identity))

        return {"items": await run_in_threadpool(_list)}

    @router.get("/api/image-conversations/summary")
    async def list_image_conversation_summary(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def _list_summary() -> list[dict[str, Any]]:
            items = _sync_and_save_conversations(identity, image_conversation_service.list(identity))
            return image_conversation_service.summarize_items(items)

        return {"items": await run_in_threadpool(_list_summary)}

    @router.get("/api/image-conversations/{conversation_id}")
    async def get_image_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def _get() -> dict[str, Any] | None:
            return _sync_single_conversation(identity, conversation_id)

        item = await run_in_threadpool(_get)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"item": item}

    @router.put("/api/image-conversations")
    async def save_image_conversations(body: ImageConversationSaveRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def _save_many() -> list[dict[str, Any]]:
            items = _sync_and_save_conversations(identity, image_conversation_service.save_many(identity, body.items))
            return image_conversation_service.summarize_items(items)

        return {"items": await run_in_threadpool(_save_many)}

    @router.put("/api/image-conversations/{conversation_id}")
    async def save_image_conversation(
        conversation_id: str,
        body: ImageConversationItemRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        conversation = dict(body.conversation or {})
        conversation["id"] = str(conversation.get("id") or conversation_id)
        def _save() -> dict[str, Any]:
            image_conversation_service.save(identity, conversation)
            item = _sync_single_conversation(identity, conversation["id"])
            if item is None:
                raise HTTPException(status_code=404, detail={"error": "conversation not found"})
            summary = image_conversation_service.summarize_items([item])
            return {"item": item, "summary": summary[0] if summary else None}

        return await run_in_threadpool(_save)

    @router.delete("/api/image-conversations/{conversation_id}")
    async def delete_image_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def _delete() -> list[dict[str, Any]]:
            items = image_conversation_service.delete(identity, conversation_id)
            return image_conversation_service.summarize_items(items)

        return {"items": await run_in_threadpool(_delete)}

    @router.delete("/api/image-conversations")
    async def clear_image_conversations(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        await run_in_threadpool(image_conversation_service.clear, identity)
        return {"items": []}

    @router.post("/api/image-tasks/generations")
    async def create_generation_task(
        body: ImageGenerationTaskRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/generations", body.model, "文生图任务", request_text=body.prompt), body.prompt)
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
        except ImageQuotaExceeded as exc:
            raise_image_quota_error(exc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/edits")
    async def create_edit_task(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources = await parse_image_edit_request(request)
        client_task_id = str(payload.get("client_task_id") or "").strip()
        if not client_task_id:
            raise HTTPException(status_code=400, detail={"error": "client_task_id is required"})
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/edits", model, "图生图任务", request_text=prompt), prompt)
        images = await read_image_sources(image_sources)
        try:
            return await run_in_threadpool(
                image_task_service.submit_edit,
                identity,
                client_task_id=client_task_id,
                prompt=prompt,
                model=model,
                size=payload["size"],
                base_url=resolve_image_base_url(request),
                images=images,
            )
        except ImageQuotaExceeded as exc:
            raise_image_quota_error(exc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/image-tasks/recover")
    async def recover_image_task_results(
        started_at: str = Query(default=""),
        model: str = Query(default=""),
        mode: str = Query(default="generate"),
        count: int = Query(default=1, ge=1, le=10),
        window_seconds: int = Query(default=900, ge=60, le=10800),
        authorization: str | None = Header(default=None),
    ):
        _ = (started_at, model, mode, count, window_seconds)
        require_identity(authorization)
        return {"items": []}

    return router
