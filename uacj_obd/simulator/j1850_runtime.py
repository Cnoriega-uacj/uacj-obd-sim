"""
J1850 transceiver runtime: reads framed bytes from a J1850 transceiver
(e.g. MC33390 for VPW, or a dual-wire driver for PWM) over a UART-style
interface, dispatches through EcuEmulator, and writes the response back.

The transceiver object is duck-typed (read(n)/write(b)/in_waiting) so
this module is fully testable without hardware. Pattern matches
kline_runtime.py.

Wire-up notes (UACJ classroom build):
  - VPW (GM 2003-2007): MC33390 + Pi UART pin, single-wire OBD-II pin 2
  - PWM (Ford SCP 2003-2004): twin-wire driver, OBD-II pins 2 (BUS+) & 10 (BUS-)
The simulator answers to whichever variant the connected transceiver
emits; framing on the byte side is identical, so we don't need to know
which variant we're on.
"""

from __future__ import annotations

import logging
import threading
import time

from .ecu import EcuEmulator
from .j1850 import (
    J1850Error,
    SRC_ECU_DEFAULT,
    decode,
    encode_segmented_response,
)

log = logging.getLogger(__name__)


class J1850Runtime:
    """
    Read complete J1850 frames from a duck-typed serial-style port,
    answer them via the ECU, and write the response. Frame boundaries
    on a J1850 transceiver are normally indicated by a SOF/EOF flag from
    the chip; here we approximate by reading until the chip's read()
    returns an empty bytes object (or until INTER_BYTE_TIMEOUT_S of idle).

    For unit tests, pass a serial mock that returns a complete frame
    from a single read() call.
    """

    INTER_BYTE_TIMEOUT_S = 0.01
    MAX_FRAME_BYTES = 11  # 3 hdr + 7 data + 1 crc

    def __init__(self, ecu: EcuEmulator, port,  # type: ignore[no-untyped-def]
                 source_address: int = SRC_ECU_DEFAULT) -> None:
        self.ecu = ecu
        self.port = port
        self.source_address = source_address
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle_request_bytes(self, frame_bytes: bytes) -> list[bytes]:
        """
        Pure-data entry point used in tests. Returns the list of response
        frames (one per segment for long payloads, one for short).
        """
        try:
            req = decode(frame_bytes)
        except J1850Error as exc:
            log.warning("J1850 decode error: %s", exc)
            return []
        response_payload = self.ecu.handle(req.data)
        if not response_payload:
            return []
        return encode_segmented_response(
            response_payload,
            target=req.source,
            source=self.source_address,
        )

    def _read_one_frame(self) -> bytes | None:
        """Read until we have a plausible complete frame or timeout out."""
        buf = bytearray()
        deadline_idle = time.monotonic() + self.INTER_BYTE_TIMEOUT_S
        while len(buf) < self.MAX_FRAME_BYTES:
            chunk = self.port.read(1)
            if not chunk:
                if buf and time.monotonic() >= deadline_idle:
                    break
                if not buf:
                    return None
                continue
            buf.extend(chunk)
            deadline_idle = time.monotonic() + self.INTER_BYTE_TIMEOUT_S
            # Try to decode with the bytes we have; if it succeeds, we're done.
            if len(buf) >= 5:
                try:
                    decode(bytes(buf))
                    return bytes(buf)
                except J1850Error:
                    continue
        return bytes(buf) if buf else None

    def run(self) -> None:
        """Blocking loop. Use stop() from another thread to exit."""
        while not self._stop.is_set():
            try:
                frame = self._read_one_frame()
            except Exception as exc:
                log.warning("J1850 read failed: %s", exc)
                time.sleep(self.INTER_BYTE_TIMEOUT_S)
                continue
            if not frame:
                continue
            for response in self.handle_request_bytes(frame):
                try:
                    self.port.write(response)
                except Exception as exc:
                    log.warning("J1850 write failed: %s", exc)
