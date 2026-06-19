"""
v0.6.14 — Tests for raw-PID accounting across diagnostics, diff,
and coverage.

v0.6.13 introduced raw passthrough. v0.6.14 makes the activity
visible: the session diagnostics endpoint now reports how many
captured PIDs landed via raw fallback, the coverage endpoint
breaks answerable down into formula vs raw, and the diff result
flags raw-only PIDs that get excluded from the numeric stats.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app
from uacj_obd.coverage import compute_coverage
from uacj_obd.diff import diff_sessions
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database


# ---------------------------------------------------------------------------
# Diagnostics endpoint — raw vs numeric breakdown
# ---------------------------------------------------------------------------


def _seed_session(tmp_path: Path, *, samples: list[dict],
                  discovered: list[str] | None = None) -> tuple[TestClient, str]:
    """Build a fake on-disk session with the given samples."""
    client = TestClient(create_app(data_root=tmp_path))
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("VINFORTEST123456", "TestMake", "TestModel", 2020,
                      "2026-06-19T00:00:00+00:00")
    folder = tmp_path / "session_folder"
    folder.mkdir()
    meta = {
        "vehicle": {"vin": "VINFORTEST123456"},
        "discovered_pids": discovered or [],
        "pid_resolution_source": "discovered" if discovered else "",
    }
    (folder / "metadata.json").write_text(json.dumps(meta))
    with (folder / "live_data.jsonl").open("w") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")
    db.insert_session(
        session_id="seeded_session",
        vin="VINFORTEST123456",
        started_at="2026-06-19T00:00:00+00:00",
        ended_at="2026-06-19T00:00:30+00:00",
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=len(samples),
        folder=str(folder),
        notes="",
    )
    return client, "seeded_session"


def test_diagnostics_reports_raw_vs_numeric_breakdown(tmp_path: Path) -> None:
    samples = [
        {"pid": "010C", "value": 800},        # numeric
        {"pid": "010D", "value": 30},         # numeric
        {"pid": "01AB", "value": "raw:CAFE"}, # raw passthrough
        {"pid": "01AC", "value": "raw:BEEF"}, # raw passthrough
    ]
    client, sid = _seed_session(tmp_path, samples=samples)
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    assert body["captured_unique_count"] == 4
    assert body["captured_numeric_count"] == 2
    assert body["captured_raw_count"] == 2
    assert sorted(body["captured_raw_pids"]) == ["01AB", "01AC"]


def test_diagnostics_zero_raw_when_no_passthrough(tmp_path: Path) -> None:
    """A normal capture with no raw fallback should report zero raw."""
    samples = [
        {"pid": "010C", "value": 800},
        {"pid": "0105", "value": 90},
    ]
    client, sid = _seed_session(tmp_path, samples=samples)
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    assert body["captured_raw_count"] == 0
    assert body["captured_numeric_count"] == 2


def test_diagnostics_handles_pid_with_both_numeric_and_raw(tmp_path: Path) -> None:
    """A PID that appears once as numeric and once as raw (would happen
    if read_pid returned numeric mid-capture but raw fallback once)
    should count toward numeric — we already have a real value for it."""
    samples = [
        {"pid": "010C", "value": 800},
        {"pid": "010C", "value": "raw:0BB8"},  # same PID, raw form
        {"pid": "01AB", "value": "raw:CAFE"},
    ]
    client, sid = _seed_session(tmp_path, samples=samples)
    body = client.get(f"/api/sessions/{sid}/diagnostics").json()
    # captured_unique_count is the union; raw_count is PIDs that AT
    # LEAST ONCE had a raw value. By design — if Cristopher sees a
    # PID listed in raw_pids it means raw fallback fired for it at
    # least once during the run, which is the signal we want.
    assert "010C" in body["captured_raw_pids"]
    assert "01AB" in body["captured_raw_pids"]


# ---------------------------------------------------------------------------
# Coverage — formula vs raw breakdown
# ---------------------------------------------------------------------------


def test_coverage_breaks_down_formula_vs_raw() -> None:
    reg = load_default_registry()
    payload = {
        "live_baseline": {
            "010C": 800,        # formula
            "010D": 30,         # formula
            "01AB": "raw:CAFE", # raw passthrough
        },
    }
    report = compute_coverage(payload, reg)
    assert report.mode01_total == 3
    assert report.mode01_answerable == 3
    assert report.mode01_via_raw == 1
    assert report.mode01_via_formula == 2


def test_coverage_pid_entry_carries_via_raw_flag() -> None:
    reg = load_default_registry()
    payload = {"live_baseline": {"01AB": "raw:CAFE", "010C": 800}}
    report = compute_coverage(payload, reg)
    raw_entry = next(e for e in report.entries if e.key == "01AB")
    formula_entry = next(e for e in report.entries if e.key == "010C")
    assert raw_entry.via_raw is True
    assert raw_entry.answerable is True
    assert formula_entry.via_raw is False
    assert formula_entry.answerable is True


def test_coverage_invalid_raw_marker_not_answerable() -> None:
    """An invalid raw marker shouldn't count as via_raw OR answerable."""
    reg = load_default_registry()
    payload = {"live_baseline": {"01AB": "raw:not_hex_zz"}}
    report = compute_coverage(payload, reg)
    entry = next(e for e in report.entries if e.key == "01AB")
    assert entry.answerable is False
    assert entry.via_raw is False


def test_coverage_endpoint_returns_raw_counts(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/scenarios", json={
        "label": "raw-coverage-test",
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_overrides": {"01AB": "raw:CAFE", "010C": 800},
    })
    sid = r.json()["scenario_id"]
    body = client.get(f"/api/scenarios/{sid}/coverage").json()
    assert body["mode01_via_raw"] == 1
    assert body["mode01_via_formula"] == 1
    raw_entry = next(e for e in body["entries"] if e["key"] == "01AB")
    assert raw_entry["via_raw"] is True
    assert raw_entry["answerable"] is True


# ---------------------------------------------------------------------------
# Diff — raw_pids surfaced
# ---------------------------------------------------------------------------


def _make_session_dir(tmp_path: Path, name: str, samples: list[dict]) -> Path:
    """Build a minimal on-disk session folder the diff helper can read."""
    d = tmp_path / name
    d.mkdir()
    (d / "metadata.json").write_text(json.dumps({"session_id": name}))
    with (d / "live_data.jsonl").open("w") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")
    (d / "dtcs.json").write_text("[]")
    (d / "monitors.json").write_text("[]")
    return d


def test_diff_surfaces_raw_pids_excluded_from_stats(tmp_path: Path) -> None:
    a = _make_session_dir(tmp_path, "a", [
        {"pid": "010C", "value": 800, "name": "RPM"},
        {"pid": "01AB", "value": "raw:CAFE", "name": "raw 01AB"},
    ])
    b = _make_session_dir(tmp_path, "b", [
        {"pid": "010C", "value": 1200, "name": "RPM"},
        {"pid": "01AC", "value": "raw:BEEF", "name": "raw 01AC"},
    ])
    result = diff_sessions(a, b)
    assert "01AB" in result["raw_pids_a"]
    assert "01AC" in result["raw_pids_b"]
    assert result["raw_pids_only_a"] == ["01AB"]
    assert result["raw_pids_only_b"] == ["01AC"]


def test_diff_with_no_raw_pids_returns_empty_lists(tmp_path: Path) -> None:
    a = _make_session_dir(tmp_path, "a", [{"pid": "010C", "value": 800}])
    b = _make_session_dir(tmp_path, "b", [{"pid": "010C", "value": 1200}])
    result = diff_sessions(a, b)
    assert result["raw_pids_a"] == []
    assert result["raw_pids_b"] == []


def test_diff_pid_with_both_numeric_and_raw_not_in_raw_only(tmp_path: Path) -> None:
    """A PID that's ever numeric belongs in stats, not in raw_only."""
    a = _make_session_dir(tmp_path, "a", [
        {"pid": "010C", "value": 800},
        {"pid": "010C", "value": "raw:0BB8"},  # also raw at some point
    ])
    b = _make_session_dir(tmp_path, "b", [
        {"pid": "010C", "value": 1200},
    ])
    result = diff_sessions(a, b)
    assert "010C" not in result["raw_pids_a"]
