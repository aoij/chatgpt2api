from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")
os.environ.setdefault("CHATGPT2API_ADMIN_USERNAME", "admin")
os.environ.setdefault("CHATGPT2API_ADMIN_PASSWORD", "admin-pass")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.accounts as accounts_module
import api.support as support_module
import api.system as system_module
from services.account_service import AccountService
from services.auth_service import AuthService
from services.config import config
from services.storage.json_storage import JSONStorageBackend
from utils.helper import anonymize_token, split_image_model


class AccountCapabilityTests(unittest.TestCase):
    def test_unknown_quota_accounts_are_available_only_when_not_throttled(self) -> None:
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "限流", "image_quota_unknown": True, "quota": 0}
            )
        )
        self.assertTrue(
            AccountService._is_image_account_available(
                {"status": "正常", "image_quota_unknown": True, "quota": 0}
            )
        )

    def test_prolite_variants_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertEqual(service._normalize_account_type("prolite"), "ProLite")
            self.assertEqual(service._normalize_account_type("pro_lite"), "ProLite")

    def test_search_account_type_ignores_unrelated_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertIsNone(
                service._search_account_type(
                    {
                        "amr": ["pwd", "otp", "mfa"],
                        "chatgpt_compute_residency": "no_constraint",
                        "chatgpt_data_residency": "no_constraint",
                        "user_id": "user-I52GFfLGFM0dokFk2dBiKEBn",
                    }
                )
            )

    def test_mark_image_result_does_not_consume_unknown_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1"])
            service.update_account(
                "token-1",
                {
                    "status": "正常",
                    "quota": 0,
                    "image_quota_unknown": True,
                },
            )

            updated = service.mark_image_result("token-1", success=True)

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 0)
            self.assertEqual(updated["status"], "正常")
            self.assertTrue(updated["image_quota_unknown"])

    def test_split_image_model_supports_plan_type_prefix(self) -> None:
        self.assertEqual(split_image_model("gpt-image-2"), (None, "gpt-image-2"))
        self.assertEqual(split_image_model("plus-codex-gpt-image-2"), ("plus", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("team-codex-gpt-image-2"), ("team", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("pro-codex-gpt-image-2"), ("pro", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("plus-gpt-image-2"), (None, None))
        self.assertEqual(split_image_model("unknown-image-model"), (None, None))

    def test_get_available_access_token_filters_by_plan_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-plus", "type": "Plus", "status": "正常", "quota": 3},
                    {"access_token": "token-pro", "type": "Pro", "status": "正常", "quota": 3},
                ]
            )

            service.fetch_remote_info = lambda access_token, event="fetch_remote_info": service.get_account(access_token)

            plus_token = service.get_available_access_token(plan_type="plus")
            pro_token = service.get_available_access_token(plan_type="pro")
            service.release_image_slot(plus_token)
            service.release_image_slot(pro_token)

            self.assertEqual(plus_token, "token-plus")
            self.assertEqual(pro_token, "token-pro")


class TokenLogTests(unittest.TestCase):
    def test_anonymize_token_hides_raw_value(self) -> None:
        token = "super-secret-token"
        token_ref = anonymize_token(token)

        self.assertTrue(token_ref.startswith("token:"))
        self.assertNotIn(token, token_ref)


class AuthServiceTests(unittest.TestCase):
    def test_create_authenticate_disable_and_delete_user_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(role="user", name="Alice", quota=2)

            self.assertEqual(item["role"], "user")
            self.assertEqual(item["name"], "Alice")
            self.assertTrue(item["enabled"])
            self.assertEqual(item["quota"], 2)
            self.assertTrue(raw_key.startswith("sk-"))
            self.assertTrue(str(item.get("link_token") or "").startswith("lk-"))

            authed = service.authenticate(raw_key)
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertEqual(authed["role"], "user")
            self.assertEqual(authed["auth_mode"], "key")
            self.assertEqual(authed["scope"], "full")
            self.assertIsNotNone(authed["last_used_at"])
            link_authed = service.authenticate(str(item["link_token"]))
            self.assertIsNotNone(link_authed)
            self.assertEqual(link_authed["id"], item["id"])
            self.assertEqual(link_authed["auth_mode"], "link")
            self.assertEqual(link_authed["scope"], "image")

            updated = service.update_key(item["id"], {"enabled": False}, role="user")
            self.assertIsNotNone(updated)
            self.assertFalse(updated["enabled"])
            self.assertIsNone(service.authenticate(raw_key))

            self.assertTrue(service.delete_key(item["id"], role="user"))
            self.assertFalse(service.delete_key(item["id"], role="user"))
            self.assertEqual(service.list_keys(role="user"), [])

    def test_user_image_quota_reserve_and_refund(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice", quota=2)
            identity = service.authenticate(raw_key)

            self.assertEqual(service.reserve_image_quota(identity, 1), 1)
            self.assertEqual(service.get_public_key(item["id"])["quota"], 1)
            service.refund_image_quota(identity, 1)
            self.assertEqual(service.get_public_key(item["id"])["quota"], 2)
            service.reserve_image_quota(identity, 2)
            self.assertEqual(service.get_public_key(item["id"])["quota"], 0)
            with self.assertRaises(Exception):
                service.reserve_image_quota(identity, 1)

    def test_authenticate_ignores_last_used_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice", quota=2)

            def fail_save() -> None:
                raise OSError("disk unavailable")

            service._save = fail_save

            authed = service.authenticate(raw_key)

            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertIsNotNone(authed["last_used_at"])

    def test_update_user_key_replaces_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            updated = service.update_key(item["id"], {"key": "sk-user-custom-key"}, role="user")

            self.assertIsNotNone(updated)
            self.assertIsNone(service.authenticate(raw_key))

            authed = service.authenticate("sk-user-custom-key")
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])

    def test_user_key_name_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            first, _ = service.create_key(role="user", name="Alice")
            second, _ = service.create_key(role="user", name="Bob")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.create_key(role="user", name="Alice")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.update_key(second["id"], {"name": "Alice"}, role="user")

            updated = service.update_key(first["id"], {"name": "Alice"}, role="user")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["name"], "Alice")

    def test_create_key_supports_openid_username_and_password_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(
                role="user",
                name="啊哈",
                quota=6,
                open_id="openid-001",
                avatar_url="https://example.com/a.jpg",
                username="啊哈",
                password="Passw0rd!",
            )

            self.assertEqual(item["open_id"], "openid-001")
            self.assertEqual(item["username"], "啊哈")
            self.assertEqual(item["avatar_url"], "https://example.com/a.jpg")
            self.assertEqual(item["key"], raw_key)

            by_password = service.authenticate_password("啊哈", "Passw0rd!")
            self.assertIsNotNone(by_password)
            self.assertEqual(by_password["id"], item["id"])
            self.assertEqual(by_password["auth_mode"], "password")
            self.assertEqual(by_password["scope"], "full")

    def test_create_key_reuses_same_openid_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            first, _ = service.create_key(
                role="user",
                name="first",
                quota=1,
                open_id="openid-same",
                username="user_one",
                password="Passw0rd!",
            )
            second, raw_key = service.create_key(
                role="user",
                name="second",
                quota=9,
                open_id="openid-same",
                avatar_url="https://example.com/b.jpg",
                username="user_two",
                password="Passw0rd!2",
            )

            self.assertEqual(first["id"], second["id"])
            self.assertEqual(second["name"], "second")
            self.assertEqual(second["quota"], 9)
            self.assertEqual(second["username"], "user_two")
            self.assertEqual(second["avatar_url"], "https://example.com/b.jpg")
            self.assertEqual(raw_key, "")

    def test_openid_and_username_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            first, _ = service.create_key(role="user", name="first", open_id="openid-1", username="user-1", password="p1")
            second, _ = service.create_key(role="user", name="second", open_id="openid-2", username="user-2", password="p2")

            with self.assertRaisesRegex(ValueError, "这个微信用户已经绑定到其他账号了"):
                service.update_key(second["id"], {"open_id": "openid-1"}, role="user")

            with self.assertRaisesRegex(ValueError, "这个登录账号已经存在"):
                service.update_key(second["id"], {"username": "user-1"}, role="user")


class MiniAppAuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.storage = JSONStorageBackend(
            Path(self.tmp_dir.name) / "accounts.json",
            Path(self.tmp_dir.name) / "auth_keys.json",
        )
        self.auth_service = AuthService(self.storage)
        self.auth_patcher = mock.patch.object(system_module, "auth_service", self.auth_service)
        self.accounts_patcher = mock.patch.object(accounts_module, "auth_service", self.auth_service)
        self.support_patcher = mock.patch.object(support_module, "auth_service", self.auth_service)
        self.auth_patcher.start()
        self.accounts_patcher.start()
        self.support_patcher.start()
        self.addCleanup(self.auth_patcher.stop)
        self.addCleanup(self.accounts_patcher.stop)
        self.addCleanup(self.support_patcher.stop)

        app = FastAPI()
        app.include_router(system_module.create_router("test-version"))
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)
        self.admin_headers = {"Authorization": f"Bearer {config.auth_key}"}

    def test_password_login_and_auth_session(self) -> None:
        item, _ = self.auth_service.create_key(
            role="user",
            name="啊哈",
            quota=8,
            open_id="openid-auth-session",
            avatar_url="https://example.com/avatar.jpg",
            username="啊哈",
            password="Passw0rd!",
        )

        login_resp = self.client.post("/auth/login", json={"username": "啊哈", "password": "Passw0rd!"})
        self.assertEqual(login_resp.status_code, 200, login_resp.text)
        login_data = login_resp.json()
        self.assertEqual(login_data["role"], "user")
        self.assertEqual(login_data["subject_id"], item["id"])
        self.assertTrue(str(login_data.get("key") or "").startswith("lk-"))

        session_resp = self.client.get("/auth/session", headers={"Authorization": f"Bearer {login_data['key']}"})
        self.assertEqual(session_resp.status_code, 200, session_resp.text)
        session_data = session_resp.json()
        self.assertEqual(session_data["id"], item["id"])
        self.assertEqual(session_data["username"], "啊哈")
        self.assertEqual(session_data["open_id"], "openid-auth-session")
        self.assertEqual(session_data["avatar_url"], "https://example.com/avatar.jpg")

    def test_miniapp_bind_and_update(self) -> None:
        bind_resp = self.client.post(
            "/api/admin/miniapp/bind",
            headers=self.admin_headers,
            json={
                "name": "图灵画友A",
                "quota": 5,
                "open_id": "openid-miniapp-bind",
                "avatar_url": "https://example.com/bind.jpg",
                "enabled": True,
                "username": "miniapp_user_a",
                "password": "Passw0rd!",
            },
        )
        self.assertEqual(bind_resp.status_code, 200, bind_resp.text)
        bind_data = bind_resp.json()["item"]
        self.assertEqual(bind_data["open_id"], "openid-miniapp-bind")
        self.assertEqual(bind_data["username"], "miniapp_user_a")

        update_resp = self.client.post(
            "/api/admin/miniapp/update",
            headers=self.admin_headers,
            json={
                "key_id": bind_data["id"],
                "name": "图灵画友B",
                "quota": 9,
                "avatar_url": "https://example.com/update.jpg",
                "username": "miniapp_user_b",
                "password": "Passw0rd!2",
            },
        )
        self.assertEqual(update_resp.status_code, 200, update_resp.text)
        updated = update_resp.json()["item"]
        self.assertEqual(updated["name"], "图灵画友B")
        self.assertEqual(updated["quota"], 9)
        self.assertEqual(updated["username"], "miniapp_user_b")
        self.assertEqual(updated["avatar_url"], "https://example.com/update.jpg")

    def test_miniapp_bind_same_openid_does_not_create_second_user(self) -> None:
        first = self.client.post(
            "/api/admin/miniapp/bind",
            headers=self.admin_headers,
            json={
                "name": "first",
                "quota": 1,
                "open_id": "openid-bind-once",
                "username": "bind_once_1",
                "password": "Passw0rd!",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_item = first.json()["item"]

        second = self.client.post(
            "/api/admin/miniapp/bind",
            headers=self.admin_headers,
            json={
                "name": "second",
                "quota": 7,
                "open_id": "openid-bind-once",
                "username": "bind_once_2",
                "password": "Passw0rd!2",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_item = second.json()["item"]

        self.assertEqual(first_item["id"], second_item["id"])
        self.assertEqual(second_item["name"], "second")
        self.assertEqual(second_item["quota"], 7)
        self.assertEqual(second_item["username"], "bind_once_2")


if __name__ == "__main__":
    unittest.main()
