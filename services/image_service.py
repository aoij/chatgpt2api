from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from services.config import config

THUMB_MAX_SIZE = (480, 480)
THUMB_QUALITY = 74
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


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


def thumbnail_url_for_image_url(base_url: str, image_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(str(image_url))
        marker = "/images/"
        if marker not in parsed.path:
            return None
        rel = unquote(parsed.path.split(marker, 1)[1]).lstrip("/")
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


def list_images(base_url: str, start_date: str = "", end_date: str = "") -> dict[str, object]:
    config.cleanup_old_images()
    items = []
    root = config.images_dir
    thumb_root = _thumb_root()
    normalized_base_url = base_url.rstrip("/")

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        day = "-".join(parts[:3]) if len(parts) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
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
        }
        if dimensions:
            item["dimensions"] = f"{dimensions[0]} x {dimensions[1]}"
        items.append(item)

    items.sort(key=lambda item: str(item["created_at"]), reverse=True)
    groups: dict[str, list[dict[str, object]]] = {}
    for item in items:
        groups.setdefault(str(item["date"]), []).append(item)
    return {"items": items, "groups": [{"date": key, "items": value} for key, value in groups.items()]}


def _iter_image_rel_paths(start_date: str = "", end_date: str = "") -> list[str]:
    root = config.images_dir
    paths: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        day = "-".join(parts[:3]) if len(parts) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
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


def delete_images(paths: list[str] | None = None, start_date: str = "", end_date: str = "", all_matching: bool = False) -> dict[str, int]:
    root = config.images_dir.resolve()
    thumb_root = _thumb_root().resolve()
    targets = _iter_image_rel_paths(start_date, end_date) if all_matching else (paths or [])
    removed = 0

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

        thumb_path = _thumb_path_for(rel).resolve()
        try:
            thumb_path.relative_to(thumb_root)
        except ValueError:
            thumb_path = None
        if thumb_path and thumb_path.is_file():
            thumb_path.unlink()

    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(thumb_root)
    return {"removed": removed}
