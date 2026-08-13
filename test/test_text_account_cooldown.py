import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")
os.environ.setdefault("CHATGPT2API_ADMIN_USERNAME", "admin")
os.environ.setdefault("CHATGPT2API_ADMIN_PASSWORD", "admin-pass")

from services.account_service import AccountService
from services.protocol import conversation as conversation_module
from services.storage.json_storage import JSONStorageBackend
from utils.helper import UpstreamHTTPError


class TextAccountCooldownTests(unittest.TestCase):
    def test_cooldown_account_is_skipped_by_text_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1", "token-2"])

            with mock.patch("services.account_service.time.time", return_value=100.0):
                service.cooldown_text_token("token-1", "429 upstream_rate_limited", seconds=2)
                selected = service.get_text_access_token()

            self.assertEqual(selected, "token-2")
            self.assertEqual(service._text_cooldown_until["token-1"], 102.0)

    def test_stream_switches_account_after_upstream_429(self) -> None:
        class StubAccountService:
            def __init__(self) -> None:
                self.cooldowns: list[tuple[str, str, int | None]] = []
                self.used: list[str] = []

            def get_text_access_token(self, excluded_tokens: set[str] | None = None) -> str:
                excluded = excluded_tokens or set()
                return next((token for token in ("token-1", "token-2") if token not in excluded), "")

            def cooldown_text_token(self, token: str, error: str = "", seconds: int | None = None) -> None:
                self.cooldowns.append((token, error, seconds))

            def mark_text_used(self, token: str) -> None:
                self.used.append(token)

            def mark_invalid_image_token(self, token: str, reason: str = "") -> None:
                raise AssertionError("429 must not be treated as an invalid token")

        account_service = StubAccountService()

        def fake_events(active_backend: SimpleNamespace, **_: object):
            if active_backend.access_token == "token-1":
                raise UpstreamHTTPError(
                    "/backend-api/conversation",
                    429,
                    {"error": "upstream_rate_limited"},
                    retry_after=2,
                )
            return iter([{"type": "conversation.delta", "delta": "ok"}])

        request = conversation_module.ConversationRequest(model="grok-4.3", prompt="hello")
        with (
            mock.patch.object(conversation_module, "account_service", account_service),
            mock.patch.object(
                conversation_module,
                "OpenAIBackendAPI",
                side_effect=lambda access_token: SimpleNamespace(access_token=access_token),
            ),
            mock.patch.object(conversation_module, "conversation_events", side_effect=fake_events),
        ):
            result = list(
                conversation_module.stream_text_deltas(
                    SimpleNamespace(access_token="token-1"),
                    request,
                )
            )

        self.assertEqual(result, ["ok"])
        self.assertEqual(account_service.used, ["token-2"])
        self.assertEqual(account_service.cooldowns[0][0], "token-1")
        self.assertEqual(account_service.cooldowns[0][2], 2)


if __name__ == "__main__":
    unittest.main()
