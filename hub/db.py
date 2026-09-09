"""SQLite persistence: job history, sent-article dedupe, small key/value settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,          -- queued | running | waiting | done | failed
    detail TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sent (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Jobs interrupted by a restart go back to the queue.
            self._conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            self._conn.commit()

    # ---- jobs
    def add_job(self, kind: str, title: str, payload: dict) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs (kind, title, status, payload, created, updated) VALUES (?, ?, 'queued', ?, ?, ?)",
                (kind, title, json.dumps(payload), now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_job(self, job_id: int, status: str, detail: str = "", result: str | None = None) -> None:
        with self._lock:
            if result is None:
                self._conn.execute(
                    "UPDATE jobs SET status=?, detail=?, updated=? WHERE id=?", (status, detail, time.time(), job_id)
                )
            else:
                self._conn.execute(
                    "UPDATE jobs SET status=?, detail=?, result=?, updated=? WHERE id=?",
                    (status, detail, result, time.time(), job_id),
                )
            self._conn.commit()

    def next_job(self, statuses: tuple[str, ...] = ("queued",)) -> dict | None:
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id LIMIT 1", statuses
            ).fetchone()
        return self._row(row)

    def jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def job(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row)

    def count_jobs(self, status: str) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0])

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        return d

    # ---- sent articles
    def was_sent(self, url: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone() is not None

    def mark_sent(self, url: str, title: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sent (url, title, sent_at) VALUES (?, ?, ?)", (url, title, time.time())
            )
            self._conn.commit()

    # ---- key/value
    def get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()
