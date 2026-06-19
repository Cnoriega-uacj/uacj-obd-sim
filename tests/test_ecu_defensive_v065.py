"""
v0.6.5 — Defensive-path coverage for `simulator/ecu.py`.

v0.6.4 left ecu.py at 87%. The uncovered lines were all defensive
branches that production code legitimately exercises but unit tests
hadn't reached: empty request bytes, dispatcher exception, NRC paths
in each Mode, freeze-frame fallback via friendly names, CVN parsing
edge cases (bytes/short/invalid), Mode 22 manufacturer PIDs.
"""

from __future__ import annotations

import pytest

from uacj_obd.simulator.ecu import (
    EcuEmulator,
    ScenarioState,
    _clean_ascii_field,
    _parse_cvn,
)


def _ecu(**state_kwargs) -> EcuEmulator:
    return EcuEmulator(ScenarioState(**state_kwargs))


# ---------------------------------------------------------------------------
# handle() — defensive entry
# ---------------------------------------------------------------------------

def test_handle_empty_bytes_returns_nrc_service_not_supported() -> None:
    ecu = _ecu()
    resp = ecu.handle(b"")
    # NRC = 0x7F service_id NRC_code; with empty input service_id = 0
    assert resp[0] == 0x7F


def test_handle_dispatcher_exception_returns_nrc() -> None:
    """If an unexpected exception escapes a mode handler, the wrapper
    catches it and returns NRC_REQUEST_OUT_OF_RANGE."""
    ecu = _ecu()
    # Monkey-patch _mode01 to raise.
    def boom(args):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced")
    ecu._mode01 = boom  # type: ignore[method-assign]
    resp = ecu.handle(bytes([0x01, 0x0C]))
    assert resp[0] == 0x7F
    # NRC code = 0x31 (REQUEST_OUT_OF_RANGE)
    assert resp[2] == 0x31


def test_handle_dispatcher_returns_none_translated_to_nrc() -> None:
    """Per the contract, a mode handler may return None → wrapper
    translates to NRC_SERVICE_NOT_SUPPORTED."""
    ecu = _ecu()
    ecu._mode01 = lambda args: None  # type: ignore[method-assign]
    resp = ecu.handle(bytes([0x01, 0x0C]))
    assert resp[0] == 0x7F


def test_handle_unknown_service_returns_nrc_via_dispatch_none() -> None:
    """A service the dispatcher doesn't know about → _dispatch returns
    None → wrapper translates to NRC."""
    ecu = _ecu()
    resp = ecu.handle(bytes([0x99, 0xAA]))  # 0x99 is reserved/unsupported
    assert resp[0] == 0x7F


# ---------------------------------------------------------------------------
# Mode 02 (freeze frame) NRC and friendly-name fallback
# ---------------------------------------------------------------------------

def test_mode02_too_short_args_returns_nrc() -> None:
    ecu = _ecu()
    # Mode 02 needs PID + frame#. Just service byte → NRC.
    resp = ecu.handle(bytes([0x02]))
    assert resp[0] == 0x7F


def test_mode02_freeze_dtc_query_without_dtc_returns_nrc() -> None:
    """PID 0x02 wants the freeze-frame DTC. If no `freeze_dtc` is set,
    NRC."""
    ecu = _ecu()
    resp = ecu.handle(bytes([0x02, 0x02, 0x00]))
    assert resp[0] == 0x7F


def test_mode02_freeze_dtc_query_with_dtc_returns_packed_code() -> None:
    ecu = _ecu(freeze_dtc="P0420")
    resp = ecu.handle(bytes([0x02, 0x02, 0x00]))
    assert resp[0] == 0x42
    assert resp[1] == 0x02
    # Two bytes of packed DTC follow the 0x00 separator
    assert resp[3:5] == bytes([0x04, 0x20])


def test_mode02_uses_friendly_name_fallback_for_freeze_frame_lookup() -> None:
    """The freeze frame on disk often stores PID values under friendly
    names (e.g. 'RPM' not '010C'). `_friendly_name` maps both ways."""
    ecu = _ecu(freeze_frame={"RPM": 1850})
    # PID 0x0C is RPM
    resp = ecu.handle(bytes([0x02, 0x0C, 0x00]))
    assert resp[0] == 0x42
    assert resp[1] == 0x0C


def test_mode02_unknown_pid_with_no_freeze_data_returns_nrc() -> None:
    ecu = _ecu(freeze_frame={})
    resp = ecu.handle(bytes([0x02, 0x0C, 0x00]))
    assert resp[0] == 0x7F


# ---------------------------------------------------------------------------
# Mode 09 NRC paths
# ---------------------------------------------------------------------------

def test_mode09_no_args_returns_nrc() -> None:
    ecu = _ecu()
    resp = ecu.handle(bytes([0x09]))
    assert resp[0] == 0x7F


def test_mode09_pid02_vin_with_short_vin_left_padded() -> None:
    """VIN shorter than 17 chars gets right-justified with null padding."""
    ecu = _ecu(vin="SHORT")
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[0] == 0x49
    raw_vin = resp[3:]
    assert len(raw_vin) == 17
    # The original "SHORT" should be at the end.
    assert raw_vin.endswith(b"SHORT")


def test_mode09_pid02_vin_too_long_truncated_to_17() -> None:
    """A VIN with more than 17 chars gets truncated."""
    ecu = _ecu(vin="X" * 25)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[3:] == b"X" * 17


def test_mode09_pid04_calibration_id_missing_returns_nrc() -> None:
    ecu = _ecu()  # no calibration_id
    resp = ecu.handle(bytes([0x09, 0x04]))
    assert resp[0] == 0x7F


def test_mode09_pid0a_ecu_name_missing_returns_nrc() -> None:
    ecu = _ecu()  # no ecu_name
    resp = ecu.handle(bytes([0x09, 0x0A]))
    assert resp[0] == 0x7F


def test_mode09_unknown_pid_returns_nrc() -> None:
    ecu = _ecu(vin="JM1BL1L72C1627697")
    # PID 0xFF is not in the mode 09 dispatch.
    resp = ecu.handle(bytes([0x09, 0xFF]))
    assert resp[0] == 0x7F


# ---------------------------------------------------------------------------
# Mode 22 (manufacturer-specific PIDs)
# ---------------------------------------------------------------------------

def test_mode22_too_short_args_returns_nrc() -> None:
    ecu = _ecu()
    # Mode 22 wants two PID bytes; just one → NRC
    resp = ecu.handle(bytes([0x22, 0x11]))
    assert resp[0] == 0x7F


def test_mode22_unknown_pid_returns_nrc() -> None:
    """A PID with no encoder OR no value in state.live returns NRC."""
    ecu = _ecu()
    resp = ecu.handle(bytes([0x22, 0xFF, 0xFF]))
    assert resp[0] == 0x7F


def test_mode22_known_ford_pid_with_value_returns_data() -> None:
    """Ford trans oil temp PID 0x22115C with a real value should
    return positive response."""
    ecu = _ecu(live={"22115C": 80.0})
    resp = ecu.handle(bytes([0x22, 0x11, 0x5C]))
    assert resp[0] == 0x62  # positive Mode 22 response
    assert resp[1] == 0x11
    assert resp[2] == 0x5C
    # 80°C → raw = (80+40)*10 = 1200 → 0x04 0xB0
    assert resp[3:] == bytes([0x04, 0xB0])


# ---------------------------------------------------------------------------
# _parse_cvn — edge cases
# ---------------------------------------------------------------------------

def test_parse_cvn_none_returns_none() -> None:
    assert _parse_cvn(None) is None


def test_parse_cvn_empty_string_returns_none() -> None:
    assert _parse_cvn("") is None


def test_parse_cvn_exact_4_bytes_passthrough() -> None:
    assert _parse_cvn(bytes([0xCD, 0xA0, 0x8E, 0x85])) == bytes([0xCD, 0xA0, 0x8E, 0x85])


def test_parse_cvn_more_than_4_bytes_truncated() -> None:
    assert _parse_cvn(bytes([0xCD, 0xA0, 0x8E, 0x85, 0x99])) == bytes([0xCD, 0xA0, 0x8E, 0x85])


def test_parse_cvn_less_than_4_bytes_zero_padded() -> None:
    """Short byte input gets left-padded with zeros to 4 bytes."""
    assert _parse_cvn(bytes([0xCD, 0xA0])) == bytes([0x00, 0x00, 0xCD, 0xA0])


def test_parse_cvn_invalid_hex_returns_none() -> None:
    assert _parse_cvn("ZZZZ") is None


def test_parse_cvn_short_hex_left_padded() -> None:
    """A 6-char hex value gets left-padded to 8 chars (4 bytes)."""
    result = _parse_cvn("A08E85")
    assert result == bytes([0x00, 0xA0, 0x8E, 0x85])


# ---------------------------------------------------------------------------
# _clean_ascii_field — defensive
# ---------------------------------------------------------------------------

def test_clean_ascii_field_none() -> None:
    assert _clean_ascii_field(None) == ""


def test_clean_ascii_field_bytes_with_undecodable_byte_replaces() -> None:
    """0xFF is not a valid ASCII byte; should not crash."""
    out = _clean_ascii_field(bytes([0xFF, 0x41, 0x42]))
    # 0xFF gets replaced with '?' or '�' depending on policy, then
    # only printable ASCII is kept → "AB".
    assert "AB" in out
