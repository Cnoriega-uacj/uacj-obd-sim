"""Tests for mode 0x22 manufacturer PID decode + session integration."""

from __future__ import annotations

from pathlib import Path

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters.mock import MockAdapter
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database, SessionStore


class _MfgMockAdapter(MockAdapter):
    """Mock that returns canned bytes for two mode 0x22 PIDs."""

    def read_raw(self, mode: int, pid: int | None = None):
        if mode != 0x22:
            return None
        # GM 0x0005 oil life — 1 byte: 0x80 → ~50%
        if pid == 0x0005:
            return bytes([0x80])
        # Honda 0x015C ATF temp — 1 byte, value - 40
        if pid == 0x015C:
            return bytes([100])  # 60 °C
        return None


def test_registry_decodes_mfg_pids() -> None:
    reg = load_default_registry()
    # GM oil life
    assert abs(reg.decode("220005", bytes([0x80])) - (0x80 * 100 / 255)) < 0.01
    # Honda ATF temp
    assert reg.decode("22015C", bytes([100])) == 60


def test_session_loop_reads_manufacturer_pids(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    store = SessionStore(tmp_path / "sessions")
    pid_reg = load_default_registry()
    a = _MfgMockAdapter()
    cfg = SessionConfig(
        pids=["010C"],
        manufacturer_pids=["220005", "22015C"],
        sample_interval_s=0.0,
    )
    sess = AcquisitionSession(a, store, db, pid_reg, cfg)
    sess.start()
    sess.run(duration_s=0.2)
    folder = sess.close()
    live = (folder / "live_data.jsonl").read_text().splitlines()
    pids_seen = {line and __import__("json").loads(line)["pid"] for line in live if line}
    assert "220005" in pids_seen
    assert "22015C" in pids_seen
