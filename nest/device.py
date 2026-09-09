"""Client for the Teleport / CrossPoint File Transfer web server on the e-reader.

The device only serves this API while the File Transfer app is open, so every
call must tolerate the device being absent. The first base URL that answers
/api/status is remembered for the rest of the session.
"""

from __future__ import annotations

import posixpath
import threading
import time
from dataclasses import dataclass

import httpx


class DeviceOffline(Exception):
    pass


@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    is_dir: bool


class DeviceClient:
    def __init__(self, base_urls: list[str], timeout: float = 4.0, transport: httpx.BaseTransport | None = None):
        self.base_urls = [u.rstrip("/") for u in base_urls]
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._active: str | None = None
        self._lock = threading.Lock()
        self.last_status: dict | None = None
        self.last_seen: float = 0.0

    # ---- discovery
    def status(self) -> dict | None:
        """Returns /api/status JSON from the first reachable base URL, or None."""
        candidates = ([self._active] if self._active else []) + [u for u in self.base_urls if u != self._active]
        for base in candidates:
            try:
                r = self._client.get(f"{base}/api/status")
                if r.status_code == 200:
                    data = r.json()
                    with self._lock:
                        self._active = base
                        self.last_status = data
                        self.last_seen = time.time()
                    return data
            except (httpx.HTTPError, ValueError):
                continue
        with self._lock:
            self.last_status = None
        return None

    @property
    def online(self) -> bool:
        return self.status() is not None

    def _base(self) -> str:
        if self._active is None and self.status() is None:
            raise DeviceOffline("device not reachable")
        assert self._active is not None
        return self._active

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            r = self._client.request(method, f"{self._base()}{path}", **kwargs)
        except httpx.HTTPError as e:
            with self._lock:
                self._active = None
            raise DeviceOffline(str(e)) from e
        return r

    # ---- files
    def list_files(self, path: str = "/") -> list[FileInfo]:
        r = self._request("GET", "/api/files", params={"path": path})
        r.raise_for_status()
        data = r.json()
        entries = data.get("files", data) if isinstance(data, dict) else data
        result = []
        for e in entries:
            name = e.get("name", "")
            result.append(
                FileInfo(
                    name=name,
                    path=e.get("path") or posixpath.join(path, name),
                    size=int(e.get("size", 0) or 0),
                    is_dir=bool(e.get("isDirectory", e.get("isDir", False))),
                )
            )
        return result

    def download(self, path: str) -> bytes:
        r = self._request("GET", "/download", params={"path": path}, timeout=max(self.timeout, 60))
        if r.status_code == 404:
            raise FileNotFoundError(path)
        r.raise_for_status()
        return r.content

    def upload(self, directory: str, filename: str, data: bytes) -> None:
        r = self._request(
            "POST",
            "/upload",
            params={"path": directory},
            files={"file": (filename, data, "application/octet-stream")},
            timeout=max(self.timeout, 120),
        )
        r.raise_for_status()

    def mkdir(self, parent: str, name: str) -> None:
        r = self._request("POST", "/mkdir", data={"path": parent, "name": name})
        r.raise_for_status()

    def ensure_dir(self, path: str) -> None:
        parent, name = posixpath.split(path.rstrip("/"))
        if not name:
            return
        try:
            if any(f.is_dir and f.name == name for f in self.list_files(parent or "/")):
                return
        except httpx.HTTPError:
            pass
        try:
            self.mkdir(parent or "/", name)
        except httpx.HTTPStatusError:
            pass  # already exists

    def delete(self, path: str) -> None:
        r = self._request("POST", "/delete", data={"path": path})
        r.raise_for_status()

    def rename(self, path: str, new_name: str) -> None:
        r = self._request("POST", "/rename", data={"path": path, "name": new_name})
        r.raise_for_status()

    def move(self, path: str, dest: str) -> None:
        r = self._request("POST", "/move", data={"path": path, "dest": dest})
        r.raise_for_status()

    # ---- read later
    READLATER_QUEUE = "/apps/readlater/queue.txt"

    def readlater_get(self) -> str:
        r = self._request("GET", "/api/readlater")
        if r.status_code == 404:
            try:
                return self.download(self.READLATER_QUEUE).decode("utf-8", "replace")
            except FileNotFoundError:
                return ""
        r.raise_for_status()
        return r.text

    def readlater_add(self, url: str, title: str = "") -> bool:
        """Queues a URL. Uses the firmware endpoint, falling back to editing queue.txt."""
        r = self._request("POST", "/api/readlater", data={"url": url, "title": title})
        if r.status_code == 200:
            return True
        if r.status_code != 404:
            r.raise_for_status()
        queue = self.readlater_get()
        if url in queue:
            return True
        if queue and not queue.endswith("\n"):
            queue += "\n"
        queue += url + (f"\t{title}" if title else "") + "\n"
        self.ensure_dir("/apps")
        self.ensure_dir("/apps/readlater")
        self.upload("/apps/readlater", "queue.txt", queue.encode("utf-8"))
        return True
