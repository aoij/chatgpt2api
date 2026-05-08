from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """抽象存储后端基类"""

    @abstractmethod
    def load_accounts(self) -> list[dict[str, Any]]:
        """加载所有账号数据"""
        pass

    @abstractmethod
    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存所有账号数据"""
        pass

    def save_account(self, account: dict[str, Any]) -> None:
        """保存单个账号数据；后端可覆盖该方法来避免重写全量账号。"""
        access_token = str(account.get("access_token") or "").strip()
        if not access_token:
            return
        items = self.load_accounts()
        found = False
        next_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("access_token") or "").strip() == access_token:
                next_items.append(account)
                found = True
            else:
                next_items.append(item)
        if not found:
            next_items.append(account)
        self.save_accounts(next_items)

    @abstractmethod
    def load_auth_keys(self) -> list[dict[str, Any]]:
        """加载所有鉴权密钥数据"""
        pass

    @abstractmethod
    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存所有鉴权密钥数据"""
        pass

    def save_auth_key(self, auth_key: dict[str, Any]) -> None:
        """保存单个鉴权密钥数据；后端可覆盖该方法来避免重写全量鉴权密钥。"""
        key_id = str((auth_key or {}).get("id") or "").strip()
        if not key_id:
            return
        items = self.load_auth_keys()
        found = False
        next_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == key_id:
                next_items.append(auth_key)
                found = True
            else:
                next_items.append(item)
        if not found:
            next_items.append(auth_key)
        self.save_auth_keys(next_items)

    def delete_auth_key(self, key_id: str) -> None:
        """删除单个鉴权密钥数据；后端可覆盖该方法来避免重写全量鉴权密钥。"""
        normalized_id = str(key_id or "").strip()
        if not normalized_id:
            return
        items = self.load_auth_keys()
        next_items = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("id") or "").strip() != normalized_id
        ]
        self.save_auth_keys(next_items)

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """健康检查，返回存储后端状态"""
        pass

    @abstractmethod
    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        pass
