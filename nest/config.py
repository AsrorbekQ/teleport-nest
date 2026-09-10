"""Configuration: config.toml next to the repo root, falling back to built-in defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
DATA_DIR = ROOT / "data"


@dataclass
class Schedule:
    cron: str  # "minute hour day month weekday"
    job: str  # digest | flush
    per_feed: int = 3
    mode: str = "digest"


@dataclass
class Config:
    device_urls: list[str] = field(default_factory=lambda: ["http://crosspoint.local"])
    device_timeout: float = 4.0
    books_dir: str = "/Books"
    calibre: str = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
    firmware_repo: Path = ROOT.parent / "crosspoint-reader-apps"
    subscriptions_file: Path | None = None  # defaults to <firmware_repo>/local/subscriptions.txt
    habits_file: Path | None = None
    briefing_file: Path | None = None
    watch_dir: Path | None = None
    digest_per_feed: int = 3
    digest_mode: str = "digest"  # digest | articles
    image_max_width: int = 480
    image_quality: int = 70
    max_images: int = 20
    host: str = "127.0.0.1"
    port: int = 8787
    apple_enabled: bool = True
    apple_calendars: list[str] = field(default_factory=list)  # empty = every calendar
    apple_refresh_minutes: int = 10
    schedules: list[Schedule] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.firmware_repo = Path(self.firmware_repo).expanduser()
        local = self.firmware_repo / "local"
        self.subscriptions_file = Path(self.subscriptions_file) if self.subscriptions_file else local / "subscriptions.txt"
        self.habits_file = Path(self.habits_file) if self.habits_file else local / "habits.txt"
        self.briefing_file = Path(self.briefing_file) if self.briefing_file else local / "briefing.txt"
        if self.watch_dir:
            self.watch_dir = Path(self.watch_dir).expanduser()

    @property
    def scripts_dir(self) -> Path:
        return self.firmware_repo / "scripts"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("TELEPORT_NEST_CONFIG", CONFIG_PATH))
    raw: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    device = raw.get("device", {})
    paths = raw.get("paths", {})
    digest = raw.get("digest", {})
    images = raw.get("images", {})
    server = raw.get("server", {})
    apple = raw.get("apple", {})
    schedules = [Schedule(**s) for s in raw.get("schedules", [])]
    urls = device.get("urls") or ([device["url"]] if device.get("url") else None)
    kwargs = {
        "device_timeout": device.get("timeout"),
        "books_dir": device.get("books_dir"),
        "calibre": paths.get("calibre"),
        "firmware_repo": paths.get("firmware_repo"),
        "subscriptions_file": paths.get("subscriptions_file"),
        "habits_file": paths.get("habits_file"),
        "briefing_file": paths.get("briefing_file"),
        "watch_dir": paths.get("watch_dir"),
        "digest_per_feed": digest.get("per_feed"),
        "digest_mode": digest.get("mode"),
        "image_max_width": images.get("max_width"),
        "image_quality": images.get("quality"),
        "max_images": images.get("max_images"),
        "host": server.get("host"),
        "port": server.get("port"),
        "apple_enabled": apple.get("enabled"),
        "apple_calendars": apple.get("calendars"),
        "apple_refresh_minutes": apple.get("refresh_minutes"),
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    if urls:
        kwargs["device_urls"] = list(urls)
    return Config(schedules=schedules, **kwargs)
