"""
ISO-TP (ISO 15765-2) framing for OBD-II over CAN.

Implements the subset needed for OBD-II diagnostic exchange:
  - Single Frame  (SF): N_PCI 0x0_, length 0..7
  - First Frame   (FF): N_PCI 0x1_, total length up to 4095 bytes
  - Consecutive   (CF): N_PCI 0x2_, sequence 0..15
  - Flow Control  (FC): N_PCI 0x30, BS=0, STmin=0 (we always send "send all")

Padding: CAN frames are 8 bytes; unused bytes are padded with 0xAA per
SAE J1979 (some tools expect 0x00; SAE J1979 specifies 0xAA but the test
suite below validates the header bytes only).

This module is pure data — it has no CAN bus dependency, so it is
testable without hardware or vCAN.
"""

from __future__ import annotations

from dataclasses import dataclass


CAN_FRAME_LEN = 8
PADDING = 0xAA


class IsoTpError(Exception):
    pass


@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    data: bytes  # length 0..8


def _pad(buf: bytes) -> bytes:
    if len(buf) >= CAN_FRAME_LEN:
        return buf[:CAN_FRAME_LEN]
    return buf + bytes([PADDING]) * (CAN_FRAME_LEN - len(buf))


class IsoTpFramer:
    """
    Encode and decode ISO-TP messages for a given (tx_id, rx_id) pair.

    For OBD-II, the standard 11-bit IDs are:
      Functional request:  0x7DF
      Physical request:    0x7E0..0x7E7   (ECU 0..7)
      Physical response:   0x7E8..0x7EF   (ECU 0..7)
    The ECU emulator's perspective: rx_id is the request, tx_id is the response.
    """

    def __init__(self, tx_id: int = 0x7E8, rx_id: int = 0x7DF) -> None:
        self.tx_id = tx_id
        self.rx_id = rx_id
        self._rx_buf: bytearray = bytearray()
        self._rx_expected: int = 0
        self._rx_seq: int = 1

    # ---------------- encoding ----------------

    def encode(self, payload: bytes) -> list[CanFrame]:
        """
        Convert a complete OBD-II response payload into 1+ CAN frames.
        """
        n = len(payload)
        if n <= 7:
            data = bytes([0x00 | n]) + payload
            return [CanFrame(self.tx_id, _pad(data))]
        if n > 4095:
            raise IsoTpError(f"payload too long for ISO-TP: {n} bytes")
        frames: list[CanFrame] = []
        # First Frame: nibble 0x1, 12-bit length
        ff_len_hi = 0x10 | ((n >> 8) & 0x0F)
        ff_len_lo = n & 0xFF
        first = bytes([ff_len_hi, ff_len_lo]) + payload[:6]
        frames.append(CanFrame(self.tx_id, _pad(first)))
        idx = 6
        seq = 1
        while idx < n:
            chunk = payload[idx : idx + 7]
            cf = bytes([0x20 | (seq & 0x0F)]) + chunk
            frames.append(CanFrame(self.tx_id, _pad(cf)))
            idx += 7
            seq = (seq + 1) & 0x0F
        return frames

    # ---------------- decoding ----------------

    def decode(self, frame: CanFrame) -> bytes | None:
        """
        Feed one CAN frame; returns a complete payload when assembled,
        otherwise None.

        Flow-control frames from the requester are not handled here; the
        ECU side is responder-only and never receives FCs.
        """
        if not frame.data:
            return None
        pci = frame.data[0]
        kind = (pci >> 4) & 0x0F
        if kind == 0x0:
            # Single frame
            length = pci & 0x0F
            self._reset()
            return bytes(frame.data[1 : 1 + length])
        if kind == 0x1:
            # First frame
            if len(frame.data) < 8:
                raise IsoTpError("first frame must be 8 bytes")
            total = ((pci & 0x0F) << 8) | frame.data[1]
            self._rx_buf = bytearray(frame.data[2:8])
            self._rx_expected = total
            self._rx_seq = 1
            return None
        if kind == 0x2:
            # Consecutive frame
            seq = pci & 0x0F
            if seq != self._rx_seq:
                raise IsoTpError(f"out-of-order CF: expected {self._rx_seq}, got {seq}")
            remaining = self._rx_expected - len(self._rx_buf)
            take = min(7, remaining)
            self._rx_buf.extend(frame.data[1 : 1 + take])
            self._rx_seq = (self._rx_seq + 1) & 0x0F
            if len(self._rx_buf) >= self._rx_expected:
                payload = bytes(self._rx_buf[: self._rx_expected])
                self._reset()
                return payload
            return None
        if kind == 0x3:
            # Flow control — ignored on the ECU side
            return None
        raise IsoTpError(f"unknown N_PCI nibble: 0x{kind:X}")

    def _reset(self) -> None:
        self._rx_buf = bytearray()
        self._rx_expected = 0
        self._rx_seq = 1
