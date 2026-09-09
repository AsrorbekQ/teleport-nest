"""Fetch a web page and reduce it to a clean article with e-ink friendly images."""

from __future__ import annotations

import io
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lhtml
from PIL import Image as PILImage
from readability import Document

from .epub import Image

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15 TeleportHub/1.0"
)
MAX_IMAGE_BYTES = 4 * 1024 * 1024


@dataclass
class Article:
    url: str
    title: str
    site: str
    html: str  # cleaned body fragment with image srcs rewritten to images/<name>
    images: list[Image] = field(default_factory=list)
    text_length: int = 0


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        follow_redirects=True,
        timeout=30,
        transport=transport,
    )


def fetch_html(url: str, client: httpx.Client | None = None) -> str:
    client = client or make_client()
    r = client.get(url)
    r.raise_for_status()
    return r.text


def process_image(data: bytes, max_width: int, quality: int) -> bytes | None:
    """Grayscale, downscale, JPEG. Returns None for images that are not worth keeping."""
    try:
        img = PILImage.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.width < 40 or img.height < 40:
        return None  # icons, spacers, tracking pixels
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = PILImage.new("RGBA", img.size, (255, 255, 255, 255))
        background.alpha_composite(img)
        img = background
    img = img.convert("L")
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))), PILImage.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def extract_article(
    url: str,
    html_text: str | None = None,
    *,
    client: httpx.Client | None = None,
    max_images: int = 20,
    image_max_width: int = 480,
    image_quality: int = 70,
) -> Article:
    client = client or make_client()
    if html_text is None:
        html_text = fetch_html(url, client)

    doc = Document(html_text, url=url)
    title = (doc.short_title() or doc.title() or url).strip()
    summary = doc.summary(html_partial=True)
    root = lhtml.fromstring(f"<div>{summary}</div>")

    images: list[Image] = []
    seen: dict[str, str] = {}
    for img in root.xpath(".//img"):
        src = img.get("src") or ""
        if not src and img.get("srcset"):
            src = img.get("srcset").split(",")[0].strip().split(" ")[0]
        if not src or src.startswith("data:"):
            img.drop_tree()
            continue
        absolute = urljoin(url, src)
        if absolute in seen:
            img.set("src", seen[absolute])
            continue
        if len(images) >= max_images:
            img.drop_tree()
            continue
        try:
            r = client.get(absolute)
            if r.status_code != 200 or len(r.content) > MAX_IMAGE_BYTES:
                raise ValueError("bad image response")
            processed = process_image(r.content, image_max_width, image_quality)
        except Exception:
            processed = None
        if processed is None:
            img.drop_tree()
            continue
        name = f"img{len(images) + 1}.jpg"
        images.append(Image(name=name, data=processed))
        seen[absolute] = f"images/{name}"
        img.set("src", f"images/{name}")
        img.set("alt", img.get("alt", ""))
        for attr in ("srcset", "sizes", "width", "height", "loading"):
            if attr in img.attrib:
                del img.attrib[attr]

    # Absolute links so they still make sense when read offline.
    for a in root.xpath(".//a[@href]"):
        a.set("href", urljoin(url, a.get("href")))

    body = "".join(lhtml.tostring(child, encoding="unicode") for child in root)
    body = re.sub(r"\s+\n", "\n", body)
    site = urlparse(url).netloc.removeprefix("www.")
    text_length = len(root.text_content().strip())
    return Article(url=url, title=title, site=site, html=body, images=images, text_length=text_length)


def guess_title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = posixpath.basename(path) or urlparse(url).netloc
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().title() or url
