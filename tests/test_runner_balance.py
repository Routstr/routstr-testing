"""Tests for the routstrd balance fetcher used by the orchestrator."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from runner.balance import fetch_total_sats


class _Handler(BaseHTTPRequestHandler):
    payload: bytes = b""
    status: int = 200

    def do_GET(self):  # noqa: N802 — stdlib signature
        if self.path != "/keys/balance":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args, **kwargs):  # silence test noise
        return


@pytest.fixture()
def fake_routstrd():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_returns_total_sats(fake_routstrd):
    server, url = fake_routstrd
    _Handler.status = 200
    _Handler.payload = json.dumps(
        {"output": {"keys": [], "total": 4242, "unit": "sat", "apikeysCalled": 0}}
    ).encode()
    assert fetch_total_sats(url) == 4242


def test_returns_none_on_404(fake_routstrd):
    server, url = fake_routstrd
    _Handler.status = 404
    _Handler.payload = b'{"error": "nope"}'
    assert fetch_total_sats(url) is None


def test_returns_none_on_unparseable_payload(fake_routstrd):
    server, url = fake_routstrd
    _Handler.status = 200
    _Handler.payload = b"not json"
    assert fetch_total_sats(url) is None


def test_returns_none_when_unreachable():
    # Port 1 should refuse — never used.
    assert fetch_total_sats("http://127.0.0.1:1") is None
