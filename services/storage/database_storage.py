from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, String, Text, bindparam, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from services.storage.base import StorageBackend

Base = declarative_base()


class AccountModel(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(Text, nullable=False)
    data = Column(Text, nullable=False)


class AuthKeyModel(Base):
    __tablename__ = "auth_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String(255), unique=True, nullable=False, index=True)
    data = Column(Text, nullable=False)


class ImageConversationModel(Base):
    __tablename__ = "image_conversations"

    owner_id = Column(String(255), primary_key=True)
    conversation_id = Column(String(255), primary_key=True)
    payload = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, index=True)
    updated_at = Column(String(64), nullable=False, index=True)


class ImageTaskModel(Base):
    __tablename__ = "image_tasks"

    owner_id = Column(String(255), primary_key=True)
    task_id = Column(String(255), primary_key=True)
    status = Column(String(32), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, index=True)
    updated_at = Column(String(64), nullable=False, index=True)


class JsonDocumentModel(Base):
    __tablename__ = "json_documents"

    doc_key = Column(String(255), primary_key=True)
    payload = Column(Text(length=4294967295), nullable=False)
    updated_at = Column(String(64), nullable=False, index=True)


class SystemLogModel(Base):
    __tablename__ = "system_logs"

    id = Column(String(64), primary_key=True)
    log_type = Column(String(64), nullable=False, index=True)
    log_time = Column(String(64), nullable=False, index=True)
    payload = Column(Text(length=4294967295), nullable=False)


class DatabaseStorageBackend(StorageBackend):
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = self._create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def load_accounts(self) -> list[dict[str, Any]]:
        return self._load_rows(AccountModel)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self._save_rows(AccountModel, accounts, "access_token")

    def save_account(self, account: dict[str, Any]) -> None:
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
        return self._load_rows(AuthKeyModel)

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        self._save_rows(AuthKeyModel, auth_keys, "id", "key_id")

    def save_auth_key(self, auth_key: dict[str, Any]) -> None:
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

    def load_json_document(self, doc_key: str) -> Any | None:
        normalized_key = str(doc_key or "").strip()
        if not normalized_key:
            return None
        mode = self._json_document_mode()
        session = self.Session()
        try:
            if mode == "legacy":
                row = session.execute(
                    text("SELECT data FROM json_documents WHERE name = :name"),
                    {"name": normalized_key},
                ).mappings().first()
                payload = row["data"] if row else None
            else:
                row = session.execute(
                    text("SELECT payload FROM json_documents WHERE doc_key = :doc_key"),
                    {"doc_key": normalized_key},
                ).mappings().first()
                payload = row["payload"] if row else None
            if payload is None:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None
        finally:
            session.close()

    def save_json_document(self, doc_key: str, payload: Any) -> None:
        normalized_key = str(doc_key or "").strip()
        if not normalized_key:
            return
        serialized = json.dumps(payload, ensure_ascii=False)
        updated_at = self._now_text()
        mode = self._json_document_mode()
        session = self.Session()
        try:
            if mode == "legacy":
                exists = session.execute(
                    text("SELECT 1 FROM json_documents WHERE name = :name"),
                    {"name": normalized_key},
                ).first()
                if exists:
                    session.execute(
                        text(
                            "UPDATE json_documents SET data = :data, updated_at = :updated_at "
                            "WHERE name = :name"
                        ),
                        {"name": normalized_key, "data": serialized, "updated_at": updated_at},
                    )
                else:
                    session.execute(
                        text(
                            "INSERT INTO json_documents (name, data, updated_at) "
                            "VALUES (:name, :data, :updated_at)"
                        ),
                        {"name": normalized_key, "data": serialized, "updated_at": updated_at},
                    )
            else:
                exists = session.execute(
                    text("SELECT 1 FROM json_documents WHERE doc_key = :doc_key"),
                    {"doc_key": normalized_key},
                ).first()
                if exists:
                    session.execute(
                        text(
                            "UPDATE json_documents SET payload = :payload, updated_at = :updated_at "
                            "WHERE doc_key = :doc_key"
                        ),
                        {"doc_key": normalized_key, "payload": serialized, "updated_at": updated_at},
                    )
                else:
                    session.execute(
                        text(
                            "INSERT INTO json_documents (doc_key, payload, updated_at) "
                            "VALUES (:doc_key, :payload, :updated_at)"
                        ),
                        {"doc_key": normalized_key, "payload": serialized, "updated_at": updated_at},
                    )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_json_document(self, doc_key: str) -> None:
        normalized_key = str(doc_key or "").strip()
        if not normalized_key:
            return
        mode = self._json_document_mode()
        session = self.Session()
        try:
            if mode == "legacy":
                session.execute(text("DELETE FROM json_documents WHERE name = :name"), {"name": normalized_key})
            else:
                session.execute(
                    text("DELETE FROM json_documents WHERE doc_key = :doc_key"),
                    {"doc_key": normalized_key},
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_log(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        log_id = str(item.get("id") or "").strip()
        if not log_id:
            return
        log_type = str(item.get("type") or "").strip() or "unknown"
        log_time = str(item.get("time") or "").strip() or self._now_text()
        payload = json.dumps(item, ensure_ascii=False)
        mode = self._log_table_mode()
        session = self.Session()
        try:
            if mode == "legacy":
                day = log_time[:10]
                exists = session.execute(text("SELECT id FROM logs WHERE id = :id"), {"id": log_id}).first()
                if exists:
                    session.execute(
                        text("UPDATE logs SET created_at = :created_at, type = :type, day = :day, data = :data WHERE id = :id"),
                        {"id": log_id, "created_at": log_time, "type": log_type, "day": day, "data": payload},
                    )
                else:
                    session.execute(
                        text(
                            "INSERT INTO logs (id, created_at, type, day, data) "
                            "VALUES (:id, :created_at, :type, :day, :data)"
                        ),
                        {"id": log_id, "created_at": log_time, "type": log_type, "day": day, "data": payload},
                    )
            else:
                exists = session.execute(
                    text("SELECT 1 FROM system_logs WHERE id = :id"),
                    {"id": log_id},
                ).first()
                if exists:
                    session.execute(
                        text(
                            "UPDATE system_logs SET log_type = :log_type, log_time = :log_time, payload = :payload "
                            "WHERE id = :id"
                        ),
                        {"id": log_id, "log_type": log_type, "log_time": log_time, "payload": payload},
                    )
                else:
                    session.execute(
                        text(
                            "INSERT INTO system_logs (id, log_type, log_time, payload) "
                            "VALUES (:id, :log_type, :log_time, :payload)"
                        ),
                        {"id": log_id, "log_type": log_type, "log_time": log_time, "payload": payload},
                    )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_logs(
        self,
        *,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_type = str(type or "").strip()
        normalized_start = str(start_date or "").strip()
        normalized_end = str(end_date or "").strip()
        row_limit = max(1, int(limit or 200))
        mode = self._log_table_mode()
        session = self.Session()
        try:
            if mode == "legacy":
                query = "SELECT id, created_at, type, data FROM logs"
                clauses: list[str] = []
                params: dict[str, Any] = {}
                if normalized_type:
                    clauses.append("type = :type")
                    params["type"] = normalized_type
                if normalized_start:
                    clauses.append("day >= :start_day")
                    params["start_day"] = normalized_start
                if normalized_end:
                    clauses.append("day <= :end_day")
                    params["end_day"] = normalized_end
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " ORDER BY created_at DESC LIMIT :limit"
                params["limit"] = row_limit
                rows = session.execute(text(query), params).mappings().all()
                items: list[dict[str, Any]] = []
                for row in rows:
                    try:
                        payload = json.loads(row["data"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        payload["id"] = str(payload.get("id") or row["id"])
                        items.append(payload)
                return items
            query = "SELECT payload, log_time FROM system_logs"
            clauses = []
            params = {}
            if normalized_type:
                clauses.append("log_type = :log_type")
                params["log_type"] = normalized_type
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY log_time DESC LIMIT :limit"
            params["limit"] = row_limit
            rows = session.execute(text(query), params).mappings().all()
            items = []
            for row in rows:
                day = str(row["log_time"] or "")[:10]
                if normalized_start and day < normalized_start:
                    continue
                if normalized_end and day > normalized_end:
                    continue
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    items.append(payload)
            return items
        finally:
            session.close()

    def delete_logs(self, ids: list[str]) -> int:
        target_ids = [str(item or "").strip() for item in ids if str(item or "").strip()]
        if not target_ids:
            return 0
        table_name = "logs" if self._log_table_mode() == "legacy" else "system_logs"
        session = self.Session()
        try:
            removed = session.execute(
                text(f"DELETE FROM {table_name} WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
                {"ids": target_ids},
            ).rowcount
            session.commit()
            return int(removed or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_log_call_urls(self, key_id: object, task_id: object, urls: list[str]) -> bool:
        normalized_key_id = str(key_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        normalized_urls = [str(url or "").strip() for url in urls if str(url or "").strip()]
        if not normalized_key_id or not normalized_task_id or not normalized_urls:
            return False
        mode = self._log_table_mode()
        table_name = "logs" if mode == "legacy" else "system_logs"
        payload_column = "data" if mode == "legacy" else "payload"
        order_column = "created_at" if mode == "legacy" else "log_time"
        session = self.Session()
        try:
            rows = session.execute(
                text(f"SELECT id, {payload_column} AS payload FROM {table_name} ORDER BY {order_column} DESC"),
            ).mappings().all()
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
                if not isinstance(detail, dict):
                    continue
                if str(detail.get("key_id") or "").strip() != normalized_key_id:
                    continue
                if str(detail.get("task_id") or "").strip() != normalized_task_id:
                    continue
                next_detail = dict(detail)
                next_detail["urls"] = list(dict.fromkeys(normalized_urls))
                next_detail.pop("thumbnail_urls", None)
                payload["detail"] = next_detail
                session.execute(
                    text(f"UPDATE {table_name} SET {payload_column} = :payload WHERE id = :id"),
                    {"id": row["id"], "payload": json.dumps(payload, ensure_ascii=False)},
                )
                session.commit()
                return True
            return False
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_image_conversations(self) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            rows = session.query(ImageConversationModel).all()
            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row.payload)
                except json.JSONDecodeError:
                    continue
                items.append(
                    {
                        "owner_id": row.owner_id,
                        "conversation_id": row.conversation_id,
                        "payload": payload,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                    }
                )
            return items
        finally:
            session.close()

    def save_image_conversations(self, conversations: list[dict[str, Any]]) -> None:
        session = self.Session()
        try:
            session.query(ImageConversationModel).delete()
            for item in conversations:
                owner_id = str(item.get("owner_id") or "").strip()
                conversation_id = str(item.get("conversation_id") or "").strip()
                payload = item.get("payload")
                if not owner_id or not conversation_id or payload is None:
                    continue
                session.add(
                    ImageConversationModel(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        payload=json.dumps(payload, ensure_ascii=False),
                        created_at=str(item.get("created_at") or self._now_text()),
                        updated_at=str(item.get("updated_at") or item.get("created_at") or self._now_text()),
                    )
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_image_tasks(self) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            rows = session.query(ImageTaskModel).all()
            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row.payload)
                except json.JSONDecodeError:
                    continue
                items.append(
                    {
                        "owner_id": row.owner_id,
                        "task_id": row.task_id,
                        "status": row.status,
                        "payload": payload,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                    }
                )
            return items
        finally:
            session.close()

    def save_image_tasks(self, tasks: list[dict[str, Any]]) -> None:
        session = self.Session()
        try:
            session.query(ImageTaskModel).delete()
            for item in tasks:
                owner_id = str(item.get("owner_id") or "").strip()
                task_id = str(item.get("task_id") or "").strip()
                payload = item.get("payload")
                if not owner_id or not task_id or payload is None:
                    continue
                session.add(
                    ImageTaskModel(
                        owner_id=owner_id,
                        task_id=task_id,
                        status=str(item.get("status") or payload.get("status") or "unknown"),
                        payload=json.dumps(payload, ensure_ascii=False),
                        created_at=str(item.get("created_at") or self._now_text()),
                        updated_at=str(item.get("updated_at") or item.get("created_at") or self._now_text()),
                    )
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def health_check(self) -> dict[str, Any]:
        try:
            session = self.Session()
            try:
                session.execute(text("SELECT 1"))
                return {
                    "status": "healthy",
                    "backend": "database",
                    "database_url": self._mask_password(self.database_url),
                    "account_count": self._count_table_rows(session, "accounts"),
                    "auth_key_count": self._count_table_rows(session, "auth_keys"),
                    "image_conversation_count": self._count_table_rows(session, "image_conversations"),
                    "image_task_count": self._count_table_rows(session, "image_tasks"),
                    "json_document_count": self._count_json_documents(session),
                    "system_log_count": self._count_logs(session),
                }
            finally:
                session.close()
        except Exception as exc:
            return {
                "status": "unhealthy",
                "backend": "database",
                "error": str(exc),
            }

    def get_backend_info(self) -> dict[str, Any]:
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

    def _load_rows(self, model: type[AccountModel] | type[AuthKeyModel]) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            items = []
            for row in session.query(model).all():
                try:
                    item_data = json.loads(row.data)
                except json.JSONDecodeError:
                    continue
                if isinstance(item_data, dict):
                    items.append(item_data)
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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _count_table_rows(self, session, table_name: str) -> int:
        if not self._has_table(table_name):
            return 0
        return int(session.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0)

    def _count_json_documents(self, session) -> int:
        if not self._has_table("json_documents"):
            return 0
        if self._json_document_mode() == "legacy":
            return int(session.execute(text("SELECT COUNT(*) FROM json_documents")).scalar() or 0)
        return int(session.execute(text("SELECT COUNT(*) FROM json_documents")).scalar() or 0)

    def _count_logs(self, session) -> int:
        mode = self._log_table_mode()
        if mode == "legacy":
            return int(session.execute(text("SELECT COUNT(*) FROM logs")).scalar() or 0)
        if self._has_table("system_logs"):
            return int(session.execute(text("SELECT COUNT(*) FROM system_logs")).scalar() or 0)
        return 0

    def _has_table(self, table_name: str) -> bool:
        return inspect(self.engine).has_table(table_name)

    def _column_names(self, table_name: str) -> set[str]:
        if not self._has_table(table_name):
            return set()
        return {str(column.get("name") or "") for column in inspect(self.engine).get_columns(table_name)}

    def _json_document_mode(self) -> str:
        columns = self._column_names("json_documents")
        if {"name", "data", "updated_at"}.issubset(columns):
            return "legacy"
        return "modern"

    def _log_table_mode(self) -> str:
        if self._has_table("logs"):
            columns = self._column_names("logs")
            if {"created_at", "type", "day", "data"}.issubset(columns):
                return "legacy"
        return "modern"

    @staticmethod
    def _now_text() -> str:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

    @staticmethod
    def _mask_password(url: str) -> str:
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
