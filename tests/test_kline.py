"""KWP2000 framing + K-Line runtime tests — pure-data, no UART required."""

from __future__ import annotations

import pytest

from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.kline import (
    ECU_ADDRESS_FUNCTIONAL,
    ECU_ADDRESS_PHYSICAL,
    KlineError,
    TESTER_ADDRESS,
    decode,
    encode_request,
    encode_response,
    total_frame_length,
)
from uacj_obd.simulator.kline_runtime import KlineRuntime


def test_short_form_round_trip() -> None:
    payload = bytes([0x01, 0x0C])  # mode 01 PID 0C
    frame = encode_request(payload)
    parsed = decode(frame)
    assert parsed.data == payload
    assert parsed.target == ECU_ADDRESS_FUNCTIONAL
    assert parsed.source == TESTER_ADDRESS
    # Last byte is the checksum
    assert frame[-1] == sum(frame[:-1]) & 0xFF


def test_long_form_uses_explicit_length_byte() -> None:
    payload = bytes(range(64))  # 64 bytes — short form caps at 63
    frame = encode_request(payload)
    parsed = decode(frame)
    assert parsed.data == payload
    # Fmt low 6 bits = 0 means long form
    assert (frame[0] & 0x3F) == 0
    # Len byte at index 3 holds the actual length
    assert frame[3] == 64


def test_decode_rejects_bad_checksum() -> None:
    payload = bytes([0x01, 0x0C])
    frame = bytearray(encode_request(payload))
    frame[-1] ^= 0xFF
    with pytest.raises(KlineError):
        decode(bytes(frame))


def test_decode_rejects_truncated_frame() -> None:
    with pytest.raises(KlineError):
        decode(b"\xC2\x33")


def test_total_frame_length_short_form() -> None:
    # Fmt(1) + tgt(1) + src(1) + 2 data bytes + cs(1) = 6
    assert total_frame_length(0xC2, b"\xC2\x33\xF1") == 6


def test_total_frame_length_long_form_needs_len_byte() -> None:
    # Long form: fmt(1) + tgt(1) + src(1) + len(1) + N data + cs(1)
    assert total_frame_length(0x80, b"\x80\x33\xF1") is None
    assert total_frame_length(0x80, b"\x80\x33\xF1\x05") == 4 + 5 + 1


def test_kline_runtime_handles_mode_01_request() -> None:
    ecu = EcuEmulator(ScenarioState(live={"010C": 1500}))
    rt = KlineRuntime(ecu, serial=None)  # serial unused in pure-data path
    request = encode_request(bytes([0x01, 0x0C]))
    response = rt.handle_request_bytes(request)
    parsed = decode(response)
    # Response is addressed back to the tester (0xF1) from the ECU
    assert parsed.target == TESTER_ADDRESS
    assert parsed.source == ECU_ADDRESS_PHYSICAL
    # Mode 01 response: 0x41, 0x0C, then RPM*4 = 6000 = 0x1770
    assert parsed.data[0] == 0x41
    assert parsed.data[1] == 0x0C
    raw = (parsed.data[2] << 8) | parsed.data[3]
    assert raw / 4 == 1500


def test_kline_runtime_handles_dtc_request() -> None:
    ecu = EcuEmulator(ScenarioState(dtcs_stored=["P0420", "P0171"]))
    rt = KlineRuntime(ecu, serial=None)
    response = rt.handle_request_bytes(encode_request(bytes([0x03])))
    parsed = decode(response)
    assert parsed.data[0] == 0x43
    assert parsed.data[1] == 2
    # P0420 → 0x04 0x20
    assert parsed.data[2:4] == bytes([0x04, 0x20])


def test_kline_runtime_handles_vin_request_long_form() -> None:
    """VIN response is 20 bytes — exercises the long-form length byte."""
    ecu = EcuEmulator(ScenarioState(vin="2HGFC2F59FH123456"))
    rt = KlineRuntime(ecu, serial=None)
    response = rt.handle_request_bytes(encode_request(bytes([0x09, 0x02])))
    parsed = decode(response)
    assert parsed.data[:3] == bytes([0x49, 0x02, 0x01])
    assert b"2HGFC2F59FH123456" in parsed.data
