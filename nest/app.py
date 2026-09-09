"""Teleport Nest: FastAPI app serving the single-page UI and its JSON API."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import data as device_data
from .config import DATA_DIR, Config, load_config
from .convert import CONVERTIBLE
from .db import Database
from .device import DeviceClient, DeviceOffline
from .jobs import UPLOAD_DIR, JobRunner

log = logging.getLogger("nest")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config: Config | None = None, device: DeviceClient | None = None, db: Database | None = None) -> FastAPI:
    config = config or load_config()
    db = db or Database(DATA_DIR / "nest.sqlite")
    device = device or DeviceClient(config.device_urls, config.device_timeout)
    runner = JobRunner(config, db, device)
    app = FastAPI(title="Teleport Nest")
    app.state.config = config
    app.state.db = db
    app.state.device = device
    app.state.runner = runner

    @app.on_event("startup")
    def _start() -> None:
        runner.start()
        if config.watch_dir:
            threading.Thread(target=_watch_folder, args=(config, runner), daemon=True).start()

    @app.on_event("shutdown")
    def _stop() -> None:
        runner.stop()

    # ---- pages
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "config": config,
                "subscriptions": device_data.read_text(config.subscriptions_file),
                "habits": device_data.read_text(config.habits_file),
                "briefing": device_data.parse_briefing(
                    device_data.read_text(config.briefing_file, device_data.BRIEFING_TEMPLATE)
                ),
                "convertible": sorted(CONVERTIBLE),
            },
        )

    # ---- device
    @app.get("/api/device/status")
    def device_status():
        status = device.status()
        return {
            "online": status is not None,
            "status": status,
            "last_seen": device.last_seen,
            "pending": db.count_jobs("waiting") + db.count_jobs("queued"),
        }

    @app.get("/api/library")
    def library(path: str | None = None):
        try:
            files = device.list_files(path or config.books_dir)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        return [f.__dict__ for f in files]

    @app.post("/api/library/delete")
    def library_delete(path: str = Form(...)):
        try:
            device.delete(path)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        return {"ok": True}

    # ---- send
    @app.post("/api/send/url")
    def send_url(url: str = Form(...)):
        urls = [u.strip() for u in url.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            raise HTTPException(400, "no URL given")
        ids = [runner.enqueue("url_epub", u, {"url": u}) for u in urls]
        return {"ok": True, "jobs": ids}

    @app.post("/api/send/readlater")
    def send_readlater(url: str = Form(...)):
        urls = [u.strip() for u in url.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            raise HTTPException(400, "no URL given")
        job_id = runner.enqueue("readlater", f"Read Later: {len(urls)} link(s)", {"urls": urls})
        return {"ok": True, "jobs": [job_id]}

    @app.post("/api/send/file")
    async def send_file(file: UploadFile = File(...)):
        name = Path(file.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in CONVERTIBLE and suffix != ".epub":
            raise HTTPException(400, f"unsupported file type {suffix}")
        dest = UPLOAD_DIR / f"{int(time.time())}-{name}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        job_id = runner.enqueue("file_epub", name, {"path": str(dest), "name": name})
        return {"ok": True, "jobs": [job_id]}

    # ---- feeds
    @app.post("/api/feeds/digest")
    def feeds_digest(per_feed: int = Form(3), mode: str = Form("digest")):
        job_id = runner.enqueue("digest", f"Digest ({mode}, {per_feed}/feed)", {"per_feed": per_feed, "mode": mode})
        return {"ok": True, "jobs": [job_id]}

    @app.post("/api/feeds/save")
    def feeds_save(text: str = Form(""), push: str = Form("0")):
        device_data.write_text(config.subscriptions_file, text.strip() + "\n")
        if push == "1":
            try:
                device_data.subscriptions_push(config, device, text.strip() + "\n")
            except DeviceOffline:
                return {"ok": True, "pushed": False, "detail": "saved locally; device offline"}
        return {"ok": True, "pushed": push == "1"}

    # ---- data
    @app.post("/api/data/habits/pull")
    def habits_pull():
        try:
            return {"ok": True, "text": device_data.habits_pull(config, device)}
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        except FileNotFoundError:
            raise HTTPException(404, "no habits.bin on the device yet")
        except Exception as e:  # script errors
            raise HTTPException(500, str(e))

    @app.post("/api/data/habits/push")
    def habits_push(text: str = Form("")):
        try:
            return {"ok": True, "detail": device_data.habits_push(config, device, text)}
        except DeviceOffline:
            device_data.write_text(config.habits_file, text)
            return {"ok": True, "pushed": False, "detail": "saved locally; device offline"}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/api/data/briefing")
    def briefing_save(
        enabled: str = Form("0"), city: str = Form(""), lat: str = Form(""), lon: str = Form(""),
        todoist_token: str = Form(""), todoist_url: str = Form(""),
    ):
        values = {"enabled": enabled, "city": city, "lat": lat, "lon": lon, "todoist_token": todoist_token,
                  "todoist_url": todoist_url}
        try:
            device_data.briefing_push(config, device, values)
            return {"ok": True, "pushed": True}
        except DeviceOffline:
            device_data.write_text(config.briefing_file, device_data.format_briefing(values))
            return {"ok": True, "pushed": False, "detail": "saved locally; device offline"}

    @app.post("/api/data/deck")
    async def deck_upload(file: UploadFile = File(...)):
        name = Path(file.filename or "deck.apkg").name
        if not name.lower().endswith(".apkg"):
            raise HTTPException(400, "upload an .apkg export")
        dest = UPLOAD_DIR / f"{int(time.time())}-{name}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        job_id = runner.enqueue("deck", f"Deck {name}", {"apkg": str(dest)})
        return {"ok": True, "jobs": [job_id]}

    # ---- jobs
    @app.get("/api/jobs")
    def jobs():
        return db.jobs()

    @app.post("/api/jobs/{job_id}/retry")
    def job_retry(job_id: int):
        if not db.job(job_id):
            raise HTTPException(404, "no such job")
        runner.retry(job_id)
        return {"ok": True}

    @app.post("/api/jobs/flush")
    def jobs_flush():
        runner.wake()
        return {"ok": True}

    @app.get("/health")
    def health():
        return JSONResponse({"ok": True})

    @app.get("/favicon.ico")
    def favicon():
        return RedirectResponse("data:,")

    return app


def _watch_folder(config: Config, runner: JobRunner) -> None:
    from .jobs import wait_for_file

    seen: set[str] = set()
    while True:
        try:
            for path in sorted(config.watch_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in CONVERTIBLE | {".epub"} and str(path) not in seen:
                    wait_for_file(path)
                    seen.add(str(path))
                    runner.enqueue("file_epub", path.name, {"path": str(path), "name": path.name})
        except FileNotFoundError:
            pass
        time.sleep(10)


app = create_app()
