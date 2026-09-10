"""Background job queue with a single worker thread and a cron scheduler.

Job kinds:
  url_epub   {url}                      -> EPUB in data/out, then push
  file_epub  {path, name}               -> Calibre conversion, then push
  digest     {per_feed, mode}           -> digest EPUB(s), then push
  push       {file, dest_dir}           -> upload; stays "waiting" while the device is offline
  readlater  {urls: [..]}               -> POST /api/readlater; waits for the device
  deck       {apkg}                     -> anki_to_deck.py, then push
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import data as device_data
from .apple import AgendaCache
from .config import DATA_DIR, Config
from .convert import convert_to_epub
from .db import Database
from .device import DeviceClient, DeviceOffline
from .epub import Book, Chapter, build_epub, safe_filename
from .extract import extract_article, make_client
from .feeds import build_digest, read_subscriptions

log = logging.getLogger("nest.jobs")
OUT_DIR = DATA_DIR / "out"
UPLOAD_DIR = DATA_DIR / "uploads"


class JobRunner:
    def __init__(self, config: Config, db: Database, device: DeviceClient):
        self.config = config
        self.db = db
        self.device = device
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="nest-jobs", daemon=True)
        self._scheduler = BackgroundScheduler()
        self.agenda: AgendaCache | None = None  # set by the app when Apple integration is on
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # ---- lifecycle
    def start(self) -> None:
        for schedule in self.config.schedules:
            try:
                trigger = CronTrigger.from_crontab(schedule.cron)
            except ValueError as e:
                log.error("bad schedule %r: %s", schedule.cron, e)
                continue
            if schedule.job == "digest":
                self._scheduler.add_job(
                    self.enqueue, trigger, args=["digest", "Scheduled digest", {"per_feed": schedule.per_feed, "mode": schedule.mode}]
                )
            elif schedule.job == "flush":
                self._scheduler.add_job(self.wake, trigger)
        self._scheduler.add_job(self.wake, "interval", seconds=30)  # retry waiting jobs when the device shows up
        if self.agenda is not None:
            self._scheduler.add_job(self.agenda.refresh, "interval", minutes=self.config.apple_refresh_minutes)
        self._scheduler.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._scheduler.shutdown(wait=False)

    def wake(self) -> None:
        self._wake.set()

    def enqueue(self, kind: str, title: str, payload: dict) -> int:
        job_id = self.db.add_job(kind, title, payload)
        self.wake()
        return job_id

    def retry(self, job_id: int) -> None:
        self.db.update_job(job_id, "queued", "retrying")
        self.wake()

    # ---- worker
    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.db.next_job(("queued",))
            if job is None and self.device.status() is not None:
                job = self.db.next_job(("waiting",))
            if job is None:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        job_id = job["id"]
        kind = job["kind"]
        payload = job["payload"]
        self.db.update_job(job_id, "running", "")
        try:
            handler = getattr(self, f"_job_{kind}")
            handler(job_id, payload)
        except DeviceOffline as e:
            self.db.update_job(job_id, "waiting", f"device offline ({e}); will retry when it appears")
        except Exception as e:  # noqa: BLE001 - job failures are reported, not raised
            log.exception("job %s failed", job_id)
            self.db.update_job(job_id, "failed", str(e)[:500])

    def _push_file(self, job_id: int, path: Path, dest_dir: str) -> None:
        """Uploads now if the device is online; otherwise queues a push job and marks this one done."""
        if self.device.status() is None:
            self.db.add_job("push", f"Send {path.name}", {"file": str(path), "dest_dir": dest_dir})
            self.db.update_job(job_id, "done", "built; queued for sending when the device is online", str(path))
            return
        self.device.ensure_dir(dest_dir)
        self.device.upload(dest_dir, path.name, path.read_bytes())
        self.db.update_job(job_id, "done", f"sent to {dest_dir}/{path.name}", str(path))

    # ---- handlers
    def _job_url_epub(self, job_id: int, payload: dict) -> None:
        url = payload["url"]
        self.db.update_job(job_id, "running", "fetching page")
        article = extract_article(
            url,
            client=make_client(),
            max_images=self.config.max_images,
            image_max_width=self.config.image_max_width,
            image_quality=self.config.image_quality,
        )
        book = Book(title=article.title, author=article.site, chapters=[Chapter(article.title, article.html, url)],
                    images=article.images)
        path = OUT_DIR / safe_filename(article.title)
        path.write_bytes(build_epub(book))
        self.db.mark_sent(url, article.title)
        self._push_file(job_id, path, self.config.books_dir)

    def _job_file_epub(self, job_id: int, payload: dict) -> None:
        source = Path(payload["path"])
        self.db.update_job(job_id, "running", "converting with Calibre")
        destination = OUT_DIR / (source.stem + ".epub")
        convert_to_epub(source, destination, self.config.calibre)
        self._push_file(job_id, destination, self.config.books_dir)

    def _job_digest(self, job_id: int, payload: dict) -> None:
        subscriptions = read_subscriptions(self.config.subscriptions_file)
        if not subscriptions:
            raise RuntimeError(f"no feeds in {self.config.subscriptions_file}")
        progress: list[str] = []

        def note(msg: str) -> None:
            progress.append(msg)
            self.db.update_job(job_id, "running", progress[-1])

        files = build_digest(
            subscriptions,
            int(payload.get("per_feed", self.config.digest_per_feed)),
            payload.get("mode", self.config.digest_mode),
            self.db,
            OUT_DIR,
            log=note,
            max_images=self.config.max_images,
            image_max_width=self.config.image_max_width,
            image_quality=self.config.image_quality,
        )
        if not files:
            self.db.update_job(job_id, "done", "nothing new since the last digest")
            return
        online = self.device.status() is not None
        for path in files:
            if online:
                self.device.ensure_dir(self.config.books_dir)
                self.device.upload(self.config.books_dir, path.name, path.read_bytes())
            else:
                self.db.add_job("push", f"Send {path.name}", {"file": str(path), "dest_dir": self.config.books_dir})
        summary = f"{len(files)} file(s) " + ("sent" if online else "built; queued for sending")
        self.db.update_job(job_id, "done", summary, json.dumps([str(p) for p in files]))

    def _job_push(self, job_id: int, payload: dict) -> None:
        path = Path(payload["file"])
        if not path.exists():
            raise FileNotFoundError(path)
        if self.device.status() is None:
            raise DeviceOffline("not reachable")
        self.device.ensure_dir(payload["dest_dir"])
        self.device.upload(payload["dest_dir"], path.name, path.read_bytes())
        self.db.update_job(job_id, "done", f"sent to {payload['dest_dir']}/{path.name}", str(path))

    def _job_readlater(self, job_id: int, payload: dict) -> None:
        if self.device.status() is None:
            raise DeviceOffline("not reachable")
        for url in payload["urls"]:
            self.device.readlater_add(url, payload.get("title", ""))
        self.db.update_job(job_id, "done", f"queued {len(payload['urls'])} link(s) on the device")

    def _job_deck(self, job_id: int, payload: dict) -> None:
        apkg = Path(payload["apkg"])
        deck = OUT_DIR / "gre.deck"
        self.db.update_job(job_id, "running", "converting .apkg")
        output = device_data.deck_build(self.config, apkg, deck)
        if self.device.status() is None:
            self.db.add_job("push", "Send gre.deck", {"file": str(deck), "dest_dir": "/apps/flashcards"})
            self.db.update_job(job_id, "done", output + "; queued for sending", str(deck))
            return
        device_data.deck_push(self.device, deck)
        self.db.update_job(job_id, "done", output + "; sent", str(deck))


def wait_for_file(path: Path, stable_seconds: float = 2.0) -> None:
    """Blocks until a file stops growing (for watched folders)."""
    last = -1
    while True:
        size = path.stat().st_size
        if size == last:
            return
        last = size
        time.sleep(stable_seconds)
