from __future__ import annotations

from typing import Any, Iterator

from services.external_image_service import external_image_service
from services.protocol.conversation import (
    ConversationRequest,
    collect_image_outputs,
    count_text_tokens,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from utils.image_tokens import count_image_output_items_tokens, image_usage


def _external_result_chunks(result: dict[str, Any], model: str) -> Iterator[dict[str, Any]]:
    """Adapt a completed external API response to the existing SSE contract."""
    yield {
        "object": "image.generation.result",
        "created": int(result.get("created") or 0),
        "model": model,
        "index": 1,
        "total": 1,
        "data": result.get("data") if isinstance(result.get("data"), list) else [],
    }


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    uploader = body.get("uploader") if isinstance(body.get("uploader"), dict) else None
    selected_account_id = str(body.get("selected_account_id") or "")
    progress_callback = body.get("progress_callback")
    # Registered external models bypass the ChatGPT account pool entirely, while
    # the current GPT/Codex models below retain their established retry behavior.
    if external_image_service.has_model(model):
        result = external_image_service.generate(
            model_name=model,
            prompt=prompt,
            n=n,
            size=size,
            response_format=response_format,
            base_url=base_url,
            uploader=uploader,
            progress_callback=progress_callback,
        )
        if body.get("stream"):
            return _external_result_chunks(result, model)
        return result
    outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        size=size,
        quality=quality,
        response_format=response_format,
        base_url=base_url,
        uploader=uploader,
        selected_account_id=selected_account_id,
        message_as_error=True,
        progress_callback=progress_callback,
    ))
    if body.get("stream"):
        return stream_image_chunks(outputs)
    result = collect_image_outputs(outputs)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
