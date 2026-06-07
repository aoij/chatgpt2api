from __future__ import annotations

import time
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from api.image_inputs import parse_image_edit_request, read_image_sources
from api.support import raise_image_quota_error, require_identity, resolve_image_base_url
from services.content_filter import check_request, request_text
from services.auth_service import ImageQuotaExceeded, auth_service
from services.image_conversation_service import image_conversation_service
from services.log_service import LoggedCall
from services.protocol import (
    anthropic_v1_messages,
    openai_v1_chat_complete,
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_models,
    openai_v1_response,
)


def _count_image_results(result: object) -> int:
    if not isinstance(result, dict):
        return 0
    data = result.get("data")
    if not isinstance(data, list):
        return 0
    count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("url") or item.get("b64_json"):
            count += 1
    return count


def _refund_unused_quota(identity: dict[str, object], reserved_quota: int, success_count: int) -> None:
    refund = max(0, int(reserved_quota or 0) - max(0, int(success_count or 0)))
    if refund > 0:
        auth_service.refund_image_quota(identity, refund)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    quality: str = "auto"
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | None = None
    n: int | None = None
    stream: bool | None = None
    modalities: list[str] | None = None
    messages: list[dict[str, object]] | None = None


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[dict[str, object]] | None = None
    system: object | None = None
    stream: bool | None = None


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def _uploader_from_identity(identity: dict[str, object]) -> dict[str, object]:
    return {
        "id": identity.get("id"),
        "name": identity.get("name"),
        "role": identity.get("role"),
        "auth_mode": identity.get("auth_mode"),
        "scope": identity.get("scope"),
    }


def _conversation_title(prompt: str) -> str:
    text = " ".join(str(prompt or "").split()).strip()
    return text[:28] or "API 生图"


def _image_items_from_openai_result(result: object) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if not isinstance(data, list):
        return []
    images: list[dict[str, object]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        url = item.get("url") if isinstance(item.get("url"), str) else ""
        b64_json = item.get("b64_json") if isinstance(item.get("b64_json"), str) else ""
        if not url and not b64_json:
            continue
        image: dict[str, object] = {
            "id": f"api-img-{uuid4().hex[:12]}",
            "status": "success",
        }
        if url:
            image["url"] = url
        elif b64_json:
            image["b64_json"] = b64_json
        revised_prompt = item.get("revised_prompt")
        if isinstance(revised_prompt, str) and revised_prompt:
            image["revised_prompt"] = revised_prompt
        images.append(image)
    return images


def _save_api_image_conversation(
    identity: dict[str, object],
    *,
    prompt: str,
    model: str,
    mode: str,
    count: int,
    size: str | None = None,
    quality: str | None = None,
    result: object = None,
    error: str = "",
) -> None:
    if identity.get("role") != "user":
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    images = _image_items_from_openai_result(result)
    if error and not images:
        images = [{
            "id": f"api-img-{uuid4().hex[:12]}",
            "status": "error",
            "error": error,
        }]
    if not images:
        return
    status = "error" if error and not any(item.get("status") == "success" for item in images) else "success"
    conversation = {
        "id": f"api-{uuid4().hex[:12]}",
        "title": _conversation_title(prompt),
        "createdAt": now,
        "updatedAt": now,
        "turns": [{
            "id": f"turn-{uuid4().hex[:12]}",
            "prompt": prompt,
            "model": model or "gpt-image-2",
            "mode": "edit" if mode == "edit" else "generate",
            "referenceImages": [],
            "count": max(1, int(count or len(images) or 1)),
            "size": size or "",
            "quality": quality or "auto",
            "images": images,
            "createdAt": now,
            "status": status,
            **({"error": error} if error else {}),
        }],
    }
    image_conversation_service.save(identity, conversation)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        try:
            return await run_in_threadpool(openai_v1_models.list_models)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["base_url"] = resolve_image_base_url(request)
        payload["uploader"] = _uploader_from_identity(identity)
        reserved_quota = 0
        try:
            reserved_quota = auth_service.reserve_image_quota(identity, body.n)
        except ImageQuotaExceeded as exc:
            raise_image_quota_error(exc)
        call = LoggedCall(identity, "/v1/images/generations", body.model, "文生图", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        try:
            result = await call.run(openai_v1_image_generations.handle, payload)
            if isinstance(result, dict):
                await run_in_threadpool(
                    _save_api_image_conversation,
                    identity,
                    prompt=body.prompt,
                    model=body.model,
                    mode="generate",
                    count=body.n,
                    size=body.size,
                    quality=body.quality,
                    result=result,
                )
                _refund_unused_quota(identity, reserved_quota, _count_image_results(result))
            if reserved_quota and int(getattr(result, "status_code", 200) or 200) >= 400:
                auth_service.refund_image_quota(identity, reserved_quota)
            return result
        except Exception:
            if reserved_quota:
                auth_service.refund_image_quota(identity, reserved_quota)
            raise

    @router.post("/v1/images/edits")
    async def edit_images(
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources = await parse_image_edit_request(request)
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/v1/images/edits", model, "图生图", request_text=prompt)
        await filter_or_log(call, prompt)
        n = int(payload["n"])
        reserved_quota = 0
        try:
            reserved_quota = auth_service.reserve_image_quota(identity, n)
        except ImageQuotaExceeded as exc:
            raise_image_quota_error(exc)
        payload["images"] = await read_image_sources(image_sources)
        payload["base_url"] = resolve_image_base_url(request)
        payload["uploader"] = _uploader_from_identity(identity)
        try:
            result = await call.run(openai_v1_image_edit.handle, payload)
            if isinstance(result, dict):
                await run_in_threadpool(
                    _save_api_image_conversation,
                    identity,
                    prompt=prompt,
                    model=model,
                    mode="edit",
                    count=n,
                    size=str(payload.get("size") or ""),
                    quality=str(payload.get("quality") or "auto"),
                    result=result,
                )
                _refund_unused_quota(identity, reserved_quota, _count_image_results(result))
            if reserved_quota and int(getattr(result, "status_code", 200) or 200) >= 400:
                auth_service.refund_image_quota(identity, reserved_quota)
            return result
        except Exception:
            if reserved_quota:
                auth_service.refund_image_quota(identity, reserved_quota)
            raise

    @router.post("/v1/chat/completions")
    async def create_chat_completion(body: ChatCompletionRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["uploader"] = _uploader_from_identity(identity)
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("prompt"), payload.get("messages"))
        call = LoggedCall(identity, "/v1/chat/completions", model, "文本生成", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(openai_v1_chat_complete.handle, payload)

    @router.post("/v1/responses")
    async def create_response(body: ResponseCreateRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["uploader"] = _uploader_from_identity(identity)
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("input"), payload.get("instructions"))
        call = LoggedCall(identity, "/v1/responses", model, "Responses", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(openai_v1_response.handle, payload)

    @router.post("/v1/messages")
    async def create_message(
            body: AnthropicMessageRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
            anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("system"), payload.get("messages"), payload.get("tools"))
        call = LoggedCall(identity, "/v1/messages", model, "Messages", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(anthropic_v1_messages.handle, payload, sse="anthropic")

    return router
