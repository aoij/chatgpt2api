from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import secrets
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.auth_service import auth_service
from services.config import DATA_DIR, config


PAY_TYPE_LABELS = {
    "wxpay": "微信",
    "alipay": "支付宝",
}

AMOUNT_QUOTA_MAP: dict[Decimal, int] = {
    Decimal("1.00"): 30,
    Decimal("5.00"): 150,
    Decimal("10.00"): 300,
}

ORDER_STATUSES = {"pending", "paid", "issued", "failed", "expired"}
DEFAULT_ORDER_EXPIRE_MINUTES = 5
DEFAULT_AUTO_CHECK_INTERVAL_SECONDS = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _clean_text(value: object, *, max_length: int = 80) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:max_length]


def _decimal_money(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount.quantize(Decimal("0.01"))


def _money_text(value: Decimal) -> str:
    return value.quantize(Decimal("0.01")).to_eng_string()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def generate_epay_sign(params: dict[str, object], key: str) -> str:
    filtered: dict[str, str] = {}
    for raw_key, raw_value in params.items():
        name = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if not name or not value or name in {"sign", "sign_type"}:
            continue
        filtered[name] = value
    sign_text = "&".join(f"{name}={filtered[name]}" for name in sorted(filtered))
    return hashlib.md5(f"{sign_text}{key}".encode("utf-8")).hexdigest()


class RechargeService:
    def __init__(self, file_path: Path | None = None):
        self.file_path = file_path or DATA_DIR / "recharge_orders.json"
        self._lock = Lock()

    @property
    def epay_base_url(self) -> str:
        return str(os.getenv("CHATGPT2API_RECHARGE_EPAY_BASE_URL") or "").strip().rstrip("/")

    @property
    def epay_query_base_url(self) -> str:
        return str(os.getenv("CHATGPT2API_RECHARGE_EPAY_QUERY_BASE_URL") or self.epay_base_url).strip().rstrip("/")

    @property
    def epay_pid(self) -> str:
        return str(os.getenv("CHATGPT2API_RECHARGE_EPAY_PID") or "").strip()

    @property
    def epay_key(self) -> str:
        return str(os.getenv("CHATGPT2API_RECHARGE_EPAY_KEY") or "").strip()

    @property
    def order_expire_minutes(self) -> int:
        return _env_int(
            "CHATGPT2API_RECHARGE_ORDER_EXPIRE_MINUTES",
            DEFAULT_ORDER_EXPIRE_MINUTES,
            minimum=1,
            maximum=60,
        )

    @property
    def auto_check_interval_seconds(self) -> int:
        return _env_int(
            "CHATGPT2API_RECHARGE_AUTO_CHECK_INTERVAL_SECONDS",
            DEFAULT_AUTO_CHECK_INTERVAL_SECONDS,
            minimum=10,
            maximum=3600,
        )

    def public_base_url(self, fallback: str = "") -> str:
        value = str(
            os.getenv("CHATGPT2API_PUBLIC_BASE_URL")
            or os.getenv("CHATGPT2API_BASE_URL")
            or config.base_url
            or fallback
            or ""
        ).strip().rstrip("/")
        return value

    def is_configured(self) -> bool:
        return bool(self.epay_base_url and self.epay_pid and self.epay_key)

    def options(self) -> dict[str, object]:
        return {
            "enabled": self.is_configured(),
            "order_expire_minutes": self.order_expire_minutes,
            "auto_check_interval_seconds": self.auto_check_interval_seconds,
            "amounts": [
                {"amount": int(amount), "money": _money_text(amount), "quota": quota}
                for amount, quota in sorted(AMOUNT_QUOTA_MAP.items())
            ],
            "pay_types": [
                {"type": pay_type, "label": label}
                for pay_type, label in PAY_TYPE_LABELS.items()
            ],
            "notice": [
                "充值金额只支持 1 元、5 元、10 元，分别对应 30 / 150 / 300 张图片额度。",
                f"订单请在 {self.order_expire_minutes} 分钟内完成支付，超时后需要重新下单。",
                "支付成功后系统每 1 分钟自动检查一次订单，可能会有短暂延迟，请支付后回到本页耐心等待。",
                "系统确认到账后会自动创建令牌，并回显一键登录画图链接。",
                "请正确填写令牌名称，后续画图页面会显示该名称。",
                "有疑问可以加 QQ 909256107 联系；需要大量额度或者 API 对接也可以联系。",
            ],
        }

    def _load_locked(self) -> list[dict[str, Any]]:
        data = _read_json_object(self.file_path)
        items = data.get("items")
        if not isinstance(items, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "pending").strip()
            if status not in ORDER_STATUSES:
                status = "pending"
            normalized.append({**item, "status": status})
        return normalized

    def _save_locked(self, items: list[dict[str, Any]]) -> None:
        _write_json_object(self.file_path, {"items": items})

    def _find_locked(self, items: list[dict[str, Any]], out_trade_no: str) -> dict[str, Any] | None:
        for item in items:
            if str(item.get("out_trade_no") or "") == out_trade_no:
                return item
        return None

    def _public_order(self, order: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(order, dict):
            return None
        created_at = _parse_datetime(order.get("created_at"))
        expires_at = order.get("expires_at")
        if not expires_at and created_at is not None:
            expires_at = (created_at + timedelta(minutes=self.order_expire_minutes)).isoformat()
        result = {
            "out_trade_no": order.get("out_trade_no"),
            "amount": order.get("amount"),
            "quota": order.get("quota"),
            "pay_type": order.get("pay_type"),
            "pay_type_label": PAY_TYPE_LABELS.get(str(order.get("pay_type") or ""), str(order.get("pay_type") or "")),
            "token_name": order.get("token_name"),
            "status": order.get("status"),
            "created_at": order.get("created_at"),
            "paid_at": order.get("paid_at"),
            "issued_at": order.get("issued_at"),
            "expires_at": expires_at,
            "auto_check_interval_seconds": self.auto_check_interval_seconds,
        }
        if order.get("status") == "issued":
            result["login_url"] = order.get("login_url")
            result["link_token"] = order.get("link_token")
            result["auth_key_id"] = order.get("auth_key_id")
        return result

    def get_order(self, out_trade_no: str) -> dict[str, Any] | None:
        normalized = _clean_text(out_trade_no, max_length=80)
        if not normalized:
            return None
        with self._lock:
            return self._public_order(self._find_locked(self._load_locked(), normalized))

    def _build_urls(self, order_no: str, public_base_url: str) -> tuple[str, str]:
        notify_url = f"{public_base_url}/api/recharge/notify"
        return_url = f"{public_base_url}/login?recharge_order={order_no}"
        return notify_url, return_url

    def create_order(self, *, amount: object, pay_type: str, token_name: str, public_base_url: str) -> dict[str, Any]:
        if not self.is_configured():
            raise ValueError("recharge payment is not configured")

        money = _decimal_money(amount)
        if money not in AMOUNT_QUOTA_MAP:
            raise ValueError("amount must be one of 1, 5, 10")

        normalized_pay_type = _clean_text(pay_type, max_length=20).lower()
        if normalized_pay_type not in PAY_TYPE_LABELS:
            raise ValueError("pay_type must be wxpay or alipay")

        normalized_token_name = _clean_text(token_name, max_length=40)
        if not normalized_token_name:
            raise ValueError("token_name is required")

        normalized_public_base_url = self.public_base_url(public_base_url)
        if not normalized_public_base_url:
            raise ValueError("public base url is required")

        now_text = _now_iso()
        quota = AMOUNT_QUOTA_MAP[money]
        money_text = _money_text(money)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=self.order_expire_minutes)).isoformat()
        with self._lock:
            items = self._load_locked()
            existing_ids = {str(item.get("out_trade_no") or "") for item in items}
            out_trade_no = ""
            for _ in range(10):
                candidate = f"cg2api{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(4)}"
                if candidate not in existing_ids:
                    out_trade_no = candidate
                    break
            if not out_trade_no:
                raise RuntimeError("failed to generate order id")

            notify_url, return_url = self._build_urls(out_trade_no, normalized_public_base_url)
            params: dict[str, object] = {
                "pid": self.epay_pid,
                "type": normalized_pay_type,
                "out_trade_no": out_trade_no,
                "notify_url": notify_url,
                "return_url": return_url,
                "name": f"{normalized_token_name} 图片额度 {quota} 张",
                "money": money_text,
            }
            params["sign"] = generate_epay_sign(params, self.epay_key)
            params["sign_type"] = "MD5"
            pay_url = f"{self.epay_base_url}/submit.php?{urlencode(params)}"

            order = {
                "out_trade_no": out_trade_no,
                "amount": money_text,
                "quota": quota,
                "pay_type": normalized_pay_type,
                "token_name": normalized_token_name,
                "status": "pending",
                "created_at": now_text,
                "expires_at": expires_at,
                "updated_at": now_text,
                "public_base_url": normalized_public_base_url,
                "notify_url": notify_url,
                "return_url": return_url,
            }
            items.append(order)
            self._save_locked(items)

        public_order = self._public_order(order) or {}
        return {**public_order, "pay_url": pay_url}

    def verify_epay_params(self, params: dict[str, object]) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "recharge payment is not configured"
        received_pid = str(params.get("pid") or "").strip()
        if received_pid != self.epay_pid:
            return False, "pid mismatch"
        received_sign = str(params.get("sign") or "").strip().lower()
        if not received_sign:
            return False, "sign is required"
        expected_sign = generate_epay_sign(params, self.epay_key)
        if not secrets.compare_digest(received_sign, expected_sign):
            return False, "sign mismatch"
        return True, ""

    def handle_paid_notify(self, params: dict[str, object]) -> dict[str, Any]:
        ok, reason = self.verify_epay_params(params)
        if not ok:
            raise ValueError(reason)
        if str(params.get("trade_status") or "").strip().upper() != "TRADE_SUCCESS":
            raise ValueError("trade status is not success")
        out_trade_no = _clean_text(params.get("out_trade_no"), max_length=80)
        if not out_trade_no:
            raise ValueError("out_trade_no is required")
        notify_money = _decimal_money(params.get("money"))
        if notify_money is None:
            raise ValueError("money is invalid")

        with self._lock:
            items = self._load_locked()
            order = self._find_locked(items, out_trade_no)
            if order is None:
                raise ValueError("order not found")
            order_money = _decimal_money(order.get("amount"))
            if order_money != notify_money:
                raise ValueError("order amount mismatch")

            now_text = _now_iso()
            if order.get("status") == "issued" and order.get("login_url"):
                order["updated_at"] = now_text
                order["last_notify_at"] = now_text
                self._save_locked(items)
                return self._public_order(order) or {}

            order["status"] = "paid"
            order["paid_at"] = order.get("paid_at") or now_text
            order["trade_no"] = str(params.get("trade_no") or "").strip()
            order["last_notify_at"] = now_text

            token_name = _clean_text(order.get("token_name"), max_length=40) or "充值用户"
            quota = int(order.get("quota") or 0)
            if quota <= 0:
                raise ValueError("order quota is invalid")

            item, _raw_key = auth_service.create_key(role="user", name=token_name, quota=quota)
            link_token = str(item.get("link_token") or "").strip()
            if not link_token:
                raise RuntimeError("created token missing login link")
            public_base_url = self.public_base_url(str(order.get("public_base_url") or ""))
            login_url = f"{public_base_url}/image/?key={link_token}"

            order["status"] = "issued"
            order["issued_at"] = now_text
            order["auth_key_id"] = item.get("id")
            order["link_token"] = link_token
            order["login_url"] = login_url
            order["updated_at"] = now_text
            self._save_locked(items)
            return self._public_order(order) or {}

    def _query_epay_order(self, out_trade_no: str) -> dict[str, Any] | None:
        if not self.is_configured():
            return None
        params = urlencode({"merchantNo": self.epay_pid, "outTradeNo": out_trade_no})
        url = f"{self.epay_query_base_url}/api/pay/query?{params}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "chatgpt2api-recharge-checker"})
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if not isinstance(data, dict) or int(data.get("code") or 0) != 200:
            return None
        detail = data.get("data")
        return detail if isinstance(detail, dict) else None

    def _build_paid_params(self, order: dict[str, Any], remote_order: dict[str, Any]) -> dict[str, object]:
        params: dict[str, object] = {
            "pid": self.epay_pid,
            "trade_no": remote_order.get("orderNo") or order.get("trade_no") or order.get("out_trade_no"),
            "out_trade_no": order.get("out_trade_no"),
            "type": order.get("pay_type"),
            "name": f"{order.get('token_name') or '充值用户'} 图片额度 {order.get('quota') or 0} 张",
            "money": order.get("amount"),
            "trade_status": "TRADE_SUCCESS",
        }
        if remote_order.get("payTime"):
            params["endtime"] = remote_order.get("payTime")
        params["sign"] = generate_epay_sign(params, self.epay_key)
        params["sign_type"] = "MD5"
        return params

    def _mark_expired(self, out_trade_no: str, reason: str = "") -> None:
        with self._lock:
            items = self._load_locked()
            order = self._find_locked(items, out_trade_no)
            if order is None or order.get("status") != "pending":
                return
            now_text = _now_iso()
            order["status"] = "expired"
            order["expired_at"] = now_text
            order["updated_at"] = now_text
            if reason:
                order["expire_reason"] = reason
            self._save_locked(items)

    def sync_pending_orders(self, *, limit: int = 100) -> dict[str, int]:
        """主动查询易支付订单状态。

        说明：该逻辑只负责把 FastPay 已确认的支付状态同步到本系统并发放令牌；
        如果 FastPay 本身没有确认到账来源，订单仍会保持待支付直到超时。
        """
        with self._lock:
            pending_orders = [
                dict(item)
                for item in self._load_locked()
                if item.get("status") == "pending" and str(item.get("out_trade_no") or "").strip()
            ][:limit]

        result = {"checked": 0, "issued": 0, "expired": 0, "failed": 0}
        now = datetime.now(timezone.utc)
        for order in pending_orders:
            out_trade_no = str(order.get("out_trade_no") or "").strip()
            if not out_trade_no:
                continue
            result["checked"] += 1
            try:
                remote_order = self._query_epay_order(out_trade_no)
                remote_status = int(remote_order.get("status") or -1) if isinstance(remote_order, dict) else -1
                if remote_status == 1 and remote_order is not None:
                    self.handle_paid_notify(self._build_paid_params(order, remote_order))
                    result["issued"] += 1
                    continue
                expires_at = _parse_datetime(order.get("expires_at"))
                if expires_at is None:
                    created_at = _parse_datetime(order.get("created_at")) or now
                    expires_at = created_at + timedelta(minutes=self.order_expire_minutes)
                if remote_status in {2, 3} or now > expires_at:
                    self._mark_expired(out_trade_no, "remote_expired" if remote_status in {2, 3} else "local_expired")
                    result["expired"] += 1
            except Exception as exc:
                result["failed"] += 1
                print(f"[recharge-checker] check order failed: out_trade_no={out_trade_no}, error={exc}")
        if any(result[key] for key in ("issued", "expired", "failed")):
            print(f"[recharge-checker] sync result: {result}")
        return result


def start_recharge_order_watcher(stop_event: Event) -> Thread:
    def worker() -> None:
        # 启动后先等一小段时间，避免服务尚未完全就绪时立刻请求自身依赖。
        stop_event.wait(5)
        while not stop_event.is_set():
            try:
                if recharge_service.is_configured():
                    recharge_service.sync_pending_orders()
            except Exception as exc:
                print(f"[recharge-checker] worker failed: {exc}")
            stop_event.wait(recharge_service.auto_check_interval_seconds)

    thread = Thread(target=worker, name="recharge-order-checker", daemon=True)
    thread.start()
    return thread


recharge_service = RechargeService()
