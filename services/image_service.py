from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from services.config import DATA_DIR, config

THUMB_MAX_SIZE = (480, 480)
THUMB_QUALITY = 74
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


def list_images(base_url: str, start_date: str = "", end_date: str = "", uploader: str = "") -> dict[str, object]:
    config.cleanup_old_images()
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
    targets = _iter_image_rel_paths(start_date, end_date, uploader) if all_matching else (paths or [])
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
