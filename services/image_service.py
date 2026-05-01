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
DOWNLOAD_JPEG_QUALITY = 92
MAX_BATCH_DOWNLOAD = 200
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_METADATA_FILE = DATA_DIR / "image_metadata.json"
_METADATA_LOCK = RLock()
_UNKNOWN_UPLOADER = "未知上传人"


def _thumb_root() -> Path:
    path = config.images_dir.parent / "image_thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thumb_path_for(rel: str) -> Path:
    return (_thumb_root() / Path(rel)).with_suffix(".webp")


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


def _jpeg_bytes(path: Path) -> bytes:
    """Return mobile-friendly JPEG bytes for an image.

    iOS/Android browsers save JPG files more consistently into the photo workflow
    than WebP/PNG blobs. Transparent images are flattened onto a white background.
    """
    output = io.BytesIO()
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        if has_alpha:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.save(output, "JPEG", quality=DOWNLOAD_JPEG_QUALITY, optimize=True)
    return output.getvalue()


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


def add_log_image_thumbnails(items: list[dict[str, object]], base_url: str) -> list[dict[str, object]]:
    normalized_base_url = base_url.rstrip("/")
    for item in items:
        detail = item.get("detail") if isinstance(item, dict) else None
        if not isinstance(detail, dict):
            continue
        urls = detail.get("urls")
        if not isinstance(urls, list):
            continue
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
    removed_paths: list[str] = []
    removed_thumbs = 0

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
    if removed_paths or removed_thumbs or removed_metadata:
        print(
            "[image-cleanup] "
            f"retention_days={retention_days}, removed_images={len(removed_paths)}, "
            f"removed_thumbs={removed_thumbs}, removed_metadata={removed_metadata}"
        )
    return {"removed": len(removed_paths), "thumbnails_removed": removed_thumbs, "metadata_removed": removed_metadata}


def list_images(base_url: str, start_date: str = "", end_date: str = "", uploader: str = "") -> dict[str, object]:
    cleanup_expired_images()
    items: list[dict[str, object]] = []
    root = config.images_dir
    thumb_root = _thumb_root()
    normalized_base_url = base_url.rstrip("/")
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

        meta = _image_metadata(rel, stored_metadata, log_index)
        if not _matches_uploader(meta, uploader):
            continue

        stat = path.stat()
        thumb_path, dimensions = _ensure_thumbnail(path, rel)
        thumbnail_url = None
        thumbnail_size = None
        if thumb_path and thumb_path.exists():
            thumb_rel = thumb_path.relative_to(thumb_root).as_posix()
            thumbnail_url = f"{normalized_base_url}/image-thumbs/{thumb_rel}"
            thumbnail_size = thumb_path.stat().st_size

        item = {
            "path": rel,
            "name": path.name,
            "date": day,
            "size": stat.st_size,
            "url": f"{normalized_base_url}/images/{rel}",
            "thumbnail_url": thumbnail_url,
            "thumbnail_size": thumbnail_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "uploader_key": _uploader_key(meta),
            "uploader_id": _clean(meta.get("uploader_id")),
            "uploader_name": _uploader_name(meta),
            "uploader_role": _clean(meta.get("uploader_role")),
        }
        if dimensions:
            item["dimensions"] = f"{dimensions[0]} x {dimensions[1]}"
            item["width"] = dimensions[0]
            item["height"] = dimensions[1]
        items.append(item)

    items.sort(key=lambda item: str(item["created_at"]), reverse=True)
    date_groups: dict[str, list[dict[str, object]]] = {}
    uploader_groups: dict[str, dict[str, object]] = {}
    for item in items:
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
        "items": items,
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
    cleanup_expired_images()
    image_rel, path = _safe_image_path(rel)
    if not image_rel or path is None:
        return None

    with _METADATA_LOCK:
        stored_metadata = _load_metadata()
    meta = _image_metadata(image_rel, stored_metadata, _log_uploader_index())
    if not _matches_uploader(meta, uploader):
        return None

    try:
        content = _jpeg_bytes(path)
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


def build_images_zip(paths: list[str] | None, uploader: str = "") -> Optional[dict[str, object]]:
    """Build a permission-checked ZIP containing selected images as JPG files."""
    normalized = _normalize_rel_paths(paths)
    if not normalized:
        return None
    if len(normalized) > MAX_BATCH_DOWNLOAD:
        normalized = normalized[:MAX_BATCH_DOWNLOAD]

    cleanup_expired_images()
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
                content = _jpeg_bytes(path)
                entry_name = _zip_entry_name(image_rel, added + 1)
            except Exception as exc:
                print(f"[image-download] zip convert jpeg failed path={path}: {exc}")
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
        "truncated": len(_normalize_rel_paths(paths)) > MAX_BATCH_DOWNLOAD,
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
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(thumb_root)
    return {"removed": removed}
