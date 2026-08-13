from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.external_image_service as external_image_module
import services.protocol.openai_v1_image_generations as image_generation_module
from services.config import ConfigStore


class FakeResponse:
    def __init__(self, status_code: int, payload: object = None, content: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    instances: list["FakeSession"] = []
    response = FakeResponse(200, {"data": []})

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.closed = False
        self.__class__.instances.append(self)

    def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.__class__.response

    def get(self, url: str, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return self.__class__.response

    def close(self):
        self.closed = True


class ExternalImageServiceTests(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        self.model = {
            "id": "grok-imagine-image",
            "label": "Grok Imagine",
            "model": "grok-imagine-image",
            "endpoint": "https://example.test/grok2api/v1/images/generations",
            "api_key": "test-client-key",
            "enabled": True,
            "supports_edit": False,
            "default_size": "1024x1024",
            "timeout_seconds": 180,
        }

    def test_config_masks_key_and_preserves_it_when_other_fields_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            store = ConfigStore(path)
            store.update({"external_image_models": [self.model]})

            response = store.get()
            public_model = response["external_image_models"][0]
            self.assertNotIn("api_key", public_model)
            self.assertTrue(public_model["has_api_key"])

            # The browser receives a masked entry and sends an empty key back on
            # ordinary saves; the server must retain the original secret.
            store.update({
                "external_image_models": [{
                    **public_model,
                    "label": "Grok Imagine Updated",
                    "api_key": "",
                }],
            })
            saved = store.get_external_image_model("grok-imagine-image")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["api_key"], "test-client-key")
            self.assertEqual(saved["label"], "Grok Imagine Updated")

    def test_external_generation_uses_bearer_key_fixed_size_and_local_result_shape(self):
        image_bytes = b"test-png-bytes"
        FakeSession.response = FakeResponse(200, {
            "created": 123,
            "data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}],
        })
        service = external_image_module.ExternalImageService()
        with (
            mock.patch.object(external_image_module.config, "get_external_image_model", return_value=self.model),
            mock.patch.object(external_image_module, "Session", FakeSession),
            mock.patch.object(external_image_module.proxy_settings, "build_session_kwargs", return_value={}),
            mock.patch.object(external_image_module, "format_image_result", return_value={"created": 123, "data": [{"url": "/images/test.png"}]} ) as formatter,
        ):
            result = service.generate(
                model_name="grok-imagine-image",
                prompt="a red apple",
                n=1,
                size="16:9",
                response_format="url",
                base_url="https://image.example.test",
                uploader={"id": "user-1"},
            )

        self.assertEqual(result["data"][0]["url"], "/images/test.png")
        session = FakeSession.instances[0]
        request = session.posts[0]
        self.assertEqual(request["url"], self.model["endpoint"])
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-client-key")
        self.assertEqual(request["json"]["model"], "grok-imagine-image")
        self.assertEqual(request["json"]["size"], "1024x1024")
        formatter.assert_called_once()
        self.assertEqual(session.closed, True)

    def test_generation_handler_routes_registered_model_before_chatgpt_pool(self):
        expected = {"created": 1, "data": [{"url": "/images/test.png"}]}
        fake_service = mock.Mock()
        fake_service.has_model.return_value = True
        fake_service.generate.return_value = expected
        with mock.patch.object(image_generation_module, "external_image_service", fake_service):
            result = image_generation_module.handle({
                "model": "grok-imagine-image",
                "prompt": "a red apple",
                "n": 1,
                "response_format": "url",
            })

        self.assertEqual(result, expected)
        fake_service.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
