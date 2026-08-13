"""External OpenAI-compatible image generation adapters.

This module deliberately owns only external image models.  The existing
ChatGPT account-pool flow remains in ``services.protocol.conversation`` and
is selected whenever a request does not match an external model configuration.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any

from curl_cffi.requests import Session

from services.config import config
from services.protocol.conversation import format_image_result
from services.proxy_service import proxy_settings


MAX_EXTERNAL_IMAGE_BYTES = 20 * 1024 * 1024


class ExternalImageModelError(RuntimeError):
    """A public, credential-safe error raised for external image providers."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _decode_b64_image(value: object) -> bytes:
    raw = _clean(value)
    if raw.startswith("data:image/") and "," in raw:
        raw = raw.split(",", 1)[1]
    if not raw:
        raise ExternalImageModelError("外部图片模型未返回图片内容")
    try:
        image = base64.b64decode(raw, validate=False)
    except (ValueError, binascii.Error) as exc:
        raise ExternalImageModelError("外部图片模型返回了无效的图片数据") from exc
    if not image:
        raise ExternalImageModelError("外部图片模型返回了空图片")
    if len(image) > MAX_EXTERNAL_IMAGE_BYTES:
        raise ExternalImageModelError("外部图片模型返回的单张图片超过大小限制")
    return image


class ExternalImageService:
    def has_model(self, model_name: object) -> bool:
        """Whether a model is registered, including disabled/incomplete entries.

        Returning true for an incomplete entry lets the request surface an
        actionable configuration error instead of incorrectly falling through
        to the ChatGPT account pool as an unsupported model.
        """
        return config.get_external_image_model(model_name) is not None

    def public_models(self) -> list[dict[str, object]]:
        """Expose enabled, credential-free options to the picture workspace."""
        items: list[dict[str, object]] = []
        for model in config.get_enabled_external_image_models():
            items.append({
                "id": _clean(model.get("id")) or _clean(model.get("model")),
                "label": _clean(model.get("label")) or _clean(model.get("model")),
                "model": _clean(model.get("model")),
                "provider": "external",
                "supports_edit": bool(model.get("supports_edit")),
                "default_size": _clean(model.get("default_size")),
            })
        return items

    def _load_model(self, model_name: object) -> dict[str, object]:
        model = config.get_external_image_model(model_name)
        if model is None:
            raise ExternalImageModelError("外部图片模型不存在")
        label = _clean(model.get("label")) or _clean(model.get("model"))
        if not bool(model.get("enabled")):
            raise ExternalImageModelError(f"外部图片模型“{label}”尚未启用")
        if not _clean(model.get("api_key")):
            raise ExternalImageModelError(f"外部图片模型“{label}”未配置 Client Key")
        return model

    @staticmethod
    def _request_payload(
        model: dict[str, object],
        *,
        prompt: str,
        n: int,
        size: object,
    ) -> dict[str, object]:
        """Build the narrow common subset accepted by OpenAI image endpoints.

        Some compatible services reject OpenAI-only fields such as ``quality``.
        Keeping the request to prompt/n/size/response_format makes the Grok
        endpoint in this task work and is the safest generic compatibility set.
        """
        payload: dict[str, object] = {
            "model": _clean(model.get("model")),
            "prompt": prompt,
            "n": max(1, int(n or 1)),
            "response_format": "b64_json",
        }
        # The drawing UI uses aspect-ratio values for the ChatGPT account pool.
        # An external provider can define a verified pixel size (Grok currently
        # uses 1024x1024), which takes precedence to avoid sending an invalid
        # ratio such as "16:9" to an OpenAI-compatible endpoint.
        normalized_size = _clean(model.get("default_size")) or _clean(size)
        if normalized_size:
            payload["size"] = normalized_size
        return payload

    @staticmethod
    def _download_url(session: Session, url: str, timeout_seconds: int) -> bytes:
        response = session.get(url, timeout=timeout_seconds)
        if int(response.status_code) >= 400:
            raise ExternalImageModelError(f"外部图片下载失败，HTTP {response.status_code}")
        image = bytes(response.content or b"")
        if not image:
            raise ExternalImageModelError("外部图片模型返回了空图片")
        if len(image) > MAX_EXTERNAL_IMAGE_BYTES:
            raise ExternalImageModelError("外部图片模型返回的单张图片超过大小限制")
        return image

    def generate(
        self,
        *,
        model_name: object,
        prompt: str,
        n: int,
        size: object,
        response_format: str,
        base_url: str | None,
        uploader: object,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Call the configured provider and convert every result into local storage.

        Returning local image URLs is essential: image task persistence, user
        image management, expiry cleanup, and downloads must not depend on an
        upstream URL that can expire after the external provider request.
        """
        model = self._load_model(model_name)
        endpoint = _clean(model.get("endpoint"))
        api_key = _clean(model.get("api_key"))
        timeout_seconds = max(5, int(model.get("timeout_seconds") or 180))
        session = Session(
            impersonate="edge101",
            verify=True,
            **proxy_settings.build_session_kwargs(),
        )
        try:
            if callable(progress_callback):
                progress_callback("calling_external_model")
            response = session.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=self._request_payload(model, prompt=prompt, n=n, size=size),
                timeout=timeout_seconds,
            )
            if int(response.status_code) >= 400:
                # Do not pass the upstream body through.  It may contain provider
                # diagnostics or request metadata that should not reach end users.
                raise ExternalImageModelError(f"外部图片模型请求失败，HTTP {response.status_code}")
            try:
                payload = response.json()
            except Exception as exc:
                raise ExternalImageModelError("外部图片模型返回了非 JSON 响应") from exc
            raw_items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(raw_items, list) or not raw_items:
                raise ExternalImageModelError("外部图片模型未返回图片结果")
            if callable(progress_callback):
                progress_callback("receiving_image")

            image_items: list[dict[str, str]] = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                revised_prompt = _clean(raw_item.get("revised_prompt")) or prompt
                if _clean(raw_item.get("b64_json")):
                    image_bytes = _decode_b64_image(raw_item.get("b64_json"))
                elif _clean(raw_item.get("url")):
                    image_bytes = self._download_url(session, _clean(raw_item.get("url")), timeout_seconds)
                else:
                    continue
                image_items.append({
                    "b64_json": base64.b64encode(image_bytes).decode("ascii"),
                    "revised_prompt": revised_prompt,
                })
            if not image_items:
                raise ExternalImageModelError("外部图片模型返回结果中不包含可用图片")
            return format_image_result(
                image_items,
                prompt,
                response_format,
                base_url,
                int(time.time()),
                uploader=uploader,
            )
        finally:
            session.close()


external_image_service = ExternalImageService()
