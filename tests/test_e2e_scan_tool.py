"""
Scan-tool-perspective end-to-end: capture a vehicle through the full
software stack and verify a virtual scan tool sees the right answers
through both CAN and K-Line responders.

This is the highest-confidence test in the suite — when this passes,
the full pipeline works.
"""

from __future__ import annotations

import json
from pathlib import Path

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.pids import load_default_registry
from uacj_obd.simulator import EcuEmulator
from uacj_obd.simulator.can_runtime import CanRuntime, scenario_to_state
from uacj_obd.simulator.iso_tp import CanFrame, IsoTpFramer
from uacj_obd.simulator.kline import decode as decode_kline, encode_request
from uacj_obd.simulator.kline_runtime import KlineRuntime
from uacj_obd.storage import Database, SessionStore


def _capture_and_build_baseline(tmp_path: Path) -> tuple[Path, dict]:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter("mock")
    sess = AcquisitionSession(a, store, db, pid_reg,
                                SessionConfig(pids=["010C", "010D", "0105", "0111"], sample_interval_s=0.0))
    sess.start()
    sess.run(duration_s=0.3)
    folder = sess.close()

    live_baseline: dict = {}
    with (folder / "live_data.jsonl").open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("pid") and obj.get("value") is not None:
                live_baseline[obj["pid"]] = obj["value"]
    return folder, live_baseline


def test_e2e_capture_to_can_scan_tool(tmp_path: Path) -> None:
    folder, live_baseline = _capture_and_build_baseline(tmp_path)
    payload = {
        "vehicle": {"vin": "2HGFC2F59FH123456"},
        "dtcs": [
            {"code": "P0301", "status": "stored", "description": "Misfire"},
            {"code": "P0300", "status": "pending", "description": ""},
        ],
        "live_baseline": live_baseline,
        "live_overrides": {"010C": 950},  # idle override on top of baseline
    }
    state = scenario_to_state(payload)
    rt = CanRuntime(EcuEmulator(state), bus=None)

    def query(req_payload: bytes) -> bytes | None:
        framer = IsoTpFramer()
        req = framer.encode(req_payload)[0]
        out = rt.handle_request_frame(CanFrame(0x7DF, req.data))
        decoder = IsoTpFramer()
        result = None
        for f in out:
            r = decoder.decode(f)
            if r is not None:
                result = r
        return result

    # Mode 09 PID 02 → VIN
    vin_resp = query(bytes([0x09, 0x02]))
    assert vin_resp is not None
    assert b"2HGFC2F59FH123456" in vin_resp

    # Mode 01 PID 0C → RPM, overridden to 950
    rpm = query(bytes([0x01, 0x0C]))
    assert rpm is not None and rpm[0] == 0x41 and rpm[1] == 0x0C
    assert ((rpm[2] << 8) | rpm[3]) / 4 == 950

    # Mode 01 PID 05 → coolant — present from baseline, not overridden
    coolant = query(bytes([0x01, 0x05]))
    assert coolant is not None and coolant[0] == 0x41 and coolant[1] == 0x05
    # decoded coolant must match the saved baseline (within 1°C of mock signal)
    assert coolant[2] - 40 == int(round(live_baseline["0105"]))

    # Mode 03 → stored DTCs include P0301
    dtcs = query(bytes([0x03]))
    assert dtcs is not None and dtcs[0] == 0x43 and dtcs[1] == 1
    # P0301 → 0x03 0x01
    assert dtcs[2:4] == bytes([0x03, 0x01])

    # Mode 07 → pending DTCs include P0300
    pending = query(bytes([0x07]))
    assert pending is not None and pending[0] == 0x47 and pending[1] == 1
    assert pending[2:4] == bytes([0x03, 0x00])


def test_e2e_capture_to_kline_scan_tool(tmp_path: Path) -> None:
    folder, live_baseline = _capture_and_build_baseline(tmp_path)
    payload = {
        "vehicle": {"vin": "2HGFC2F59FH123456"},
        "dtcs": [{"code": "P0420", "status": "stored", "description": ""}],
        "live_baseline": live_baseline,
    }
    state = scenario_to_state(payload)
    rt = KlineRuntime(EcuEmulator(state), serial=None)

    # VIN read over K-Line
    resp = rt.handle_request_bytes(encode_request(bytes([0x09, 0x02])))
    parsed = decode_kline(resp)
    assert b"2HGFC2F59FH123456" in parsed.data

    # RPM should come from baseline (no override here)
    resp = rt.handle_request_bytes(encode_request(bytes([0x01, 0x0C])))
    parsed = decode_kline(resp)
    assert parsed.data[0] == 0x41 and parsed.data[1] == 0x0C
    raw_rpm = ((parsed.data[2] << 8) | parsed.data[3]) / 4
    assert abs(raw_rpm - live_baseline["010C"]) < 5  # allow 4-RPM rounding

    # DTCs
    resp = rt.handle_request_bytes(encode_request(bytes([0x03])))
    parsed = decode_kline(resp)
    assert parsed.data[0] == 0x43 and parsed.data[1] == 1
    assert parsed.data[2:4] == bytes([0x04, 0x20])  # P0420


def test_baseline_overrides_merge_order(tmp_path: Path) -> None:
    """live_overrides must take precedence over live_baseline."""
    payload = {
        "live_baseline": {"010C": 800, "010D": 60},
        "live_overrides": {"010C": 3500},
    }
    state = scenario_to_state(payload)
    assert state.live["010C"] == 3500   # override wins
    assert state.live["010D"] == 60     # baseline survives
