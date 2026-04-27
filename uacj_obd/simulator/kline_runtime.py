"""
K-Line UART runtime: reads KWP2000 frames from a serial port, dispatches
through EcuEmulator, writes responses back. The serial port is duck-typed
to keep this module testable without hardware.
"""

from __future__ import annotations

import logging
import threading
import time

from .ecu import EcuEmulator
from .kline import (
    ECU_ADDRESS_PHYSICAL,
    KlineError,
    KwpFrame,
    decode,
    encode_response,
    total_frame_length,
)

log = logging.getLogger(__name__)


class KlineRuntime:
    """
    Continuously reads bytes from the UART, assembles KWP2000 frames,
    and answers each request. The serial object only needs `read(n)`,
    `write(b)`, and `in_waiting` — compatible with pyserial.
    """

    INTER_BYTE_TIMEOUT_S = 0.05

    def __init__(self, ecu: EcuEmulator, serial) -> None:  # type: ignore[no-untyped-def]
        self.ecu = ecu
        self.serial = serial
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle_request_bytes(self, frame_bytes: bytes) -> bytes:
        """
        Pure-data entry point used in tests: take a complete KWP2000 frame
        and return the encoded response frame bytes.
        """
        try:
            req = decode(frame_bytes)
        except KlineError as exc:
            log.warning("KWP decode error: %s", exc)
            return b""
        response_payload = self.ecu.handle(req.data)
        if not response_payload:
            return b""
        # Real ECUs always answer with their physical address as source,
        # regardless of whether the request came in on the physical or
        # functional address. The tester address (req.source) becomes
        # the target.
        return encode_response(response_payload, target=req.source, source=ECU_ADDRESS_PHYSICAL)

    def _read_one_frame(self) -> bytes | None:
        """Best-effort blocking read of one complete KWP frame from the UART."""
        # First byte (fmt)
        first = self.serial.read(1)
        if not first:
            return None
        fmt = first[0]
        # Read enough bytes to determine length
        head = bytearray(first)
        head.extend(self.serial.read(2))  # tgt + src
        expected = total_frame_length(fmt, bytes(head))
        if expected is None:
            head.extend(self.serial.read(1))  # Len byte
            expected = total_frame_length(fmt, bytes(head))
        if expected is None:
            return None
        remaining = expected - len(head)
        if remaining > 0:
            head.extend(self.serial.read(remaining))
        return bytes(head)

    def run(self) -> None:
        """Blocking loop. Use stop() from another thread to exit."""
        while not self._stop.is_set():
            try:
                frame = self._read_one_frame()
            except Exception as exc:
                log.warning("UART read failed: %s", exc)
                time.sleep(self.INTER_BYTE_TIMEOUT_S)
                continue
            if not frame:
                continue
            response = self.handle_request_bytes(frame)
            if response:
                try:
                    self.serial.write(response)
                except Exception as exc:
                    log.warning("UART write failed: %s", exc)

    @classmethod
    def open_serial(cls, ecu: EcuEmulator, port: str = "/dev/serial0",
                     baudrate: int = 10400, timeout: float = 0.1) -> "KlineRuntime":
        """Open a pyserial UART tied to the L9637 transceiver."""
        try:
            import serial  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError(f"pyserial not available: {exc}") from exc
        ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        return cls(ecu, ser)
