import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import pytest

TOKEN = "test-token"

FILES = {
    "disk:/Photos/a.jpg": b"A" * 1000,
    "disk:/Photos/b.jpg": b"B" * 2500,
    "disk:/Photos/2026/c.jpg": b"C" * 70000,
    "disk:/Docs/readme.txt": b"hello",
}
DIRS = {"disk:/", "disk:/Photos", "disk:/Photos/2026", "disk:/Docs"}


def parent(path):
    head = path.rsplit("/", 1)[0]
    return "disk:/" if head == "disk:" else head


def entry(path):
    name = path.rsplit("/", 1)[1]
    if path in DIRS:
        return {"name": name, "path": path, "type": "dir"}
    body = FILES[path]
    return {
        "name": name,
        "path": path,
        "type": "file",
        "size": len(body),
        "md5": hashlib.md5(body).hexdigest(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path.startswith("/blob/"):
            path = unquote(url.path[len("/blob/"):])
            self.server.hits.append(path)
            body = FILES[path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.headers.get("Authorization") != f"OAuth {TOKEN}":
            return self.reply(401, {})
        if url.path == "/v1/disk":
            return self.reply(200, {"user": {"login": "tester"}})
        path = query.get("path", "")
        if url.path == "/v1/disk/resources":
            if path in FILES:
                return self.reply(200, entry(path))
            if path not in DIRS:
                return self.reply(404, {})
            children = sorted(
                (entry(p) for p in [*DIRS, *FILES] if p != path and parent(p) == path),
                key=lambda item: item["name"],
            )
            limit = int(query.get("limit", 20))
            offset = int(query.get("offset", 0))
            payload = entry(path)
            payload["_embedded"] = {
                "items": children[offset:offset + limit],
                "total": len(children),
            }
            return self.reply(200, payload)
        if url.path == "/v1/disk/resources/download":
            if path not in FILES:
                return self.reply(404, {})
            href = f"http://127.0.0.1:{self.server.server_port}/blob/{quote(path, safe='')}"
            return self.reply(200, {"href": href})
        self.reply(404, {})


@pytest.fixture
def api(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("YADISK_API_URL", f"http://127.0.0.1:{server.server_port}/v1/disk")
    monkeypatch.setenv("YADISK_TOKEN", TOKEN)
    yield server
    server.shutdown()
    server.server_close()
