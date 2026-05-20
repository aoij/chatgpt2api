from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Optional
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageOps

from services.config import DATA_DIR, config

THUMB_MAX_SIZE = (480, 480)
THUMB_QUALITY = 74
DOWNLOAD_JPEG_QUALITY = 90
MAX_BATCH_DOWNLOAD = 200
CLEANUP_INTERVAL_SECONDS = 600
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_METADATA_FILE = DATA_DIR / "image_metadata.json"
_METADATA_LOCK = RLock()
_CLEANUP_LOCK = RLock()
_LAST_CLEANUP_AT = 0.0
_LIST_CACHE_LOCK = RLock()
_LIST_CACHE: dict[str, object] = {
    "signature": "",
    "created_at": 0.0,
    "items": [],
    "uploaders": [],
}
LIST_CACHE_TTL_SECONDS = 20
_UNKNOWN_UPLOADER = "未知上传人"


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _thumb_root() -> Path:
    path = config.images_dir.parent / "image_thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_root() -> Path:
    path = config.images_dir.parent / "image_downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thumb_path_for(rel: str) -> Path:
    return (_thumb_root() / Path(rel)).with_suffix(".webp")


def _download_jpeg_path_for(rel: str) -> Path:
    return (_download_root() / Path(rel)).with_suffix(".jpg")


def _image_dimensions(path: Path) -> Optional[tuple[int, int]]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception as exc:
        print(f"[image-thumbnail] read dimensions failed path={path}: {exc}")
        return None


def _ensure_thumbnail(path: Path, rel: str) -> tuple[Optional[Path], Optional[tuple[int, int]]]:
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        return None, None

    thumb_path = _thumb_path_for(rel)
    dimensions: Optional[tuple[int, int]] = None
    try:
        source_mtime = path.stat().st_mtime
        if thumb_path.exists() and thumb_path.stat().st_mtime >= source_mtime:
            dimensions = _image_dimensions(path)
            return thumb_path, dimensions

        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            dimensions = image.size
            image.thumbnail(THUMB_MAX_SIZE, Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.save(thumb_path, "WEBP", quality=THUMB_QUALITY, method=6)
        return thumb_path, dimensions
    except Exception as exc:
        print(f"[image-thumbnail] generate failed path={path}: {exc}")
        return None, dimensions or _image_dimensions(path)



def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_image_path(rel: str) -> tuple[str, Optional[Path]]:
    image_rel = str(rel or "").strip().lstrip("/")
    if not image_rel:
        return "", None
    root = config.images_dir.resolve()
    path = (root / image_rel).resolve()
    if not _is_relative_to(path, root) or not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
        return image_rel, None
    return image_rel, path


def _write_jpeg(source_path: Path, target_path: Path) -> bytes:
    """Convert and cache mobile-friendly JPEG bytes for an image.

    iOS/Android browsers save JPG files more consistently into the photo workflow
    than WebP/PNG blobs. Transparent images are flattened onto a white background.
    """
    output = io.BytesIO()
    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        if has_alpha:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.save(output, "JPEG", quality=DOWNLOAD_JPEG_QUALITY)
    content = output.getvalue()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)
    return content


def _jpeg_bytes(path: Path, rel: str) -> bytes:
    """Return mobile-friendly JPEG bytes, using an on-disk conversion cache."""
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return path.read_bytes()

    cache_path = _download_jpeg_path_for(rel)
    try:
        source_mtime = path.stat().st_mtime
        if cache_path.exists() and cache_path.stat().st_mtime >= source_mtime:
            return cache_path.read_bytes()
    except OSError:
        pass
    return _write_jpeg(path, cache_path)


def _download_filename(rel: str, index: int | None = None) -> str:
    path = Path(rel)
    stem = path.stem or "image"
    prefix = f"{index:03d}_" if index is not None else ""
    return f"{prefix}{stem}.jpg"


def _zip_entry_name(rel: str, index: int) -> str:
    path = Path(rel).with_suffix(".jpg")
    parts = [part for part in path.parts if part not in {"", ".", ".."}]
    if not parts:
        return _download_filename(rel, index)
    if len(parts) == 1:
        return _download_filename(rel, index)
    return Path(*parts).as_posix()


def _clean(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _metadata_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _METADATA_FILE


def _load_metadata() -> dict[str, dict[str, Any]]:
    path = _metadata_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    images = data.get("images") if "images" in data else data
    if not isinstance(images, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in images.items():
        rel = str(key or "").strip().lstrip("/")
        if rel and isinstance(value, dict):
            result[rel] = dict(value)
    return result


def _save_metadata(data: dict[str, dict[str, Any]]) -> None:
    path = _metadata_file()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"images": data}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _normalize_uploader(uploader: object) -> dict[str, str]:
    if isinstance(uploader, dict):
        uploader_id = _clean(uploader.get("uploader_id") or uploader.get("id") or uploader.get("key_id") or uploader.get("subject_id"))
        uploader_name = _clean(uploader.get("uploader_name") or uploader.get("name") or uploader.get("key_name") or uploader.get("username"), uploader_id or _UNKNOWN_UPLOADER)
        uploader_role = _clean(uploader.get("uploader_role") or uploader.get("role"))
        auth_mode = _clean(uploader.get("auth_mode"))
        scope = _clean(uploader.get("scope"))
    else:
        uploader_id = ""
        uploader_name = _clean(uploader, _UNKNOWN_UPLOADER)
        uploader_role = ""
        auth_mode = ""
        scope = ""
    return {
        "uploader_id": uploader_id,
        "uploader_name": uploader_name or _UNKNOWN_UPLOADER,
        "uploader_role": uploader_role,
        "auth_mode": auth_mode,
        "scope": scope,
    }


def _uploader_key(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return "unknown"
    return _clean(meta.get("uploader_id") or meta.get("uploader_name"), "unknown")


def _uploader_name(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return _UNKNOWN_UPLOADER
    return _clean(meta.get("uploader_name") or meta.get("uploader_id"), _UNKNOWN_UPLOADER)


def _matches_uploader(meta: dict[str, Any] | None, uploader: str = "") -> bool:
    expected = _clean(uploader).lower()
    if not expected:
        return True
    candidates = {
        _uploader_key(meta).lower(),
        _clean((meta or {}).get("uploader_id")).lower(),
        _clean((meta or {}).get("uploader_name")).lower(),
    }
    return expected in candidates


def record_image_metadata(rel: str, uploader: object = None, **extra: object) -> None:
    image_rel = str(rel or "").strip().lstrip("/")
    if not image_rel:
        return
    metadata = {
        **_normalize_uploader(uploader),
        "created_at": _now_text(),
    }
    for key, value in extra.items():
        if value is not None and value != "":
            metadata[key] = value
    with _METADATA_LOCK:
        data = _load_metadata()
        data[image_rel] = metadata
        _save_metadata(data)
    _invalidate_list_cache()


def ensure_thumbnail_for_rel(rel: str) -> bool:
    image_rel, path = _safe_image_path(rel)
    if not image_rel or path is None:
        return False
    thumb_path, _ = _ensure_thumbnail(path, image_rel)
    if not thumb_path or not thumb_path.exists():
        return False
    _invalidate_list_cache()
    return True


def resolve_thumbnail_file(rel: str) -> Optional[Path]:
    image_rel = str(rel or "").strip().lstrip("/")
    if not image_rel:
        return None
    source_root = config.images_dir.resolve()
    thumb_root = _thumb_root().resolve()

    rel_path = Path(image_rel)
    candidates: list[str] = []
    try:
        normalized_webp = rel_path.with_suffix(".webp").as_posix()
        candidates.append(normalized_webp)
    except Exception:
        pass
    candidates.append(image_rel)

    for candidate in candidates:
        candidate_path = (thumb_root / Path(candidate)).resolve()
        if _is_relative_to(candidate_path, thumb_root) and candidate_path.is_file():
            return candidate_path

    source_candidates: list[Path] = []
    if rel_path.suffix:
        source_candidates.append((source_root / rel_path).resolve())
    else:
        for suffix in _IMAGE_SUFFIXES:
            source_candidates.append((source_root / rel_path).with_suffix(suffix).resolve())

    for source_path in source_candidates:
        if not _is_relative_to(source_path, source_root) or not source_path.is_file():
            continue
        try:
            source_rel = source_path.relative_to(source_root).as_posix()
        except Exception:
            continue
        thumb_path, _ = _ensure_thumbnail(source_path, source_rel)
        if thumb_path and thumb_path.exists():
            return thumb_path
    return None


def _remove_image_metadata(rel_paths: list[str]) -> None:
    normalized = {str(rel or "").strip().lstrip("/") for rel in rel_paths if str(rel or "").strip()}
    if not normalized:
        return
    with _METADATA_LOCK:
        data = _load_metadata()
        changed = False
        for rel in normalized:
            if data.pop(rel, None) is not None:
                changed = True
        if changed:
            _save_metadata(data)


def _relative_image_path_from_url(image_url: str) -> str:
    parsed = urlsplit(str(image_url or ""))
    marker = "/images/"
    if marker not in parsed.path:
        return ""
    return unquote(parsed.path.split(marker, 1)[1]).lstrip("/")


def _log_uploader_index() -> dict[str, dict[str, Any]]:
    path = DATA_DIR / "logs.jsonl"
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        detail = item.get("detail")
        if not isinstance(detail, dict):
            continue
        urls = detail.get("urls")
        if not isinstance(urls, list):
            continue
        uploader = _normalize_uploader(detail)
        uploader["created_at"] = _clean(detail.get("ended_at") or detail.get("started_at") or item.get("time"))
        for url in urls:
            rel = _relative_image_path_from_url(str(url or ""))
            if rel:
                result[rel] = dict(uploader)
    return result


def thumbnail_url_for_image_url(base_url: str, image_url: str) -> Optional[str]:
    try:
        rel = _relative_image_path_from_url(image_url)
        if not rel:
            return None
        root = config.images_dir.resolve()
        path = (root / rel).resolve()
        if not _is_relative_to(path, root) or not path.is_file():
            return None
        thumb_path, _ = _ensure_thumbnail(path, rel)
        if not thumb_path or not thumb_path.exists():
            return None
        thumb_rel = thumb_path.relative_to(_thumb_root()).as_posix()
        return f"{base_url.rstrip('/')}/image-thumbs/{thumb_rel}"
    except Exception as exc:
        print(f"[image-thumbnail] resolve log thumbnail failed url={image_url}: {exc}")
        return None


def _list_local_image_urls_for_day(base_url: str, day: str) -> list[tuple[str, str]]:
    parts = [part for part in str(day or "").strip().split("-") if part]
    if len(parts) != 3:
        return []
    day_dir = config.images_dir / parts[0] / parts[1] / parts[2]
    if not day_dir.exists() or not day_dir.is_dir():
        return []
    result: list[tuple[str, str]] = []
    for path in sorted(day_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        rel = path.relative_to(config.images_dir).as_posix()
        timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        result.append((timestamp, f"{base_url.rstrip('/')}/images/{rel}"))
    return result


def _repair_log_urls_with_local_images(
    item: dict[str, object],
    urls: list[str],
    base_url: str,
    local_images_by_day: dict[str, list[tuple[str, str]]],
) -> list[str]:
    if not urls or any("/images/" in str(url or "") for url in urls):
        return urls
    if not all("chatgpt.com/backend-api/estuary/" in str(url or "") for url in urls):
        return urls
    time_text = _clean(item.get("time"))
    if len(time_text) < 10:
        return urls
    day = time_text[:10]
    local_images = local_images_by_day.get(day)
    if local_images is None:
        local_images = _list_local_image_urls_for_day(base_url, day)
        local_images_by_day[day] = local_images
    if not local_images:
        return urls
    target_count = len(urls)
    window: list[tuple[int, str]] = []
    item_ts = _timestamp(time_text)
    for image_time, image_url in local_images:
        image_ts = _timestamp(image_time)
        if item_ts <= 0 or image_ts <= 0:
            continue
        delta = abs(int(image_ts - item_ts))
        if delta <= 30:
            window.append((delta, image_url))
    if len(window) < target_count:
        return urls
    window.sort(key=lambda row: row[0])
    chosen: list[str] = []
    for _delta, image_url in window:
        if image_url in chosen:
            continue
        chosen.append(image_url)
        if len(chosen) >= target_count:
            return chosen
    return urls


def add_log_image_thumbnails(items: list[dict[str, object]], base_url: str) -> list[dict[str, object]]:
    normalized_base_url = base_url.rstrip("/")
    local_images_by_day: dict[str, list[tuple[str, str]]] = {}
    for item in items:
        detail = item.get("detail") if isinstance(item, dict) else None
        if not isinstance(detail, dict):
            continue
        urls = detail.get("urls")
        if not isinstance(urls, list):
            continue
        repaired_urls = _repair_log_urls_with_local_images(item, [str(url) for url in urls if isinstance(url, str)], normalized_base_url, local_images_by_day)
        if repaired_urls != urls:
            detail["urls"] = repaired_urls
            urls = repaired_urls
        thumbnails: list[Optional[str]] = []
        has_thumbnail = False
        for url in urls:
            thumbnail = thumbnail_url_for_image_url(normalized_base_url, url) if isinstance(url, str) else None
            thumbnails.append(thumbnail)
            has_thumbnail = has_thumbnail or bool(thumbnail)
        if has_thumbnail:
            detail["thumbnail_urls"] = thumbnails
    return items


def _image_day(path: Path, rel: str) -> str:
    parts = rel.split("/")
    return "-".join(parts[:3]) if len(parts) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _image_metadata(rel: str, stored: dict[str, dict[str, Any]], log_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = stored.get(rel) or log_index.get(rel) or {}
    normalized = _normalize_uploader(meta)
    return {**normalized, **{key: value for key, value in meta.items() if key not in normalized}}


def _build_uploader_summary(items: list[dict[str, object]]) -> list[dict[str, object]]:
    uploaders: dict[str, dict[str, object]] = {}
    for item in items:
        key = _clean(item.get("uploader_key"), "unknown")
        current = uploaders.setdefault(key, {
            "key": key,
            "id": item.get("uploader_id") or "",
            "name": item.get("uploader_name") or _UNKNOWN_UPLOADER,
            "role": item.get("uploader_role") or "",
            "count": 0,
        })
        current["count"] = int(current.get("count") or 0) + 1
    return sorted(uploaders.values(), key=lambda item: (-int(item.get("count") or 0), str(item.get("name") or "")))


def _invalidate_list_cache() -> None:
    with _LIST_CACHE_LOCK:
        _LIST_CACHE["signature"] = ""
        _LIST_CACHE["created_at"] = 0.0
        _LIST_CACHE["items"] = []
        _LIST_CACHE["uploaders"] = []


def _images_dir_signature(root: Path) -> str:
    try:
        stat = root.stat()
        return f"{int(stat.st_mtime_ns)}:{stat.st_size}"
    except OSError:
        return "missing"


def cleanup_expired_images() -> dict[str, int]:
    """删除超过保留期的图片、缩略图，并同步清理图片元数据。

    图片保留期由 ``config.image_retention_days`` 控制，当前部署设置为 10 天。
    兼容两种判断：
    - 文件 mtime 超过保留期；
    - 生成图片保存路径中的 YYYY/MM/DD 日期超过保留期。
    """
    try:
        retention_days = max(1, int(config.image_retention_days))
    except Exception:
        retention_days = 10
    cutoff_ts = time.time() - retention_days * 86400
    cutoff_day = datetime.fromtimestamp(cutoff_ts).strftime("%Y-%m-%d")

    root = config.images_dir.resolve()
    thumb_root = _thumb_root().resolve()
    download_root = _download_root().resolve()
    removed_paths: list[str] = []
    removed_thumbs = 0
    removed_downloads = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(root).as_posix()
            expired_by_mtime = path.stat().st_mtime < cutoff_ts
            expired_by_day = _image_day(path, rel) < cutoff_day
            if not expired_by_mtime and not expired_by_day:
                continue
            path.unlink()
            removed_paths.append(rel)
        except Exception as exc:
            print(f"[image-cleanup] remove image failed path={path}: {exc}")

    for rel in removed_paths:
        try:
            thumb_path = _thumb_path_for(rel).resolve()
            if _is_relative_to(thumb_path, thumb_root) and thumb_path.is_file():
                thumb_path.unlink()
                removed_thumbs += 1
        except Exception as exc:
            print(f"[image-cleanup] remove thumbnail failed rel={rel}: {exc}")
        try:
            download_path = _download_jpeg_path_for(rel).resolve()
            if _is_relative_to(download_path, download_root) and download_path.is_file():
                download_path.unlink()
                removed_downloads += 1
        except Exception as exc:
            print(f"[image-cleanup] remove download cache failed rel={rel}: {exc}")

    if thumb_root.exists():
        for thumb_path in thumb_root.rglob("*"):
            if not thumb_path.is_file():
                continue
            try:
                thumb_rel_path = thumb_path.relative_to(thumb_root)
                source_candidates = [(root / thumb_rel_path).with_suffix(suffix) for suffix in _IMAGE_SUFFIXES]
                source_exists = any(candidate.is_file() for candidate in source_candidates)
                if source_exists and thumb_path.stat().st_mtime >= cutoff_ts:
                    continue
                thumb_path.unlink()
                removed_thumbs += 1
            except Exception as exc:
                print(f"[image-cleanup] remove orphan thumbnail failed path={thumb_path}: {exc}")

    if download_root.exists():
        for download_path in download_root.rglob("*"):
            if not download_path.is_file():
                continue
            try:
                download_rel_path = download_path.relative_to(download_root)
                source_candidates = [(root / download_rel_path).with_suffix(suffix) for suffix in _IMAGE_SUFFIXES]
                source_exists = any(candidate.is_file() for candidate in source_candidates)
                if source_exists and download_path.stat().st_mtime >= cutoff_ts:
                    continue
                download_path.unlink()
                removed_downloads += 1
            except Exception as exc:
                print(f"[image-cleanup] remove orphan download cache failed path={download_path}: {exc}")

    removed_metadata = 0
    with _METADATA_LOCK:
        metadata = _load_metadata()
        if metadata:
            next_metadata: dict[str, dict[str, Any]] = {}
            for rel, value in metadata.items():
                image_path = (root / rel).resolve()
                if rel in removed_paths or not _is_relative_to(image_path, root) or not image_path.is_file():
                    removed_metadata += 1
                    continue
                next_metadata[rel] = value
            if removed_metadata:
                _save_metadata(next_metadata)

    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(thumb_root)
    _cleanup_empty_dirs(download_root)
    if removed_paths or removed_thumbs or removed_downloads or removed_metadata:
        _invalidate_list_cache()
        print(
            "[image-cleanup] "
            f"retention_days={retention_days}, removed_images={len(removed_paths)}, "
            f"removed_thumbs={removed_thumbs}, removed_downloads={removed_downloads}, "
            f"removed_metadata={removed_metadata}"
        )
    return {
        "removed": len(removed_paths),
        "thumbnails_removed": removed_thumbs,
        "download_cache_removed": removed_downloads,
        "metadata_removed": removed_metadata,
    }


def cleanup_expired_images_if_due() -> dict[str, int]:
    global _LAST_CLEANUP_AT
    now = time.time()
    with _CLEANUP_LOCK:
        if now - _LAST_CLEANUP_AT < CLEANUP_INTERVAL_SECONDS:
            return {"removed": 0, "thumbnails_removed": 0, "download_cache_removed": 0, "metadata_removed": 0}
        _LAST_CLEANUP_AT = now
    return cleanup_expired_images()


def list_images(
    base_url: str,
    start_date: str = "",
    end_date: str = "",
    uploader: str = "",
    limit: int | None = 200,
    offset: int = 0,
) -> dict[str, object]:
    cleanup_expired_images_if_due()
    root = config.images_dir
    normalized_base_url = base_url.rstrip("/")
    signature = _images_dir_signature(root)
    now = time.time()
    with _LIST_CACHE_LOCK:
        cache_items = _LIST_CACHE.get("items")
        if (
            _LIST_CACHE.get("signature") == signature
            and isinstance(cache_items, list)
            and now - float(_LIST_CACHE.get("created_at") or 0) < LIST_CACHE_TTL_SECONDS
        ):
            all_items = [dict(item) for item in cache_items if isinstance(item, dict)]
        else:
            all_items = []

    if not all_items:
        thumb_root = _thumb_root()
        with _METADATA_LOCK:
            stored_metadata = _load_metadata()
        log_index = _log_uploader_index()

        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            day = _image_day(path, rel)
            meta = _image_metadata(rel, stored_metadata, log_index)
            stat = path.stat()
            item = {
                "path": rel,
                "name": path.name,
                "date": day,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "uploader_key": _uploader_key(meta),
                "uploader_id": _clean(meta.get("uploader_id")),
                "uploader_name": _uploader_name(meta),
                "uploader_role": _clean(meta.get("uploader_role")),
            }
            all_items.append(item)

        all_items.sort(key=lambda item: str(item["created_at"]), reverse=True)
        with _LIST_CACHE_LOCK:
            _LIST_CACHE["signature"] = signature
            _LIST_CACHE["created_at"] = time.time()
            _LIST_CACHE["items"] = [dict(item) for item in all_items]

    items: list[dict[str, object]] = []
    for item in all_items:
        day = str(item.get("date") or "")
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        if not _matches_uploader(item, uploader):
            continue
        public_item = dict(item)
        rel = str(public_item.get("path") or "")
        thumbnail_path = str(public_item.pop("thumbnail_path", "") or "")
        public_item["url"] = f"{normalized_base_url}/images/{rel}"
        public_item["thumbnail_url"] = f"{normalized_base_url}/image-thumbs/{thumbnail_path}" if thumbnail_path else None
        items.append(public_item)

    total = len(items)
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(int(limit or 200), 500)) if limit is not None else total
    page_items = items[safe_offset:safe_offset + safe_limit]
    enriched_page_items: list[dict[str, object]] = []
    for item in page_items:
        public_item = dict(item)
        rel = str(public_item.get("path") or "")
        public_item["url"] = f"{normalized_base_url}/images/{rel}"
        image_rel, path = _safe_image_path(rel)
        thumbnail_url: str | None = None
        if image_rel and path is not None:
            thumb_path, dimensions = _ensure_thumbnail(path, image_rel)
            if thumb_path and thumb_path.exists():
                thumbnail_rel = thumb_path.relative_to(_thumb_root()).as_posix()
                thumbnail_url = f"{normalized_base_url}/image-thumbs/{thumbnail_rel}"
            if dimensions:
                public_item["dimensions"] = f"{dimensions[0]} x {dimensions[1]}"
                public_item["width"] = dimensions[0]
                public_item["height"] = dimensions[1]
        public_item["thumbnail_url"] = thumbnail_url
        enriched_page_items.append(public_item)

    date_groups: dict[str, list[dict[str, object]]] = {}
    uploader_groups: dict[str, dict[str, object]] = {}
    for item in enriched_page_items:
        date_groups.setdefault(str(item["date"]), []).append(item)
        uploader_key = str(item.get("uploader_key") or "unknown")
        group = uploader_groups.setdefault(uploader_key, {
            "uploader_key": uploader_key,
            "uploader_id": item.get("uploader_id") or "",
            "uploader_name": item.get("uploader_name") or _UNKNOWN_UPLOADER,
            "items": [],
        })
        group_items = group.get("items")
        if isinstance(group_items, list):
            group_items.append(item)
    uploaders = _build_uploader_summary(items)
    return {
        "items": enriched_page_items,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "groups": [{"date": key, "items": value} for key, value in date_groups.items()],
        "uploader_groups": sorted(uploader_groups.values(), key=lambda item: (-len(item.get("items") or []), str(item.get("uploader_name") or ""))),
        "uploaders": uploaders,
    }


def _iter_image_rel_paths(start_date: str = "", end_date: str = "", uploader: str = "") -> list[str]:
    root = config.images_dir
    paths: list[str] = []
    with _METADATA_LOCK:
        stored_metadata = _load_metadata()
    log_index = _log_uploader_index()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        day = _image_day(path, rel)
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        if not _matches_uploader(_image_metadata(rel, stored_metadata, log_index), uploader):
            continue
        paths.append(rel)
    return paths


def _normalize_rel_paths(paths: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in paths or []:
        rel = str(item or "").strip().lstrip("/")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        normalized.append(rel)
    return normalized


def build_image_download(rel: str, uploader: str = "") -> Optional[dict[str, object]]:
    """Build a permission-checked, mobile-friendly JPEG download payload."""
    cleanup_expired_images_if_due()
    image_rel, path = _safe_image_path(rel)
    if not image_rel or path is None:
        return None

    with _METADATA_LOCK:
        stored_metadata = _load_metadata()
    meta = _image_metadata(image_rel, stored_metadata, _log_uploader_index())
    if not _matches_uploader(meta, uploader):
        return None

    try:
        content = _jpeg_bytes(path, image_rel)
        filename = _download_filename(image_rel)
        media_type = "image/jpeg"
    except Exception as exc:
        print(f"[image-download] convert jpeg failed path={path}: {exc}")
        content = path.read_bytes()
        filename = path.name
        suffix = path.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"

    return {
        "path": image_rel,
        "filename": filename,
        "content": content,
        "media_type": media_type,
        "size": len(content),
    }


def build_images_zip(
    paths: list[str] | None,
    uploader: str = "",
    start_date: str = "",
    end_date: str = "",
    all_matching: bool = False,
) -> Optional[dict[str, object]]:
    """Build a permission-checked ZIP containing full-size JPG conversions."""
    if all_matching:
        normalized = _iter_image_rel_paths(start_date=start_date, end_date=end_date, uploader=uploader)
    else:
        normalized = _normalize_rel_paths(paths)
        if start_date or end_date or uploader:
            allowed = set(_iter_image_rel_paths(start_date=start_date, end_date=end_date, uploader=uploader))
            normalized = [rel for rel in normalized if rel in allowed]
    if not normalized:
        return None

    truncated = len(normalized) > MAX_BATCH_DOWNLOAD
    if truncated:
        normalized = normalized[:MAX_BATCH_DOWNLOAD]

    cleanup_expired_images_if_due()
    root = config.images_dir.resolve()
    with _METADATA_LOCK:
        stored_metadata = _load_metadata()
    log_index = _log_uploader_index()

    output = io.BytesIO()
    added = 0
    used_names: set[str] = set()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for rel in normalized:
            image_rel, path = _safe_image_path(rel)
            if not image_rel or path is None:
                continue
            meta = _image_metadata(image_rel, stored_metadata, log_index)
            if not _matches_uploader(meta, uploader):
                continue
            try:
                content = _jpeg_bytes(path, image_rel)
                entry_name = _zip_entry_name(image_rel, added + 1)
            except Exception as exc:
                print(f"[image-download] zip convert full-size jpeg failed path={path}: {exc}")
                content = path.read_bytes()
                try:
                    entry_name = path.relative_to(root).as_posix()
                except Exception:
                    entry_name = _download_filename(image_rel, added + 1)
            if entry_name in used_names:
                entry_name = _download_filename(image_rel, added + 1)
            used_names.add(entry_name)
            archive.writestr(entry_name, content)
            added += 1

    if added <= 0:
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    content = output.getvalue()
    return {
        "filename": f"images-{timestamp}.zip",
        "content": content,
        "media_type": "application/zip",
        "count": added,
        "truncated": truncated,
        "size": len(content),
    }


def _cleanup_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            continue


def delete_images(
    paths: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    uploader: str = "",
    all_matching: bool = False,
) -> dict[str, int]:
    root = config.images_dir.resolve()
    thumb_root = _thumb_root().resolve()
    if all_matching:
        targets = _iter_image_rel_paths(start_date, end_date, uploader)
    else:
        targets = _normalize_rel_paths(paths)
        if start_date or end_date or uploader:
            allowed = set(_iter_image_rel_paths(start_date, end_date, uploader))
            targets = [rel for rel in targets if rel in allowed]
    removed = 0
    removed_paths: list[str] = []

    for item in targets:
        rel = str(item or "").strip().lstrip("/")
        if not rel:
            continue
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        path.unlink()
        removed += 1
        removed_paths.append(rel)

        thumb_path = _thumb_path_for(rel).resolve()
        try:
            thumb_path.relative_to(thumb_root)
        except ValueError:
            thumb_path = None
        if thumb_path and thumb_path.is_file():
            thumb_path.unlink()

    _remove_image_metadata(removed_paths)
    if removed:
        _invalidate_list_cache()
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(thumb_root)
    return {"removed": removed}
