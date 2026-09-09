import io
from pathlib import Path

import httpx
from PIL import Image

from hub.extract import extract_article

FIXTURE = Path(__file__).parent / "fixtures" / "article.html"


def png_bytes(w=800, h=400):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/images/hero.png":
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
    return httpx.Response(200, text=FIXTURE.read_text(), headers={"content-type": "text/html"})


def test_extracts_article_and_images():
    client = httpx.Client(transport=httpx.MockTransport(handler))
    article = extract_article("https://blog.test/posts/slow-reading", client=client, image_max_width=480)
    assert article.title.startswith("Why Slow Reading Wins")
    assert article.site == "blog.test"
    assert "attention is a finite resource" in article.html
    assert "newsletter" not in article.html and "<script" not in article.html
    assert len(article.images) == 1 and article.images[0].name == "img1.jpg"
    assert 'src="images/img1.jpg"' in article.html
    assert 'href="https://blog.test/related"' in article.html
    img = Image.open(io.BytesIO(article.images[0].data))
    assert img.mode == "L" and img.width == 480
