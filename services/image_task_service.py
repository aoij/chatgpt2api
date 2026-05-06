from __future__ import annotations

import base64
import json
import os
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.auth_service import ImageQuotaExceeded, auth_service
from services.config import DATA_DIR, config
from services.log_service import LOG_TYPE_CALL, log_service
from services.protocol import openai_v1_image_edit, openai_v1_image_generations
from utils.log import logger
try:
    import pika
except Exception:  # pragma: no cover - runtime fallback when vendor package is absent
    pika = None

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
DEFAULT_RABBITMQ_PORT = 5672
DEFAULT_RABBITMQ_QUEUE = "chatgpt2api.image_tasks"
DEFAULT_RABBITMQ_RETRY_DELAY_SECONDS = 5
DEFAULT_TASK_LIST_LIMIT = 24


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


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
    return item


def _compact_image_data(data: object) -> object:
    if not isinstance(data, list):
        return data
    result: list[object] = []
    for item in data:
        if not isinstance(item, dict):
            result.append(item)
            continue
        next_item = dict(item)
        if next_item.get("url"):
            next_item.pop("b64_json", None)
        result.append(next_item)
    return result


def _encode_queue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(payload)
    images = payload.get("images")
    if isinstance(images, list):
        encoded_images: list[dict[str, str]] = []
        for item in images:
            if (
                isinstance(item, tuple)
                and len(item) == 3
                and isinstance(item[0], (bytes, bytearray))
            ):
                image_data, filename, content_type = item
                encoded_images.append(
                    {
                        "data_b64": base64.b64encode(bytes(image_data)).decode("ascii"),
                        "filename": str(filename or "image.png"),
                        "content_type": str(content_type or "image/png"),
                    }
                )
        encoded["images"] = encoded_images
    return encoded


def _decode_queue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(payload)
    images = payload.get("images")
    if isinstance(images, list):
        decoded_images: list[tuple[bytes, str, str]] = []
        for item in images:
            if not isinstance(item, dict):
                continue
            data_b64 = _clean(item.get("data_b64"))
            if not data_b64:
                continue
            try:
                image_data = base64.b64decode(data_b64.encode("ascii"))
            except Exception:
                continue
            decoded_images.append(
                (
                    image_data,
                    _clean(item.get("filename"), "image.png"),
                    _clean(item.get("content_type"), "image/png"),
                )
            )
        decoded["images"] = decoded_images
    return decoded


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
        self._rabbitmq_enabled = _env_bool("CHATGPT2API_IMAGE_TASK_USE_RABBITMQ", True) and pika is not None
        self._rabbitmq_host = _clean(os.getenv("CHATGPT2API_IMAGE_TASK_RABBITMQ_HOST"), "host.docker.internal")
        self._rabbitmq_port = _env_int(
            "CHATGPT2API_IMAGE_TASK_RABBITMQ_PORT",
            DEFAULT_RABBITMQ_PORT,
            minimum=1,
            maximum=65535,
        )
        self._rabbitmq_user = _clean(os.getenv("CHATGPT2API_IMAGE_TASK_RABBITMQ_USER"), "guest")
        self._rabbitmq_password = _clean(os.getenv("CHATGPT2API_IMAGE_TASK_RABBITMQ_PASSWORD"), "guest")
        self._rabbitmq_vhost = _clean(os.getenv("CHATGPT2API_IMAGE_TASK_RABBITMQ_VHOST"), "/")
        self._rabbitmq_queue = _clean(os.getenv("CHATGPT2API_IMAGE_TASK_RABBITMQ_QUEUE"), DEFAULT_RABBITMQ_QUEUE)
        self._rabbitmq_retry_delay_seconds = _env_int(
            "CHATGPT2API_IMAGE_TASK_RABBITMQ_RETRY_DELAY_SECONDS",
            DEFAULT_RABBITMQ_RETRY_DELAY_SECONDS,
            minimum=1,
            maximum=60,
        )
        self._worker_count = 0
        self._upstream_concurrency = 0
        self._upstream_condition = threading.Condition()
        self._active_upstream_slots = 0
        self._started_worker_count = 0
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
        if self._rabbitmq_enabled:
            logger.info(
                {
                    "event": "image_task_queue_transport",
                    "transport": "rabbitmq",
                    "host": self._rabbitmq_host,
                    "port": self._rabbitmq_port,
                    "vhost": self._rabbitmq_vhost,
                    "queue": self._rabbitmq_queue,
                    "workers": self._worker_count,
                    "upstream_concurrency": self._upstream_concurrency,
                }
            )
        else:
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
                return min(max(int(configured), 1), 16)
            except (TypeError, ValueError):
                pass
        return _env_int(
            "CHATGPT2API_IMAGE_TASK_WORKERS",
            getattr(config, "image_task_worker_count", DEFAULT_IMAGE_TASK_WORKERS),
            minimum=1,
            maximum=16,
        )

    def reload_runtime_settings(self) -> dict[str, int]:
        worker_count = self._resolve_worker_count()
        upstream_concurrency = worker_count
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
            "base_url": base_url,
            "uploader": _uploader_from_identity(identity),
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
            "base_url": base_url,
            "uploader": _uploader_from_identity(identity),
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)][:DEFAULT_TASK_LIST_LIMIT]
        with self._lock:
            if self._cleanup_if_due_locked():
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
            if self._cleanup_if_due_locked():
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

            return {
                "transport": "rabbitmq" if self._rabbitmq_enabled else "memory",
                "workers": self._worker_count,
                "upstream_concurrency": self._upstream_concurrency,
                "active_upstream_slots": self._active_upstream_slots,
                "queued": queued,
                "running": running,
                "processing": queued + running,
                "recent_avg_duration_ms": avg_duration_ms,
                "estimated_wait_ms": estimated_wait_ms,
                "frontend_batch_limit": max(1, int(getattr(config, "image_account_concurrency", DEFAULT_UPSTREAM_CONCURRENCY))),
                "recent_avg_stage_ms": {
                    "slot_wait_ms": _avg_metric("slot_wait_ms"),
                    "upstream_stream_ms": _avg_metric("upstream_stream_ms"),
                    "resolve_urls_ms": _avg_metric("resolve_urls_ms"),
                    "download_images_ms": _avg_metric("download_images_ms"),
                    "save_images_ms": _avg_metric("save_images_ms"),
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

    def _ensure_worker_threads(self) -> None:
        while self._started_worker_count < self._worker_count:
            self._started_worker_count += 1
            index = self._started_worker_count
            thread = threading.Thread(
                target=self._rabbitmq_worker if self._rabbitmq_enabled else self._worker,
                args=(index,),
                name=f"image-task-worker-{index}",
                daemon=True,
            )
            thread.start()

    def _worker_enabled(self, worker_index: int) -> bool:
        return worker_index <= self._worker_count

    def _acquire_upstream_slot(self, timeout_seconds: int) -> bool:
        with self._upstream_condition:
            self._active_upstream_slots += 1
            return True

    def _release_upstream_slot(self) -> None:
        with self._upstream_condition:
            if self._active_upstream_slots > 0:
                self._active_upstream_slots -= 1
            self._upstream_condition.notify_all()

    def _rabbitmq_parameters(self):
        if pika is None:
            raise RuntimeError("pika is not available")
        credentials = pika.PlainCredentials(self._rabbitmq_user, self._rabbitmq_password)
        return pika.ConnectionParameters(
            host=self._rabbitmq_host,
            port=self._rabbitmq_port,
            virtual_host=self._rabbitmq_vhost,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
            socket_timeout=15,
        )

    def _declare_rabbitmq_queue(self, channel) -> None:
        channel.queue_declare(queue=self._rabbitmq_queue, durable=True)

    def _enqueue_task(self, key: str, mode: str, payload: dict[str, Any]) -> None:
        if not self._rabbitmq_enabled:
            self._queue.put((key, mode, payload))
            return
        if pika is None:
            raise RuntimeError("pika is not available")
        message = json.dumps(
            {
                "key": key,
                "mode": mode,
                "payload": _encode_queue_payload(payload),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        connection = pika.BlockingConnection(self._rabbitmq_parameters())
        try:
            channel = connection.channel()
            self._declare_rabbitmq_queue(channel)
            channel.basic_publish(
                exchange="",
                routing_key=self._rabbitmq_queue,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                ),
            )
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _worker(self, worker_index: int) -> None:
        while True:
            if not self._worker_enabled(worker_index):
                return
            try:
                key, mode, payload = self._queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                self._run_task(key, mode, payload)
            except Exception as exc:
                print(f"[image-task-worker] unexpected error key={key}: {exc}")
            finally:
                self._queue.task_done()

    def _rabbitmq_worker(self, worker_index: int) -> None:
        while True:
            if not self._worker_enabled(worker_index):
                return
            connection = None
            try:
                if pika is None:
                    raise RuntimeError("pika is not available")
                connection = pika.BlockingConnection(self._rabbitmq_parameters())
                channel = connection.channel()
                self._declare_rabbitmq_queue(channel)
                channel.basic_qos(prefetch_count=1)
                for method_frame, _, body in channel.consume(
                    self._rabbitmq_queue,
                    inactivity_timeout=5,
                    auto_ack=False,
                ):
                    if not self._worker_enabled(worker_index):
                        return
                    if method_frame is None or body is None:
                        if connection.is_closed:
                            break
                        continue
                    delivery_tag = method_frame.delivery_tag
                    try:
                        raw = json.loads(body.decode("utf-8"))
                        key = _clean(raw.get("key"))
                        mode = "edit" if raw.get("mode") == "edit" else "generate"
                        payload = _decode_queue_payload(raw.get("payload") if isinstance(raw.get("payload"), dict) else {})
                        if not key:
                            channel.basic_ack(delivery_tag)
                            continue
                        with self._lock:
                            existing = dict(self._tasks.get(key) or {})
                        if existing.get("status") in TERMINAL_STATUSES:
                            channel.basic_ack(delivery_tag)
                            continue
                        self._run_task(key, mode, payload)
                        channel.basic_ack(delivery_tag)
                    except Exception as exc:
                        logger.warning(
                            {
                                "event": "image_task_rabbitmq_consume_failed",
                                "error": str(exc),
                            }
                        )
                        try:
                            channel.basic_nack(delivery_tag, requeue=False)
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning(
                    {
                        "event": "image_task_rabbitmq_worker_retry",
                        "error": str(exc),
                        "retry_delay_seconds": self._rabbitmq_retry_delay_seconds,
                    }
                )
                time.sleep(self._rabbitmq_retry_delay_seconds)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

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
            self._update_task(key, status=TASK_STATUS_RUNNING, error="")
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
                message = _clean(result.get("message")) or "image task returned no image data"
                raise RuntimeError(message)
            duration_ms = int((time.time() - started) * 1000) if started > 0 else 0
            metrics = dict(result.get("metrics") or {})
            metrics["slot_wait_ms"] = slot_wait_ms
            success_count = _count_success_images(data)
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
                data=_compact_image_data(data),
                error="",
                duration_ms=duration_ms,
                reserved_quota=0,
                metrics=metrics,
            )
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
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                task["metrics"] = metrics
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

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        now = time.time()
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                updated_at = _timestamp(task.get("updated_at")) or _timestamp(task.get("created_at"))
                if updated_at > 0 and now - updated_at < self._running_task_timeout_seconds:
                    continue
                reserved_quota = int(task.get("reserved_quota") or 0)
                if reserved_quota:
                    auth_service.refund_image_quota_by_id(task.get("owner_id"), reserved_quota)
                    task["reserved_quota"] = 0
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "图片任务处理超时或服务已重启，已自动终止，请重新生成"
                task["updated_at"] = _now_iso()
                self._dirty = True
                changed = True
        return changed

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
