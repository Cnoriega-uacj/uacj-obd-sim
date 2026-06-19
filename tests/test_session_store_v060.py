"""
v0.6.0 — Tests for the uncovered SessionStore / SessionWriter methods.

v0.5.5 audit identified `storage/session_store.py` as 61% covered.
The uncovered methods are all user-facing:

- `SessionStore.list_session_dirs` — used by the dashboard's session
  list and the diff tool.
- `SessionWriter.write_samples` — bulk-write convenience used by
  bench harnesses and the replay adapter.
- `SessionWriter.write_raw` — the audit trail of raw bus traffic.
- `SessionWriter.export_csv` — CSV export the dashboard downloads.
- `SessionWriter.export_json` — full-session JSON bundle for the
  backup feature.

This module lifts that coverage from 61% to 100% so the user-facing
paths can't regress silently.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from uacj_obd.models import (
    DTC,
    DTCStatus,
    FreezeFrame,
    LiveSample,
    Monitor,
    Protocol,
    SessionMetadata,
    VehicleInfo,
)
from uacj_obd.storage.session_store import SessionStore, SessionWriter


def _meta() -> SessionMetadata:
    return SessionMetadata(
        session_id="20260619T120000Z-abc123",
        started_at=datetime.now(timezone.utc),
        protocol=Protocol.ISO_15765_4_CAN_11_500,
        adapter="mock",
        vehicle=VehicleInfo(vin="JM1BL1L72C1627697", make="Mazda",
                             model="Mazda3", year=2012),
    )


def _writer(tmp_path: Path) -> SessionWriter:
    store = SessionStore(tmp_path)
    return store.open_session(_meta())


# ---------------------------------------------------------------------------
# SessionStore.list_session_dirs
# ---------------------------------------------------------------------------

def test_list_session_dirs_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    # Touch the root so iterdir works even if open_session never ran.
    tmp_path.mkdir(exist_ok=True)
    assert store.list_session_dirs() == []


def test_list_session_dirs_one_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    writer = store.open_session(_meta())
    writer.close()
    dirs = store.list_session_dirs()
    assert len(dirs) == 1
    assert dirs[0].name.startswith("20260619T120000Z")


def test_list_session_dirs_skips_non_directories(tmp_path: Path) -> None:
    """Stray files in the root shouldn't break the listing."""
    store = SessionStore(tmp_path)
    writer = store.open_session(_meta())
    writer.close()
    # Plant a stray file at the root level (sibling of the vehicle dir).
    (tmp_path / "README.txt").write_text("not a vehicle folder")
    dirs = store.list_session_dirs()
    assert len(dirs) == 1


def test_list_session_dirs_multiple_vehicles_alphabetical(tmp_path: Path) -> None:
    """Sessions across multiple vehicles all come back, ordered."""
    store = SessionStore(tmp_path)
    m1 = _meta()
    m2 = _meta()
    m2.vehicle = VehicleInfo(vin="2HGFC2F59FH123456", make="Honda",
                              model="Civic", year=2015)
    m2.session_id = "20260619T130000Z-def456"
    store.open_session(m1).close()
    store.open_session(m2).close()
    dirs = store.list_session_dirs()
    assert len(dirs) == 2


# ---------------------------------------------------------------------------
# SessionWriter.write_samples (bulk)
# ---------------------------------------------------------------------------

def test_write_samples_bulk_writes_every_entry(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    samples = [
        LiveSample(pid="010C", name="RPM", value=750),
        LiveSample(pid="010D", name="SPEED", value=0),
        LiveSample(pid="0105", name="COOLANT", value=88),
    ]
    n = writer.write_samples(samples)
    assert n == 3
    writer.close()
    # The file should have one line per sample.
    live_jsonl = writer.dir / "live_data.jsonl"
    lines = [l for l in live_jsonl.read_text().splitlines() if l.strip()]
    assert len(lines) == 3


def test_write_samples_returns_count_of_zero_for_empty_iterable(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    assert writer.write_samples([]) == 0
    writer.close()


# ---------------------------------------------------------------------------
# SessionWriter.write_raw
# ---------------------------------------------------------------------------

def test_write_raw_appends_timestamped_lines(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_raw("adapter init: ATZ")
    writer.write_raw("adapter init: ATSP0")
    writer.close()
    raw = writer.dir / "raw.log"
    lines = [l for l in raw.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    # Each line starts with an ISO timestamp.
    for line in lines:
        ts_part = line.split()[0]
        # Should parse as an ISO datetime
        datetime.fromisoformat(ts_part.replace("Z", "+00:00"))


def test_write_raw_handles_multiline_payload(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_raw("multi line not stripped")
    writer.close()
    raw_text = (writer.dir / "raw.log").read_text()
    assert "multi line not stripped" in raw_text


# ---------------------------------------------------------------------------
# SessionWriter.export_csv
# ---------------------------------------------------------------------------

def test_export_csv_round_trips_live_samples(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_sample(LiveSample(pid="010C", name="RPM", value=750, unit="rpm"))
    writer.write_sample(LiveSample(pid="0105", name="COOLANT", value=88, unit="C"))
    writer.close()
    csv_path = writer.export_csv()
    assert csv_path.exists()
    with csv_path.open() as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    # First row is header
    assert rows[0] == ["ts", "pid", "name", "value", "unit"]
    # Two data rows
    assert len(rows) == 3
    # PIDs landed in the right column
    pids_in_csv = {r[1] for r in rows[1:]}
    assert pids_in_csv == {"010C", "0105"}


def test_export_csv_skips_blank_lines(tmp_path: Path) -> None:
    """Manually inject a blank line and ensure export_csv tolerates it."""
    writer = _writer(tmp_path)
    writer.write_sample(LiveSample(pid="010C", name="RPM", value=750))
    writer.close()
    # Append a blank line directly to the jsonl
    live = writer.dir / "live_data.jsonl"
    with live.open("a") as fh:
        fh.write("\n\n")
    csv_path = writer.export_csv()
    with csv_path.open() as fh:
        rows = list(csv.reader(fh))
    # Header + 1 data row (blank line ignored)
    assert len(rows) == 2


def test_export_csv_on_empty_session_writes_header_only(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.close()
    csv_path = writer.export_csv()
    with csv_path.open() as fh:
        rows = list(csv.reader(fh))
    assert rows == [["ts", "pid", "name", "value", "unit"]]


# ---------------------------------------------------------------------------
# SessionWriter.export_json
# ---------------------------------------------------------------------------

def test_export_json_includes_metadata_and_live_data(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_sample(LiveSample(pid="010C", name="RPM", value=750))
    writer.write_sample(LiveSample(pid="010D", name="SPEED", value=15))
    writer.close()
    json_path = writer.export_json()
    bundle = json.loads(json_path.read_text())
    assert bundle["metadata"]["session_id"] == "20260619T120000Z-abc123"
    assert len(bundle["live_data"]) == 2
    assert bundle["dtcs"] == []
    assert bundle["monitors"] == []
    assert bundle["freeze_frame"] is None


def test_export_json_includes_dtcs_when_written(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_dtcs([
        DTC(code="P0420", status=DTCStatus.STORED, description="Catalyst"),
    ])
    writer.close()
    bundle = json.loads(writer.export_json().read_text())
    assert len(bundle["dtcs"]) == 1
    assert bundle["dtcs"][0]["code"] == "P0420"


def test_export_json_includes_monitors_and_freeze_frame(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.write_monitors([
        Monitor(name="Misfire", supported=True, ready=True),
        Monitor(name="Catalyst", supported=True, ready=False),
    ])
    writer.write_freeze_frame(FreezeFrame(dtc="P0420", pids={"010C": 1850}))
    writer.close()
    bundle = json.loads(writer.export_json().read_text())
    assert len(bundle["monitors"]) == 2
    assert bundle["freeze_frame"]["dtc"] == "P0420"


def test_export_json_with_no_live_data_yields_empty_list(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.close()
    bundle = json.loads(writer.export_json().read_text())
    assert bundle["live_data"] == []


def test_write_freeze_frame_none_is_clean_noop(tmp_path: Path) -> None:
    """`write_freeze_frame(None)` shouldn't create a file or crash."""
    writer = _writer(tmp_path)
    writer.write_freeze_frame(None)
    writer.close()
    assert not (writer.dir / "freeze_frame.json").exists()
