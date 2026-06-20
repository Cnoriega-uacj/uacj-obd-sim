"""
v0.6.16 — End-to-end test: AcquisitionSession captures mixed
numeric + raw PIDs through one capture run.

Unit tests cover Elm327Adapter._read_pid_raw in isolation, plus the
metadata-writing + diagnostics endpoint paths separately. This test
covers the interaction: a session run iterates a list of PIDs, the
adapter returns numeric for some and raw for others, both kinds land
in live_data.jsonl correctly, and the adapter_metrics counters end
up in metadata.json with the right counts.

Uses a custom Adapter subclass (not a deep mock of python-obd) so
the test is hardware-independent and stays meaningful.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from uacj_obd.acquisition.session import AcquisitionSession, SessionConfig
from uacj_obd.adapters.base import (
    Adapter,
    AdapterStatus,
    ConnectionState,
)
from uacj_obd.models import (
    DTC,
    FreezeFrame,
    LiveSample,
    Monitor,
    Protocol,
    VehicleInfo,
)
from uacj_obd.storage import Database, SessionStore


class _MixedAdapter(Adapter):
    """
    Adapter that emits numeric samples for some PIDs and raw markers
    for others — modeling the real-world v0.6.16 case where python-obd
    decodes some PIDs natively and `_read_pid_raw` catches the rest.

    `raw_pids` is a set of PID keys the adapter should respond to as
    raw passthrough (via the `value="raw:HEX"` convention). All other
    PIDs in `supported` return numeric samples.
    """

    def __init__(
        self,
        supported: set[str],
        raw_pids: set[str],
        raw_failures: set[str] | None = None,
    ) -> None:
        self._supported = supported
        self._raw_pids = raw_pids
        # PIDs where the raw fallback "fires" (counter increments) but
        # returns None (no actual data) — models the bus-silent case.
        self._raw_failures = raw_failures or set()
        self._connected = False
        self._raw_attempts = 0
        self._raw_successes = 0

    def connect(self) -> AdapterStatus:
        self._connected = True
        return self.status()

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            state=ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED,
            protocol=Protocol.ISO_15765_4_CAN_11_500,
            adapter_name="MixedAdapter (test)",
        )

    def supported_pids(self) -> set[str]:
        return set(self._supported)

    def read_pid(self, pid: str) -> LiveSample | None:
        if pid not in self._supported:
            return None
        if pid in self._raw_pids:
            self._raw_attempts += 1
            if pid in self._raw_failures:
                return None
            self._raw_successes += 1
            return LiveSample(
                pid=pid, name=f"raw {pid}",
                value="raw:CAFE", unit=None,
            )
        # Numeric sample for everything else
        return LiveSample(pid=pid, name=f"pid {pid}", value=123.0, unit="")

    def stream_pids(self, pids):
        while self._connected:
            for pid in pids:
                s = self.read_pid(pid)
                if s is not None:
                    yield s

    def read_vehicle_info(self) -> VehicleInfo:
        return VehicleInfo(vin="VINMIXED" + "0" * 9, make="Test", model="Test", year=2020)

    def read_dtcs(self) -> list[DTC]:
        return []

    def clear_dtcs(self) -> bool:
        return True

    def read_freeze_frame(self) -> FreezeFrame | None:
        return None

    def read_monitors(self) -> list[Monitor]:
        return []

    def read_metrics(self) -> dict[str, int]:
        return {
            "raw_attempts": self._raw_attempts,
            "raw_successes": self._raw_successes,
        }

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        return None


def _run_session(tmp_path: Path, adapter: Adapter, duration_s: float = 0.5) -> Path:
    """Run one full capture cycle and return the session folder."""
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    config = SessionConfig(sample_interval_s=0.0, min_cycle_seconds=0.0)
    sess = AcquisitionSession(adapter=adapter, db=db, store=store, config=config)
    sess.start()
    sess.run(duration_s=duration_s)
    folder = sess.close()
    return folder


def test_acquisition_round_trip_numeric_and_raw(tmp_path: Path) -> None:
    """A capture against a mixed adapter should land BOTH numeric and
    raw samples in live_data.jsonl with the right value shapes."""
    adapter = _MixedAdapter(
        supported={"010C", "010D", "0114", "0115"},
        raw_pids={"0114", "0115"},
    )
    folder = _run_session(tmp_path, adapter)

    live = folder / "live_data.jsonl"
    samples = [json.loads(line) for line in live.read_text().splitlines() if line.strip()]
    by_pid = {}
    for s in samples:
        by_pid.setdefault(s["pid"], []).append(s["value"])

    # Numeric PIDs get numeric values
    assert all(isinstance(v, (int, float)) for v in by_pid.get("010C", []))
    # Raw PIDs get the marker
    assert all(isinstance(v, str) and v.startswith("raw:") for v in by_pid.get("0114", []))


def test_acquisition_writes_adapter_metrics_to_metadata(tmp_path: Path) -> None:
    """The raw_attempts / raw_successes counters end up in metadata.json
    via the new adapter_metrics field."""
    adapter = _MixedAdapter(
        supported={"010C", "0114", "0115", "0116"},
        raw_pids={"0114", "0115", "0116"},
    )
    folder = _run_session(tmp_path, adapter, duration_s=0.3)
    meta = json.loads((folder / "metadata.json").read_text())
    metrics = meta.get("adapter_metrics") or {}
    # Three raw PIDs polled at least once per cycle, multiple cycles
    assert metrics.get("raw_attempts", 0) >= 3
    assert metrics.get("raw_successes", 0) >= 3
    # All raw reads succeeded in this test
    assert metrics["raw_attempts"] == metrics["raw_successes"]


def test_acquisition_records_raw_attempt_failures(tmp_path: Path) -> None:
    """A raw PID that the adapter's raw read fires for but returns
    None (bus silent) should bump raw_attempts but NOT raw_successes."""
    adapter = _MixedAdapter(
        supported={"010C", "0114", "0115"},
        raw_pids={"0114", "0115"},
        raw_failures={"0115"},  # 0115 is "raw attempted but fails"
    )
    folder = _run_session(tmp_path, adapter, duration_s=0.3)
    meta = json.loads((folder / "metadata.json").read_text())
    metrics = meta["adapter_metrics"]
    # raw_attempts counts BOTH 0114 (successes) and 0115 (failures)
    assert metrics["raw_attempts"] > metrics["raw_successes"]
    # 0115 did not produce data; should not appear in live_data.jsonl
    samples = [
        json.loads(line) for line in
        (folder / "live_data.jsonl").read_text().splitlines() if line.strip()
    ]
    captured_pids = {s["pid"] for s in samples}
    assert "0115" not in captured_pids
    assert "0114" in captured_pids


def test_acquisition_pid_resolution_source_discovered(tmp_path: Path) -> None:
    """When the adapter implements supported_pids() with non-empty
    output, pid_resolution_source must be 'discovered'."""
    adapter = _MixedAdapter(
        supported={"010C", "010D"},
        raw_pids=set(),
    )
    folder = _run_session(tmp_path, adapter)
    meta = json.loads((folder / "metadata.json").read_text())
    assert meta["pid_resolution_source"] == "discovered"
    assert sorted(meta["discovered_pids"]) == ["010C", "010D"]


def test_acquisition_no_raw_metrics_for_zero_attempts(tmp_path: Path) -> None:
    """An adapter with no raw PIDs in supported should report metrics
    with both counters at 0 — informational, not missing."""
    adapter = _MixedAdapter(
        supported={"010C", "010D"},
        raw_pids=set(),
    )
    folder = _run_session(tmp_path, adapter)
    meta = json.loads((folder / "metadata.json").read_text())
    metrics = meta.get("adapter_metrics") or {}
    assert metrics == {"raw_attempts": 0, "raw_successes": 0}


def test_diagnostics_endpoint_returns_acquisition_metrics(tmp_path: Path) -> None:
    """The diagnostics endpoint reads the metrics back through the
    same path Cristopher's dashboard does."""
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app

    # Run the session against a normal AcquisitionSession + custom
    # adapter, then attach the resulting folder to a fresh API to
    # query the diagnostics endpoint.
    adapter = _MixedAdapter(
        supported={"010C", "0114", "0115", "0116", "0117"},
        raw_pids={"0114", "0115", "0116", "0117"},
        raw_failures={"0117"},
    )
    folder = _run_session(tmp_path, adapter, duration_s=0.3)
    session_id = folder.name

    # The acquisition wrote the session to tmp_path/sessions and tmp_path/uacj.db.
    # create_app reads from those same paths.
    client = TestClient(create_app(data_root=tmp_path))
    body = client.get(f"/api/sessions/{session_id}/diagnostics").json()

    assert body["session_id"] == session_id
    assert body["captured_raw_count"] >= 3  # 0114, 0115, 0116 succeeded
    assert "0117" in body["missing_after_capture"]
    metrics = body["adapter_metrics"]
    assert metrics["raw_attempts"] > metrics["raw_successes"]
