import unittest

from services.register import mail_provider


class _FakeResponse:
    """最小响应桩，覆盖 provider 测试里会用到的 requests.Response 接口。"""

    def __init__(self, status_code: int, payload, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._payload


class _FakeSession:
    """顺序返回预设响应，便于验证 provider 的请求路径与解析逻辑。"""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method.upper(), "url": url, "kwargs": kwargs})
        if not self._responses:
            raise AssertionError(f"未为请求预置响应: {method} {url}")
        return self._responses.pop(0)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        return None


class MailProviderTests(unittest.TestCase):
    def test_create_provider_dispatches_mail_tm(self) -> None:
        provider = mail_provider._create_provider(
            {
                "request_timeout": 5,
                "wait_timeout": 1,
                "wait_interval": 0.1,
                "providers": [{"enable": True, "type": "mail_tm", "domain": ["alpha.test"]}],
            }
        )
        try:
            self.assertIsInstance(provider, mail_provider.MailTmProvider)
        finally:
            provider.close()

    def test_mail_tm_create_and_fetch_message(self) -> None:
        conf = {"request_timeout": 5, "wait_timeout": 1, "wait_interval": 0.1, "user_agent": "ua", "proxy": ""}
        provider = mail_provider.MailTmProvider({"provider_ref": "mail_tm#1", "domain": ["alpha.test"]}, conf)
        provider.session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {
                        "hydra:member": [
                            {"domain": "alpha.test", "isActive": True, "isPrivate": False},
                            {"domain": "beta.test", "isActive": True, "isPrivate": False},
                        ]
                    },
                ),
                _FakeResponse(201, {"id": "acc-1"}),
                _FakeResponse(200, {"token": "mailtm-token"}),
                _FakeResponse(
                    200,
                    {
                        "hydra:member": [
                            {
                                "id": "msg-1",
                                "createdAt": "2026-07-09T08:00:00+00:00",
                                "subject": "Verify",
                            }
                        ]
                    },
                ),
                _FakeResponse(
                    200,
                    {
                        "id": "msg-1",
                        "subject": "Verify",
                        "from": {"address": "no-reply@example.com"},
                        "text": "Your verification code is 123456",
                        "html": ["<p>Your verification code is <strong>123456</strong></p>"],
                        "createdAt": "2026-07-09T08:00:00+00:00",
                        "to": [{"address": "tester@alpha.test"}],
                    },
                ),
            ]
        )

        mailbox = provider.create_mailbox("tester")
        self.assertEqual("mail_tm", mailbox["provider"])
        self.assertEqual("tester@alpha.test", mailbox["address"])
        self.assertEqual("mailtm-token", mailbox["token"])

        message = provider.fetch_latest_message(mailbox)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual("Verify", message["subject"])
        self.assertEqual("no-reply@example.com", message["sender"])
        self.assertIn("123456", message["text_content"])
        self.assertEqual("123456", mail_provider._extract_code(message))

    def test_dropmail_create_and_fetch_message(self) -> None:
        conf = {"request_timeout": 5, "wait_timeout": 1, "wait_interval": 0.1, "user_agent": "ua", "proxy": ""}
        provider = mail_provider.DropMailProvider(
            {"provider_ref": "dropmail#1", "api_token": "af_test_token", "permanent_domain_only": True},
            conf,
        )
        provider.session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {
                        "data": {
                            "introduceSession": {
                                "id": "session-1",
                                "expiresAt": "2026-07-09T09:00:00+00:00",
                                "addresses": [{"address": "dropmail-user@dropmail.test"}],
                            }
                        }
                    },
                ),
                _FakeResponse(
                    200,
                    {
                        "data": {
                            "session": {
                                "mailsCount": 1,
                                "mailsAfterId": [
                                    {
                                        "id": "mail-1",
                                        "fromAddr": "no-reply@example.com",
                                        "toAddr": "dropmail-user@dropmail.test",
                                        "text": "Verification code: 654321",
                                        "html": "<p>Verification code: <strong>654321</strong></p>",
                                        "raw": "raw mail",
                                        "headerSubject": "Welcome",
                                        "receivedAt": "2026-07-09T08:05:00+00:00",
                                    }
                                ],
                            }
                        }
                    },
                ),
            ]
        )

        mailbox = provider.create_mailbox()
        self.assertEqual("dropmail", mailbox["provider"])
        self.assertEqual("session-1", mailbox["session_id"])
        self.assertEqual("dropmail-user@dropmail.test", mailbox["address"])

        message = provider.fetch_latest_message(mailbox)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual("Welcome", message["subject"])
        self.assertEqual("no-reply@example.com", message["sender"])
        self.assertEqual("mail-1", mailbox["last_mail_id"])
        self.assertEqual("654321", mail_provider._extract_code(message))


if __name__ == "__main__":
    unittest.main()
