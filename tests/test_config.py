from nest.config import Config


def test_destination_defaults_and_overrides():
    c = Config(library_folders=["/Books", "/Papers"], library_defaults={"url": "Articles/"})
    assert c.library_folders == ["/Books", "/Papers"]
    assert c.destination("url") == "/Articles"
    assert c.destination("pdf") == "/Papers"
    assert c.destination("file") == "/Books"
    assert c.destination("digest", "  /Digests/2026 ") == "/Digests/2026"
    assert c.destination("digest", "") == "/Digests"
    assert c.destination("unknown") == "/Books"
