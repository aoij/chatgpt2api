from __future__ import annotations

import json
import os
import queue
import threading
import time
import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.account_service import account_service
from services.auth_service import ImageQuotaExceeded, auth_service
from services.config import DATA_DIR, config
from services.log_service import LOG_TYPE_CALL, log_service
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_image_edit, openai_v1_image_generations
from utils.log import logger

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
DEFAULT_IMAGE_TASK_WORKERS = 10
DEFAULT_CLEANUP_INTERVAL_SECONDS = 600
DEFAULT_RUNNING_TASK_TIMEOUT_SECONDS = 15 * 60
DEFAULT_UPSTREAM_CONCURRENCY = 3
DEFAULT_TASK_LIST_LIMIT = 24
MAX_IMAGE_TASK_WORKERS = 24
MAX_UPSTREAM_CONCURRENCY = 12
DEFAULT_PERSIST_WORKERS = 4


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _uploader_from_identity(identity: dict[str, object]) -> dict[str, object]:
    return {
        "id": identity.get("id"),
        "name": identity.get("name"),
        "role": identity.get("role"),
        "auth_mode": identity.get("auth_mode"),
        "scope": identity.get("scope"),
    }


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _queue_item_key(item: tuple[str, str, dict[str, Any]] | None) -> str:
    if not item:
        return ""
    try:
        return _clean(item[0])
    except Exception:
        return ""


def _hash_account_id(access_token: str) -> str:
    token = _clean(access_token)
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:16] if token else ""


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _count_success_images(data: object) -> int:
    if not isinstance(data, list):
        return 0
    count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("url") or item.get("b64_json"):
            count += 1
    return count


def _strip_task_item_runtime_fields(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(item)
    cleaned.pop("_persist", None)
    cleaned.pop("_persist_status", None)
    cleaned.pop("_persist_error", None)
    return cleaned


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("data") is not None:
        item["data"] = _compact_image_data(task.get("data"))
    if task.get("error"):
        item["error"] = task.get("error")
    if isinstance(task.get("duration_ms"), int):
        item["duration_ms"] = task.get("duration_ms")
    if isinstance(task.get("metrics"), dict):
        item["metrics"] = task.get("metrics")
    persist_summary = task.get("persist_summary")
    if isinstance(persist_summary, dict):
        item["persist_summary"] = dict(persist_summary)
    return item


def _compact_image_data(data: object) -> object:
    if not isinstance(data, list):
        return data
    result: list[object] = []
    for item in data:
        if not isinstance(item, dict):
            result.append(item)
            continue
        next_item = _strip_task_item_runtime_fields(item)
        if next_item.get("url"):
            next_item.pop("b64_json", None)
        persist_status = _clean(item.get("_persist_status"))
        if persist_status:
            next_item["persist_status"] = persist_status
        result.append(next_item)
    return result


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_generations.handle,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_edit.handle,
        retention_days_getter: Callable[[], int] | None = None,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue()
        self._worker_count = 0
        self._upstream_concurrency = 0
        self._upstream_condition = threading.Condition()
        self._active_upstream_slots = 0
        self._started_worker_count = 0
        self._active_worker_tasks: dict[int, tuple[str, float]] = {}
        self._persist_queue: queue.Queue[tuple[str, dict[str, Any], dict[str, Any]]] = queue.Queue()
        self._persist_worker_count = _env_int(
            "CHATGPT2API_IMAGE_PERSIST_WORKERS",
            DEFAULT_PERSIST_WORKERS,
            minimum=1,
            maximum=8,
        )
        self._persist_threads_started = 0
        self._dirty = False
        self._running_task_timeout_seconds = _env_int(
            "CHATGPT2API_IMAGE_TASK_TIMEOUT_SECONDS",
            DEFAULT_RUNNING_TASK_TIMEOUT_SECONDS,
            minimum=60,
            maximum=12 * 60 * 60,
        )
        self._last_cleanup_at = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._tasks = self._load_locked()
            changed = self._recover_unfinished_locked()
            changed = self._cleanup_locked() or changed
            if changed:
                self._dirty = True
                self._save_locked()
        logger.info(
            {
                "event": "image_task_queue_transport",
                "transport": "memory",
                "workers": self._worker_count,
                "upstream_concurrency": self._upstream_concurrency,
            }
        )
        self.reload_runtime_settings()
        self._start_workers()

    def _resolve_worker_count(self) -> int:
        configured = getattr(config, "data", {}).get("image_task_worker_count")
        if configured is not None:
            try:
                return min(max(int(configured), 1), MAX_IMAGE_TASK_WORKERS)
            except (TypeError, ValueError):
                pass
        return _env_int(
            "CHATGPT2API_IMAGE_TASK_WORKERS",
            getattr(config, "image_task_worker_count", DEFAULT_IMAGE_TASK_WORKERS),
            minimum=1,
            maximum=MAX_IMAGE_TASK_WORKERS,
        )

    def _resolve_upstream_concurrency(self) -> int:
        configured = getattr(config, "data", {}).get("image_upstream_concurrency")
        if configured is not None:
            try:
                return min(max(int(configured), 1), MAX_UPSTREAM_CONCURRENCY)
            except (TypeError, ValueError):
                pass
        return _env_int(
            "CHATGPT2API_IMAGE_UPSTREAM_CONCURRENCY",
            getattr(config, "image_upstream_concurrency", DEFAULT_UPSTREAM_CONCURRENCY),
            minimum=1,
            maximum=MAX_UPSTREAM_CONCURRENCY,
        )

    def reload_runtime_settings(self) -> dict[str, int]:
        worker_count = self._resolve_worker_count()
        upstream_concurrency = self._resolve_upstream_concurrency()
        worker_changed = False
        upstream_changed = False
        with self._lock:
            worker_changed = worker_count != self._worker_count
            self._worker_count = worker_count
        with self._upstream_condition:
            upstream_changed = upstream_concurrency != self._upstream_concurrency
            if upstream_changed:
                self._upstream_concurrency = upstream_concurrency
                self._upstream_condition.notify_all()
        if worker_changed or upstream_changed:
            logger.info(
                {
                    "event": "image_task_runtime_settings_reloaded",
                    "workers": worker_count,
                    "upstream_concurrency": upstream_concurrency,
                }
            )
        if worker_changed:
            self._ensure_worker_threads()
        return {
            "workers": worker_count,
            "upstream_concurrency": upstream_concurrency,
        }

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        base_url: str,
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": size,
            "response_format": "url",
            "defer_local_save": True,
            "base_url": base_url,
            "uploader": _uploader_from_identity(identity),
            "selected_account_id": _clean(identity.get("selected_account_id")),
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        base_url: str,
        images: list[tuple[bytes, str, str]],
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "images": images,
            "model": model,
            "n": 1,
            "size": size,
            "response_format": "url",
            "defer_local_save": True,
            "base_url": base_url,
            "uploader": _uploader_from_identity(identity),
            "selected_account_id": _clean(identity.get("selected_account_id")),
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)][:DEFAULT_TASK_LIST_LIMIT]
        with self._lock:
            changed = self._recover_stale_unfinished_locked()
            changed = self._cleanup_if_due_locked() or changed
            if changed:
                self._save_locked()
            items = []
            missing_ids = []
            for task_id in requested_ids:
                task = self._tasks.get(_task_key(owner, task_id))
                if task is None:
                    missing_ids.append(task_id)
                else:
                    items.append(_public_task(task))
            if not requested_ids:
                items = [
                    _public_task(task)
                    for task in self._tasks.values()
                    if task.get("owner_id") == owner
                ]
                items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                missing_ids = []
            return {"items": items, "missing_ids": missing_ids}

    def get_runtime_stats(self) -> dict[str, Any]:
        with self._lock:
            changed = self._cleanup_if_due_locked()
            changed = self._recover_stale_unfinished_locked() or changed
            if changed:
                self._save_locked()
            tasks = sorted(
                self._tasks.values(),
                key=lambda item: str(item.get("updated_at") or ""),
                reverse=True,
            )
            queued = sum(1 for item in tasks if item.get("status") == TASK_STATUS_QUEUED)
            running = sum(1 for item in tasks if item.get("status") == TASK_STATUS_RUNNING)
            recent_success = [
                item for item in tasks
                if item.get("status") == TASK_STATUS_SUCCESS and int(item.get("duration_ms") or 0) > 0
            ][:20]
            avg_duration_ms = (
                int(sum(int(item.get("duration_ms") or 0) for item in recent_success) / len(recent_success))
                if recent_success else 0
            )

            def _avg_metric(name: str) -> int:
                values = [
                    int((item.get("metrics") or {}).get(name) or 0)
                    for item in recent_success
                    if isinstance(item.get("metrics"), dict) and int((item.get("metrics") or {}).get(name) or 0) > 0
                ]
                return int(sum(values) / len(values)) if values else 0

            effective_parallel = max(1, running or self._worker_count or 1)
            estimated_wait_ms = int((queued / effective_parallel) * avg_duration_ms) if queued > 0 and avg_duration_ms > 0 else 0
            oldest_running_seconds = 0
            now = time.time()
            for item in tasks:
                if item.get("status") != TASK_STATUS_RUNNING:
                    continue
                started_at = _timestamp(item.get("started_at")) or _timestamp(item.get("updated_at")) or _timestamp(item.get("created_at"))
                if started_at > 0:
                    oldest_running_seconds = max(oldest_running_seconds, int(now - started_at))
            account_pool_stats = account_service.image_pool_stats()

            return {
                "transport": "memory",
                "workers": self._worker_count,
                "upstream_concurrency": self._upstream_concurrency,
                "active_upstream_slots": self._active_upstream_slots,
                "queue_size": self._queue.qsize(),
                "persist_queue_size": self._persist_queue.qsize(),
                "oldest_running_seconds": oldest_running_seconds,
                "queued": queued,
                "running": running,
                "processing": queued + running,
                "recent_avg_duration_ms": avg_duration_ms,
                "estimated_wait_ms": estimated_wait_ms,
                "frontend_batch_limit": max(1, int(getattr(config, "image_account_concurrency", DEFAULT_UPSTREAM_CONCURRENCY))),
                "per_account_concurrency": account_pool_stats.get("per_account_concurrency"),
                "account_inflight": account_pool_stats.get("inflight"),
                "account_inflight_accounts": account_pool_stats.get("inflight_accounts"),
                "account_cooldown_accounts": account_pool_stats.get("cooldown_accounts"),
                "recent_avg_stage_ms": {
                    "slot_wait_ms": _avg_metric("slot_wait_ms"),
                    "slot_wait_with_zeros_ms": (
                        int(sum(int((item.get("metrics") or {}).get("slot_wait_ms") or 0) for item in recent_success) / len(recent_success))
                        if recent_success else 0
                    ),
                    "upstream_stream_ms": _avg_metric("upstream_stream_ms"),
                    "resolve_urls_ms": _avg_metric("resolve_urls_ms"),
                    "download_images_ms": _avg_metric("download_images_ms"),
                    "save_images_ms": _avg_metric("save_images_ms"),
                    "background_download_images_ms": _avg_metric("background_download_images_ms"),
                },
            }

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        now = _now_iso()
        should_start = False
        reserved_quota = 0
        with self._lock:
            cleaned = self._cleanup_if_due_locked()
            task = self._tasks.get(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            try:
                reserved_quota = auth_service.reserve_image_quota(identity, 1)
            except ImageQuotaExceeded:
                raise
            task = {
                "id": task_id,
                "owner_id": owner,
                "owner_name": _clean(identity.get("name")),
                "owner_role": _clean(identity.get("role")),
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "reserved_quota": reserved_quota,
                "duration_ms": 0,
                "created_at": now,
                "updated_at": now,
            }
            self._tasks[key] = task
            self._dirty = True
            self._save_locked()
            should_start = True

        if should_start:
            try:
                self._enqueue_task(key, mode, payload)
            except Exception as exc:
                reserved_quota = 0
                owner_id = ""
                with self._lock:
                    task = self._tasks.get(key) or {}
                    reserved_quota = int(task.get("reserved_quota") or 0)
                    owner_id = _clean(task.get("owner_id"))
                    if reserved_quota:
                        task["reserved_quota"] = 0
                if reserved_quota:
                    auth_service.refund_image_quota_by_id(owner_id, reserved_quota)
                self._update_task(
                    key,
                    status=TASK_STATUS_ERROR,
                    error=f"failed to enqueue image task: {exc}",
                    data=[],
                )
        return _public_task(task)

    def _start_workers(self) -> None:
        self._ensure_worker_threads()
        self._ensure_persist_threads()

    def _ensure_worker_threads(self) -> None:
        while self._started_worker_count < self._worker_count:
            self._started_worker_count += 1
            index = self._started_worker_count
            thread = threading.Thread(
                target=self._worker,
                args=(index,),
                name=f"image-task-worker-{index}",
                daemon=True,
            )
            thread.start()

    def _ensure_persist_threads(self) -> None:
        while self._persist_threads_started < self._persist_worker_count:
            self._persist_threads_started += 1
            index = self._persist_threads_started
            thread = threading.Thread(
                target=self._persist_worker,
                args=(index,),
                name=f"image-persist-worker-{index}",
                daemon=True,
            )
            thread.start()

    def _worker_enabled(self, worker_index: int) -> bool:
        return worker_index <= self._worker_count

    def _acquire_upstream_slot(self, timeout_seconds: int) -> bool:
        deadline = time.time() + max(1, timeout_seconds)
        with self._upstream_condition:
            while self._active_upstream_slots >= max(1, self._upstream_concurrency):
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._upstream_condition.wait(timeout=min(1.0, remaining))
            self._active_upstream_slots += 1
            return True

    def _release_upstream_slot(self) -> None:
        with self._upstream_condition:
            if self._active_upstream_slots > 0:
                self._active_upstream_slots -= 1
            self._upstream_condition.notify_all()

    def _enqueue_task(self, key: str, mode: str, payload: dict[str, Any]) -> None:
        self._queue.put((key, mode, payload))

    def _worker(self, worker_index: int) -> None:
        while True:
            if not self._worker_enabled(worker_index):
                return
            try:
                key, mode, payload = self._queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    self._active_worker_tasks[worker_index] = (key, time.time())
                self._run_task(key, mode, payload)
            except Exception as exc:
                print(f"[image-task-worker] unexpected error key={key}: {exc}")
            finally:
                with self._lock:
                    self._active_worker_tasks.pop(worker_index, None)
                self._queue.task_done()

    def _persist_worker(self, worker_index: int) -> None:
        while True:
            try:
                key, item, context = self._persist_queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                self._persist_task_image(key, item, context)
            except Exception as exc:
                logger.warning(
                    {
                        "event": "image_task_persist_worker_failed",
                        "worker": worker_index,
                        "key": key,
                        "error": str(exc),
                    }
                )
            finally:
                self._persist_queue.task_done()

    def _log_task_call(self, key: str, *, started: float, status: str, result: object = None, error: str = "") -> None:
        task = self._tasks.get(key, {})
        endpoint = "/v1/images/edits" if task.get("mode") == "edit" else "/v1/images/generations"
        detail: dict[str, Any] = {
            "key_id": task.get("owner_id"),
            "key_name": task.get("owner_name") or task.get("owner_id"),
            "role": task.get("owner_role") or "user",
            "endpoint": endpoint,
            "model": task.get("model"),
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if error:
            detail["error"] = error
        urls: list[str] = []
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and isinstance(item.get("url"), str):
                        urls.append(item["url"])
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        summary = "图生图调用完成" if endpoint.endswith("/edits") else "文生图调用完成"
        if status != "success":
            summary = "图生图调用失败" if endpoint.endswith("/edits") else "文生图调用失败"
        log_service.add(LOG_TYPE_CALL, summary, detail)

    def _persist_summary(self, data: list[Any]) -> dict[str, int]:
        pending = 0
        completed = 0
        failed = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            status = _clean(item.get("_persist_status"))
            if status == "done":
                completed += 1
            elif status == "error":
                failed += 1
            elif isinstance(item.get("_persist"), dict):
                pending += 1
        return {
            "pending": pending,
            "completed": completed,
            "failed": failed,
        }

    def _prepare_persist_jobs(
        self,
        key: str,
        payload: dict[str, Any],
        data: list[Any],
    ) -> tuple[dict[str, int], list[tuple[str, dict[str, Any], dict[str, Any]]]]:
        context = {
            "base_url": _clean(payload.get("base_url"), _clean(getattr(config, "base_url", ""))),
            "uploader": payload.get("uploader") if isinstance(payload.get("uploader"), dict) else None,
            "selected_account_id": _clean(payload.get("selected_account_id")),
        }
        jobs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("_persist"), dict):
                continue
            item["_persist_status"] = "queued"
            item.pop("_persist_error", None)
            jobs.append((key, dict(item), dict(context)))
        return self._persist_summary(data), jobs

    def _enqueue_persist_jobs(self, jobs: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
        for key, item, context in jobs:
            try:
                self._persist_queue.put_nowait((key, item, context))
            except Exception:
                self._update_persist_result(key, item, None, "persist queue unavailable", 0)

    def _persist_task_image(self, key: str, queued_item: dict[str, Any], context: dict[str, Any]) -> None:
        persist_meta = queued_item.get("_persist") if isinstance(queued_item.get("_persist"), dict) else {}
        persist_id = _clean(persist_meta.get("id"))
        source_url = _clean(persist_meta.get("source_url"))
        conversation_id = _clean(persist_meta.get("conversation_id"))
        file_ids = [
            _clean(item) for item in (persist_meta.get("file_ids") if isinstance(persist_meta.get("file_ids"), list) else [])
            if _clean(item)
        ]
        sediment_ids = [
            _clean(item) for item in (persist_meta.get("sediment_ids") if isinstance(persist_meta.get("sediment_ids"), list) else [])
            if _clean(item)
        ]
        position = max(1, int(persist_meta.get("position") or 1))
        account_id = _clean(persist_meta.get("account_id")) or _clean(context.get("selected_account_id"))
        if not persist_id and not source_url:
            self._update_persist_result(key, queued_item, None, "missing source url")
            return
        started = time.time()
        saved_url = ""
        error = ""
        try:
            content = b""
            last_download_error: Exception | None = None
            for token in self._resolve_persist_access_tokens(account_id):
                try:
                    backend = OpenAIBackendAPI(access_token=token)
                    content = self._download_persist_image_bytes(
                        backend=backend,
                        source_url=source_url,
                        conversation_id=conversation_id,
                        file_ids=file_ids,
                        sediment_ids=sediment_ids,
                        position=position,
                    )
                    if content:
                        break
                except Exception as exc:
                    last_download_error = exc
                    logger.warning(
                        {
                            "event": "image_task_persist_account_retry",
                            "account_id": _hash_account_id(token),
                            "conversation_id": conversation_id,
                            "error": str(exc),
                        }
                    )
                    continue
            if not content and last_download_error is not None:
                raise last_download_error
            if not content:
                raise RuntimeError("upstream image download returned empty body")
            from services.openai_backend_api import _is_image_bytes
            if not _is_image_bytes(content):
                raise RuntimeError("upstream image download returned non-image body")
            from services.protocol.conversation import save_image_bytes
            saved_url = save_image_bytes(
                content,
                _clean(context.get("base_url"), _clean(getattr(config, "base_url", ""))) or None,
                context.get("uploader") if isinstance(context.get("uploader"), dict) else None,
            )
        except Exception as exc:
            from services.openai_backend_api import _friendly_image_download_error
            error = _friendly_image_download_error(exc)
        finally:
            elapsed_ms = max(0, int((time.time() - started) * 1000))
            self._update_persist_result(key, queued_item, saved_url or None, error, elapsed_ms)

    def _resolve_persist_access_tokens(self, preferred_account_id: str) -> list[str]:
        preferred_id = _clean(preferred_account_id)
        ordered_tokens: list[str] = []
        seen_tokens: set[str] = set()

        def _append_token(token_value: object) -> None:
            token = _clean(token_value)
            if not token or token in seen_tokens:
                return
            ordered_tokens.append(token)
            seen_tokens.add(token)

        if preferred_id:
            for token in account_service.tokens_for_ids([preferred_id]):
                _append_token(token)
        for item in account_service.list_token_items():
            _append_token(item.get("access_token"))
        if ordered_tokens:
            return ordered_tokens
        raise RuntimeError("no available account for background image persist")

    def _download_persist_image_bytes(
        self,
        *,
        backend: OpenAIBackendAPI,
        source_url: str,
        conversation_id: str,
        file_ids: list[str],
        sediment_ids: list[str],
        position: int,
    ) -> bytes:
        urls_to_try: list[str] = []
        primary_url = _clean(source_url)
        if primary_url:
            urls_to_try.append(primary_url)
        if conversation_id or file_ids or sediment_ids:
            try:
                refreshed_urls = backend.resolve_conversation_image_urls(conversation_id, file_ids, sediment_ids, poll=False)
            except Exception as exc:
                logger.warning(
                    {
                        "event": "image_task_persist_resolve_urls_failed",
                        "conversation_id": conversation_id,
                        "file_ids": file_ids,
                        "sediment_ids": sediment_ids,
                        "error": str(exc),
                    }
                )
                refreshed_urls = []
            for candidate in refreshed_urls:
                candidate_url = _clean(candidate)
                if candidate_url and candidate_url not in urls_to_try:
                    urls_to_try.append(candidate_url)
        if not urls_to_try:
            raise RuntimeError("missing image download url")
        preferred_index = max(0, position - 1)
        if preferred_index < len(urls_to_try):
            urls_to_try = [urls_to_try[preferred_index]] + [url for idx, url in enumerate(urls_to_try) if idx != preferred_index]
        last_error: Exception | None = None
        for candidate in urls_to_try:
            try:
                images = backend.download_image_bytes([candidate])
                if images and isinstance(images[0], (bytes, bytearray)):
                    return bytes(images[0])
            except Exception as exc:
                last_error = exc
                logger.warning(
                    {
                        "event": "image_task_persist_download_failed",
                        "conversation_id": conversation_id,
                        "candidate_url": candidate,
                        "error": str(exc),
                    }
                )
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("upstream image download returned empty body")

    def _update_persist_result(
        self,
        key: str,
        queued_item: dict[str, Any],
        saved_url: str | None,
        error: str = "",
        elapsed_ms: int = 0,
    ) -> None:
        persist_lookup = (queued_item.get("_persist") or {}) if isinstance(queued_item.get("_persist"), dict) else {}
        persist_id = _clean(persist_lookup.get("id"))
        source_url = _clean(persist_lookup.get("source_url"))
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return
            raw_data = task.get("data")
            if not isinstance(raw_data, list):
                return
            changed = False
            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                persist_meta = item.get("_persist") if isinstance(item.get("_persist"), dict) else {}
                current_persist_id = _clean(persist_meta.get("id"))
                current_source_url = _clean(persist_meta.get("source_url"))
                if persist_id and current_persist_id != persist_id:
                    continue
                if not persist_id and source_url and current_source_url != source_url:
                    continue
                if saved_url:
                    item["url"] = saved_url
                    item["_persist_status"] = "done"
                    item.pop("_persist_error", None)
                else:
                    item["_persist_status"] = "error"
                    item["_persist_error"] = error or "persist failed"
                changed = True
                break
            if not changed:
                return
            metrics = dict(task.get("metrics") or {})
            if elapsed_ms > 0:
                metrics["background_download_images_ms"] = int(metrics.get("background_download_images_ms") or 0) + elapsed_ms
            task["metrics"] = metrics
            task["persist_summary"] = self._persist_summary(raw_data)
            task["updated_at"] = _now_iso()
            self._dirty = True
            self._save_locked()

    def _run_task(self, key: str, mode: str, payload: dict[str, Any]) -> None:
        slot_wait_started = time.time()
        started = 0.0
        slot_acquired = False
        try:
            slot_acquired = self._acquire_upstream_slot(self._running_task_timeout_seconds)
            if not slot_acquired:
                raise RuntimeError("image task waiting for upstream slot timed out")
            started = time.time()
            slot_wait_ms = max(0, int((started - slot_wait_started) * 1000))
            self._update_task(key, status=TASK_STATUS_RUNNING, error="", started_at=_now_iso())
            logger.info({
                "event": "image_task_start",
                "key": key,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "slot_wait_ms": slot_wait_ms,
                "upstream_concurrency": self._upstream_concurrency,
            })
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                raise RuntimeError(message)
            duration_ms = int((time.time() - started) * 1000) if started > 0 else 0
            metrics = dict(result.get("metrics") or {})
            metrics["slot_wait_ms"] = slot_wait_ms
            success_count = _count_success_images(data)
            persist_summary, persist_jobs = self._prepare_persist_jobs(key, payload, data)
            reserved_quota = 0
            owner_id = ""
            with self._lock:
                task = self._tasks.get(key) or {}
                reserved_quota = int(task.get("reserved_quota") or 0)
                owner_id = _clean(task.get("owner_id"))
                if reserved_quota:
                    task["reserved_quota"] = 0
            if reserved_quota > success_count:
                auth_service.refund_image_quota_by_id(owner_id, reserved_quota - success_count)
            self._update_task(
                key,
                status=TASK_STATUS_SUCCESS,
                data=data,
                error="",
                duration_ms=duration_ms,
                reserved_quota=0,
                metrics=metrics,
                persist_summary=persist_summary,
            )
            if persist_jobs:
                self._enqueue_persist_jobs(persist_jobs)
            logger.info({
                "event": "image_task_success",
                "key": key,
                "mode": mode,
                "image_count": len(data),
                "duration_ms": duration_ms,
                "metrics": metrics,
            })
            self._log_task_call(key, started=started, status="success", result=result)
        except Exception as exc:
            message = str(exc) or "image task failed"
            reserved_quota = 0
            owner_id = ""
            with self._lock:
                task = self._tasks.get(key) or {}
                reserved_quota = int(task.get("reserved_quota") or 0)
                owner_id = _clean(task.get("owner_id"))
                if reserved_quota:
                    task["reserved_quota"] = 0
            if reserved_quota:
                auth_service.refund_image_quota_by_id(owner_id, reserved_quota)
            duration_ms = int((time.time() - (started or slot_wait_started)) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=message, data=[], duration_ms=duration_ms)
            logger.warning({
                "event": "image_task_failed",
                "key": key,
                "mode": mode,
                "duration_ms": duration_ms,
                "error": message,
            })
            self._log_task_call(key, started=started or slot_wait_started, status="failed", error=message)
        finally:
            if slot_acquired:
                self._release_upstream_slot()

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return
            task.update(updates)
            task["updated_at"] = _now_iso()
            self._dirty = True
            self._save_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "owner_name": _clean(item.get("owner_name")),
                "owner_role": _clean(item.get("owner_role")),
                "status": status,
                "mode": "edit" if item.get("mode") == "edit" else "generate",
                "model": _clean(item.get("model"), "gpt-image-2"),
                "size": _clean(item.get("size")),
                "reserved_quota": int(item.get("reserved_quota") or 0),
                "duration_ms": int(item.get("duration_ms") or 0),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
            }
            started_at = _clean(item.get("started_at"))
            if started_at:
                task["started_at"] = started_at
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                task["metrics"] = metrics
            persist_summary = item.get("persist_summary")
            if isinstance(persist_summary, dict):
                task["persist_summary"] = {
                    "pending": int(persist_summary.get("pending") or 0),
                    "completed": int(persist_summary.get("completed") or 0),
                    "failed": int(persist_summary.get("failed") or 0),
                }
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _save_locked(self) -> None:
        if not self._dirty:
            return
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps({"tasks": items}, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
        self._dirty = False

    def _fail_unfinished_task_locked(self, task: dict[str, Any], message: str) -> None:
        reserved_quota = int(task.get("reserved_quota") or 0)
        if reserved_quota:
            auth_service.refund_image_quota_by_id(task.get("owner_id"), reserved_quota)
            task["reserved_quota"] = 0
        task["status"] = TASK_STATUS_ERROR
        task["error"] = message
        task["updated_at"] = _now_iso()
        self._dirty = True

    def _recover_stale_unfinished_locked(self) -> bool:
        changed = False
        now = time.time()
        queued_keys = {_queue_item_key(item) for item in list(getattr(self._queue, "queue", []))}
        running_keys = {item[0] for item in self._active_worker_tasks.values() if item and item[0]}
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                key = _task_key(_clean(task.get("owner_id")), _clean(task.get("id")))
                if task.get("status") == TASK_STATUS_QUEUED and key in queued_keys:
                    continue
                if task.get("status") == TASK_STATUS_RUNNING and key in running_keys:
                    started_at = _timestamp(task.get("started_at")) or _timestamp(task.get("updated_at")) or _timestamp(task.get("created_at"))
                    if started_at > 0 and now - started_at < self._running_task_timeout_seconds:
                        continue
                    self._fail_unfinished_task_locked(
                        task,
                        f"图片任务处理超时（超过 {self._running_task_timeout_seconds} 秒），已自动失败并返还额度，请稍后重试",
                    )
                    changed = True
                    continue
                updated_at = _timestamp(task.get("updated_at")) or _timestamp(task.get("created_at"))
                if updated_at > 0 and updated_at <= now and now - updated_at < self._running_task_timeout_seconds:
                    continue
                self._fail_unfinished_task_locked(task, "图片任务处理超时或服务已重启，任务已中断，请重新生成")
                changed = True
        return changed

    def _recover_unfinished_locked(self) -> bool:
        return self._recover_stale_unfinished_locked()

    def _cleanup_locked(self) -> bool:
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        if removed_keys:
            self._dirty = True
        return bool(removed_keys)

    def _cleanup_if_due_locked(self) -> bool:
        now = time.time()
        if now - self._last_cleanup_at < DEFAULT_CLEANUP_INTERVAL_SECONDS:
            return False
        self._last_cleanup_at = now
        return self._cleanup_locked()


image_task_service = ImageTaskService(DATA_DIR / "image_tasks.json")
