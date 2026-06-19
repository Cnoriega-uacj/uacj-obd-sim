"""
v0.6.6 — Tests for the operational safeguards.

Covers `uacj_obd.safeguards` (pure helpers) and the dashboard endpoints
that wire them in: `/api/health` (version), `/api/disk`, and
`/api/sim/version-check`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from uacj_obd import __version__
from uacj_obd.api import create_app
from uacj_obd.safeguards import (
    LOW_FREE_DISK_WARN_BYTES,
    MAX_SESSION_DURATION_S,
    MIN_FREE_DISK_BYTES,
    check_disk_space,
    compare_versions,
    normalize_session_duration,
)


# ---------------------------------------------------------------------------
# normalize_session_duration
# ---------------------------------------------------------------------------

def test_normalize_duration_none_returns_none() -> None:
    assert normalize_session_duration(None) is None


def test_normalize_duration_negative_returns_none() -> None:
    """Negative duration → caller meant indefinite."""
    assert normalize_session_duration(-1) is None


def test_normalize_duration_zero_returns_none() -> None:
    assert normalize_session_duration(0) is None


def test_normalize_duration_below_cap_passes_through() -> None:
    assert normalize_session_duration(30.0) == 30.0
    assert normalize_session_duration(60.0) == 60.0
    assert normalize_session_duration(1800.0) == 1800.0


def test_normalize_duration_at_cap_passes_through() -> None:
    assert normalize_session_duration(MAX_SESSION_DURATION_S) == MAX_SESSION_DURATION_S


def test_normalize_duration_above_cap_clamps() -> None:
    """Any value above the cap gets clamped down."""
    assert normalize_session_duration(MAX_SESSION_DURATION_S + 1) == MAX_SESSION_DURATION_S
    assert normalize_session_duration(99999.0) == MAX_SESSION_DURATION_S


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------

def test_check_disk_space_returns_status(tmp_path: Path) -> None:
    status = check_disk_space(tmp_path)
    assert status.total_bytes > 0
    assert status.free_bytes > 0


def test_check_disk_space_on_nonexistent_path_walks_up(tmp_path: Path) -> None:
    """If the data_root doesn't exist yet, the helper should walk up to
    the first existing parent rather than crashing."""
    target = tmp_path / "deeply" / "nested" / "data_does_not_exist"
    status = check_disk_space(target)
    assert status.total_bytes > 0  # walked up to tmp_path which exists


def test_check_disk_space_refuses_when_below_minimum() -> None:
    """Mock shutil.disk_usage to return below-minimum free bytes."""
    fake_usage = type("U", (), {
        "total": 1000 * MIN_FREE_DISK_BYTES,
        "used": 999 * MIN_FREE_DISK_BYTES,
        "free": MIN_FREE_DISK_BYTES - 1,
    })()
    with patch("uacj_obd.safeguards.shutil.disk_usage", return_value=fake_usage):
        status = check_disk_space("/tmp")
    assert status.ok is False
    assert status.warn is True
    assert "refusing to start" in status.message


def test_check_disk_space_warns_when_below_low_threshold() -> None:
    """Free space between MIN and LOW thresholds → ok but warn."""
    free_bytes = (MIN_FREE_DISK_BYTES + LOW_FREE_DISK_WARN_BYTES) // 2
    fake_usage = type("U", (), {
        "total": 10 * LOW_FREE_DISK_WARN_BYTES,
        "used": 10 * LOW_FREE_DISK_WARN_BYTES - free_bytes,
        "free": free_bytes,
    })()
    with patch("uacj_obd.safeguards.shutil.disk_usage", return_value=fake_usage):
        status = check_disk_space("/tmp")
    assert status.ok is True
    assert status.warn is True
    assert "low disk space" in status.message


def test_check_disk_space_clean_when_plenty_free() -> None:
    fake_usage = type("U", (), {
        "total": 100 * LOW_FREE_DISK_WARN_BYTES,
        "used": 0,
        "free": 100 * LOW_FREE_DISK_WARN_BYTES,
    })()
    with patch("uacj_obd.safeguards.shutil.disk_usage", return_value=fake_usage):
        status = check_disk_space("/tmp")
    assert status.ok is True
    assert status.warn is False


# ---------------------------------------------------------------------------
# compare_versions
# ---------------------------------------------------------------------------

def test_compare_versions_exact_match() -> None:
    assert compare_versions("0.6.6", "0.6.6") == "match"


def test_compare_versions_pi_older() -> None:
    verdict = compare_versions("0.6.6", "0.6.5")
    assert "Pi is older" in verdict


def test_compare_versions_pi_newer() -> None:
    verdict = compare_versions("0.6.5", "0.6.6")
    assert "Pi is newer" in verdict


def test_compare_versions_minor_jump() -> None:
    verdict = compare_versions("0.6.6", "0.5.4")
    assert "Pi is older" in verdict


def test_compare_versions_handles_empty_strings() -> None:
    assert compare_versions("", "0.6.6") == "unknown"
    assert compare_versions("0.6.6", "") == "unknown"
    assert compare_versions("", "") == "unknown"


def test_compare_versions_handles_none() -> None:
    assert compare_versions(None, "0.6.6") == "unknown"  # type: ignore[arg-type]
    assert compare_versions("0.6.6", None) == "unknown"  # type: ignore[arg-type]


def test_compare_versions_handles_dev_suffix() -> None:
    """v0.7.0-dev should compare as (0, 7, 0) — stopping at the dash."""
    verdict = compare_versions("0.7.0-dev", "0.6.6")
    assert "Pi is older" in verdict


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_health_endpoint_reports_version(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == __version__


def test_disk_endpoint_returns_status(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/disk")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "warn" in body
    assert "free_bytes" in body
    assert "total_bytes" in body


def test_sim_version_check_unreachable(tmp_path: Path) -> None:
    """When the sim_url is unreachable, the endpoint returns 200 with
    verdict='unreachable' rather than 502 — the dashboard needs to be
    able to render the info."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/sim/version-check", params={"sim_url": "http://127.0.0.1:1/"})
    assert r.status_code == 200
    body = r.json()
    assert body["laptop_version"] == __version__
    assert body["pi_version"] is None
    assert body["verdict"] == "unreachable"


def test_sim_version_check_match_via_monkeypatch(tmp_path: Path, monkeypatch) -> None:
    """When the Pi reports the same version, verdict='match'."""
    client = TestClient(create_app(data_root=tmp_path))

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"ok": True, "version": __version__, "vin": None, "stored_dtcs": []}

    class _FakeClient:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a, **kw): ...
        def get(self, url, **kw) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.get("/api/sim/version-check")
    assert r.json()["verdict"] == "match"


def test_sim_version_check_mismatch_via_monkeypatch(tmp_path: Path, monkeypatch) -> None:
    """When the Pi reports an older version, verdict mentions
    'Pi is older' and gives the update command."""
    client = TestClient(create_app(data_root=tmp_path))

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"ok": True, "version": "0.4.10", "vin": None, "stored_dtcs": []}

    class _FakeClient:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a, **kw): ...
        def get(self, url, **kw) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.get("/api/sim/version-check")
    body = r.json()
    assert "older" in body["verdict"]
    assert "git pull" in body["verdict"]


def test_start_session_clamps_overlong_duration(tmp_path: Path) -> None:
    """A request with duration_s above the cap should still succeed but
    the response should report the clamped value the loop will use."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/sessions/start", json={
        "adapter": "mock",
        "duration_s": 99999.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["applied_duration_s"] == MAX_SESSION_DURATION_S
    # Clean up — stop the session immediately
    client.post("/api/sessions/stop")


def test_start_session_refuses_when_disk_full(tmp_path: Path) -> None:
    """If the data_root has less than MIN_FREE_DISK_BYTES, /sessions/start
    returns 507 Insufficient Storage."""
    fake_usage = type("U", (), {
        "total": 10 * MIN_FREE_DISK_BYTES,
        "used": 9 * MIN_FREE_DISK_BYTES,
        "free": MIN_FREE_DISK_BYTES - 1,
    })()
    client = TestClient(create_app(data_root=tmp_path))
    with patch("uacj_obd.safeguards.shutil.disk_usage", return_value=fake_usage):
        r = client.post("/api/sessions/start", json={
            "adapter": "mock",
            "duration_s": 1.0,
        })
    assert r.status_code == 507
    assert "refusing" in r.text.lower()


def test_pi_simulator_health_includes_version(tmp_path: Path) -> None:
    """The Pi-side simulator server must also report its version so
    the laptop's version-check has something to compare against."""
    from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
    from uacj_obd.simulator.server import make_simulator_server
    ecu = EcuEmulator(ScenarioState())
    app = make_simulator_server(ecu)
    client = TestClient(app)
    r = client.get("/api/sim/health")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == __version__
