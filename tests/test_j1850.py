"""
Tests for SAE J1850 framing + the simulator runtime.

J1850 transceiver hardware (MC33390 for VPW, dual-wire driver for PWM)
is not in the v0.3.0 BOM. These tests prove the framing + dispatch
layer is correct, so once a chip arrives only the electrical-side
wrapper has to be plugged in. Same hardware-free pattern as test_kline.py.
"""

from __future__ import annotations

import pytest

from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.j1850 import (
    J1850Error,
    J1850Frame,
    PRI_FUNCTIONAL_REQUEST,
    PRI_RESPONSE,
    SRC_ECU_DEFAULT,
    TGT_FUNCTIONAL_OBD,
    TGT_TESTER,
    crc8,
    decode,
    encode_request,
    encode_response,
    encode_segmented_response,
)
from uacj_obd.simulator.j1850_runtime import J1850Runtime


# --- CRC ----------------------------------------------------------------


def test_crc8_is_deterministic_and_position_sensitive():
    # CRC-8 must be deterministic for identical input...
    assert crc8(b"\x01\x02\x03") == crc8(b"\x01\x02\x03")
    # ...and sensitive to byte order (catches a swapped-args regression).
    assert crc8(b"\x01\x02") != crc8(b"\x02\x01")
    # Single-byte computation matches the full SAE J1850 init/xor-out: the
    # reference vector for 0x00 (init 0xFF, poly 0x1D, xor-out 0xFF) is 0x3B.
    assert crc8(b"\x00") == 0x3B


def test_crc8_round_trip():
    # Building a frame and decoding it must round-trip via the same CRC.
    payload = b"\x01\x0C"  # mode 01, PID 0C (RPM)
    frame = encode_request(payload)
    parsed = decode(frame)
    assert parsed.data == payload


# --- framing ------------------------------------------------------------


def test_encode_request_layout():
    payload = b"\x09\x02"  # request VIN
    frame = encode_request(payload)
    assert frame[0] == PRI_FUNCTIONAL_REQUEST
    assert frame[1] == TGT_FUNCTIONAL_OBD
    assert frame[2] == TGT_TESTER
    assert frame[3:5] == payload
    assert frame[5] == crc8(frame[:-1])


def test_encode_response_uses_response_priority():
    payload = b"\x41\x0C\x1A\xF8"  # mode 01 PID 0C response, RPM ~1726
    frame = encode_response(payload)
    assert frame[0] == PRI_RESPONSE
    assert frame[1] == TGT_TESTER
    assert frame[2] == SRC_ECU_DEFAULT
    parsed = decode(frame)
    assert parsed.data == payload


def test_decode_rejects_short_frame():
    with pytest.raises(J1850Error):
        decode(b"\x68\x6A\xF1")  # missing data + CRC


def test_decode_rejects_oversize():
    big = bytes([PRI_RESPONSE, TGT_TESTER, SRC_ECU_DEFAULT]) + b"\x00" * 8
    big = big + bytes([crc8(big)])
    with pytest.raises(J1850Error):
        decode(big)


def test_decode_rejects_bad_crc():
    payload = b"\x41\x0C\x1A\xF8"
    frame = encode_response(payload)
    bad = frame[:-1] + bytes([(frame[-1] ^ 0x55) & 0xFF])
    with pytest.raises(J1850Error):
        decode(bad)


def test_encode_rejects_empty_payload():
    with pytest.raises(J1850Error):
        J1850Frame(priority=PRI_RESPONSE, target=TGT_TESTER,
                    source=SRC_ECU_DEFAULT, data=b"").encode()


def test_encode_rejects_oversize_payload():
    with pytest.raises(J1850Error):
        J1850Frame(priority=PRI_RESPONSE, target=TGT_TESTER,
                    source=SRC_ECU_DEFAULT, data=b"\x00" * 8).encode()


# --- segmentation -------------------------------------------------------


def test_segmented_response_short_payload_is_single_frame():
    payload = b"\x41\x0C\x1A\xF8"
    frames = encode_segmented_response(payload)
    assert len(frames) == 1
    assert decode(frames[0]).data == payload


def test_segmented_response_vin_uses_five_segments():
    # Mode 09 PID 02 VIN: 0x49 0x02 NODI=1 + 17 ASCII bytes = 20 bytes
    vin = b"1HGCM82633A123456"  # 17 bytes
    full = bytes([0x49, 0x02, 0x01]) + vin
    frames = encode_segmented_response(full)
    # 17 ASCII bytes packed 4 per segment → 5 segments (last has 1 byte)
    assert len(frames) == 5
    # Each frame must decode and start with [0x49, 0x02, seq#]
    for i, frame in enumerate(frames, start=1):
        parsed = decode(frame)
        assert parsed.data[0] == 0x49
        assert parsed.data[1] == 0x02
        assert parsed.data[2] == i


# --- runtime dispatch ---------------------------------------------------


class _FakePort:
    """In-memory duck-typed port that returns frames in chunks of 1 byte
    (matches the runtime's read(1) loop) and captures everything written."""

    def __init__(self, frames_in: list[bytes]) -> None:
        self._buf = bytearray(b"".join(frames_in))
        self.written = bytearray()

    def read(self, n: int) -> bytes:
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def write(self, b: bytes) -> int:
        self.written.extend(b)
        return len(b)


def test_runtime_answers_mode_01_pid_0c_rpm():
    state = ScenarioState(live={"010C": 1726})
    ecu = EcuEmulator(state)
    runtime = J1850Runtime(ecu, _FakePort([]))
    request = encode_request(b"\x01\x0C")
    responses = runtime.handle_request_bytes(request)
    assert len(responses) == 1
    parsed = decode(responses[0])
    # Mode 01 PID 0C response: 0x41 0x0C HH LL with HH*256+LL = RPM*4
    assert parsed.data[0] == 0x41
    assert parsed.data[1] == 0x0C
    rpm = ((parsed.data[2] << 8) | parsed.data[3]) / 4
    assert abs(rpm - 1726) < 1


def test_runtime_segments_long_vin_response():
    state = ScenarioState(vin="1HGCM82633A123456")
    ecu = EcuEmulator(state)
    runtime = J1850Runtime(ecu, _FakePort([]))
    request = encode_request(b"\x09\x02")
    responses = runtime.handle_request_bytes(request)
    # 20-byte payload → 5 segmented frames
    assert len(responses) == 5
    # Re-assemble: each frame's data is [0x49, 0x02, seq#, ...up-to-4-body-bytes].
    # body = NODI(0x01) + 17 VIN bytes; strip NODI to recover VIN.
    body = b"".join(decode(f).data[3:] for f in responses)
    assert body[0] == 0x01  # NODI count
    assert body[1:].rstrip(b"\x00") == b"1HGCM82633A123456"


def test_runtime_clear_dtcs_round_trip():
    state = ScenarioState(dtcs_stored=["P0420"], dtcs_pending=["P0171"])
    ecu = EcuEmulator(state)
    runtime = J1850Runtime(ecu, _FakePort([]))
    request = encode_request(b"\x04")
    responses = runtime.handle_request_bytes(request)
    assert len(responses) == 1
    parsed = decode(responses[0])
    assert parsed.data == b"\x44"
    assert state.dtcs_stored == []
    assert state.dtcs_pending == []


def test_runtime_decodes_full_frame_from_byte_stream():
    state = ScenarioState(live={"010D": 73})  # 73 km/h
    ecu = EcuEmulator(state)
    request_frame = encode_request(b"\x01\x0D")
    port = _FakePort([request_frame])
    runtime = J1850Runtime(ecu, port)
    frame = runtime._read_one_frame()
    assert frame == request_frame
    responses = runtime.handle_request_bytes(frame)
    parsed = decode(responses[0])
    assert parsed.data == b"\x41\x0D\x49"  # 0x49 = 73 km/h
