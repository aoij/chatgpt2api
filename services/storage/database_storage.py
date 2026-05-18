from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, String, Text, create_engine, Integer, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from services.storage.base import StorageBackend

Base = declarative_base()


class AccountModel(Base):
    """账号数据模型"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(String(2048), unique=True, nullable=False, index=True)
    data = Column(Text, nullable=False)  # JSON 格式存储完整账号数据


class AuthKeyModel(Base):
    """鉴权密钥数据模型"""
    __tablename__ = "auth_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String(255), unique=True, nullable=False, index=True)
    data = Column(Text, nullable=False)


class ImageConversationModel(Base):
    """图片会话数据模型"""
    __tablename__ = "image_conversations"

    owner_id = Column(String(255), primary_key=True)
    conversation_id = Column(String(255), primary_key=True)
    payload = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, index=True)
    updated_at = Column(String(64), nullable=False, index=True)



class ImageTaskModel(Base):
    """Image generation task data model."""
    __tablename__ = "image_tasks"

    owner_id = Column(String(255), primary_key=True)
    task_id = Column(String(255), primary_key=True)
    status = Column(String(32), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, index=True)
    updated_at = Column(String(64), nullable=False, index=True)


class DatabaseStorageBackend(StorageBackend):
    """数据库存储后端（支持 SQLite、PostgreSQL、MySQL 等）"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = self._create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def load_accounts(self) -> list[dict[str, Any]]:
        """从数据库加载账号数据"""
        session = self.Session()
        try:
            accounts = []
            for row in session.query(AccountModel).all():
                try:
                    account_data = json.loads(row.data)
                    if isinstance(account_data, dict):
                        accounts.append(account_data)
                except json.JSONDecodeError:
                    continue
            return accounts
        finally:
            session.close()

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存账号数据到数据库"""
        self._save_rows(AccountModel, accounts, "access_token")

    def save_account(self, account: dict[str, Any]) -> None:
        """只更新单个账号，避免生图计数时重写全量 accounts 表。"""
        access_token = str((account or {}).get("access_token") or "").strip()
        if not access_token:
            return
        session = self.Session()
        try:
            payload = json.dumps(account, ensure_ascii=False)
            row = session.query(AccountModel).filter(AccountModel.access_token == access_token).one_or_none()
            if row is None:
                session.add(AccountModel(access_token=access_token, data=payload))
            else:
                row.data = payload
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_auth_keys(self) -> list[dict[str, Any]]:
        """从数据库加载鉴权密钥数据"""
        return self._load_rows(AuthKeyModel)

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存鉴权密钥数据到数据库"""
        self._save_rows(AuthKeyModel, auth_keys, "id", "key_id")

    def save_auth_key(self, auth_key: dict[str, Any]) -> None:
        """只更新单个鉴权密钥，避免额度扣减时重写全量 auth_keys 表。"""
        key_id = str((auth_key or {}).get("id") or "").strip()
        if not key_id:
            return
        session = self.Session()
        try:
            payload = json.dumps(auth_key, ensure_ascii=False)
            row = session.query(AuthKeyModel).filter(AuthKeyModel.key_id == key_id).one_or_none()
            if row is None:
                session.add(AuthKeyModel(key_id=key_id, data=payload))
            else:
                row.data = payload
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_auth_key(self, key_id: str) -> None:
        normalized_id = str(key_id or "").strip()
        if not normalized_id:
            return
        session = self.Session()
        try:
            session.query(AuthKeyModel).filter(AuthKeyModel.key_id == normalized_id).delete()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _load_rows(self, model: type[AccountModel] | type[AuthKeyModel]) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            items = []
            for row in session.query(model).all():
                try:
                    item_data = json.loads(row.data)
                    if isinstance(item_data, dict):
                        items.append(item_data)
                except json.JSONDecodeError:
                    continue
            return items
        finally:
            session.close()

    def _save_rows(
        self,
        model: type[AccountModel] | type[AuthKeyModel],
        items: list[dict[str, Any]],
        source_key: str,
        target_key: str | None = None,
    ) -> None:
        session = self.Session()
        try:
            session.query(model).delete()
            for item in items:
                if not isinstance(item, dict):
                    continue
                key_value = str(item.get(source_key) or "").strip()
                if not key_value:
                    continue
                session.add(
                    model(
                        **{target_key or source_key: key_value},
                        data=json.dumps(item, ensure_ascii=False),
                    )
                )
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    @staticmethod
    def _create_engine(database_url: str):
        url = make_url(database_url)
        kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }
        if url.drivername.startswith("sqlite"):
            if url.database and url.database != ":memory:":
                Path(url.database).parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            kwargs["poolclass"] = StaticPool if url.database == ":memory:" else NullPool

        engine = create_engine(database_url, **kwargs)
        if url.drivername.startswith("sqlite"):
            DatabaseStorageBackend._configure_sqlite_engine(engine)
        return engine

    @staticmethod
    def _configure_sqlite_engine(engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        try:
            session = self.Session()
            try:
                # 尝试执行简单查询
                session.execute(text("SELECT 1"))
                count = session.query(AccountModel).count()
                auth_key_count = session.query(AuthKeyModel).count()
                return {
                    "status": "healthy",
                    "backend": "database",
                    "database_url": self._mask_password(self.database_url),
                    "account_count": count,
                    "auth_key_count": auth_key_count,
                }
            finally:
                session.close()
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "database",
                "error": str(e),
            }

    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        db_type = "unknown"
        if "sqlite" in self.database_url:
            db_type = "sqlite"
        elif "postgresql" in self.database_url or "postgres" in self.database_url:
            db_type = "postgresql"
        elif "mysql" in self.database_url:
            db_type = "mysql"
        
        return {
            "type": "database",
            "db_type": db_type,
            "description": f"数据库存储 ({db_type})",
            "database_url": self._mask_password(self.database_url),
        }

    @staticmethod
    def _mask_password(url: str) -> str:
        """隐藏数据库连接字符串中的密码"""
        if "://" not in url:
            return url
        try:
            protocol, rest = url.split("://", 1)
            if "@" in rest:
                credentials, host = rest.split("@", 1)
                if ":" in credentials:
                    username, _ = credentials.split(":", 1)
                    return f"{protocol}://{username}:****@{host}"
            return url
        except Exception:
            return url
