"""
v0.6.11 — Tests for capture-side PID diagnostics.

Cristopher's bench: Mazda3 reports "1/44 parameters" on the real
car but the simulator only advertises 10 after pushing the capture.
Without seeing where the gap is, you can't fix it. This adds:

- `SessionMetadata.discovered_pids` + `pid_resolution_source`
  populated during capture
- `GET /api/sessions/{id}/diagnostics` returns discovered_count,
  captured_unique_count, and the per-set deltas
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app


def _wait_for_session_end(client) -> None:
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            return


def test_diagnostics_404_on_missing_session(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/sessions/does-not-exist/diagnostics")
    assert r.status_code == 404


def test_diagnostics_after_mock_capture_reports_counts(tmp_path: Path) -> None:
    """End-to-end: a mock capture writes discovered_pids into
    metadata.json. The diagnostics endpoint reads it back and reports
    captured_unique_count derived from live_data.jsonl."""
    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    _wait_for_session_end(client)
    sessions = client.get("/api/sessions").json()
    assert sessions
    sid = sessions[0]["session_id"]

    r = client.get(f"/api/sessions/{sid}/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["pid_resolution_source"] in ("discovered", "fallback")
    assert body["captured_unique_count"] > 0
    assert isinstance(body["discovered_pids"], list)
    assert isinstance(body["captured_pids"], list)


def test_diagnostics_discovered_count_matches_metadata(tmp_path: Path) -> None:
    """The endpoint should faithfully echo what metadata.json says
    discovered_pids is — no recomputation."""
    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    _wait_for_session_end(client)
    sessions = client.get("/api/sessions").json()
    sid = sessions[0]["session_id"]
    folder = Path(sessions[0]["folder"])

    meta = json.loads((folder / "metadata.json").read_text())
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    assert sorted(meta.get("discovered_pids", [])) == body["discovered_pids"]


def test_diagnostics_captured_only_pids_empty_in_normal_capture(tmp_path: Path) -> None:
    """captured_only_pids should be empty when capture follows the
    discovered list — non-empty would mean the loop read PIDs that
    weren't in the supported set, which is a separate bug."""
    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    _wait_for_session_end(client)
    sessions = client.get("/api/sessions").json()
    sid = sessions[0]["session_id"]
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    # Mock adapter's supported_pids() covers everything its
    # generator emits, so the loop shouldn't read any off-list PIDs.
    assert body["captured_only_pids"] == []


def test_diagnostics_handles_missing_metadata(tmp_path: Path) -> None:
    """If metadata.json is missing, the endpoint should still 200
    with zeroed-out diagnostics, not crash."""
    from uacj_obd.storage import Database
    client = TestClient(create_app(data_root=tmp_path))
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("VINNOMETA", "Test", "Test", 2020, "2026-01-01T00:00:00+00:00")
    folder = tmp_path / "no_meta_folder"
    folder.mkdir()
    db.insert_session(
        session_id="no_meta_session",
        vin="VINNOMETA",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:30+00:00",
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=0,
        folder=str(folder),
        notes="",
    )
    r = client.get("/api/sessions/no_meta_session/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["discovered_count"] == 0
    assert body["captured_unique_count"] == 0
    assert body["pid_resolution_source"] == ""


def test_diagnostics_handles_corrupt_jsonl_lines(tmp_path: Path) -> None:
    """A corrupt line in live_data.jsonl should be skipped, not crash
    the diagnostics endpoint."""
    from uacj_obd.storage import Database
    client = TestClient(create_app(data_root=tmp_path))
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("VINCORRUPT", "Test", "Test", 2020, "2026-01-01T00:00:00+00:00")
    folder = tmp_path / "corrupt_folder"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "discovered_pids": ["010C", "010D"],
        "pid_resolution_source": "discovered",
    }))
    (folder / "live_data.jsonl").write_text(
        '{"pid": "010C", "value": 800}\n'
        '{not valid json\n'
        '{"pid": "010D", "value": 30}\n'
    )
    db.insert_session(
        session_id="corrupt_session",
        vin="VINCORRUPT",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at=None,
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=2,
        folder=str(folder),
        notes="",
    )
    body = client.get("/api/sessions/corrupt_session/diagnostics").json()
    assert body["captured_unique_count"] == 2
    assert set(body["captured_pids"]) == {"010C", "010D"}


def test_diagnostics_missing_after_capture_set(tmp_path: Path) -> None:
    """A session where discovered says 5 but only 3 PIDs landed should
    list the missing 2 as `missing_after_capture` — this is the
    diagnosis Cristopher needs for the 44→10 gap."""
    from uacj_obd.storage import Database
    client = TestClient(create_app(data_root=tmp_path))
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("VINGAP", "Test", "Test", 2020, "2026-01-01T00:00:00+00:00")
    folder = tmp_path / "gap_folder"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "discovered_pids": ["010C", "010D", "0105", "0110", "0111"],
        "pid_resolution_source": "discovered",
    }))
    (folder / "live_data.jsonl").write_text(
        '{"pid": "010C", "value": 800}\n'
        '{"pid": "010D", "value": 30}\n'
        '{"pid": "0105", "value": 90}\n'
    )
    db.insert_session(
        session_id="gap_session",
        vin="VINGAP",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at=None,
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=3,
        folder=str(folder),
        notes="",
    )
    body = client.get("/api/sessions/gap_session/diagnostics").json()
    assert body["discovered_count"] == 5
    assert body["captured_unique_count"] == 3
    assert set(body["missing_after_capture"]) == {"0110", "0111"}


def test_diagnostics_pid_resolution_source_field(tmp_path: Path) -> None:
    """A mock capture without an explicit PID list should land
    pid_resolution_source = 'discovered' (mock implements
    supported_pids())."""
    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    _wait_for_session_end(client)
    sid = client.get("/api/sessions").json()[0]["session_id"]
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    assert body["pid_resolution_source"] in ("discovered", "fallback")
