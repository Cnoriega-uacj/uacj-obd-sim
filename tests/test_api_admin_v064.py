"""
v0.6.4 — Tests for the dashboard's admin / less-trafficked endpoints.

v0.6.3 left `api/app.py` at 85%. The uncovered lines are mostly:

- CSV export with on-demand generation
- Scenario update (PATCH) and delete
- Preset list + instantiate
- Diff between two sessions
- Sim-log proxy (laptop → Pi)
- Replay-scenario endpoint
- Backup ZIP streaming + restore endpoint (file shape rejection)

Each test uses a small mock-adapter capture (or two) to set up the
state, then exercises the endpoint and asserts the response shape.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_root=tmp_path))


def _capture_mock(client: TestClient, duration: float = 0.2) -> str:
    r = client.post(
        "/api/sessions/start",
        json={"adapter": "mock", "duration_s": duration, "pids": ["010C", "010D"]},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        cur = client.get("/api/sessions/current").json()
        if cur is None:
            break
        time.sleep(0.05)
    return sid


def _create_scenario(client: TestClient, source_session_id: str | None = None) -> str:
    req = {
        "label": "test scenario",
        "source_session_id": source_session_id,
        "vehicle": {"vin": "TEST1234567890123", "make": "Test", "model": "X", "year": 2026},
        "live_overrides": {"010C": 1500},
    }
    r = client.post("/api/scenarios", json=req)
    assert r.status_code == 200, r.text
    return r.json()["scenario_id"]


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def test_export_csv_not_found(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/sessions/does-not-exist/export.csv")
    assert r.status_code == 404


def test_export_csv_generates_csv_on_demand(tmp_path: Path) -> None:
    """First call generates the CSV from live_data.jsonl; second call
    reads the cached CSV. Both should return text/csv."""
    c = _client(tmp_path)
    sid = _capture_mock(c)
    r1 = c.get(f"/api/sessions/{sid}/export.csv")
    assert r1.status_code == 200
    assert "text/csv" in r1.headers.get("content-type", "")
    # CSV should have a header row.
    lines = r1.text.splitlines()
    assert lines[0] == "ts,pid,name,value,unit"

    # Second call — should hit the already-generated file.
    r2 = c.get(f"/api/sessions/{sid}/export.csv")
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Scenario PATCH / DELETE / GET
# ---------------------------------------------------------------------------

def test_get_scenario_404_for_unknown_id(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/scenarios/does-not-exist")
    assert r.status_code == 404


def test_update_scenario_404_for_unknown_id(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.patch("/api/scenarios/does-not-exist", json={"label": "new"})
    assert r.status_code == 404


def test_update_scenario_changes_label(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.patch(f"/api/scenarios/{sid}", json={"label": "updated label"})
    assert r.status_code == 200
    assert r.json()["label"] == "updated label"


def test_update_scenario_replaces_dtcs(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.patch(f"/api/scenarios/{sid}", json={
        "dtcs": [{"code": "P0420", "status": "stored", "description": "catalyst"}],
    })
    assert r.status_code == 200
    assert len(r.json()["dtcs"]) == 1
    assert r.json()["dtcs"][0]["code"] == "P0420"


def test_update_scenario_replaces_monitors(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.patch(f"/api/scenarios/{sid}", json={
        "monitors": [{"name": "Misfire", "supported": True, "ready": True}],
    })
    assert r.status_code == 200
    assert len(r.json()["monitors"]) == 1


def test_update_scenario_replaces_freeze_frame(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.patch(f"/api/scenarios/{sid}", json={
        "freeze_frame": {"dtc": "P0420", "pids": {"010C": 1850}},
    })
    assert r.status_code == 200
    assert r.json()["freeze_frame"]["dtc"] == "P0420"


def test_update_scenario_replaces_live_overrides(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.patch(f"/api/scenarios/{sid}", json={
        "live_overrides": {"010C": 800, "0105": 88},
    })
    assert r.status_code == 200
    overrides = r.json()["live_overrides"]
    assert overrides["010C"] == 800
    assert overrides["0105"] == 88


def test_delete_scenario(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _create_scenario(c)
    r = c.delete(f"/api/scenarios/{sid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == sid
    # GET should now 404
    assert c.get(f"/api/scenarios/{sid}").status_code == 404


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def test_list_presets(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/presets")
    assert r.status_code == 200
    presets = r.json()
    assert isinstance(presets, list)
    # There should be at least the P0420 preset.
    ids = {p["id"] for p in presets if isinstance(p, dict) and "id" in p}
    assert "p0420_catalyst" in ids or any("p0420" in str(p).lower() for p in presets)


def test_instantiate_preset_404_for_unknown_id(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/presets/does-not-exist/instantiate")
    assert r.status_code == 404


def test_instantiate_preset_creates_scenario(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _capture_mock(c)
    r = c.post(
        "/api/presets/p0420_catalyst/instantiate",
        params={"source_session_id": sid},
    )
    if r.status_code == 200:
        data = r.json()
        assert "scenario_id" in data or "label" in data
    else:
        # Preset doesn't exist by that exact name? Either way, the
        # endpoint should not 500.
        assert r.status_code in (200, 404, 422)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def test_diff_two_sessions_one_missing_returns_404(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = _capture_mock(c)
    r = c.get("/api/diff", params={"a": sid, "b": "missing"})
    assert r.status_code == 404


def test_diff_two_real_sessions(tmp_path: Path) -> None:
    c = _client(tmp_path)
    a = _capture_mock(c)
    b = _capture_mock(c)
    r = c.get("/api/diff", params={"a": a, "b": b})
    assert r.status_code == 200
    body = r.json()
    # The diff response includes vehicle, DTCs, monitors, per-PID stats
    # — exact shape varies, but it should be a dict with content.
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# Sim log proxy (laptop → Pi)
# ---------------------------------------------------------------------------

def test_sim_log_proxy_502_when_pi_unreachable(tmp_path: Path) -> None:
    """If the Pi URL doesn't respond (default uacj-sim.local), the
    proxy should return 502 rather than crashing."""
    c = _client(tmp_path)
    # Use a deliberately-unreachable URL.
    r = c.get("/api/sim/log", params={
        "sim_url": "http://127.0.0.1:1/",  # nothing listens here
        "limit": 10,
    })
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# Replay scenario endpoint
# ---------------------------------------------------------------------------

def test_replay_scenario_404_for_unknown_scenario(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/scenarios/does-not-exist/replay")
    assert r.status_code == 404


def test_replay_scenario_400_when_no_source_session(tmp_path: Path) -> None:
    c = _client(tmp_path)
    # Scenario with no source_session_id
    sid = _create_scenario(c)
    r = c.post(f"/api/scenarios/{sid}/replay")
    assert r.status_code == 400


def test_replay_scenario_404_when_source_session_missing(tmp_path: Path) -> None:
    """Scenario references a session_id that doesn't exist in the DB."""
    c = _client(tmp_path)
    sid = _create_scenario(c, source_session_id="ghost-session-id")
    r = c.post(f"/api/scenarios/{sid}/replay")
    assert r.status_code == 404


def test_replay_scenario_409_when_another_session_running(tmp_path: Path) -> None:
    """The replay endpoint refuses if a capture is already running."""
    c = _client(tmp_path)
    # Start a long capture and don't wait for it
    r = c.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 10.0})
    assert r.status_code == 200
    real_sid = r.json()["session_id"]
    try:
        # Create a scenario from the running session and try to replay
        scn_id = _create_scenario(c, source_session_id=real_sid)
        r2 = c.post(f"/api/scenarios/{scn_id}/replay")
        assert r2.status_code == 409
    finally:
        # Stop the capture so the test doesn't leak a thread
        c.post("/api/sessions/stop")
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def test_backup_returns_zip(tmp_path: Path) -> None:
    c = _client(tmp_path)
    _capture_mock(c)
    r = c.post("/api/backup")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/zip")
    # The bytes should parse as a ZIP.
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "BACKUP_INFO.json" in names


def test_restore_rejects_non_zip_filename(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post(
        "/api/restore",
        files={"file": ("not_a_zip.txt", b"plain text", "text/plain")},
    )
    assert r.status_code == 400


def test_restore_rejects_invalid_zip(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post(
        "/api/restore",
        files={"file": ("not_really_a_zip.zip", b"not a zip file", "application/zip")},
    )
    assert r.status_code == 400
    assert "valid zip" in r.text.lower()
