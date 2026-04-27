"""ISO-TP framing tests — pure-data, no CAN bus required."""

from __future__ import annotations

import pytest

from uacj_obd.simulator.iso_tp import CanFrame, IsoTpError, IsoTpFramer


def test_single_frame_round_trip() -> None:
    f = IsoTpFramer(tx_id=0x7E8, rx_id=0x7DF)
    payload = b"\x41\x0c\x1a\xf8"  # mode 01 PID 0C response: RPM = 1726
    frames = f.encode(payload)
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x7E8
    # Single-frame: PCI byte = 0x04, then 4 payload bytes, padded
    assert frames[0].data[0] == 0x04
    assert frames[0].data[1:5] == payload
    # Decoder extracts the original
    g = IsoTpFramer()
    out = g.decode(frames[0])
    assert out == payload


def test_multi_frame_vin_round_trip() -> None:
    """Mode 09 PID 02 VIN response is 20 bytes — needs FF + 2 CFs."""
    vin_payload = b"\x49\x02\x01" + b"2HGFC2F59FH123456" + b"\x00"  # 21 bytes incl pad
    payload = b"\x49\x02\x01" + b"2HGFC2F59FH123456"  # 20 bytes
    f = IsoTpFramer(tx_id=0x7E8)
    frames = f.encode(payload)
    # 20 bytes → first frame has 6, then 7+7 = 20
    assert len(frames) == 3
    # First frame format: 0x10 | length_hi, length_lo
    assert (frames[0].data[0] & 0xF0) == 0x10
    assert ((frames[0].data[0] & 0x0F) << 8 | frames[0].data[1]) == 20
    # Consecutive frames have sequence numbers 0x21, 0x22
    assert (frames[1].data[0] & 0xF0) == 0x20
    assert (frames[1].data[0] & 0x0F) == 1
    assert (frames[2].data[0] & 0x0F) == 2

    # Round-trip through decoder
    g = IsoTpFramer()
    assert g.decode(frames[0]) is None
    assert g.decode(frames[1]) is None
    out = g.decode(frames[2])
    assert out == payload


def test_decode_rejects_out_of_order_cf() -> None:
    f = IsoTpFramer()
    # Construct First Frame announcing 14 bytes
    ff = CanFrame(0x7DF, bytes([0x10, 0x0E]) + b"ABCDEF")
    assert f.decode(ff) is None
    # Send CF with wrong sequence (2 instead of 1)
    cf_bad = CanFrame(0x7DF, bytes([0x22]) + b"GHIJKLM" + b"\x00")
    with pytest.raises(IsoTpError):
        f.decode(cf_bad)


def test_payload_too_long_raises() -> None:
    f = IsoTpFramer()
    with pytest.raises(IsoTpError):
        f.encode(b"\x00" * 5000)
