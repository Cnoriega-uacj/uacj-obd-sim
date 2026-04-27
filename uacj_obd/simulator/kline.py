"""
KWP2000 (ISO 14230-3) framing over K-Line.

Used by 2003–2007 European and Asian vehicles that haven't moved to CAN.
The K-Line is a single-wire UART bus at 10.4 kbps; the L9637 transceiver
wired to a Pi UART pin handles the electrical side. This module is pure
data: it converts between OBD-II payloads and KWP2000 frame bytes.

Frame format (header byte Fmt + optional addressing + length byte + data + checksum):

  +----+-----+-----+-----+----------------+----+
  |Fmt | Tgt | Src | Len |     Data       | CS |
  +----+-----+-----+-----+----------------+----+
   ^^^---^^^---^^^   ^^^   data bytes      sum mod 256

  Fmt high two bits: addressing mode
      0b10 (0x80) — physical addressing, with target+source bytes
      0b11 (0xC0) — functional addressing, with target+source bytes
  Fmt low six bits: data length (0..63), or 0 to indicate "see Len byte"

  When the low six bits of Fmt are 0, a Len byte follows the addresses
  giving 0..255 bytes of data.

  Checksum: arithmetic sum of all preceding bytes, mod 256.

The OBD-II tester address is typically 0xF1, the ECU is 0x33 for
functional/broadcast and a specific physical ID for direct addressing.

This module implements the subset required for OBD-II diagnostic
exchange — physical addressing only — which covers what the L9637
training harness will send.
"""

from __future__ import annotations

from dataclasses import dataclass


TESTER_ADDRESS = 0xF1
ECU_ADDRESS_PHYSICAL = 0x10  # responder's own address; tester uses 0xF1
ECU_ADDRESS_FUNCTIONAL = 0x33  # functional broadcast target


class KlineError(Exception):
    pass


@dataclass(frozen=True)
class KwpFrame:
    fmt: int
    target: int
    source: int
    data: bytes

    def encode(self) -> bytes:
        n = len(self.data)
        if n == 0:
            raise KlineError("empty data not allowed")
        # Use the long form (Len byte) whenever data > 63 bytes; otherwise
        # encode the length into the low 6 bits of Fmt for compactness.
        if n <= 63:
            fmt = (self.fmt & 0xC0) | (n & 0x3F)
            buf = bytes([fmt, self.target, self.source]) + self.data
        else:
            fmt = self.fmt & 0xC0  # length nibble = 0 → expect Len byte
            buf = bytes([fmt, self.target, self.source, n & 0xFF]) + self.data
        checksum = sum(buf) & 0xFF
        return buf + bytes([checksum])


def encode_request(payload: bytes,
                     target: int = ECU_ADDRESS_FUNCTIONAL,
                     source: int = TESTER_ADDRESS,
                     functional: bool = True) -> bytes:
    fmt = 0xC0 if functional else 0x80
    return KwpFrame(fmt=fmt, target=target, source=source, data=payload).encode()


def encode_response(payload: bytes,
                      target: int = TESTER_ADDRESS,
                      source: int = ECU_ADDRESS_PHYSICAL) -> bytes:
    return KwpFrame(fmt=0x80, target=target, source=source, data=payload).encode()


def decode(frame: bytes) -> KwpFrame:
    """
    Parse one complete KWP2000 frame and return the structured form.
    Raises KlineError on malformed input.
    """
    if len(frame) < 5:
        raise KlineError(f"frame too short: {len(frame)} bytes")
    fmt = frame[0]
    target = frame[1]
    source = frame[2]
    short_len = fmt & 0x3F
    if short_len == 0:
        if len(frame) < 6:
            raise KlineError("missing Len byte for long-form frame")
        data_len = frame[3]
        data_start = 4
    else:
        data_len = short_len
        data_start = 3
    end = data_start + data_len
    if len(frame) < end + 1:
        raise KlineError(f"truncated: expected {end + 1} bytes, got {len(frame)}")
    data = frame[data_start:end]
    cs = frame[end]
    expected = sum(frame[:end]) & 0xFF
    if cs != expected:
        raise KlineError(f"checksum mismatch: expected 0x{expected:02X}, got 0x{cs:02X}")
    return KwpFrame(fmt=fmt, target=target, source=source, data=bytes(data))


SLOW_INIT_ADDRESS_OBD = 0x33  # ISO 9141-2 / KWP2000 OBD entry address
SLOW_INIT_SYNC_BYTE = 0x55    # ECU sync after the address byte
KEY_BYTE_1 = 0x08             # KWP2000 fast = 0x8F; here we present "old" 9141-2 KB1
KEY_BYTE_2 = 0x08             # KB2: protocol version (matches KB1 for 9141-2)


def slow_init_step(rx_byte: int) -> bytes:
    """
    Stateless decoder for one byte of a 5-baud slow-init handshake.

    Returns the bytes the ECU should send back, or empty bytes if the
    received byte is part of a multi-step handshake we are still
    waiting on.

    Sequence per ISO 14230-2 §5.2.4 / ISO 9141-2:
      tester → 0x33 (address byte at 5 baud)
      ECU    → 0x55 KB1 KB2 (at protocol baud, typically 10.4 kbps)
      tester → ~KB2 within W4 (25-50ms)
      ECU    → ~0x33 (= 0xCC)
      → handshake complete, normal KWP frames follow
    """
    inverted_kb2 = (~KEY_BYTE_2) & 0xFF
    inverted_addr = (~SLOW_INIT_ADDRESS_OBD) & 0xFF
    if rx_byte == SLOW_INIT_ADDRESS_OBD:
        return bytes([SLOW_INIT_SYNC_BYTE, KEY_BYTE_1, KEY_BYTE_2])
    if rx_byte == inverted_kb2:
        return bytes([inverted_addr])
    return b""


def total_frame_length(fmt: int, peek: bytes) -> int | None:
    """
    Inspect partial bytes to compute the expected total frame length.
    Useful for the UART loop to know how many bytes to keep reading.
    Returns None if not enough bytes have been received yet to decide.
    """
    short_len = fmt & 0x3F
    if short_len > 0:
        return 3 + short_len + 1  # fmt + tgt + src + data + cs
    if len(peek) < 4:
        return None
    return 4 + peek[3] + 1
