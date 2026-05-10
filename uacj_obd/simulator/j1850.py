"""
SAE J1850 framing for pre-CAN OBD-II vehicles (2003-2007 GM/Ford).

Two electrical variants exist — VPW (GM, single-wire, 10.4 kbps) and
PWM (Ford SCP, two-wire, 41.6 kbps). The byte-level frame format is
identical between the two; only the line encoding differs. This module
handles the shared framing layer; the electrical encoding is the
transceiver's job (MC33390 for VPW, separate dual-wire driver for PWM).

Frame layout (3-byte header form, used for OBD-II):

  +-----+-----+-----+----------------+-----+
  | PRI | TGT | SRC |   data 1..7    | CRC |
  +-----+-----+-----+----------------+-----+

  PRI (priority/type) — high three bits encode priority (lower number =
      higher priority), bit 4 is "header type" (0 = 3-byte header,
      1 = 1-byte header — we always use 3-byte for OBD-II), bit 3 is
      "in-frame response allowed", bits 2..0 are addressing mode.
      Standard OBD-II values:
        0x68 — functional, 3-byte header, IFR allowed   (Ford SCP req)
        0x6B — physical,   3-byte header, IFR allowed   (typical GM)
        0xC4 / 0x48 — response variants (high priority)

  TGT — target address. 0x6A is the standard OBD-II functional address.
        0x10 is a typical ECU physical address.

  SRC — source address. Tester is 0xF1.

  CRC — SAE J1850 CRC-8, polynomial 0x1D, seed 0xFF, XOR-out 0xFF.

This module is pure data — it has no MC33390 / serial dependency, so
every test runs without hardware (same pattern as iso_tp.py / kline.py).
"""

from __future__ import annotations

from dataclasses import dataclass


PRI_FUNCTIONAL_REQUEST = 0x68
PRI_PHYSICAL_REQUEST = 0x6B
PRI_RESPONSE = 0x48

TGT_FUNCTIONAL_OBD = 0x6A
TGT_TESTER = 0xF1
SRC_ECU_DEFAULT = 0x10


class J1850Error(Exception):
    pass


def crc8(data: bytes) -> int:
    """
    SAE J1850 CRC-8: poly 0x1D, init 0xFF, xor-out 0xFF, no reflection.
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0xFF


@dataclass(frozen=True)
class J1850Frame:
    priority: int
    target: int
    source: int
    data: bytes

    def encode(self) -> bytes:
        if not self.data:
            raise J1850Error("empty data not allowed")
        if len(self.data) > 7:
            raise J1850Error(f"J1850 OBD-II frame data limited to 7 bytes, got {len(self.data)}")
        head = bytes([self.priority, self.target, self.source]) + self.data
        return head + bytes([crc8(head)])


def encode_request(payload: bytes,
                     target: int = TGT_FUNCTIONAL_OBD,
                     source: int = TGT_TESTER,
                     functional: bool = True) -> bytes:
    pri = PRI_FUNCTIONAL_REQUEST if functional else PRI_PHYSICAL_REQUEST
    return J1850Frame(priority=pri, target=target, source=source, data=payload).encode()


def encode_response(payload: bytes,
                      target: int = TGT_TESTER,
                      source: int = SRC_ECU_DEFAULT) -> bytes:
    return J1850Frame(priority=PRI_RESPONSE, target=target, source=source, data=payload).encode()


def decode(frame: bytes) -> J1850Frame:
    """
    Parse one complete J1850 frame. Raises J1850Error on malformed input.
    """
    if len(frame) < 5:
        raise J1850Error(f"frame too short: {len(frame)} bytes (need at least 5)")
    if len(frame) > 11:
        raise J1850Error(f"frame too long: {len(frame)} bytes (max 11 for 7-byte payload)")
    expected = crc8(frame[:-1])
    if frame[-1] != expected:
        raise J1850Error(f"CRC mismatch: expected 0x{expected:02X}, got 0x{frame[-1]:02X}")
    return J1850Frame(
        priority=frame[0],
        target=frame[1],
        source=frame[2],
        data=bytes(frame[3:-1]),
    )


# OBD-II over J1850 carries only single-frame messages (≤ 7 data bytes
# after the header). Mode 09 VIN response — the longest standard payload —
# uses message segmentation with sequential frames, each carrying a
# segment number in the first data byte.

def encode_segmented_response(payload: bytes,
                                target: int = TGT_TESTER,
                                source: int = SRC_ECU_DEFAULT) -> list[bytes]:
    """
    Split a long response (e.g. mode 09 VIN, mode 03 multi-DTC) into one
    or more J1850 frames. Per SAE J1979, the first response data byte
    carries a 1-based message number when segmentation is required.

    For payloads ≤ 7 bytes, returns a single frame with no segmentation.
    For longer payloads, the response service byte (e.g. 0x49) and any
    sub-PID stay in frame 1; subsequent frames carry [seq#, ...chunk].
    """
    if len(payload) <= 7:
        return [encode_response(payload, target=target, source=source)]
    # Frame 1: service + sub-pid + first chunk; following frames begin
    # with the byte index. We use the SAE J1979 multi-message convention
    # of putting the message# as the data byte right after the service +
    # PID (NODI) — most pre-CAN OBD-II responses for 17-byte VIN use 5
    # message numbers of 4 bytes each.
    frames: list[bytes] = []
    if len(payload) < 3:
        raise J1850Error("segmented payload must include at least service+pid+nodi bytes")
    service = payload[0]
    pid = payload[1]
    body = payload[2:]
    # Pack 4-byte chunks per message# (SAE J1979 layout for VIN segmented response)
    seq = 1
    idx = 0
    while idx < len(body):
        chunk = body[idx:idx + 4]
        idx += 4
        frame_data = bytes([service, pid, seq]) + chunk
        frames.append(encode_response(frame_data, target=target, source=source))
        seq += 1
    return frames
