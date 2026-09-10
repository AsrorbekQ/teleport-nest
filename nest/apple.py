"""Today's agenda from Apple Calendar and Reminders, read through AppleScript.

The device cannot talk to Apple directly, so Nest serves the list as plain text
(one line per item) that the firmware's Briefing app fetches over the LAN:
calendar events first as "HH:MM Title", then reminders, with "! " marking
overdue ones. Reminders.app answers slowly (10 s or more with a large
database), so the result is cached and refreshed in the background.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("nest.apple")

MAX_ITEMS = 12
MAX_TEXT = 90
SCRIPT_TIMEOUT = 120
# Calendars that mirror content already covered elsewhere (reminders show up as events there).
SKIPPED_CALENDARS = ("Scheduled Reminders", "Siri Suggestions")

# tab-separated: "HH:MM" (blank for all-day) \t summary \t calendar name
CALENDAR_SCRIPT = """
set d1 to (current date)
set time of d1 to 0
set d2 to d1 + 1 * days
set out to ""
tell application "Calendar"
  repeat with c in calendars
    set cname to name of c
    if {CALENDAR_FILTER} then
      set evs to (every event of c whose start date >= d1 and start date < d2)
      repeat with e in evs
        set sd to start date of e
        set t to ""
        if not (allday event of e) then set t to (text -2 thru -1 of ("0" & (hours of sd))) & ":" & (text -2 thru -1 of ("0" & (minutes of sd)))
        set out to out & t & tab & (summary of e) & tab & cname & linefeed
      end repeat
    end if
  end repeat
end tell
return out
"""

# tab-separated: "1" if overdue (due before today) else "0" \t name
REMINDERS_SCRIPT = """
set today to (current date)
set time of today to 0
set cutoff to today + 1 * days
set out to ""
tell application "Reminders"
  set rs to (every reminder whose completed is false and due date < cutoff)
  repeat with r in rs
    set flag to "0"
    if (due date of r) < today then set flag to "1"
    set out to out & flag & tab & (name of r) & linefeed
  end repeat
end tell
return out
"""


@dataclass
class Agenda:
    events: list[str]
    reminders: list[str]
    fetched_at: float
    error: str = ""

    def lines(self) -> list[str]:
        return (self.events + self.reminders)[:MAX_ITEMS]

    def text(self) -> str:
        return "\n".join(self.lines()) + ("\n" if self.lines() else "")


def parse_calendar_output(raw: str) -> list[str]:
    """'HH:MM\\tTitle\\tCalendar' lines -> 'HH:MM Title' sorted by time, all-day first."""
    items: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[1].strip():
            continue
        when, title = parts[0].strip(), parts[1].strip()
        items.append((when, title))
    items.sort(key=lambda it: (it[0] != "", it[0]))
    return [_clip(f"{when} {title}" if when else title) for when, title in items]


def parse_reminders_output(raw: str) -> list[str]:
    """'1|0\\tName' lines -> 'Name' with '! ' prefix when overdue; overdue first."""
    items: list[tuple[bool, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) < 2 or not parts[1].strip():
            continue
        items.append((parts[0].strip() == "1", parts[1].strip()))
    items.sort(key=lambda it: not it[0])
    return [_clip(("! " if overdue else "") + name) for overdue, name in items]


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _osascript(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    return proc.stdout


def _quote_list(names: list[str] | tuple[str, ...]) -> str:
    return "{" + ", ".join('"' + n.replace('"', '\\"') + '"' for n in names) + "}"


def _calendar_filter(calendars: list[str]) -> str:
    if calendars:
        return f"cname is in {_quote_list(calendars)}"
    return f"cname is not in {_quote_list(SKIPPED_CALENDARS)}"


def fetch_agenda(calendars: list[str] | None = None) -> Agenda:
    """Runs both AppleScripts. macOS asks for Automation permission the first time."""
    errors: list[str] = []
    events: list[str] = []
    reminders: list[str] = []
    try:
        events = parse_calendar_output(_osascript(CALENDAR_SCRIPT.replace("{CALENDAR_FILTER}", _calendar_filter(calendars or []))))
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        errors.append(f"Calendar: {e}")
    try:
        reminders = parse_reminders_output(_osascript(REMINDERS_SCRIPT))
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        errors.append(f"Reminders: {e}")
    return Agenda(events, reminders, time.time(), "; ".join(errors))


class AgendaCache:
    """Serves the last agenda instantly and refreshes it in a background thread."""

    def __init__(self, calendars: list[str] | None = None, max_age: float = 600):
        self.calendars = calendars or []
        self.max_age = max_age
        self._agenda: Agenda | None = None
        self._lock = threading.Lock()
        self._refreshing = False
        self._idle = threading.Event()
        self._idle.set()

    @property
    def agenda(self) -> Agenda | None:
        return self._agenda

    def refresh(self) -> Agenda:
        """Fetches now; a caller arriving mid-refresh waits for that result instead of starting another."""
        with self._lock:
            if self._refreshing:
                waiting = True
            else:
                waiting = False
                self._refreshing = True
                self._idle.clear()
        if waiting:
            self._idle.wait(SCRIPT_TIMEOUT * 2)
            return self._agenda or Agenda([], [], 0, "refresh did not finish")
        try:
            agenda = fetch_agenda(self.calendars)
            if agenda.error:
                log.warning("agenda: %s", agenda.error)
            self._agenda = agenda
            return agenda
        finally:
            with self._lock:
                self._refreshing = False
                self._idle.set()

    def get(self, wait: bool = False) -> Agenda:
        """Fresh enough -> cached; stale -> refresh now (wait) or kick a background refresh."""
        agenda = self._agenda
        stale = agenda is None or time.time() - agenda.fetched_at > self.max_age
        if not stale:
            return agenda  # type: ignore[return-value]
        if wait:
            return self.refresh()
        if not self._refreshing:
            threading.Thread(target=self.refresh, daemon=True).start()
        return agenda or Agenda([], [], 0, "warming up")
