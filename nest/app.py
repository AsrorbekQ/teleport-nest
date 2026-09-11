"""Nest: FastAPI app serving the single-page UI and its JSON API."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import data as device_data
from .apple import AgendaCache
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
    agenda = AgendaCache(config.apple_calendars, config.apple_refresh_minutes * 60) if config.apple_enabled else None
    runner.agenda = agenda
    app = FastAPI(title="Nest")
    app.state.config = config
    app.state.db = db
    app.state.device = device
    app.state.runner = runner

    @app.on_event("startup")
    def _start() -> None:
        runner.start()
        if agenda is not None:
            threading.Thread(target=agenda.refresh, daemon=True).start()
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
                "tasks_url_default": tasks_url_default(config),
                "apple_enabled": agenda is not None,
                "convertible": sorted(CONVERTIBLE),
                "folders": config.library_folders,
                "defaults": config.library_defaults,
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

    @app.get("/api/library/folders")
    def library_folders():
        """Configured folders plus any other top-level folder found on the card (skips dot and app dirs)."""
        folders = list(config.library_folders)
        try:
            for f in device.list_files("/"):
                if f.is_dir and not f.name.startswith(".") and f.path not in folders and f.name.lower() not in ("apps", "fonts", "sleep", "websites"):
                    folders.append(f.path)
        except DeviceOffline:
            pass
        return {"folders": folders, "defaults": config.library_defaults}

    @app.get("/api/library")
    def library(path: str | None = None):
        folder = path or config.library_folders[0]
        try:
            files = device.list_files(folder)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        except Exception:
            files = []  # folder does not exist yet
        return [f.__dict__ for f in files]

    @app.post("/api/library/delete")
    def library_delete(path: str = Form(...)):
        try:
            device.delete(path)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        return {"ok": True}

    @app.post("/api/library/move")
    def library_move(path: str = Form(...), dest: str = Form(...)):
        try:
            device.ensure_dir(dest)
            device.move(path, dest)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        return {"ok": True}

    @app.post("/api/library/mkdir")
    def library_mkdir(path: str = Form(...)):
        try:
            device.ensure_dir(path)
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        return {"ok": True}

    # ---- send
    @app.post("/api/send/url")
    def send_url(url: str = Form(...), dest: str = Form("")):
        urls = [u.strip() for u in url.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            raise HTTPException(400, "no URL given")
        payload = {"dest": dest.strip()} if dest.strip() else {}
        ids = [runner.enqueue("url_epub", u, {"url": u, **payload}) for u in urls]
        return {"ok": True, "jobs": ids}

    @app.post("/api/send/readlater")
    def send_readlater(url: str = Form(...)):
        urls = [u.strip() for u in url.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            raise HTTPException(400, "no URL given")
        job_id = runner.enqueue("readlater", f"Read Later: {len(urls)} link(s)", {"urls": urls})
        return {"ok": True, "jobs": [job_id]}

    @app.post("/api/send/file")
    async def send_file(file: UploadFile = File(...), dest: str = Form("")):
        name = Path(file.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in CONVERTIBLE and suffix != ".epub":
            raise HTTPException(400, f"unsupported file type {suffix}")
        dest_path = UPLOAD_DIR / f"{int(time.time())}-{name}"
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        payload = {"path": str(dest_path), "name": name}
        if dest.strip():
            payload["dest"] = dest.strip()
        job_id = runner.enqueue("file_epub", name, payload)
        return {"ok": True, "jobs": [job_id]}

    # ---- feeds
    @app.post("/api/feeds/digest")
    def feeds_digest(per_feed: int = Form(3), mode: str = Form("digest"), dest: str = Form("")):
        payload = {"per_feed": per_feed, "mode": mode}
        if dest.strip():
            payload["dest"] = dest.strip()
        job_id = runner.enqueue("digest", f"Digest ({mode}, {per_feed}/feed)", payload)
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
        tasks_url: str = Form(""),
    ):
        if not tasks_url.strip() and agenda is not None:
            tasks_url = tasks_url_default(config)
        values = {"enabled": enabled, "city": city, "lat": lat, "lon": lon, "tasks_url": tasks_url.strip()}
        try:
            device_data.briefing_push(config, device, values)
            return {"ok": True, "pushed": True}
        except DeviceOffline:
            device_data.write_text(config.briefing_file, device_data.format_briefing(values))
            return {"ok": True, "pushed": False, "detail": "saved locally; device offline"}

    @app.get("/api/data/briefing/device")
    def briefing_device():
        """The config as it is on the card right now, to check that a push landed."""
        try:
            return {"ok": True, "text": device.download("/apps/briefing/config.txt").decode("utf-8", "replace")}
        except DeviceOffline:
            raise HTTPException(503, "device offline")
        except Exception as e:
            raise HTTPException(404, f"no config on the device yet ({e})")

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

    # ---- briefing tasks (fetched by the device over the LAN)
    @app.get("/api/briefing/tasks.txt", response_class=PlainTextResponse)
    def briefing_tasks_text():
        if agenda is None:
            return PlainTextResponse("", status_code=404)
        return agenda.get().text()

    @app.get("/api/briefing/tasks")
    def briefing_tasks(refresh: int = 0):
        if agenda is None:
            raise HTTPException(404, "Apple integration is off (apple.enabled in config.toml)")
        current = agenda.refresh() if refresh else agenda.get()
        return {"lines": current.lines(), "fetched_at": current.fetched_at, "error": current.error}

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


def lan_ip() -> str:
    """The address the reader can reach this Mac on (no packets are sent)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def bonjour_name() -> str:
    """This Mac's .local name; the reader resolves it with mDNS, so a changing DHCP address does not matter."""
    import subprocess

    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return f"{name}.local" if name else lan_ip()


def tasks_url_default(config: Config) -> str:
    return f"http://{bonjour_name()}:{config.port}/api/briefing/tasks.txt"


def _watch_folder(config: Config, runner: JobRunner) -> None:
    from .jobs import wait_for_file

    seen: set[str] = set()
    while True:
        try:
            for path in sorted(config.watch_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in CONVERTIBLE | {".epub"} and str(path) not in seen:
                    wait_for_file(path)
                    seen.add(str(path))
                    runner.enqueue("file_epub", path.name, {"path": str(path), "name": path.name, "dest": config.library_defaults["watch"]})
        except FileNotFoundError:
            pass
        time.sleep(10)


app = create_app()
