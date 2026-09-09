"""Minimal EPUB 3 writer: one or more XHTML chapters plus embedded images."""

from __future__ import annotations

import html
import io
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from lxml import etree, html as lhtml


@dataclass
class Chapter:
    title: str
    body_html: str  # HTML fragment (already cleaned); converted to XHTML here
    source_url: str = ""


@dataclass
class Image:
    name: str  # file name inside OEBPS/images/
    data: bytes
    mime: str = "image/jpeg"


@dataclass
class Book:
    title: str
    author: str = "Teleport Hub"
    language: str = "en"
    chapters: list[Chapter] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    description: str = ""


CSS = """
body { font-family: serif; line-height: 1.4; margin: 0 4%; }
h1 { font-size: 1.5em; margin: 0.6em 0; }
h2, h3 { font-size: 1.2em; }
img { max-width: 100%; height: auto; }
p.source { font-size: 0.8em; color: #444; }
pre, code { font-family: monospace; font-size: 0.85em; white-space: pre-wrap; }
blockquote { margin: 0.8em 1.2em; font-style: italic; }
"""


def _safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-") or "id"


def fragment_to_xhtml(fragment: str) -> str:
    """Parses tolerant HTML and re-serialises it as well-formed XHTML body content."""
    if not fragment.strip():
        return "<p></p>"
    root = lhtml.fromstring(f"<div>{fragment}</div>")
    for bad in root.xpath(".//script|.//style|.//iframe|.//noscript|.//form|.//button|.//svg"):
        bad.drop_tree()
    for el in root.iter():
        # XHTML rejects attributes that HTML tolerates; keep the useful ones only.
        for attr in list(el.attrib):
            if attr.startswith("on") or attr in ("style", "class", "id", "srcset", "sizes", "loading", "decoding"):
                del el.attrib[attr]
    inner = b"".join(etree.tostring(child, method="xml", encoding="utf-8") for child in root)
    text = (root.text or "")
    return html.escape(text) + inner.decode("utf-8")


def _chapter_xhtml(chapter: Chapter, index: int) -> bytes:
    body = fragment_to_xhtml(chapter.body_html)
    source = (
        f'<p class="source">Source: <a href="{html.escape(chapter.source_url, quote=True)}">'
        f"{html.escape(chapter.source_url)}</a></p>"
        if chapter.source_url
        else ""
    )
    doc = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html.escape(chapter.title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body><h1>{html.escape(chapter.title)}</h1>
{body}
{source}
</body></html>
"""
    # Validate well-formedness now so a broken chapter fails loudly instead of on the device.
    etree.fromstring(doc.encode("utf-8"))
    return doc.encode("utf-8")


def build_epub(book: Book) -> bytes:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chapters = book.chapters or [Chapter(book.title, "<p></p>")]

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    files: list[tuple[str, bytes]] = []
    nav_items = []
    for i, chapter in enumerate(chapters, 1):
        name = f"chapter{i}.xhtml"
        files.append((f"OEBPS/{name}", _chapter_xhtml(chapter, i)))
        manifest.append(f'<item id="ch{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i}"/>')
        nav_items.append(f'<li><a href="{name}">{html.escape(chapter.title)}</a></li>')
    for image in book.images:
        files.append((f"OEBPS/images/{image.name}", image.data))
        manifest.append(f'<item id="img-{_safe_id(image.name)}" href="images/{image.name}" media-type="{image.mime}"/>')

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{book.language}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{book_id}</dc:identifier>
<dc:title>{html.escape(book.title)}</dc:title>
<dc:creator>{html.escape(book.author)}</dc:creator>
<dc:language>{book.language}</dc:language>
<dc:description>{html.escape(book.description)}</dc:description>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine>
{chr(10).join(spine)}
</spine>
</package>
"""
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html.escape(book.title)}</title></head>
<body><nav epub:type="toc" id="toc"><h1>{html.escape(book.title)}</h1><ol>
{chr(10).join(nav_items)}
</ol></nav></body></html>
"""
    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files:
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def safe_filename(title: str, ext: str = ".epub", max_len: int = 80) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "untitled"
    return name[:max_len].rstrip() + ext
