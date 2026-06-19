"""
v0.6.10 — Tests for scenario coverage reporting.

`uacj_obd.coverage.compute_coverage` computes what the simulator
will actually answer for a scenario payload — used by the dashboard
to preview coverage before pushing, and to surface the "captured
N mode-01 PIDs but only M have encoders" gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app
from uacj_obd.coverage import compute_coverage
from uacj_obd.pids import load_default_registry


def test_coverage_empty_payload() -> None:
    """Empty payload → zero everywhere, one note flagging the empty
    mode-01 bitmap."""
    reg = load_default_registry()
    report = compute_coverage({}, reg)
    assert report.total_pids == 0
    assert report.mode01_total == 0
    assert report.mode01_answerable == 0
    assert any("no mode-01 PIDs" in n for n in report.notes)


def test_coverage_counts_baseline_pids() -> None:
    reg = load_default_registry()
    payload = {
        "live_baseline": {
            "010C": 800,    # RPM (encodable)
            "010D": 30,     # Speed (encodable)
            "0104": 23,     # Load (encodable)
        },
    }
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 3
    assert report.mode01_answerable == 3
    assert report.mode01_unanswered == 0


def test_coverage_flags_unanswerable_pids() -> None:
    """A baseline that includes a PID with no encoder should report
    it as not-answerable."""
    reg = load_default_registry()
    payload = {
        "live_baseline": {
            "010C": 800,
            "01FF": 0,  # nonsense PID, no encoder
        },
    }
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 2
    assert report.mode01_answerable == 1
    assert report.mode01_unanswered == 1
    assert any("no encoder in this simulator" in n for n in report.notes)


def test_coverage_overrides_count_alongside_baseline() -> None:
    """live_overrides should merge with baseline for the count."""
    reg = load_default_registry()
    payload = {
        "live_baseline": {"010C": 800},
        "live_overrides": {"0105": 90},  # ECT
    }
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 2
    assert report.mode01_answerable == 2


def test_coverage_timeseries_pids_count() -> None:
    """PIDs that only appear in live_timeseries should also count."""
    reg = load_default_registry()
    payload = {
        "live_baseline": {},
        "live_timeseries": [
            {"t": 0, "pid": "010C", "value": 800},
            {"t": 1, "pid": "010D", "value": 30},
        ],
    }
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 2
    assert report.mode01_answerable == 2


def test_coverage_keys_normalised_to_upper() -> None:
    """Lowercase PID keys in the payload must still be recognised."""
    reg = load_default_registry()
    payload = {"live_baseline": {"010c": 800, "010d": 30}}
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 2
    assert report.mode01_answerable == 2


def test_coverage_mode09_fields_detected() -> None:
    """The vehicle block should drive the mode_09 presence list."""
    reg = load_default_registry()
    payload = {
        "vehicle": {
            "vin": "1HGCM82633A123456",
            "calibration_id": "HND-CIV-2015-A1",
            "cvn": "CDA08E85",
            "ecu_name": "ECM",
        },
    }
    report = compute_coverage(payload, reg)
    assert set(report.mode09_present) == {"0902", "0904", "0906", "090A"}


def test_coverage_flags_missing_vehicle_fields() -> None:
    """Notes should explicitly call out missing VIN / Cal ID / CVN."""
    reg = load_default_registry()
    report = compute_coverage({"vehicle": {}}, reg)
    notes = " ".join(report.notes)
    assert "VIN" in notes
    assert "Cal ID" in notes or "calibration_id" in notes
    assert "CVN" in notes


def test_coverage_mode22_counted_separately(tmp_path: Path) -> None:
    """Mode 22 PIDs go in the mode22_total bucket, not mode01."""
    reg = load_default_registry()
    payload = {"live_baseline": {"22115C": 50, "010C": 800}}
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 1
    assert report.mode22_total == 1


def test_coverage_entries_carry_name_and_unit() -> None:
    """Per-PID entries should be enriched with the registry's name/unit
    so the dashboard can show 'Engine RPM (rpm)' not just '010C'."""
    reg = load_default_registry()
    payload = {"live_baseline": {"010C": 800}}
    report = compute_coverage(payload, reg)
    entry = next(e for e in report.entries if e.key == "010C")
    assert entry.name  # exact name depends on registry data
    assert entry.answerable is True


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


def test_coverage_endpoint_404_on_missing_scenario(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/scenarios/does-not-exist/coverage")
    assert r.status_code == 404


def test_coverage_endpoint_uses_inline_baseline(tmp_path: Path) -> None:
    """A scenario with its own live_baseline (no source_session_id)
    should report coverage straight from it."""
    client = TestClient(create_app(data_root=tmp_path))
    # Create a scenario with an explicit vehicle so VIN/Cal/CVN don't
    # all NRC in the notes (we test that case elsewhere).
    r = client.post("/api/scenarios", json={
        "label": "test-coverage",
        "vehicle": {"vin": "1HGCM82633A123456",
                    "calibration_id": "HND-CIV-2015-A1",
                    "cvn": "CDA08E85"},
        "live_overrides": {"010C": 1500, "0105": 90, "0104": 30},
    })
    sid = r.json()["scenario_id"]
    r = client.get(f"/api/scenarios/{sid}/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["mode01_total"] == 3
    assert body["mode01_answerable"] == 3
    assert "0902" in body["mode09_present"]
    assert "0906" in body["mode09_present"]


def test_coverage_endpoint_resolves_source_session(tmp_path: Path) -> None:
    """When the scenario was created from a source_session_id with no
    inline baseline, /coverage should pull baseline PIDs from the
    session's live_data.jsonl — same merge the push pipeline does."""
    client = TestClient(create_app(data_root=tmp_path))

    # Run a mock capture so a source session exists with real data.
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            break
    sessions = client.get("/api/sessions").json()
    assert sessions, "expected at least one captured session"
    source_sid = sessions[0]["session_id"]

    r = client.post("/api/scenarios", json={
        "label": "from-source",
        "source_session_id": source_sid,
    })
    assert r.status_code == 200
    sid = r.json()["scenario_id"]

    r = client.get(f"/api/scenarios/{sid}/coverage")
    assert r.status_code == 200
    body = r.json()
    # The mock adapter emits multiple PIDs continuously — there should
    # be at least one mode-01 PID in the resolved baseline.
    assert body["mode01_total"] > 0
    assert body["mode01_answerable"] > 0
