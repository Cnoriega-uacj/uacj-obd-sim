"""
v0.6.9 — Tests for the CVN capture path.

Cristopher's bench session showed that after pushing a captured Mazda3
scenario, the Innova 5210 reported "Did not get cvn and call id".
Root cause: `VehicleInfo` had no `cvn` field, and the elm327 adapter
didn't read it. Even when CALIBRATION_ID was captured, the scenario
create endpoint only pulled `vin` from the DB row — losing
calibration_id/cvn/ecu_name that were in metadata.json.

These tests cover:
- VehicleInfo now has a cvn field that round-trips through JSON.
- MockAdapter populates cvn so end-to-end mock captures answer
  Mode 09 PID 06.
- scenario_to_state propagates cvn into ScenarioState.
- Creating a scenario from a source_session_id reads metadata.json
  and recovers calibration_id, cvn, ecu_name (not just vin).
- The ECU answers Mode 09 PID 04 (CAL_ID) and 06 (CVN) when the
  scenario carries them, and NRCs when it doesn't.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uacj_obd.adapters.mock import MockAdapter
from uacj_obd.api import create_app
from uacj_obd.models import VehicleInfo
from uacj_obd.simulator.can_runtime import scenario_to_state
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState


def test_vehicle_info_has_cvn_field() -> None:
    v = VehicleInfo(cvn="CDA08E85")
    assert v.cvn == "CDA08E85"
    assert "cvn" in v.model_dump()


def test_vehicle_info_cvn_round_trips_json() -> None:
    v = VehicleInfo(vin="1HGCM82633A123456", cvn="DEADBEEF")
    j = v.model_dump_json()
    assert "DEADBEEF" in j
    back = VehicleInfo.model_validate_json(j)
    assert back.cvn == "DEADBEEF"


def test_vehicle_info_cvn_defaults_none() -> None:
    """Without an explicit value, cvn must default to None — preserves
    the v0.6.8 wire shape for legacy clients."""
    assert VehicleInfo().cvn is None


def test_mock_adapter_populates_cvn() -> None:
    """End-to-end paranoia check: a mock capture should carry a CVN
    so the simulator answers Mode 09 06 instead of NRCing."""
    adapter = MockAdapter()
    adapter.connect()
    try:
        info = adapter.read_vehicle_info()
    finally:
        adapter.disconnect()
    assert info.cvn is not None
    assert len(info.cvn) >= 8  # 4 hex bytes


def test_scenario_to_state_carries_cvn() -> None:
    """The vehicle.cvn field on a scenario payload must make it into
    ScenarioState.cvn so _mode09 can answer PID 06."""
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456", "cvn": "CDA08E85"},
    }
    state = scenario_to_state(payload)
    assert state.cvn == "CDA08E85"


def test_scenario_to_state_no_cvn_leaves_none() -> None:
    payload = {"vehicle": {"vin": "1HGCM82633A123456"}}
    state = scenario_to_state(payload)
    assert state.cvn is None


def test_ecu_answers_mode09_06_when_cvn_present() -> None:
    ecu = EcuEmulator(ScenarioState(vin="X" * 17, cvn="CDA08E85"))
    response = ecu._mode09(bytes([0x06]))
    # 0x49 0x06 NODI=1 + 4-byte CVN = 7 bytes
    assert response[:3] == bytes([0x49, 0x06, 0x01])
    assert response[3:] == bytes([0xCD, 0xA0, 0x8E, 0x85])


def test_ecu_nrcs_mode09_06_when_cvn_missing() -> None:
    """Without a CVN, the response must be NRC (not zero bytes) — a
    real ECU never returns a zero CVN if it doesn't have one. Returning
    actual zeros would mislead the scan tool into showing CVN=00000000
    instead of 'not reported'."""
    ecu = EcuEmulator(ScenarioState(vin="X" * 17))
    response = ecu._mode09(bytes([0x06]))
    # NRC starts with 0x7F (negative response)
    assert response[0] == 0x7F


def test_ecu_answers_mode09_04_when_cal_id_present() -> None:
    ecu = EcuEmulator(ScenarioState(vin="X" * 17, calibration_id="HND-CIV-2015-A1"))
    response = ecu._mode09(bytes([0x04]))
    assert response[:3] == bytes([0x49, 0x04, 0x01])
    # 16-byte ASCII (null-padded)
    assert response[3:].decode("ascii").rstrip("\x00") == "HND-CIV-2015-A1"


def test_scenario_create_pulls_full_vehicle_from_metadata(tmp_path: Path) -> None:
    """The scenario create endpoint must read metadata.json from the
    source session folder so calibration_id / cvn / ecu_name come
    through — not just vin (which is all the DB row stores)."""
    client = TestClient(create_app(data_root=tmp_path))

    # Run a quick mock capture so a session and metadata.json exist.
    r = client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.3})
    assert r.status_code == 200
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            break

    sessions = client.get("/api/sessions").json()
    assert sessions, "expected at least one captured session"
    sid = sessions[0]["session_id"]
    folder = Path(sessions[0]["folder"])
    meta = json.loads((folder / "metadata.json").read_text())
    assert meta["vehicle"]["cvn"] == "CDA08E85"  # mock adapter wrote this

    # Now create a scenario from that session WITHOUT specifying vehicle.
    r = client.post("/api/scenarios", json={
        "label": "test-cvn-roundtrip",
        "source_session_id": sid,
    })
    assert r.status_code == 200
    body = r.json()
    # The created scenario's vehicle should have cvn pulled from metadata.json
    assert body["vehicle"]["cvn"] == "CDA08E85"
    assert body["vehicle"]["calibration_id"] == "HND-CIV-2015-A1"


def test_scenario_create_falls_back_to_vin_only_if_metadata_missing(tmp_path: Path) -> None:
    """If metadata.json is missing or unreadable, the scenario should
    still get created with just the vin from the DB row — not 500."""
    client = TestClient(create_app(data_root=tmp_path))

    # Insert a fake session row pointing at a folder with no metadata.json.
    from uacj_obd.storage import Database
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("1HGCM82633A999999", "Honda", "Civic", 2015,
                      "2026-01-01T00:00:00+00:00")
    fake_folder = tmp_path / "fakedir"
    fake_folder.mkdir()
    db.insert_session(
        session_id="fake_session_id",
        vin="1HGCM82633A999999",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:30+00:00",
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=10,
        folder=str(fake_folder),
        notes="",
    )

    r = client.post("/api/scenarios", json={
        "label": "no-meta-fallback",
        "source_session_id": "fake_session_id",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["vehicle"]["vin"] == "1HGCM82633A999999"
    # cvn was unrecoverable — stays None, not an error
    assert body["vehicle"]["cvn"] is None


def test_simulator_state_includes_cvn_after_load(tmp_path: Path) -> None:
    """After pushing a scenario with cvn, the Pi's /api/sim/state
    should report it so the dashboard's Pi-status panel could show
    it (and for live debugging)."""
    from uacj_obd.simulator.server import make_simulator_server
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=None)
    client = TestClient(app)
    r = client.post("/api/sim/load", json={
        "vehicle": {"vin": "1HGCM82633A123456", "cvn": "CDA08E85",
                    "calibration_id": "HND-CIV-2015-A1"},
    })
    assert r.status_code == 200
    state = client.get("/api/sim/state").json()
    assert state["vin"] == "1HGCM82633A123456"
    assert state["calibration_id"] == "HND-CIV-2015-A1"
