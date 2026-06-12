#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql
from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.storage.database_storage import DatabaseStorageBackend


STATE_DOC_FILENAMES: dict[str, str] = {
    "register_config": "register.json",
    "recharge_orders": "recharge_orders.json",
    "image_tags": "image_tags.json",
    "image_index": "image_index.json",
    "image_metadata": "image_metadata.json",
    "invalid_image_tokens": "invalid_image_tokens.json",
    "image_conversation_deletions": "image_conversation_deletions.json",
    "cpa_config": "cpa_config.json",
    "sub2api_config": "sub2api_config.json",
    "backup_state": "backup_state.json",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _timestamp(value: object) -> float:
    text = _clean(value)
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _sqlite_tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        return {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'")}
    finally:
        conn.close()


def _sqlite_rows(path: Path, query: str) -> list[sqlite3.Row]:
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        return list(cur.execute(query))
    finally:
        conn.close()


@dataclass
class SourceBundle:
    accounts: list[dict[str, Any]]
    auth_keys: list[dict[str, Any]]
    image_conversations: list[dict[str, Any]]
    image_tasks: list[dict[str, Any]]
    json_documents: dict[str, Any]
    logs: list[dict[str, Any]]


def load_accounts(data_dir: Path, sqlite_path: Path) -> list[dict[str, Any]]:
    tables = _sqlite_tables(sqlite_path)
    if "accounts" in tables:
        items: list[dict[str, Any]] = []
        for row in _sqlite_rows(sqlite_path, "select data from accounts"):
            try:
                payload = json.loads(row["data"])
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        if items:
            return items
    accounts_path = data_dir / "accounts.json"
    raw = _read_json(accounts_path, [])
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("accounts") or []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def load_auth_keys(data_dir: Path, sqlite_path: Path) -> list[dict[str, Any]]:
    tables = _sqlite_tables(sqlite_path)
    merged: dict[str, dict[str, Any]] = {}
    if "auth_keys" in tables:
        for row in _sqlite_rows(sqlite_path, "select data from auth_keys"):
            try:
                payload = json.loads(row["data"])
            except Exception:
                continue
            key_id = _clean((payload or {}).get("id"))
            if key_id:
                merged[key_id] = payload
    raw = _read_json(data_dir / "auth_keys.json", {})
    items = raw.get("items") if isinstance(raw, dict) else raw
    if isinstance(items, list):
        for payload in items:
            if not isinstance(payload, dict):
                continue
            key_id = _clean(payload.get("id"))
            if key_id:
                merged[key_id] = payload
    return list(merged.values())


def load_image_conversations(data_dir: Path, sqlite_path: Path) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    tables = _sqlite_tables(sqlite_path)
    if "image_conversations" in tables:
        for row in _sqlite_rows(
            sqlite_path,
            "select owner_id, conversation_id, payload, created_at, updated_at from image_conversations",
        ):
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            key = (_clean(row["owner_id"]), _clean(row["conversation_id"]))
            merged[key] = {
                "owner_id": key[0],
                "conversation_id": key[1],
                "payload": payload,
                "created_at": _clean(row["created_at"]) or _now_text(),
                "updated_at": _clean(row["updated_at"]) or _clean(row["created_at"]) or _now_text(),
            }
    raw = _read_json(data_dir / "image_conversations.json", {})
    owners = raw.get("owners") if isinstance(raw, dict) else {}
    if isinstance(owners, dict):
        for owner_id, conversations in owners.items():
            if not isinstance(conversations, list):
                continue
            for payload in conversations:
                if not isinstance(payload, dict):
                    continue
                conversation_id = _clean(payload.get("id"))
                if not conversation_id:
                    continue
                key = (_clean(owner_id), conversation_id)
                candidate = {
                    "owner_id": key[0],
                    "conversation_id": key[1],
                    "payload": payload,
                    "created_at": _clean(payload.get("createdAt")) or _now_text(),
                    "updated_at": _clean(payload.get("updatedAt")) or _clean(payload.get("createdAt")) or _now_text(),
                }
                current = merged.get(key)
                if current is None or _timestamp(candidate["updated_at"]) >= _timestamp(current.get("updated_at")):
                    merged[key] = candidate
    return list(merged.values())


def load_image_tasks(data_dir: Path, sqlite_path: Path) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    tables = _sqlite_tables(sqlite_path)
    if "image_tasks" in tables:
        for row in _sqlite_rows(
            sqlite_path,
            "select owner_id, task_id, status, payload, created_at, updated_at from image_tasks",
        ):
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            key = (_clean(row["owner_id"]), _clean(row["task_id"]))
            merged[key] = {
                "owner_id": key[0],
                "task_id": key[1],
                "status": _clean(row["status"]) or _clean(payload.get("status")) or "unknown",
                "payload": payload,
                "created_at": _clean(row["created_at"]) or _now_text(),
                "updated_at": _clean(row["updated_at"]) or _clean(row["created_at"]) or _now_text(),
            }
    raw = _read_json(data_dir / "image_tasks.json", {})
    tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    if isinstance(tasks, list):
        for payload in tasks:
            if not isinstance(payload, dict):
                continue
            owner_id = _clean(payload.get("owner_id"))
            task_id = _clean(payload.get("id") or payload.get("task_id"))
            if not owner_id or not task_id:
                continue
            key = (owner_id, task_id)
            candidate = {
                "owner_id": owner_id,
                "task_id": task_id,
                "status": _clean(payload.get("status")) or "unknown",
                "payload": payload,
                "created_at": _clean(payload.get("created_at")) or _now_text(),
                "updated_at": _clean(payload.get("updated_at")) or _clean(payload.get("created_at")) or _now_text(),
            }
            current = merged.get(key)
            if current is None or _timestamp(candidate["updated_at"]) >= _timestamp(current.get("updated_at")):
                merged[key] = candidate
    return list(merged.values())


def load_json_documents(data_dir: Path, sqlite_path: Path) -> dict[str, Any]:
    docs: dict[str, Any] = {}
    tables = _sqlite_tables(sqlite_path)
    if "json_documents" in tables:
        rows = _sqlite_rows(sqlite_path, "select * from json_documents")
        for row in rows:
            row_map = dict(row)
            key = _clean(row_map.get("doc_key") or row_map.get("name"))
            raw_payload = row_map.get("payload") if "payload" in row_map else row_map.get("data")
            if not key or raw_payload is None:
                continue
            try:
                docs[key] = json.loads(raw_payload)
            except Exception:
                continue
    for doc_key, file_name in STATE_DOC_FILENAMES.items():
        path = data_dir / file_name
        if path.exists():
            docs[doc_key] = _read_json(path, {})
    return docs


def load_logs(data_dir: Path, sqlite_path: Path) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    tables = _sqlite_tables(sqlite_path)
    if "system_logs" in tables:
        for row in _sqlite_rows(sqlite_path, "select id, payload from system_logs"):
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            log_id = _clean((payload or {}).get("id") or row["id"])
            if log_id:
                merged[log_id] = payload
    elif "logs" in tables:
        for row in _sqlite_rows(sqlite_path, "select id, data from logs"):
            try:
                payload = json.loads(row["data"])
            except Exception:
                continue
            log_id = _clean((payload or {}).get("id") or row["id"])
            if log_id:
                merged[log_id] = payload
    for item in _load_jsonl(data_dir / "logs.jsonl"):
        log_id = _clean(item.get("id"))
        if not log_id:
            log_id = f"legacy-{len(merged) + 1}"
            item = {**item, "id": log_id}
        merged[log_id] = item
    return list(merged.values())


def load_source_bundle(data_dir: Path) -> SourceBundle:
    sqlite_path = data_dir / "accounts.db"
    return SourceBundle(
        accounts=load_accounts(data_dir, sqlite_path),
        auth_keys=load_auth_keys(data_dir, sqlite_path),
        image_conversations=load_image_conversations(data_dir, sqlite_path),
        image_tasks=load_image_tasks(data_dir, sqlite_path),
        json_documents=load_json_documents(data_dir, sqlite_path),
        logs=load_logs(data_dir, sqlite_path),
    )


def create_mysql_database(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("mysql"):
        return
    database_name = _clean(url.database)
    if not database_name:
        raise ValueError("MySQL DATABASE_URL 缺少数据库名")
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=int(url.port or 3306),
        user=url.username or "root",
        password=url.password or "",
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()


def clear_target(backend: DatabaseStorageBackend) -> None:
    session = backend.Session()
    try:
        for table_name in ("system_logs", "logs", "json_documents", "image_tasks", "image_conversations", "auth_keys", "accounts"):
            try:
                session.execute(text(f"DELETE FROM {table_name}"))
            except Exception:
                session.rollback()
                continue
        session.commit()
    finally:
        session.close()


def _execute_batch(session, sql, rows: list[dict[str, Any]], batch_size: int = 1000) -> int:
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        if not batch:
            continue
        session.execute(sql, batch)
        session.commit()
        inserted += len(batch)
    return inserted


def bulk_insert_accounts(backend: DatabaseStorageBackend, accounts: list[dict[str, Any]], batch_size: int = 1000) -> int:
    if not accounts:
        return 0
    session = backend.Session()
    try:
        sql = text("INSERT INTO accounts (access_token, data) VALUES (:access_token, :data)")
        rows = []
        for item in accounts:
            access_token = _clean((item or {}).get("access_token"))
            if not access_token:
                continue
            rows.append({"access_token": access_token, "data": json.dumps(item, ensure_ascii=False)})
        return _execute_batch(session, sql, rows, batch_size=batch_size)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert_auth_keys(backend: DatabaseStorageBackend, auth_keys: list[dict[str, Any]], batch_size: int = 1000) -> int:
    if not auth_keys:
        return 0
    session = backend.Session()
    try:
        sql = text("INSERT INTO auth_keys (key_id, data) VALUES (:key_id, :data)")
        rows = []
        for item in auth_keys:
            key_id = _clean((item or {}).get("id"))
            if not key_id:
                continue
            rows.append({"key_id": key_id, "data": json.dumps(item, ensure_ascii=False)})
        return _execute_batch(session, sql, rows, batch_size=batch_size)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert_image_conversations(
    backend: DatabaseStorageBackend,
    conversations: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    if not conversations:
        return 0
    session = backend.Session()
    try:
        sql = text(
            "INSERT INTO image_conversations (owner_id, conversation_id, payload, created_at, updated_at) "
            "VALUES (:owner_id, :conversation_id, :payload, :created_at, :updated_at)"
        )
        rows = []
        for item in conversations:
            owner_id = _clean(item.get("owner_id"))
            conversation_id = _clean(item.get("conversation_id"))
            payload = item.get("payload")
            if not owner_id or not conversation_id or payload is None:
                continue
            rows.append(
                {
                    "owner_id": owner_id,
                    "conversation_id": conversation_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "created_at": _clean(item.get("created_at")) or _now_text(),
                    "updated_at": _clean(item.get("updated_at")) or _clean(item.get("created_at")) or _now_text(),
                }
            )
        return _execute_batch(session, sql, rows, batch_size=batch_size)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert_image_tasks(
    backend: DatabaseStorageBackend,
    tasks: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    if not tasks:
        return 0
    session = backend.Session()
    try:
        sql = text(
            "INSERT INTO image_tasks (owner_id, task_id, status, payload, created_at, updated_at) "
            "VALUES (:owner_id, :task_id, :status, :payload, :created_at, :updated_at)"
        )
        rows = []
        for item in tasks:
            owner_id = _clean(item.get("owner_id"))
            task_id = _clean(item.get("task_id"))
            payload = item.get("payload")
            if not owner_id or not task_id or payload is None:
                continue
            rows.append(
                {
                    "owner_id": owner_id,
                    "task_id": task_id,
                    "status": _clean(item.get("status")) or _clean((payload or {}).get("status")) or "unknown",
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "created_at": _clean(item.get("created_at")) or _now_text(),
                    "updated_at": _clean(item.get("updated_at")) or _clean(item.get("created_at")) or _now_text(),
                }
            )
        return _execute_batch(session, sql, rows, batch_size=batch_size)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert_json_documents(backend: DatabaseStorageBackend, docs: dict[str, Any]) -> int:
    if not docs:
        return 0
    mode = backend._json_document_mode()  # type: ignore[attr-defined]
    session = backend.Session()
    try:
        if mode == "legacy":
            sql = text("INSERT INTO json_documents (name, data, updated_at) VALUES (:name, :data, :updated_at)")
            rows = [
                {
                    "name": doc_key,
                    "data": json.dumps(payload, ensure_ascii=False),
                    "updated_at": _now_text(),
                }
                for doc_key, payload in docs.items()
            ]
        else:
            sql = text(
                "INSERT INTO json_documents (doc_key, payload, updated_at) "
                "VALUES (:doc_key, :payload, :updated_at)"
            )
            rows = [
                {
                    "doc_key": doc_key,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "updated_at": _now_text(),
                }
                for doc_key, payload in docs.items()
            ]
        return _execute_batch(session, sql, rows, batch_size=200)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert_logs(backend: DatabaseStorageBackend, logs: list[dict[str, Any]], batch_size: int = 1000) -> int:
    if not logs:
        return 0
    mode = backend._log_table_mode()  # type: ignore[attr-defined]
    table_name = "logs" if mode == "legacy" else "system_logs"
    session = backend.Session()
    inserted = 0
    try:
        if mode == "legacy":
            sql = text(
                "INSERT INTO logs (id, created_at, type, day, data) "
                "VALUES (:id, :created_at, :type, :day, :data)"
            )
        else:
            sql = text(
                "INSERT INTO system_logs (id, log_type, log_time, payload) "
                "VALUES (:id, :log_type, :log_time, :payload)"
            )
        batch: list[dict[str, Any]] = []
        for item in logs:
            log_id = _clean(item.get("id")) or f"legacy-{inserted + len(batch) + 1}"
            log_type = _clean(item.get("type")) or "unknown"
            log_time = _clean(item.get("time")) or _now_text()
            payload = json.dumps({**item, "id": log_id}, ensure_ascii=False)
            if mode == "legacy":
                batch.append(
                    {
                        "id": log_id,
                        "created_at": log_time,
                        "type": log_type,
                        "day": log_time[:10],
                        "data": payload,
                    }
                )
            else:
                batch.append(
                    {
                        "id": log_id,
                        "log_type": log_type,
                        "log_time": log_time,
                        "payload": payload,
                    }
                )
            if len(batch) >= batch_size:
                session.execute(sql, batch)
                session.commit()
                inserted += len(batch)
                batch = []
        if batch:
            session.execute(sql, batch)
            session.commit()
            inserted += len(batch)
        return inserted
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def write_target(backend: DatabaseStorageBackend, bundle: SourceBundle) -> dict[str, int]:
    clear_target(backend)
    bulk_insert_accounts(backend, bundle.accounts)
    bulk_insert_auth_keys(backend, bundle.auth_keys)
    bulk_insert_image_conversations(backend, bundle.image_conversations)
    bulk_insert_image_tasks(backend, bundle.image_tasks)
    bulk_insert_json_documents(backend, bundle.json_documents)
    bulk_insert_logs(backend, bundle.logs)
    health = backend.health_check()
    return {
        "accounts": int(health.get("account_count") or 0),
        "auth_keys": int(health.get("auth_key_count") or 0),
        "image_conversations": int(health.get("image_conversation_count") or 0),
        "image_tasks": int(health.get("image_task_count") or 0),
        "json_documents": int(health.get("json_document_count") or 0),
        "logs": int(health.get("system_log_count") or 0),
    }


def backup_source_data(data_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_base = backup_dir / f"chatgpt2api-data-backup-{timestamp}"
    archive_path = shutil.make_archive(str(archive_base), "gztar", root_dir=data_dir)
    return Path(archive_path)


def backup_sqlite_file(sqlite_path: Path, backup_dir: Path) -> Path | None:
    if not sqlite_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{sqlite_path.stem}.{timestamp}.db.gz"
    with sqlite_path.open("rb") as src, gzip.open(backup_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return backup_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移 chatgpt2api 全量状态到 MySQL")
    parser.add_argument(
        "--data-dir",
        default=str(ROOT_DIR / "data"),
        help="源数据目录，默认项目 data 目录",
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="目标数据库 URL，例如 mysql+pymysql://root:pass@106.12.11.147:5019/chatgpt2api_aoij",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(ROOT_DIR / "backup" / "mysql-migrations"),
        help="备份输出目录",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="跳过源数据备份",
    )
    parser.add_argument(
        "--create-database",
        action="store_true",
        help="目标是 MySQL 时先自动创建数据库",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    backup_dir = Path(args.backup_dir).resolve()

    if not data_dir.exists():
        print(f"[migrate] source data dir not found: {data_dir}")
        return 1

    if not args.skip_backup:
        archive = backup_source_data(data_dir, backup_dir)
        sqlite_backup = backup_sqlite_file(data_dir / "accounts.db", backup_dir)
        print(f"[backup] source archive: {archive}")
        if sqlite_backup is not None:
            print(f"[backup] sqlite snapshot: {sqlite_backup}")

    if args.create_database:
        create_mysql_database(args.database_url)
        print("[migrate] ensured target database exists")

    bundle = load_source_bundle(data_dir)
    print(
        "[source] "
        + json.dumps(
            {
                "accounts": len(bundle.accounts),
                "auth_keys": len(bundle.auth_keys),
                "image_conversations": len(bundle.image_conversations),
                "image_tasks": len(bundle.image_tasks),
                "json_documents": len(bundle.json_documents),
                "logs": len(bundle.logs),
            },
            ensure_ascii=False,
        )
    )

    backend = DatabaseStorageBackend(args.database_url)
    counts = write_target(backend, bundle)
    print("[target] " + json.dumps(counts, ensure_ascii=False))
    print("[migrate] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
