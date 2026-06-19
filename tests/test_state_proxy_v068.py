"""
v0.6.8 — Tests for the laptop's Pi-state proxy endpoint.

Covers `GET /api/sim/state-proxy` which the dashboard polls every 10s
to render the "Pi Status" panel. Verifies happy-path merging,
unreachable handling, and partial failures (state ok but persistence
unavailable).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from uacj_obd.api import create_app


class _FakeResponse:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake", request=None, response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict:
        return self._body


class _FakeClient:
    """
    Minimal httpx.Client stand-in. Routes by URL suffix to a configured
    response dict. Any URL not in the routes raises ConnectError to
    model an unreachable Pi.
    """

    def __init__(self, routes: dict[str, _FakeResponse] | Exception):
        self._routes = routes

    def __enter__(self):
        return self

    def __exit__(self, *a, **kw):
        return None

    def get(self, url: str, **kw):
        if isinstance(self._routes, Exception):
            raise self._routes
        for suffix, resp in self._routes.items():
            if url.endswith(suffix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise httpx.ConnectError(f"no route for {url}")


def _install(monkeypatch, routes):
    """Patch httpx.Client to a _FakeClient bound to the given routes."""
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _FakeClient(routes))


def test_state_proxy_unreachable(tmp_path: Path) -> None:
    """No fake httpx → real httpx fails to connect → reachable=False."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/sim/state-proxy", params={"sim_url": "http://127.0.0.1:1/"})
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["sim_url"] == "http://127.0.0.1:1/"
    assert "could not reach simulator" in body["error"]


def test_state_proxy_merges_state_and_persistence(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    _install(monkeypatch, {
        "/api/sim/state": _FakeResponse({
            "vin": "1HGCM82633A123456",
            "stored_dtcs": ["P0420"],
            "replay": {"running": True, "samples_applied": 42, "loop": True},
        }),
        "/api/sim/persistence": _FakeResponse({
            "enabled": True,
            "exists": True,
            "vin": "1HGCM82633A123456",
            "size_bytes": 512,
            "mtime": 1700000000.0,
        }),
    })
    r = client.get("/api/sim/state-proxy")
    body = r.json()
    assert body["reachable"] is True
    assert body["state"]["vin"] == "1HGCM82633A123456"
    assert body["state"]["replay"]["running"] is True
    assert body["persistence"]["enabled"] is True
    assert body["persistence"]["exists"] is True


def test_state_proxy_state_ok_persistence_fails(tmp_path: Path, monkeypatch) -> None:
    """Older Pi (pre-v0.6.7) doesn't have /persistence — proxy should
    still return reachable=True with persistence={"enabled": False}."""
    client = TestClient(create_app(data_root=tmp_path))
    _install(monkeypatch, {
        "/api/sim/state": _FakeResponse({"vin": "OLDVIN", "stored_dtcs": []}),
        "/api/sim/persistence": _FakeResponse({}, status_code=404),
    })
    r = client.get("/api/sim/state-proxy")
    body = r.json()
    assert body["reachable"] is True
    assert body["state"]["vin"] == "OLDVIN"
    assert body["persistence"] == {"enabled": False}


def test_state_proxy_state_unreachable_returns_error(tmp_path: Path, monkeypatch) -> None:
    """If /state itself fails, the whole proxy reports unreachable."""
    client = TestClient(create_app(data_root=tmp_path))
    _install(monkeypatch, httpx.ConnectError("connection refused"))
    r = client.get("/api/sim/state-proxy")
    body = r.json()
    assert body["reachable"] is False
    assert "could not reach simulator" in body["error"]


def test_state_proxy_respects_custom_sim_url(tmp_path: Path, monkeypatch) -> None:
    """sim_url query param should override the default."""
    captured: dict = {}

    class _CapturingClient:
        def __enter__(self): return self
        def __exit__(self, *a, **kw): return None
        def get(self, url, **kw):
            captured["url"] = url
            return _FakeResponse({"vin": None, "stored_dtcs": []})

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _CapturingClient())
    client = TestClient(create_app(data_root=tmp_path))
    client.get("/api/sim/state-proxy", params={"sim_url": "http://10.0.0.5:8765/"})
    assert captured["url"].startswith("http://10.0.0.5:8765/")


def test_state_proxy_strips_trailing_slash(tmp_path: Path, monkeypatch) -> None:
    """sim_url with trailing slash shouldn't double up the path."""
    seen_urls: list[str] = []

    class _CapturingClient:
        def __enter__(self): return self
        def __exit__(self, *a, **kw): return None
        def get(self, url, **kw):
            seen_urls.append(url)
            return _FakeResponse({"vin": None, "stored_dtcs": []})

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _CapturingClient())
    client = TestClient(create_app(data_root=tmp_path))
    client.get("/api/sim/state-proxy", params={"sim_url": "http://x/"})
    for u in seen_urls:
        assert "//api/" not in u
