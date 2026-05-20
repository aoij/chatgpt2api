from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.system as system_module
import services.image_service as image_service_module


class ImageManagerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(system_module.create_router("test-version"))
        self.client = TestClient(app)

    def test_user_image_query_is_forced_to_self_subject(self) -> None:
        with (
            mock.patch.object(system_module, "require_identity", return_value={"id": "user-123", "role": "user", "name": "Alice"}),
            mock.patch.object(system_module, "list_images", return_value={"items": [], "groups": [], "uploaders": [], "uploader_groups": []}) as list_mock,
        ):
            response = self.client.get("/api/images?uploader=other-user", headers={"Authorization": "Bearer test-user"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list_mock.call_args.kwargs["uploader"], "user-123")

    def test_user_image_delete_is_forced_to_self_subject(self) -> None:
        with (
            mock.patch.object(system_module, "require_identity", return_value={"id": "user-123", "role": "user", "name": "Alice"}),
            mock.patch.object(system_module, "delete_images", return_value={"removed": 1}) as delete_mock,
        ):
            response = self.client.post(
                "/api/images/delete",
                headers={"Authorization": "Bearer test-user"},
                json={"paths": ["2026/04/30/a.png"], "uploader": "other-user"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(delete_mock.call_args.kwargs["uploader"], "user-123")

    def test_admin_image_query_keeps_requested_uploader(self) -> None:
        with (
            mock.patch.object(system_module, "require_identity", return_value={"id": "admin", "role": "admin", "name": "管理员"}),
            mock.patch.object(system_module, "list_images", return_value={"items": [], "groups": [], "uploaders": [], "uploader_groups": []}) as list_mock,
        ):
            response = self.client.get("/api/images?uploader=user-456", headers={"Authorization": "Bearer admin"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list_mock.call_args.kwargs["uploader"], "user-456")


class ImageServiceDeletePermissionTests(unittest.TestCase):
    def test_delete_images_only_removes_matching_uploader_when_paths_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            images_dir = root / "images"
            image_one = images_dir / "2026" / "04" / "30" / "user-1.png"
            image_two = images_dir / "2026" / "04" / "30" / "user-2.png"
            image_one.parent.mkdir(parents=True, exist_ok=True)
            image_one.write_bytes(b"one")
            image_two.write_bytes(b"two")

            metadata_path = root / "image_metadata.json"
            metadata_path.write_text(json.dumps({
                "images": {
                    "2026/04/30/user-1.png": {"uploader_id": "user-1", "uploader_name": "Alice"},
                    "2026/04/30/user-2.png": {"uploader_id": "user-2", "uploader_name": "Bob"},
                }
            }, ensure_ascii=False), encoding="utf-8")

            fake_config = SimpleNamespace(
                images_dir=images_dir,
                cleanup_old_images=lambda: None,
            )

            with (
                mock.patch.object(image_service_module, "config", fake_config),
                mock.patch.object(image_service_module, "_METADATA_FILE", metadata_path),
            ):
                result = image_service_module.delete_images(
                    paths=["2026/04/30/user-1.png", "2026/04/30/user-2.png"],
                    uploader="user-1",
                )

            self.assertEqual(result["removed"], 1)
            self.assertFalse(image_one.exists())
            self.assertTrue(image_two.exists())

    def test_download_images_respects_uploader_and_returns_full_size_jpeg_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            images_dir = root / "images"
            image_one = images_dir / "2026" / "04" / "30" / "user-1.png"
            image_two = images_dir / "2026" / "04" / "30" / "user-2.png"
            image_one.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (255, 0, 0)).save(image_one)
            Image.new("RGB", (16, 16), (0, 0, 255)).save(image_two)

            metadata_path = root / "image_metadata.json"
            metadata_path.write_text(json.dumps({
                "images": {
                    "2026/04/30/user-1.png": {"uploader_id": "user-1", "uploader_name": "Alice"},
                    "2026/04/30/user-2.png": {"uploader_id": "user-2", "uploader_name": "Bob"},
                }
            }, ensure_ascii=False), encoding="utf-8")

            fake_config = SimpleNamespace(
                images_dir=images_dir,
                image_retention_days=10,
                cleanup_old_images=lambda: None,
            )

            with (
                mock.patch.object(image_service_module, "config", fake_config),
                mock.patch.object(image_service_module, "_METADATA_FILE", metadata_path),
            ):
                single = image_service_module.build_image_download("2026/04/30/user-1.png", uploader="user-1")
                denied = image_service_module.build_image_download("2026/04/30/user-2.png", uploader="user-1")
                batch = image_service_module.build_images_zip(
                    ["2026/04/30/user-1.png", "2026/04/30/user-2.png"],
                    uploader="user-1",
                )

            self.assertIsNotNone(single)
            self.assertEqual(single["media_type"], "image/jpeg")
            self.assertTrue(str(single["filename"]).endswith(".jpg"))
            self.assertIsNone(denied)
            self.assertIsNotNone(batch)
            with zipfile.ZipFile(BytesIO(batch["content"])) as archive:
                self.assertEqual(archive.namelist(), ["2026/04/30/user-1.jpg"])
                with Image.open(BytesIO(archive.read("2026/04/30/user-1.jpg"))) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.size, (16, 16))


if __name__ == "__main__":
    unittest.main()
