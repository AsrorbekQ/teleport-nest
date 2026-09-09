import zipfile
import io

from lxml import etree

from hub.epub import Book, Chapter, Image, build_epub, fragment_to_xhtml, safe_filename


def test_epub_layout_is_valid():
    book = Book(title="Test & Book", author="Hub", chapters=[Chapter("One", "<p>Hello <b>world<br>again</p>", "https://x.test/a")],
                images=[Image("img1.jpg", b"\xff\xd8\xff\xd9")])
    data = build_epub(book)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert names[0] == "mimetype"
    assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/epub+zip"
    assert "META-INF/container.xml" in names and "OEBPS/content.opf" in names and "OEBPS/nav.xhtml" in names
    opf = etree.fromstring(zf.read("OEBPS/content.opf"))
    ns = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    assert opf.find(".//dc:title", ns).text == "Test & Book"
    hrefs = {i.get("href") for i in opf.findall(".//o:item", ns)}
    assert {"chapter1.xhtml", "nav.xhtml", "images/img1.jpg", "style.css"} <= hrefs
    # every chapter is well-formed XHTML
    chapter = etree.fromstring(zf.read("OEBPS/chapter1.xhtml"))
    assert chapter.tag.endswith("html")
    assert "x.test/a" in zf.read("OEBPS/chapter1.xhtml").decode()


def test_fragment_to_xhtml_closes_tags_and_drops_scripts():
    out = fragment_to_xhtml('<p>a<br>b<script>x()</script><img src="i.jpg" onclick="evil()"></p>')
    etree.fromstring(f"<div>{out}</div>")
    assert "<script" not in out and "onclick" not in out and "<br/>" in out


def test_safe_filename():
    assert safe_filename('A/B: "C"?') == "A B C.epub"
    assert safe_filename("") == "untitled.epub"
    assert len(safe_filename("x" * 200)) <= 85
