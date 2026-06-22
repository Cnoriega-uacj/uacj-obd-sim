"""
v0.6.17 — Tests for multi-ECU bitmap probing and the new
PID 0x67 (multi-ECT) and 0x68 (multi-IAT) encoders.

Cristopher's Innova on the real Mazda3 showed "ECT 1" / "ECT 2"
and "IAT 11" / "IAT 12" — the transmission ECU at $7E9 reports
its own copy of the engine sensors, and our raw bitmap probe was
filtering responses to engine ECU only.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from uacj_obd.adapters import elm327 as elm_mod
from uacj_obd.simulator.encoders import (
    _ENCODERS,
    encode_pid,
    is_answerable,
)


# ---------------------------------------------------------------------------
# PID 0x67 — multi-ECT
# ---------------------------------------------------------------------------


def test_multi_ect_encoder_registered() -> None:
    assert "0167" in _ENCODERS


def test_multi_ect_single_sensor_encodes() -> None:
    """A single number value → ECT-1 supported only."""
    out = encode_pid("0167", 87.0)
    # Bitmap byte 0 = bit 0 set (ECT1 supported), then ECT1 = 87 + 40 = 127
    assert out == bytes([0x01, 127])


def test_multi_ect_two_sensors_encodes() -> None:
    """[ECT1, ECT2] list → both supported bits set."""
    out = encode_pid("0167", [87.0, 83.0])
    # Bitmap = bits 0 + 1 = 0x03; values 127 and 123
    assert out == bytes([0x03, 127, 123])


def test_multi_ect_below_freezing() -> None:
    """ECT of -10°C → raw byte 30 (offset by 40)."""
    out = encode_pid("0167", -10.0)
    assert out == bytes([0x01, 30])


def test_multi_ect_high_clamps_to_u8() -> None:
    """ECT above 215°C clamps to 0xFF (255 - 40 = 215 max)."""
    out = encode_pid("0167", 300.0)
    assert out == bytes([0x01, 0xFF])


def test_multi_ect_invalid_returns_empty_bitmap() -> None:
    """Non-numeric, non-list value → bitmap=0, no sensor bytes."""
    out = encode_pid("0167", "not a number")
    assert out == bytes([0x00])


def test_multi_ect_raw_passthrough_wins() -> None:
    """If the scenario carries 'raw:HEX', that bypasses the formula."""
    out = encode_pid("0167", "raw:037F7B")
    assert out == bytes([0x03, 0x7F, 0x7B])


def test_multi_ect_is_answerable() -> None:
    assert is_answerable("0167", 87.0) is True
    assert is_answerable("0167", [87.0, 83.0]) is True


# ---------------------------------------------------------------------------
# PID 0x68 — multi-IAT
# ---------------------------------------------------------------------------


def test_multi_iat_encoder_registered() -> None:
    assert "0168" in _ENCODERS


def test_multi_iat_two_sensors_encodes() -> None:
    """[IAT11, IAT12] → bitmap 0x03 + two values."""
    out = encode_pid("0168", [50.0, 55.0])
    # 50 + 40 = 90, 55 + 40 = 95
    assert out == bytes([0x03, 90, 95])


def test_multi_iat_eight_sensors() -> None:
    """Spec allows up to 8 sensors."""
    out = encode_pid("0168", [10.0] * 8)
    assert out[0] == 0xFF  # all 8 bits set
    assert len(out) == 9
    assert all(b == 50 for b in out[1:])  # 10 + 40 = 50 each


def test_multi_iat_truncates_past_eight() -> None:
    """More than 8 → truncated to 8."""
    out = encode_pid("0168", [10.0] * 12)
    assert len(out) == 9  # bitmap + 8 sensors


def test_multi_iat_single_number() -> None:
    out = encode_pid("0168", 25.0)
    assert out == bytes([0x01, 65])


# ---------------------------------------------------------------------------
# Multi-ECU bitmap probe — ECU.ALL used so trans ECU also gets included
# ---------------------------------------------------------------------------


class _CapturingConn:
    """Fake python-obd connection that records the ECU filter used."""

    def __init__(self) -> None:
        self.queries: list = []

    def is_connected(self) -> bool:
        return True

    def query(self, cmd, force=False):
        self.queries.append(cmd)
        # Return a minimal bitmap covering just PID 0x01 so the probe
        # walks back cleanly.
        return SimpleNamespace(
            value=bytes([0x80, 0x00, 0x00, 0x00]),
            messages=[],
            is_null=lambda: False,
        )


def test_raw_bitmap_probe_uses_ecu_all() -> None:
    """v0.6.17: probe must use ECU.ALL (= 255) so trans-ECU
    responses don't get filtered out."""
    from obd import ECU

    adapter = elm_mod.Elm327Adapter()
    conn = _CapturingConn()
    adapter._conn = conn
    adapter._raw_supported_pids()

    # ECU.ALL is 255 in python-obd
    assert ECU.ALL == 255
    # Every synthesized command should use ECU.ALL, not ECU.ENGINE
    assert conn.queries, "expected at least one probe query"
    for cmd in conn.queries:
        assert cmd.ecu == ECU.ALL, (
            f"command {cmd.command} used ECU={cmd.ecu}, "
            f"expected ECU.ALL ({ECU.ALL})"
        )


def test_raw_bitmap_probe_picks_up_multi_ecu_response() -> None:
    """Engine + trans both responding to PID 0x00 should produce a
    union of their bitmaps in the discovered set."""

    class _MultiEcuConn:
        def is_connected(self) -> bool:
            return True

        def query(self, cmd, force=False):
            # Both ECUs respond — engine claims PID 0x01, trans claims PID 0x05.
            engine_msg = SimpleNamespace(data=bytes([0x80, 0x00, 0x00, 0x00]))
            trans_msg = SimpleNamespace(data=bytes([0x08, 0x00, 0x00, 0x00]))  # bit 4 = PID 0x05
            return SimpleNamespace(
                value=[engine_msg, trans_msg],
                messages=[engine_msg, trans_msg],
                is_null=lambda: False,
            )

    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _MultiEcuConn()
    pids = adapter._raw_supported_pids()
    # PID 0x01 (engine) and PID 0x05 (trans) must both appear.
    assert "0101" in pids
    assert "0105" in pids


# ---------------------------------------------------------------------------
# Simulator answers Mode 01 PID 0x67/0x68 with the multi-sensor format
# ---------------------------------------------------------------------------


def test_simulator_answers_pid_67_multi_ect() -> None:
    """End-to-end: ECU emulator answers Mode 01 PID 0x67 with the
    bitmap + ECT1 + ECT2 bytes when the scenario carries [87, 83]."""
    from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
    state = ScenarioState(vin="X" * 17, live={"0167": [87.0, 83.0]})
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0x67]))
    assert response == bytes([0x41, 0x67, 0x03, 127, 123])


def test_simulator_answers_pid_68_multi_iat() -> None:
    """Mode 01 PID 0x68 returns bitmap + IAT11 + IAT12."""
    from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
    state = ScenarioState(vin="X" * 17, live={"0168": [50.0, 55.0]})
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0x68]))
    assert response == bytes([0x41, 0x68, 0x03, 90, 95])


def test_mode01_pid_00_bitmap_advertises_pid_67_when_present() -> None:
    """A scenario carrying PID 0x67 should appear in the supported-PID
    bitmap of group 0x60 (covers PIDs 0x61..0x80)."""
    from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
    state = ScenarioState(vin="X" * 17, live={"0167": 87.0})
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0x60]))
    # PID 0x67 is at position (0x67 - 0x61) = 6 in group 0x60.
    # Byte 0, bit 7 - 6 = bit 1.
    bitmap = response[2:6]
    assert (bitmap[0] & (1 << 1)) != 0
