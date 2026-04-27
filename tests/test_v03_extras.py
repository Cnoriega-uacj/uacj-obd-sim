"""v0.3 additions: session diff + 5-baud slow-init."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.adapters.mock import MockAdapter
from uacj_obd.api import create_app
from uacj_obd.diff import diff_sessions
from uacj_obd.models import DTC, DTCStatus
from uacj_obd.pids import load_default_registry
from uacj_obd.simulator.kline import (
    KEY_BYTE_1,
    KEY_BYTE_2,
    SLOW_INIT_ADDRESS_OBD,
    SLOW_INIT_SYNC_BYTE,
    slow_init_step,
)
from uacj_obd.storage import Database, SessionStore


def _capture(tmp_path: Path, name_suffix: str, dtcs: list[DTC]) -> Path:
    db = Database(tmp_path / f"uacj_{name_suffix}.db")
    store = SessionStore(tmp_path / f"sessions_{name_suffix}")
    pid_reg = load_default_registry()
    a = MockAdapter(dtcs=dtcs)
    sess = AcquisitionSession(a, store, db, pid_reg,
                                SessionConfig(pids=["010C", "010D", "0105"], sample_interval_s=0.0))
    sess.start()
    sess.run(duration_s=0.2)
    return sess.close()


# ---- session diff ----

def test_diff_detects_dtc_added_and_removed(tmp_path: Path) -> None:
    folder_a = _capture(tmp_path, "a", [
        DTC(code="P0420", status=DTCStatus.STORED, description=""),
        DTC(code="P0171", status=DTCStatus.PENDING, description=""),
    ])
    folder_b = _capture(tmp_path, "b", [
        DTC(code="P0420", status=DTCStatus.STORED, description=""),
        DTC(code="P0301", status=DTCStatus.STORED, description=""),
    ])
    diff = diff_sessions(folder_a, folder_b)
    added = {d["code"] for d in diff["dtcs"]["added"]}
    removed = {d["code"] for d in diff["dtcs"]["removed"]}
    common = {d["code"] for d in diff["dtcs"]["common"]}
    assert "P0301" in added
    assert "P0171" in removed
    assert "P0420" in common


def test_diff_pid_stats(tmp_path: Path) -> None:
    folder_a = _capture(tmp_path, "a", [])
    folder_b = _capture(tmp_path, "b", [])
    diff = diff_sessions(folder_a, folder_b)
    pids = {p["pid"]: p for p in diff["pids"]}
    rpm = pids.get("010C")
    assert rpm is not None
    assert rpm["a"]["n"] > 0
    assert rpm["b"]["n"] > 0
    assert "delta_pct" in rpm


def test_diff_endpoint_returns_404_for_missing_session(tmp_path: Path) -> None:
    c = TestClient(create_app(data_root=tmp_path))
    r = c.get("/api/diff?a=does-not-exist&b=also-not")
    assert r.status_code == 404


def test_diff_endpoint_round_trip(tmp_path: Path) -> None:
    c = TestClient(create_app(data_root=tmp_path))
    sids = []
    for _ in range(2):
        r = c.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.2,
                                                  "pids": ["010C", "010D"]})
        sids.append(r.json()["session_id"])
        deadline = time.time() + 5
        while time.time() < deadline:
            if not c.get("/api/sessions/current").json().get("active"):
                break
            time.sleep(0.1)
    r = c.get(f"/api/diff?a={sids[0]}&b={sids[1]}")
    assert r.status_code == 200
    body = r.json()
    assert body["session_a"] == sids[0]
    assert body["session_b"] == sids[1]


# ---- 5-baud slow-init ----

def test_slow_init_address_byte_returns_sync_and_keys() -> None:
    out = slow_init_step(SLOW_INIT_ADDRESS_OBD)
    assert out == bytes([SLOW_INIT_SYNC_BYTE, KEY_BYTE_1, KEY_BYTE_2])


def test_slow_init_inverted_kb2_returns_inverted_address() -> None:
    inverted = (~KEY_BYTE_2) & 0xFF
    out = slow_init_step(inverted)
    assert out == bytes([(~SLOW_INIT_ADDRESS_OBD) & 0xFF])


def test_slow_init_unrelated_byte_returns_empty() -> None:
    assert slow_init_step(0xAA) == b""


class _ScriptedSerial:
    """Minimal serial stand-in: returns scripted reads, captures writes."""

    def __init__(self, script: list[bytes]) -> None:
        self.script = list(script)
        self.writes: list[bytes] = []

    def read(self, n: int) -> bytes:
        if not self.script:
            return b""
        chunk = self.script.pop(0)
        return chunk[:n] if n else b""

    def write(self, b: bytes) -> int:
        self.writes.append(bytes(b))
        return len(b)


def test_kline_runtime_handles_slow_init_handshake() -> None:
    """The runtime should answer slow-init bytes without treating them as a frame."""
    from uacj_obd.simulator import EcuEmulator
    from uacj_obd.simulator.kline_runtime import KlineRuntime

    serial = _ScriptedSerial([bytes([SLOW_INIT_ADDRESS_OBD])])
    rt = KlineRuntime(EcuEmulator(), serial=serial)
    frame = rt._read_one_frame()
    assert frame is None  # no full request frame yet — it was a handshake byte
    assert serial.writes
    assert serial.writes[0] == bytes([SLOW_INIT_SYNC_BYTE, KEY_BYTE_1, KEY_BYTE_2])
