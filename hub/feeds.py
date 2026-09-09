"""RSS digest: newest entries per feed, fetched as full articles, packed into EPUBs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import feedparser
import httpx

from .db import Database
from .epub import Book, Chapter, Image, build_epub, safe_filename
from .extract import Article, extract_article, make_client


@dataclass
class FeedItem:
    feed_title: str
    title: str
    url: str
    summary_html: str
    published: str


def read_subscriptions(path: Path) -> list[str]:
    if not path.exists():
        return []
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def latest_items(feed_url: str, limit: int, client: httpx.Client | None = None) -> list[FeedItem]:
    client = client or make_client()
    r = client.get(feed_url)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    feed_title = parsed.feed.get("title", feed_url)
    items = []
    for entry in parsed.entries[:limit]:
        link = entry.get("link") or ""
        if not link:
            continue
        summary = ""
        if entry.get("content"):
            summary = entry["content"][0].get("value", "")
        elif entry.get("summary"):
            summary = entry["summary"]
        published = ""
        if entry.get("published_parsed"):
            published = time.strftime("%Y-%m-%d", entry["published_parsed"])
        items.append(FeedItem(feed_title, entry.get("title", link), link, summary, published))
    return items


def article_or_summary(item: FeedItem, client: httpx.Client, **image_opts) -> Article:
    """Full page when it extracts to real text, otherwise the feed's own summary."""
    try:
        article = extract_article(item.url, client=client, **image_opts)
        if article.text_length >= 400:
            if not article.title or article.title == item.url:
                article.title = item.title
            return article
    except Exception:
        pass
    return Article(url=item.url, title=item.title, site=item.feed_title, html=item.summary_html or "<p></p>")


def build_digest(
    subscriptions: list[str],
    per_feed: int,
    mode: str,
    db: Database,
    out_dir: Path,
    *,
    client: httpx.Client | None = None,
    log=print,
    **image_opts,
) -> list[Path]:
    """Returns the EPUB files written. mode: "digest" (one book) or "articles" (one per article)."""
    client = client or make_client()
    out_dir.mkdir(parents=True, exist_ok=True)
    collected: list[tuple[FeedItem, Article]] = []
    for feed_url in subscriptions:
        try:
            items = latest_items(feed_url, per_feed, client)
        except Exception as e:
            log(f"feed failed: {feed_url}: {e}")
            continue
        for item in items:
            if db.was_sent(item.url):
                continue
            article = article_or_summary(item, client, **image_opts)
            collected.append((item, article))
            log(f"fetched: {item.title}")

    written: list[Path] = []
    if not collected:
        return written

    if mode == "articles":
        for item, article in collected:
            book = Book(title=article.title, author=item.feed_title, chapters=[Chapter(article.title, article.html, item.url)],
                        images=article.images)
            path = out_dir / safe_filename(f"{item.feed_title} - {article.title}")
            path.write_bytes(build_epub(book))
            db.mark_sent(item.url, article.title)
            written.append(path)
        return written

    # One digest: images need unique names across chapters.
    chapters: list[Chapter] = []
    images: list[Image] = []
    for index, (item, article) in enumerate(collected, 1):
        html_body = article.html
        for image in article.images:
            new_name = f"c{index}-{image.name}"
            html_body = html_body.replace(f"images/{image.name}", f"images/{new_name}")
            images.append(Image(name=new_name, data=image.data, mime=image.mime))
        heading = f"{article.title}"
        chapters.append(Chapter(heading, f"<p><em>{item.feed_title}{' - ' + item.published if item.published else ''}</em></p>" + html_body, item.url))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    book = Book(title=f"Digest {stamp}", author="Teleport Hub", chapters=chapters, images=images,
                description=f"{len(chapters)} articles from {len(subscriptions)} feeds")
    path = out_dir / safe_filename(f"Digest {stamp}")
    path.write_bytes(build_epub(book))
    for item, article in collected:
        db.mark_sent(item.url, article.title)
    written.append(path)
    return written
