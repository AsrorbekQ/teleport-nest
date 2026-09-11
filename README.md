# Nest

Nest is a small local web app that feeds a [Teleport](https://github.com/AsrorbekQ/teleport) e-reader (Xteink X4 running the Teleport / CrossPoint firmware) from your computer:

- **Web page → EPUB.** Paste URLs; each becomes a clean EPUB with grayscale, downscaled images and is sent to the device's Books folder.
- **Document → EPUB.** Drop PDF, DOCX, MOBI, HTML, Markdown and more; Calibre converts them with an e-ink profile.
- **RSS digest.** Newest articles from your feeds, fetched in full, packed into one digest book or one book per article. Already-sent articles are skipped. Can run on a schedule.
- **Read Later.** Send links to the device's Read Later queue; the reader fetches them itself.
- **Device data.** Edit habits, RSS subscriptions and the sleep-briefing config, upload an Anki `.apkg` to rebuild the flashcard deck, browse and delete books on the device.
- **Briefing tasks.** Serves today's Apple Calendar events and due Reminders as plain text at `/api/briefing/tasks.txt`; the reader's Briefing app fetches it over the LAN. macOS asks once for Automation access to Calendar and Reminders.
- **Offline-tolerant.** Everything you send while the reader is off waits in the Jobs list and goes out automatically the next time the device shows up.

## Requirements

- macOS or Linux, Python 3.11+
- [Calibre](https://calibre-ebook.com) for document conversion (macOS default path is preconfigured)
- The firmware repo checked out next to this one (`../crosspoint-reader-apps`) for the habits and flashcard scripts and the gitignored `local/` data files

## Run

```sh
cp config.example.toml config.toml   # optional, edit device URL etc.
./run.sh                             # creates .venv on first run
open http://127.0.0.1:8787
```

Set `server.host = "0.0.0.0"` in `config.toml` to use Nest from your phone on the same Wi-Fi.

## How the device receives files

The firmware only runs its web server inside the **File Transfer** app. Open it on the reader, connect to your Wi-Fi, and Nest finds the device at `http://crosspoint.local` (or the IP shown on the reader's screen; add it under `[device] urls`). The status bar turns green and queued jobs flush. Close File Transfer when done; Nest keeps queuing.

Every send action has a folder picker; defaults come from `[library]` in `config.toml` (web pages to `/Articles`, documents to `/Books`, PDFs to `/Papers`, digests to `/Digests`). The Library tab browses those folders and moves or deletes files. Data pushes go to `/apps/rss/subscriptions.txt`, `/apps/habits/habits.bin`, `/apps/flashcards/gre.deck`, `/apps/briefing/config.txt` and `/apps/readlater/queue.txt`.

## Config keys

See `config.example.toml`. Sections: `[device]` (urls, timeout), `[library]` (folders and per-kind defaults), `[paths]` (calibre, firmware_repo, data files, optional watch_dir), `[digest]` (per_feed, mode), `[images]` (max_width, quality, max_images), `[server]` (host, port), and `[[schedules]]` entries with a cron expression and a job (`digest` or `flush`).

`config.toml` and `data/` (SQLite database, generated EPUBs, uploads) are gitignored; nothing personal is committed.

## Tests

```sh
.venv/bin/python -m pytest
```

Covers the EPUB writer, article extraction and image processing on a fixture page, and the device client against a fake device, including the Read Later fallback for firmware without the `/api/readlater` endpoint.

## Always on (macOS login item)

A launchd agent cannot run Nest from a folder under `~/Desktop`, `~/Documents` or
`~/Downloads`: macOS denies those folders to launchd-spawned processes without
any prompt. Wrap Nest in a small app instead, which gets the normal
"allow access to your Desktop folder" prompt once:

```sh
cat > /tmp/nest.applescript <<'EOF2'
do shell script "cd '/path/to/teleport-nest' && while :; do .venv/bin/uvicorn nest.app:app --host 0.0.0.0 --port 8787 >> data-nest.log 2>&1; sleep 5; done"
EOF2
osacompile -o ~/Applications/Nest.app /tmp/nest.applescript
plutil -insert LSBackgroundOnly -bool true ~/Applications/Nest.app/Contents/Info.plist
open ~/Applications/Nest.app
```

Then start it at login. Either add `~/Applications/Nest.app` under System Settings >
General > Login Items, or install a launchd agent that only runs `open -g -a
~/Applications/Nest.app` (launchd may run `open`; it is the app that touches the
repo):

```sh
cat > ~/Library/LaunchAgents/com.teleport.nest.plist <<'EOF2'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.teleport.nest</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-g</string><string>-a</string><string>/Users/you/Applications/Nest.app</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF2
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.teleport.nest.plist
```

The loop inside the app restarts uvicorn if it crashes. To stop or restart (for example
after pulling new code), kill the app and the server together, then reopen:

```sh
pkill -f "Nest.app/Contents/MacOS"; pkill -f "uvicorn nest.app"; sleep 2; open ~/Applications/Nest.app
```
Log: `data-nest.log` in the repo.

## Layout

```
nest/app.py       FastAPI routes and the single-page UI
nest/device.py    client for the device's File Transfer API
nest/extract.py   fetch + readability + image processing
nest/epub.py      EPUB 3 writer
nest/convert.py   Calibre wrapper
nest/feeds.py     RSS digest builder
nest/jobs.py      job queue, worker thread, cron scheduler
nest/data.py      habits / feeds / deck / briefing editors
nest/apple.py     Calendar + Reminders agenda via AppleScript
nest/db.py        SQLite (jobs, sent articles, settings)
```
