import json

import httpx
import pytest

from hub.device import DeviceClient, DeviceOffline


class FakeDevice:
    def __init__(self, readlater_endpoint=True):
        self.files = {"/apps/readlater/queue.txt": b"https://a.test/1\tOne\n"}
        self.uploads = []
        self.readlater_endpoint = readlater_endpoint
        self.readlater_posts = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host != "good.local":
            raise httpx.ConnectError("no route")
        if path == "/api/status":
            return httpx.Response(200, json={"version": "1", "ip": "10.0.0.2", "mode": "STA", "freeHeap": 1, "device": "X4"})
        if path == "/api/files":
            return httpx.Response(200, json={"path": "/Books", "files": [
                {"name": "a.epub", "size": 10, "isDirectory": False}, {"name": "sub", "size": 0, "isDirectory": True}]})
        if path == "/download":
            p = request.url.params.get("path")
            return httpx.Response(200, content=self.files[p]) if p in self.files else httpx.Response(404)
        if path == "/upload":
            self.uploads.append((request.url.params.get("path"), request.content))
            return httpx.Response(200, text="ok")
        if path == "/mkdir":
            return httpx.Response(200)
        if path == "/api/readlater":
            if not self.readlater_endpoint:
                return httpx.Response(404)
            self.readlater_posts.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


def make(fake, urls=("http://dead.local", "http://good.local")):
    return DeviceClient(list(urls), timeout=1, transport=httpx.MockTransport(fake.handler))


def test_status_picks_first_reachable_url():
    fake = FakeDevice()
    client = make(fake)
    assert client.status()["ip"] == "10.0.0.2"
    assert client.online


def test_offline_raises():
    client = make(FakeDevice(), urls=("http://dead.local",))
    assert client.status() is None
    with pytest.raises(DeviceOffline):
        client.list_files("/")


def test_list_and_upload():
    fake = FakeDevice()
    client = make(fake)
    files = client.list_files("/Books")
    assert [f.name for f in files] == ["a.epub", "sub"] and files[1].is_dir and files[0].path == "/Books/a.epub"
    client.upload("/Books", "b.epub", b"data")
    assert fake.uploads[0][0] == "/Books" and b"b.epub" in fake.uploads[0][1]


def test_readlater_uses_endpoint():
    fake = FakeDevice()
    client = make(fake)
    assert client.readlater_add("https://b.test/2", "Two")
    assert fake.readlater_posts == [{"url": "https://b.test/2", "title": "Two"}]


def test_readlater_falls_back_to_queue_file():
    fake = FakeDevice(readlater_endpoint=False)
    client = make(fake)
    assert client.readlater_add("https://b.test/2", "Two")
    directory, body = fake.uploads[-1]
    assert directory == "/apps/readlater"
    assert b"https://a.test/1\tOne\nhttps://b.test/2\tTwo\n" in body
