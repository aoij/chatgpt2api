from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.recharge_service import RechargeService, generate_epay_sign


class RechargeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            key: os.environ.get(key)
            for key in (
                "CHATGPT2API_RECHARGE_EPAY_BASE_URL",
                "CHATGPT2API_RECHARGE_EPAY_PUBLIC_BASE_URL",
                "CHATGPT2API_RECHARGE_EPAY_QUERY_BASE_URL",
                "CHATGPT2API_RECHARGE_EPAY_PID",
            "CHATGPT2API_RECHARGE_EPAY_KEY",
            "CHATGPT2API_PUBLIC_BASE_URL",
            "CHATGPT2API_RECHARGE_ORDER_EXPIRE_MINUTES",
            "CHATGPT2API_RECHARGE_AUTO_CHECK_INTERVAL_SECONDS",
            )
        }
        os.environ["CHATGPT2API_RECHARGE_EPAY_BASE_URL"] = "http://pay.example.test"
        os.environ.pop("CHATGPT2API_RECHARGE_EPAY_PUBLIC_BASE_URL", None)
        os.environ["CHATGPT2API_RECHARGE_EPAY_QUERY_BASE_URL"] = "http://pay.example.test"
        os.environ["CHATGPT2API_RECHARGE_EPAY_PID"] = "MNEWAPI001"
        os.environ["CHATGPT2API_RECHARGE_EPAY_KEY"] = "secret"
        os.environ["CHATGPT2API_PUBLIC_BASE_URL"] = "http://localhost:3002"
        os.environ["CHATGPT2API_RECHARGE_ORDER_EXPIRE_MINUTES"] = "5"
        os.environ["CHATGPT2API_RECHARGE_AUTO_CHECK_INTERVAL_SECONDS"] = "60"

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_create_order_and_issue_token_by_notify(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = RechargeService(Path(temp_dir) / "orders.json")
            order = service.create_order(
                amount=1,
                pay_type="wxpay",
                token_name="测试令牌",
                public_base_url="http://localhost:3002",
            )

            self.assertEqual(order["amount"], "1.00")
            self.assertEqual(order["quota"], 30)
            self.assertIn("/submit.php?", order["pay_url"])

            params = {
                "pid": "MNEWAPI001",
                "trade_no": "T123",
                "out_trade_no": order["out_trade_no"],
                "type": "wxpay",
                "name": "测试",
                "money": "1.00",
                "trade_status": "TRADE_SUCCESS",
            }
            params["sign"] = generate_epay_sign(params, "secret")
            params["sign_type"] = "MD5"

            issued = service.handle_paid_notify(params)
            self.assertEqual(issued["status"], "issued")
            self.assertEqual(issued["quota"], 30)
            self.assertTrue(str(issued["login_url"]).startswith("http://localhost:3002/image/?key=lk-"))

            # 重复通知必须幂等，不能二次创建不同登录链接。
            repeated = service.handle_paid_notify(params)
            self.assertEqual(repeated["login_url"], issued["login_url"])

    def test_sync_pending_orders_issues_paid_remote_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = RechargeService(Path(temp_dir) / "orders.json")
            order = service.create_order(
                amount=1,
                pay_type="wxpay",
                token_name="自动检查",
                public_base_url="http://localhost:3002",
            )

            service._query_epay_order = lambda out_trade_no: {  # type: ignore[method-assign]
                "orderNo": "FP123",
                "outTradeNo": out_trade_no,
                "status": 1,
                "payAmount": "1.00",
            }
            result = service.sync_pending_orders()
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["issued"], 1)
            synced = service.get_order(order["out_trade_no"])
            self.assertIsNotNone(synced)
            self.assertEqual(synced["status"], "issued")

    def test_amount_must_be_allowed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = RechargeService(Path(temp_dir) / "orders.json")
            with self.assertRaises(ValueError):
                service.create_order(
                    amount=2,
                    pay_type="wxpay",
                    token_name="测试令牌",
                    public_base_url="http://localhost:3002",
                )

    def test_public_pay_url_can_differ_from_query_base_url(self) -> None:
        os.environ["CHATGPT2API_RECHARGE_EPAY_BASE_URL"] = "http://internal-pay.example.test"
        os.environ["CHATGPT2API_RECHARGE_EPAY_PUBLIC_BASE_URL"] = "https://pay.example.test/fastpay-server"
        os.environ["CHATGPT2API_RECHARGE_EPAY_QUERY_BASE_URL"] = "http://internal-pay.example.test/fastpay-server"
        with TemporaryDirectory() as temp_dir:
            service = RechargeService(Path(temp_dir) / "orders.json")
            order = service.create_order(
                amount=1,
                pay_type="wxpay",
                token_name="public url",
                public_base_url="http://localhost:3002",
            )

            self.assertTrue(str(order["pay_url"]).startswith("https://pay.example.test/fastpay-server/submit.php?"))
            self.assertEqual(service.epay_query_base_url, "http://internal-pay.example.test/fastpay-server")


if __name__ == "__main__":
    unittest.main()
