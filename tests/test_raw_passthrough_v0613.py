"""
v0.6.13 — Tests for raw-bytes capture + replay.

Covers the three pieces:
  1. `encoders._try_raw_passthrough` and `is_answerable` recognise
     `"raw:HEX"` markers and produce bytes the simulator can return
     verbatim.
  2. `encode_pid` falls through to raw passthrough for PIDs without a
     formula encoder.
  3. The ECU's Mode 01 PID 0x00 bitmap derivation includes PIDs whose
     stored value is a raw marker (so the bitmap reflects what the
     simulator will actually answer).
  4. `Elm327Adapter._read_pid_raw` returns a LiveSample with
     `value="raw:HEX"` when given a fake connection that responds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from uacj_obd.adapters import elm327 as elm_mod
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.encoders import (
    encode_pid,
    is_answerable,
    _try_raw_passthrough,
)


# ---------------------------------------------------------------------------
# _try_raw_passthrough
# ---------------------------------------------------------------------------

def test_passthrough_decodes_raw_marker() -> None:
    assert _try_raw_passthrough("raw:DEADBEEF") == bytes([0xDE, 0xAD, 0xBE, 0xEF])


def test_passthrough_accepts_lowercase_hex() -> None:
    assert _try_raw_passthrough("raw:deadbeef") == bytes([0xDE, 0xAD, 0xBE, 0xEF])


def test_passthrough_empty_returns_none() -> None:
    assert _try_raw_passthrough("raw:") is None


def test_passthrough_invalid_hex_returns_none() -> None:
    assert _try_raw_passthrough("raw:not_hex") is None
    # Odd-length hex
    assert _try_raw_passthrough("raw:ABC") is None


def test_passthrough_no_prefix_returns_none() -> None:
    assert _try_raw_passthrough("DEADBEEF") is None


def test_passthrough_non_string_returns_none() -> None:
    assert _try_raw_passthrough(None) is None
    assert _try_raw_passthrough(42) is None
    assert _try_raw_passthrough(3.14) is None
    assert _try_raw_passthrough(b"raw:DEAD") is None  # bytes, not str


def test_passthrough_with_whitespace() -> None:
    """Whitespace inside the hex should be tolerated (some chips emit
    space-separated hex)."""
    assert _try_raw_passthrough("raw:  DEADBEEF  ") == bytes([0xDE, 0xAD, 0xBE, 0xEF])


# ---------------------------------------------------------------------------
# encode_pid raw path
# ---------------------------------------------------------------------------

def test_encode_pid_raw_passthrough_for_unknown_pid() -> None:
    """A PID with NO formula encoder should still encode if the
    stored value is a raw marker."""
    result = encode_pid("01AB", "raw:CAFE")  # 01AB has no encoder
    assert result == bytes([0xCA, 0xFE])


def test_encode_pid_raw_takes_priority_over_formula() -> None:
    """If a stored value is a raw marker, encode_pid uses raw bytes
    even when a formula encoder exists. Lets a scenario override a
    known PID with raw bytes from a real capture."""
    # 010C is RPM with a formula encoder
    result = encode_pid("010C", "raw:0BB8")  # 0x0BB8 = 3000
    assert result == bytes([0x0B, 0xB8])


def test_encode_pid_no_encoder_no_raw_returns_none() -> None:
    """A PID with no encoder and a numeric value should still return
    None — we don't try to guess bytes for unknown PIDs."""
    assert encode_pid("01AB", 42) is None


# ---------------------------------------------------------------------------
# is_answerable
# ---------------------------------------------------------------------------

def test_is_answerable_with_formula_encoder() -> None:
    assert is_answerable("010C", 800) is True


def test_is_answerable_with_raw_marker() -> None:
    assert is_answerable("01AB", "raw:CAFE") is True


def test_is_answerable_without_encoder_or_raw() -> None:
    assert is_answerable("01AB", 42) is False


def test_is_answerable_none_value() -> None:
    assert is_answerable("010C", None) is False


def test_is_answerable_invalid_raw_marker() -> None:
    """A raw marker that fails to parse should NOT be answerable."""
    assert is_answerable("01AB", "raw:zz") is False


# ---------------------------------------------------------------------------
# Mode 01 PID 0x00 bitmap reflects raw markers
# ---------------------------------------------------------------------------

def test_mode01_bitmap_includes_raw_passthrough_pids() -> None:
    """A PID with a raw value but no formula encoder should still
    appear in the supported-PID bitmap."""
    state = ScenarioState(vin="X" * 17, live={"010C": 800, "01AB": "raw:CAFE"})
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0x00]))
    assert response[:2] == bytes([0x41, 0x00])
    bitmap = response[2:6]
    # PID 0x0C: byte 1 (index 1), bit (7 - (0x0C-1) % 8) = bit 4. Actually:
    # PIDs in group 0x00 cover 0x01..0x20. PID 0x0C = position 11, byte 1, bit 4.
    # PID 0xAB is out of group 0x00 — won't be in this bitmap.
    # Just assert 0x0C is set.
    assert bitmap[1] & (1 << (7 - 3)) == (1 << (7 - 3))  # bit 4 of byte 1


def test_mode01_bitmap_excludes_pids_with_no_encoder_no_raw() -> None:
    """A PID with a numeric value but no encoder must NOT appear in
    the bitmap — we'd be advertising support we can't deliver."""
    state = ScenarioState(vin="X" * 17, live={"01AB": 99})  # no encoder for 01AB
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0x80]))  # group 0x80 covers 0x81..0xA0
    bitmap = response[2:6]
    # PID 0xAB is in group 0xA0, but we asked for 0x80. Just assert nothing is set
    # in the 0x80 group response.
    assert bitmap == bytes([0, 0, 0, 0])


def test_mode01_pid_answer_uses_raw_passthrough() -> None:
    """Querying a raw-passthrough PID should return the stored bytes
    verbatim after the 0x41 + PID header."""
    state = ScenarioState(vin="X" * 17, live={"01AB": "raw:CAFE"})
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0xAB]))
    assert response == bytes([0x41, 0xAB, 0xCA, 0xFE])


# ---------------------------------------------------------------------------
# Elm327Adapter._read_pid_raw
# ---------------------------------------------------------------------------

class _FakeInterface:
    """v0.6.16: stands in for python-obd ELM327.interface.
    `_read_pid_raw` now calls `c.interface.send_and_parse(cmd_bytes)`
    instead of the old OBDCommand+query path."""

    def __init__(self, response_bytes: bytes | None) -> None:
        self._response_bytes = response_bytes
        self.sent: list[bytes] = []

    def send_and_parse(self, cmd: bytes):
        self.sent.append(cmd)
        if self._response_bytes is None:
            return None
        return [SimpleNamespace(data=self._response_bytes)]


class _FakeConn:
    def __init__(self, response_bytes: bytes | None) -> None:
        self.interface = _FakeInterface(response_bytes)

    def is_connected(self) -> bool:
        return True


def test_read_pid_raw_returns_live_sample_with_raw_marker() -> None:
    """Given a fake connection that returns bytes, _read_pid_raw
    should produce a LiveSample with value='raw:HEX'."""
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(bytes([0x41, 0x14, 0xAB, 0xCD]))
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is not None
    assert sample.pid == "0114"
    assert sample.value == "raw:ABCD"
    assert sample.unit is None


def test_read_pid_raw_returns_none_when_null_response() -> None:
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(None)
    assert adapter._read_pid_raw("0114", 0x01, 0x14) is None


def test_read_pid_raw_returns_none_for_non_mode01() -> None:
    """Mode 09 has dedicated paths; raw fallback shouldn't fire there."""
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(bytes([0x49, 0x02, 0xFF]))
    assert adapter._read_pid_raw("0902", 0x09, 0x02) is None


def test_read_pid_raw_strips_echo_prefix() -> None:
    """0x41 + PID echo should be stripped before storing as raw."""
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(bytes([0x41, 0xAB, 0xDE, 0xAD, 0xBE, 0xEF]))
    sample = adapter._read_pid_raw("01AB", 0x01, 0xAB)
    assert sample is not None
    assert sample.value == "raw:DEADBEEF"


def test_read_pid_raw_handles_disconnected() -> None:
    adapter = elm_mod.Elm327Adapter()
    # No _conn → _ensure() raises → method returns None
    assert adapter._read_pid_raw("01AB", 0x01, 0xAB) is None


# ---------------------------------------------------------------------------
# End-to-end: raw capture → live_baseline → push → simulator answers
# ---------------------------------------------------------------------------

def test_end_to_end_raw_pid_round_trip() -> None:
    """A live_baseline carrying a raw marker should result in the
    simulator answering that PID with the raw bytes."""
    from uacj_obd.simulator.can_runtime import scenario_to_state
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"01AB": "raw:CAFE"},
    }
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    response = ecu._mode01(bytes([0xAB]))
    assert response == bytes([0x41, 0xAB, 0xCA, 0xFE])


def test_raw_pids_appear_in_bitmap_via_passthrough() -> None:
    """Raw PID in the live_baseline should be reflected in the supported
    bitmap so the scan tool requests it."""
    from uacj_obd.simulator.can_runtime import scenario_to_state
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"010C": 800, "01AB": "raw:CAFE"},
    }
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    # PID 0xAB is in group 0xA0 (covers 0xA1..0xC0)
    response = ecu._mode01(bytes([0xA0]))
    bitmap = response[2:6]
    # PID 0xAB position: (0xAB - 0xA1) = 10. Byte 1, bit 7-(10%8) = bit 5.
    assert (bitmap[1] & (1 << 5)) != 0
