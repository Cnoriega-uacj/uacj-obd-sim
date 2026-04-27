"""End-to-end smoke test of the Phase 1 acquisition flow."""

from __future__ import annotations

import json
from pathlib import Path

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import ConnectionState, open_adapter
from uacj_obd.adapters.mock import MockAdapter
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database, SessionStore


def test_mock_adapter_lifecycle() -> None:
    a = MockAdapter()
    assert a.status().state == ConnectionState.DISCONNECTED
    a.connect()
    assert a.status().state == ConnectionState.CONNECTED
    info = a.read_vehicle_info()
    assert info.vin and len(info.vin) == 17
    dtcs = a.read_dtcs()
    assert any(d.code == "P0420" for d in dtcs)
    monitors = a.read_monitors()
    assert any(m.name == "Catalyst" for m in monitors)
    sample = a.read_pid("010C")
    assert sample is not None and sample.name == "RPM"
    a.disconnect()


def test_full_capture_flow(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter("mock")
    cfg = SessionConfig(pids=["010C", "010D", "0105"], sample_interval_s=0.0)
    sess = AcquisitionSession(a, store, db, pid_reg, cfg)

    meta = sess.start()
    assert meta.session_id
    assert meta.vehicle.vin

    n = sess.run(duration_s=0.5)
    assert n > 0

    folder = sess.close()
    assert folder.exists()
    assert (folder / "metadata.json").exists()
    assert (folder / "live_data.jsonl").exists()
    assert (folder / "dtcs.json").exists()
    assert (folder / "monitors.json").exists()

    # session is queryable from DB
    row = db.get_session(meta.session_id)
    assert row is not None
    assert row["sample_count"] >= n

    # vehicle is registered
    vehicles = db.list_vehicles()
    assert len(vehicles) == 1
    assert vehicles[0]["vin"] == meta.vehicle.vin


def test_pid_registry_decode() -> None:
    reg = load_default_registry()
    rpm = reg.get("010C")
    assert rpm is not None
    # 0x0F 0x70 → (15*256 + 112)/4 = 988 RPM
    assert reg.decode("010C", bytes([0x0F, 0x70])) == 988.0
    # speed 0x32 → 50 km/h
    assert reg.decode("010D", bytes([0x32])) == 50


def test_pid_registry_includes_manufacturer() -> None:
    reg = load_default_registry()
    ford_pids = reg.by_manufacturer("Ford")
    assert any(p.name == "FORD_TRANS_OIL_TEMP" for p in ford_pids)
    gm_pids = reg.by_manufacturer("GM")
    assert any(p.name == "GM_ENGINE_OIL_LIFE" for p in gm_pids)


def test_session_store_per_vehicle_layout(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter("mock")
    sess = AcquisitionSession(a, store, db, pid_reg, SessionConfig(sample_interval_s=0.0))
    sess.start()
    sess.run(duration_s=0.2)
    folder = sess.close()
    # vehicle folder is named with VIN
    assert "2HGFC2F59FH123456" in str(folder)
    # session folder is a child of the vehicle folder
    assert folder.parent.name.startswith("2HGFC2F59FH123456")


def test_export_csv(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter("mock")
    sess = AcquisitionSession(a, store, db, pid_reg, SessionConfig(sample_interval_s=0.0))
    sess.start()
    sess.run(duration_s=0.2)
    folder = sess.close()
    # mimic the API export path
    jsonl = folder / "live_data.jsonl"
    samples = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    assert all("pid" in s and "value" in s for s in samples)
