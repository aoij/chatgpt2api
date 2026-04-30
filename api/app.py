from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from api import accounts, ai, image_tasks, register, system
from api.support import resolve_web_asset, start_limited_account_watcher
from services.config import config


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        thread = start_limited_account_watcher(stop_event)
        config.cleanup_old_images()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)

    app = FastAPI(title="chatgpt2api", version=app_version, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ai.create_router())
    app.include_router(accounts.create_router())
    app.include_router(image_tasks.create_router())
    app.include_router(register.create_router())
    app.include_router(system.create_router(app_version))
    if config.images_dir.exists():
        app.mount("/images", StaticFiles(directory=str(config.images_dir)), name="images")
    image_thumbs_dir = config.images_dir.parent / "image_thumbs"
    image_thumbs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/image-thumbs", StaticFiles(directory=str(image_thumbs_dir)), name="image_thumbs")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_web(full_path: str):
        asset = resolve_web_asset(full_path)
        if asset is not None:
            if asset.suffix == ".html":
                text = asset.read_text(encoding="utf-8")
                # Cloudflare/browser may have cached the original chunk path.
                # Add a version query so image-manager loads the patched chunk that uses WebP thumbnails.
                text = text.replace(
                    "0wk.g3-a2cx57.js",
                    "0wk.g3-a2cx57.js?v=thumb-opt-20260428",
                )
                text = text.replace(
                    "0sm2er~jf-i~i.js",
                    "0sm2er~jf-i~i.js?v=log-thumb-20260428",
                )
                return HTMLResponse(text, headers={"Cache-Control": "no-cache"})
            return FileResponse(asset)
        if full_path.strip("/").startswith("_next/"):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback)

    return app
