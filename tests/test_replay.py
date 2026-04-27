"""Tests for the capture → save → replay (with overrides) round-trip."""

from __future__ import annotations

from pathlib import Path

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.adapters.replay import ReplayAdapter
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database, SessionStore


def _capture(tmp_path: Path) -> Path:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter("mock")
    sess = AcquisitionSession(a, store, db, pid_reg,
                                SessionConfig(pids=["010C", "010D", "0105"], sample_interval_s=0.0))
    sess.start()
    sess.run(duration_s=0.3)
    return sess.close()


def test_replay_reproduces_saved_session(tmp_path: Path) -> None:
    folder = _capture(tmp_path)
    r = ReplayAdapter(folder)
    r.connect()
    info = r.read_vehicle_info()
    assert info.vin == "2HGFC2F59FH123456"
    dtcs = r.read_dtcs()
    assert any(d.code == "P0420" for d in dtcs)
    rpm = r.read_pid("010C")
    assert rpm is not None and rpm.name == "RPM"
    r.disconnect()


def test_replay_with_dtc_override(tmp_path: Path) -> None:
    folder = _capture(tmp_path)
    overrides = {
        "dtcs": [{"code": "P0301", "status": "stored",
                   "description": "Cylinder 1 Misfire Detected"}],
    }
    r = ReplayAdapter(folder, scenario_overrides=overrides)
    r.connect()
    dtcs = r.read_dtcs()
    assert len(dtcs) == 1
    assert dtcs[0].code == "P0301"


def test_replay_with_live_override(tmp_path: Path) -> None:
    folder = _capture(tmp_path)
    overrides = {"live_overrides": {"010C": 4500}}
    r = ReplayAdapter(folder, scenario_overrides=overrides)
    r.connect()
    rpm = r.read_pid("010C")
    assert rpm is not None
    assert rpm.value == 4500
    # Non-overridden PIDs still come from the saved data
    speed = r.read_pid("010D")
    assert speed is not None and speed.name == "SPEED"


def test_replay_clear_dtcs_only_affects_overrides(tmp_path: Path) -> None:
    folder = _capture(tmp_path)
    r = ReplayAdapter(folder, scenario_overrides={"dtcs": [
        {"code": "P0420", "status": "stored", "description": ""},
    ]})
    r.connect()
    assert r.clear_dtcs() is True
    assert r.read_dtcs() == []
