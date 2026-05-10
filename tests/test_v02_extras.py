"""v0.2 additions: presets, mode 0x22 encoding, simulator request log."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app
from uacj_obd.presets import apply_monitors_override, list_presets
from uacj_obd.simulator import EcuEmulator, ScenarioState
from uacj_obd.simulator.encoders import encode_mfg_pid


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_root=tmp_path))


# -------- presets --------

def test_presets_listed() -> None:
    listed = list_presets()
    assert any(p["id"] == "p0420_catalyst" for p in listed)
    assert all("description" in p for p in listed)


def test_apply_monitor_override() -> None:
    saved = [
        {"name": "Misfire", "supported": True, "ready": True},
        {"name": "Catalyst", "supported": True, "ready": True},
    ]
    out = apply_monitors_override(saved, {"Catalyst": {"ready": False}})
    assert out[0]["ready"] is True
    assert out[1]["ready"] is False
    assert saved[1]["ready"] is True  # input unchanged


def test_preset_instantiate_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    # capture a session first so the preset has a vehicle to attach to
    r = c.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.2,
                                              "pids": ["010C", "010D"]})
    assert r.status_code == 200
    src_id = r.json()["session_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        if not c.get("/api/sessions/current").json().get("active"):
            break
        time.sleep(0.1)

    r = c.post(f"/api/presets/p0301_misfire/instantiate?source_session_id={src_id}")
    assert r.status_code == 200
    payload = r.json()
    assert payload["label"].startswith("P0301")
    assert any(d["code"] == "P0301" for d in payload["dtcs"])
    # Live overrides came from the preset
    assert payload["live_overrides"]["010C"] == 920


def test_preset_with_monitor_override_applied(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.2})
    sid = r.json()["session_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        if not c.get("/api/sessions/current").json().get("active"):
            break
        time.sleep(0.1)
    r = c.post(f"/api/presets/monitors_incomplete/instantiate?source_session_id={sid}")
    assert r.status_code == 200
    monitors = r.json()["monitors"]
    cat = next((m for m in monitors if m["name"] == "Catalyst"), None)
    assert cat is not None
    assert cat["ready"] is False  # preset overrode the saved-ready value


# -------- mode 0x22 encoding --------

def test_mfg_pid_encoder_round_trip() -> None:
    # GM oil life 50% → 0x80, decode formula b[0] * 100/255 ≈ 50
    out = encode_mfg_pid("220005", 50)
    assert out == bytes([int(round(50 * 255 / 100))])

    # Honda ATF temp 60°C → 100, decoded back as b[0] - 40 = 60
    assert encode_mfg_pid("22015C", 60) == bytes([100])


def test_ecu_responds_to_mode_22_request() -> None:
    state = ScenarioState(live={"220005": 75})
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x22, 0x00, 0x05]))
    assert resp[0] == 0x62
    assert resp[1:3] == bytes([0x00, 0x05])
    # Decode the response byte back: 75% oil life
    assert resp[3] == int(round(75 * 255 / 100))


def test_ecu_unknown_mode_22_pid_returns_nrc() -> None:
    ecu = EcuEmulator()
    resp = ecu.handle(bytes([0x22, 0xAB, 0xCD]))
    assert resp[0] == 0x7F
    assert resp[1] == 0x22


# -------- request log --------

def test_request_log_captures_each_interaction() -> None:
    ecu = EcuEmulator(ScenarioState(live={"010C": 1500}, vin="2HGFC2F59FH123456"))
    ecu.handle(bytes([0x01, 0x0C]))  # RPM read
    ecu.handle(bytes([0x09, 0x02]))  # VIN read
    ecu.handle(bytes([0x99]))         # unsupported service
    log = ecu.recent_log()
    assert len(log) == 3
    assert log[0]["service"] == 0x01
    assert "PID 0x0C" in log[0]["summary"]
    assert log[1]["service"] == 0x09
    assert "PID 0x02" in log[1]["summary"]
    assert log[2]["service"] == 0x99
    assert "NRC" in log[2]["summary"]


def test_request_log_is_bounded() -> None:
    ecu = EcuEmulator(state=ScenarioState(live={"010C": 1000}), log_capacity=20)
    for _ in range(50):
        ecu.handle(bytes([0x01, 0x0C]))
    log = ecu.recent_log(limit=100)
    assert len(log) == 20  # bounded ring buffer


def test_log_summary_for_clear_dtcs() -> None:
    ecu = EcuEmulator()
    ecu.handle(bytes([0x04]))
    log = ecu.recent_log()
    assert log[-1]["service"] == 0x04
    assert log[-1]["summary"] == "clear DTCs"
