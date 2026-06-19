"""
v0.6.7 — Tests for Pi-side scenario persistence and the simulator
server's auto-restore behaviour.

Covers `uacj_obd.simulator.scenario_persistence` (pure helpers) and
the wiring in `uacj_obd.simulator.server` (save on /load, auto-restore
on construction, persistence endpoints).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uacj_obd.simulator.ecu import EcuEmulator
from uacj_obd.simulator.scenario_persistence import (
    MAX_PAYLOAD_BYTES,
    clear_last_scenario,
    load_last_scenario,
    persistence_status,
    save_last_scenario,
)
from uacj_obd.simulator.server import make_simulator_server


def _sample_payload() -> dict:
    return {
        "vehicle": {
            "vin": "1HGCM82633A123456",
            "make": "Honda",
            "model": "Accord",
            "year": 2015,
        },
        "dtcs": [{"code": "P0420", "status": "stored"}],
        "live_overrides": {"010C": 1200},
    }


# ---------------------------------------------------------------------------
# save_last_scenario
# ---------------------------------------------------------------------------

def test_save_writes_payload_to_path(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    assert save_last_scenario(_sample_payload(), target) is True
    assert target.exists()
    on_disk = json.loads(target.read_text())
    assert on_disk["vehicle"]["vin"] == "1HGCM82633A123456"


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "nested" / "last.json"
    assert save_last_scenario(_sample_payload(), target) is True
    assert target.exists()


def test_save_rejects_non_dict_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    assert save_last_scenario("not a dict", target) is False  # type: ignore[arg-type]
    assert not target.exists()


def test_save_rejects_oversized_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    huge = {"blob": "x" * (MAX_PAYLOAD_BYTES + 100)}
    assert save_last_scenario(huge, target) is False
    assert not target.exists()


def test_save_overwrites_existing_atomically(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    save_last_scenario({"vehicle": {"vin": "OLD"}}, target)
    save_last_scenario({"vehicle": {"vin": "NEW"}}, target)
    assert json.loads(target.read_text())["vehicle"]["vin"] == "NEW"


def test_save_does_not_leak_tempfile_on_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)
    save_last_scenario(_sample_payload(), target)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "last.json"]
    assert leftovers == [], f"unexpected leftover files: {leftovers}"


def test_save_handles_non_json_serializable_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    payload = {"thing": object()}
    assert save_last_scenario(payload, target) is False  # type: ignore[arg-type]
    assert not target.exists()


# ---------------------------------------------------------------------------
# load_last_scenario
# ---------------------------------------------------------------------------

def test_load_returns_none_when_absent(tmp_path: Path) -> None:
    assert load_last_scenario(tmp_path / "missing.json") is None


def test_load_returns_saved_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)
    loaded = load_last_scenario(target)
    assert loaded is not None
    assert loaded["vehicle"]["vin"] == "1HGCM82633A123456"
    assert loaded["live_overrides"]["010C"] == 1200


def test_load_quarantines_corrupt_json(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    target.write_text("{not valid json")
    assert load_last_scenario(target) is None
    assert not target.exists(), "corrupt file should be quarantined"
    assert (tmp_path / "last.json.corrupt").exists()


def test_load_quarantines_non_dict_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    target.write_text(json.dumps([1, 2, 3]))
    assert load_last_scenario(target) is None
    assert (tmp_path / "last.json.corrupt").exists()


def test_load_handles_binary_garbage(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    target.write_bytes(b"\xff\xfe\xfd not utf-8")
    assert load_last_scenario(target) is None
    assert (tmp_path / "last.json.corrupt").exists()


# ---------------------------------------------------------------------------
# clear_last_scenario
# ---------------------------------------------------------------------------

def test_clear_removes_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)
    assert clear_last_scenario(target) is True
    assert not target.exists()


def test_clear_is_idempotent_when_absent(tmp_path: Path) -> None:
    assert clear_last_scenario(tmp_path / "missing.json") is True


# ---------------------------------------------------------------------------
# persistence_status
# ---------------------------------------------------------------------------

def test_status_reports_absent(tmp_path: Path) -> None:
    info = persistence_status(tmp_path / "missing.json")
    assert info == {"exists": False, "path": str(tmp_path / "missing.json")}


def test_status_reports_present_with_vin(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)
    info = persistence_status(target)
    assert info["exists"] is True
    assert info["vin"] == "1HGCM82633A123456"
    assert info["size_bytes"] > 0


# ---------------------------------------------------------------------------
# Simulator server integration
# ---------------------------------------------------------------------------

def test_load_endpoint_persists_payload(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target)
    client = TestClient(app)
    r = client.post("/api/sim/load", json=_sample_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["loaded"] is True
    assert body["persisted"] is True
    assert target.exists()


def test_load_endpoint_with_persistence_disabled(tmp_path: Path) -> None:
    """persistence_path=None disables the mirror entirely."""
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=None)
    client = TestClient(app)
    r = client.post("/api/sim/load", json=_sample_payload())
    assert r.status_code == 200
    assert r.json()["persisted"] is False


def test_auto_restore_re_applies_scenario_on_construction(tmp_path: Path) -> None:
    """Simulating a Pi reboot: persist, then build a fresh server
    with auto_restore=True and the ECU should come up pre-loaded."""
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)

    fresh_ecu = EcuEmulator()
    assert fresh_ecu.state.vin in (None, "")  # baseline

    app = make_simulator_server(fresh_ecu, persistence_path=target, auto_restore=True)
    assert fresh_ecu.state.vin == "1HGCM82633A123456"

    # The /api/sim/state endpoint should also reflect the restore.
    client = TestClient(app)
    r = client.get("/api/sim/state")
    assert r.json()["vin"] == "1HGCM82633A123456"


def test_auto_restore_no_op_when_no_saved_scenario(tmp_path: Path) -> None:
    """Auto-restore with no file on disk shouldn't crash or warn-fatal."""
    target = tmp_path / "last.json"
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target, auto_restore=True)
    # ECU stays at defaults; the endpoint still works.
    client = TestClient(app)
    assert client.get("/api/sim/health").status_code == 200


def test_auto_restore_disabled_by_default(tmp_path: Path) -> None:
    """Tests don't auto-restore unless the caller asks for it."""
    target = tmp_path / "last.json"
    save_last_scenario(_sample_payload(), target)
    fresh_ecu = EcuEmulator()
    make_simulator_server(fresh_ecu, persistence_path=target)  # no auto_restore
    assert fresh_ecu.state.vin in (None, "")


def test_persistence_endpoint_reports_status(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target)
    client = TestClient(app)

    r = client.get("/api/sim/persistence")
    assert r.json() == {"enabled": True, "exists": False, "path": str(target)}

    client.post("/api/sim/load", json=_sample_payload())
    r = client.get("/api/sim/persistence")
    body = r.json()
    assert body["enabled"] is True
    assert body["exists"] is True
    assert body["vin"] == "1HGCM82633A123456"


def test_persistence_endpoint_when_disabled(tmp_path: Path) -> None:
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=None)
    client = TestClient(app)
    assert client.get("/api/sim/persistence").json() == {"enabled": False}


def test_persistence_clear_endpoint(tmp_path: Path) -> None:
    target = tmp_path / "last.json"
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target)
    client = TestClient(app)
    client.post("/api/sim/load", json=_sample_payload())
    assert target.exists()

    r = client.post("/api/sim/persistence/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["cleared"] is True
    assert not target.exists()


def test_persistence_clear_when_disabled(tmp_path: Path) -> None:
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=None)
    client = TestClient(app)
    body = client.post("/api/sim/persistence/clear").json()
    assert body == {"enabled": False, "cleared": False}


def test_corrupt_persisted_file_does_not_break_auto_restore(tmp_path: Path) -> None:
    """A corrupt file on disk should be quarantined and the server
    should come up with default state, not crash."""
    target = tmp_path / "last.json"
    target.write_text("{not valid")
    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target, auto_restore=True)
    client = TestClient(app)
    assert client.get("/api/sim/health").status_code == 200
    # The corrupt file got quarantined.
    assert (tmp_path / "last.json.corrupt").exists()


def test_load_with_timeline_persists_and_restores(tmp_path: Path) -> None:
    """A payload with live_timeseries should round-trip through
    persistence and re-arm the replay engine on auto-restore."""
    target = tmp_path / "last.json"
    payload = _sample_payload()
    payload["live_timeseries"] = [
        {"t": 0.0, "pid": "010C", "value": 800},
        {"t": 0.1, "pid": "010C", "value": 1200},
    ]
    payload["live_timeseries_loop"] = True

    ecu = EcuEmulator()
    app = make_simulator_server(ecu, persistence_path=target)
    client = TestClient(app)
    r = client.post("/api/sim/load", json=payload)
    assert r.json()["replay_samples"] == 2

    # New ecu + auto-restore should re-arm the engine.
    fresh_ecu = EcuEmulator()
    fresh_app = make_simulator_server(
        fresh_ecu, persistence_path=target, auto_restore=True,
    )
    fresh_client = TestClient(fresh_app)
    state = fresh_client.get("/api/sim/state").json()
    assert state["vin"] == "1HGCM82633A123456"
    assert state["replay"]["running"] is True
