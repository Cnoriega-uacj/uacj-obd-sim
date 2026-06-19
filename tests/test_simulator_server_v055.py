"""
v0.5.5 — Tests for the Pi-side simulator HTTP server.

Audit finding: `uacj_obd/simulator/server.py` had **0% coverage**
before this module. The `EcuEmulator` and `scenario_to_state` were
heavily tested in isolation, but the FastAPI wrapper that the Pi
actually runs to receive `/api/sim/load` from the dashboard had never
been exercised. If the v0.5.0 ReplayEngine wiring broke, or the
v0.4.12 CVN dispatch broke at the HTTP boundary, nothing in CI would
catch it. This module locks the HTTP routes in.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.server import make_simulator_server


def _client() -> TestClient:
    ecu = EcuEmulator(ScenarioState())
    app = make_simulator_server(ecu)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health / state
# ---------------------------------------------------------------------------

def test_health_returns_ok_for_empty_state() -> None:
    c = _client()
    r = c.get("/api/sim/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["vin"] is None
    assert body["stored_dtcs"] == []


def test_state_returns_full_snapshot() -> None:
    c = _client()
    r = c.get("/api/sim/state")
    assert r.status_code == 200
    body = r.json()
    for required in ("vin", "calibration_id", "ecu_name", "stored_dtcs",
                       "pending_dtcs", "permanent_dtcs", "live_pids",
                       "monitor_status", "replay"):
        assert required in body, f"missing field {required!r}"
    # Replay sub-dict shape
    rep = body["replay"]
    assert rep["running"] is False
    assert rep["samples_applied"] == 0
    assert rep["iterations"] == 0


# ---------------------------------------------------------------------------
# /api/sim/load — the critical endpoint
# ---------------------------------------------------------------------------

def test_load_scenario_with_vin_updates_state() -> None:
    c = _client()
    payload = {
        "vehicle": {"vin": "JM1BL1L72C1627697"},
        "dtcs": [{"code": "P0420", "status": "stored"}],
    }
    r = c.post("/api/sim/load", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["loaded"] is True
    assert body["vin"] == "JM1BL1L72C1627697"
    assert body["replay_samples"] == 0

    # Health endpoint should reflect the new state.
    state = c.get("/api/sim/state").json()
    assert state["vin"] == "JM1BL1L72C1627697"
    assert state["stored_dtcs"] == ["P0420"]


def test_load_scenario_with_replay_starts_engine() -> None:
    c = _client()
    payload = {
        "vehicle": {"vin": "TEST1234567890123"},
        "live_timeseries": [
            {"t": 0.0, "pid": "010C", "value": 800},
            {"t": 0.05, "pid": "010C", "value": 1500},
            {"t": 0.1, "pid": "010C", "value": 2000},
        ],
        "live_timeseries_loop": True,
    }
    r = c.post("/api/sim/load", json=payload)
    assert r.status_code == 200
    assert r.json()["replay_samples"] == 3

    # Give the engine a chance to mutate state.
    time.sleep(0.3)
    state = c.get("/api/sim/state").json()
    assert state["replay"]["running"] is True
    # samples_applied should be growing because we set loop=True and
    # waited > one full pass.
    assert state["replay"]["samples_applied"] >= 3

    # Stop the engine explicitly so it doesn't leak past test boundaries.
    stop_resp = c.post("/api/sim/replay/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["stopped"] is True


def test_load_second_scenario_stops_previous_replay() -> None:
    """v0.5.0 invariant: loading a new scenario MUST stop any old
    ReplayEngine so its writes can't race with the new scenario's
    static live_overrides."""
    c = _client()
    c.post("/api/sim/load", json={
        "vehicle": {"vin": "FIRST_VIN_PLACEHOLDER"},
        "live_timeseries": [{"t": 0.0, "pid": "010C", "value": 999}],
        "live_timeseries_loop": True,
    })
    time.sleep(0.1)
    assert c.get("/api/sim/state").json()["replay"]["running"] is True

    # Load a second scenario WITHOUT replay.
    c.post("/api/sim/load", json={
        "vehicle": {"vin": "SECOND_VIN_PLACEHLD"},
        "live_overrides": {"010C": 500},
    })
    time.sleep(0.1)
    state = c.get("/api/sim/state").json()
    assert state["replay"]["running"] is False
    assert state["vin"] == "SECOND_VIN_PLACEHLD"


def test_load_empty_payload_does_not_crash() -> None:
    """Defensive: an empty payload should produce a clean response,
    not a 500."""
    c = _client()
    r = c.post("/api/sim/load", json={})
    assert r.status_code == 200
    assert r.json()["loaded"] is True


def test_clear_dtcs_endpoint() -> None:
    c = _client()
    c.post("/api/sim/load", json={
        "vehicle": {"vin": "JM1BL1L72C1627697"},
        "dtcs": [{"code": "P0420", "status": "stored"}],
    })
    assert c.get("/api/sim/state").json()["stored_dtcs"] == ["P0420"]

    r = c.post("/api/sim/clear")
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    assert c.get("/api/sim/state").json()["stored_dtcs"] == []


def test_replay_stop_when_nothing_running_is_clean() -> None:
    c = _client()
    r = c.post("/api/sim/replay/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["stopped"] is False
    assert "no replay" in body["reason"].lower()


# ---------------------------------------------------------------------------
# /api/sim/log
# ---------------------------------------------------------------------------

def test_log_endpoint_returns_empty_list_on_fresh_ecu() -> None:
    c = _client()
    r = c.get("/api/sim/log")
    assert r.status_code == 200
    assert r.json() == []


def test_log_records_requests_after_load() -> None:
    """A `load` causes scenario_to_state but doesn't itself add a log
    entry — only OBD-II requests through the ECU do. We exercise the
    ECU directly through its emulator to confirm the log endpoint
    surfaces requests."""
    ecu = EcuEmulator(ScenarioState(vin="JM1BL1L72C1627697"))
    app = make_simulator_server(ecu)
    client = TestClient(app)
    # Drive the ECU with a Mode 09 PID 02 (VIN) request.
    ecu.handle(bytes([0x09, 0x02]))
    log_entries = client.get("/api/sim/log").json()
    assert len(log_entries) >= 1
    # Each entry has the expected shape from EcuEmulator._log_interaction
    entry = log_entries[-1]
    assert "ts" in entry
    assert "request" in entry
    assert "response" in entry


def test_log_endpoint_respects_limit_parameter() -> None:
    ecu = EcuEmulator(ScenarioState(vin="JM1BL1L72C1627697"))
    app = make_simulator_server(ecu)
    client = TestClient(app)
    for _ in range(5):
        ecu.handle(bytes([0x09, 0x02]))
    r = client.get("/api/sim/log?limit=3")
    assert r.status_code == 200
    assert len(r.json()) <= 3
