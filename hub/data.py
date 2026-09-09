"""Device data editors: habits, RSS subscriptions, flashcard deck, briefing config.

Text lives in the firmware repo's gitignored local/ folder; binary files are
produced by the firmware's own scripts and pushed to the SD card over Wi-Fi.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config
from .device import DeviceClient

BRIEFING_KEYS = ("enabled", "city", "lat", "lon", "todoist_token", "todoist_url")
BRIEFING_TEMPLATE = """# Teleport sleep briefing. Copied to /apps/briefing/config.txt on the device.
enabled=0
city=
lat=
lon=
todoist_token=
"""


def read_text(path: Path, default: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() else default


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_briefing(text: str) -> dict[str, str]:
    values = {k: "" for k in BRIEFING_KEYS}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in values:
            values[key.strip()] = value.strip()
    return values


def format_briefing(values: dict[str, str]) -> str:
    lines = ["# Teleport sleep briefing. Copied to /apps/briefing/config.txt on the device."]
    for key in BRIEFING_KEYS:
        value = values.get(key, "")
        if key == "enabled":
            value = "1" if value in ("1", "true", "on", "yes") else "0"
        if key == "todoist_url" and not value:
            continue
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def run_script(config: Config, script: str, *args: str) -> str:
    path = config.scripts_dir / script
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; set paths.firmware_repo in config.toml")
    proc = subprocess.run([sys.executable, str(path), *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"{script} failed")
    return proc.stdout.strip()


# ---- habits
def habits_pull(config: Config, device: DeviceClient) -> str:
    """Downloads habits.bin from the device and converts it to text, refreshing local/habits.txt."""
    data = device.download("/apps/habits/habits.bin")
    with tempfile.TemporaryDirectory() as tmp:
        bin_path = Path(tmp) / "habits.bin"
        bin_path.write_bytes(data)
        run_script(config, "habits_tool.py", "export", str(bin_path), str(config.habits_file))
    return read_text(config.habits_file)


def habits_push(config: Config, device: DeviceClient, text: str) -> str:
    write_text(config.habits_file, text)
    with tempfile.TemporaryDirectory() as tmp:
        bin_path = Path(tmp) / "habits.bin"
        output = run_script(config, "habits_tool.py", "import", str(config.habits_file), str(bin_path))
        device.ensure_dir("/apps")
        device.ensure_dir("/apps/habits")
        device.upload("/apps/habits", "habits.bin", bin_path.read_bytes())
    return output


# ---- subscriptions
def subscriptions_push(config: Config, device: DeviceClient, text: str) -> None:
    write_text(config.subscriptions_file, text)
    device.ensure_dir("/apps")
    device.ensure_dir("/apps/rss")
    device.upload("/apps/rss", "subscriptions.txt", text.encode("utf-8"))


# ---- briefing
def briefing_push(config: Config, device: DeviceClient, values: dict[str, str]) -> str:
    text = format_briefing(values)
    write_text(config.briefing_file, text)
    device.ensure_dir("/apps")
    device.ensure_dir("/apps/briefing")
    device.upload("/apps/briefing", "config.txt", text.encode("utf-8"))
    return text


# ---- flashcards
def deck_build(config: Config, apkg: Path, out: Path) -> str:
    return run_script(config, "anki_to_deck.py", str(apkg), str(out))


def deck_push(device: DeviceClient, deck: Path, name: str = "gre.deck") -> None:
    device.ensure_dir("/apps")
    device.ensure_dir("/apps/flashcards")
    device.upload("/apps/flashcards", name, deck.read_bytes())
