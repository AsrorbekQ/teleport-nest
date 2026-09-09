# Teleport Hub

A small local web app that feeds a [Teleport](https://github.com/AsrorbekQ/teleport) e-reader (Xteink X4 running the Teleport / CrossPoint firmware) from your computer:

- **Web page → EPUB.** Paste URLs; each becomes a clean EPUB with grayscale, downscaled images and is sent to the device's Books folder.
- **Document → EPUB.** Drop PDF, DOCX, MOBI, HTML, Markdown and more; Calibre converts them with an e-ink profile.
- **RSS digest.** Newest articles from your feeds, fetched in full, packed into one digest book or one book per article. Already-sent articles are skipped. Can run on a schedule.
- **Read Later.** Send links to the device's Read Later queue; the reader fetches them itself.
- **Device data.** Edit habits, RSS subscriptions and the sleep-briefing config, upload an Anki `.apkg` to rebuild the flashcard deck, browse and delete books on the device.
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

Set `server.host = "0.0.0.0"` in `config.toml` to use the hub from your phone on the same Wi-Fi.

## How the device receives files

The firmware only runs its web server inside the **File Transfer** app. Open it on the reader, connect to your Wi-Fi, and the hub finds the device at `http://crosspoint.local` (or the IP shown on the reader's screen; add it under `[device] urls`). The status bar turns green and queued jobs flush. Close File Transfer when done; the hub keeps queuing.

Files land in `/Books`. Data pushes go to `/apps/rss/subscriptions.txt`, `/apps/habits/habits.bin`, `/apps/flashcards/gre.deck`, `/apps/briefing/config.txt` and `/apps/readlater/queue.txt`.

## Config keys

See `config.example.toml`. Sections: `[device]` (urls, timeout, books_dir), `[paths]` (calibre, firmware_repo, data files, optional watch_dir), `[digest]` (per_feed, mode), `[images]` (max_width, quality, max_images), `[server]` (host, port), and `[[schedules]]` entries with a cron expression and a job (`digest` or `flush`).

`config.toml` and `data/` (SQLite database, generated EPUBs, uploads) are gitignored; nothing personal is committed.

## Tests

```sh
.venv/bin/python -m pytest
```

Covers the EPUB writer, article extraction and image processing on a fixture page, and the device client against a fake device, including the Read Later fallback for firmware without the `/api/readlater` endpoint.

## Layout

```
hub/app.py       FastAPI routes and the single-page UI
hub/device.py    client for the device's File Transfer API
hub/extract.py   fetch + readability + image processing
hub/epub.py      EPUB 3 writer
hub/convert.py   Calibre wrapper
hub/feeds.py     RSS digest builder
hub/jobs.py      job queue, worker thread, cron scheduler
hub/data.py      habits / feeds / deck / briefing editors
hub/db.py        SQLite (jobs, sent articles, settings)
```
