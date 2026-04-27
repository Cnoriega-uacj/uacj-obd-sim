"""
ECU emulator: stateless transformer from request payload → response payload.

Drives the simulator board's responses to a student's scan tool. All
state (DTCs, sensor values, monitors, VIN) lives in `ScenarioState`,
which the laptop pushes over the network.

Designed to be entirely testable without a CAN bus — the bus runtime
is a thin wrapper that calls into here.

Supported services (subset of SAE J1979 needed for training):
  0x01: Show current data (PID values + supported-PID bitmaps)
  0x02: Show freeze-frame data
  0x03: Show stored DTCs
  0x04: Clear DTCs
  0x07: Show pending DTCs
  0x09: Vehicle information (VIN, calibration ID, ECU name)
  0x0A: Show permanent DTCs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .encoders import encode_pid, encodable_pids, supported_pid_bitmap


# Negative response codes (NRC)
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
NRC_REQUEST_OUT_OF_RANGE = 0x31


def _dtc_code_to_bytes(code: str) -> bytes:
    """
    Encode a DTC string like "P0420" or "C1234" into 2 bytes per SAE J2012.

    Top 2 bits of byte 0 encode the letter:
      P=00, C=01, B=10, U=11
    Remaining 14 bits are the 4 hex digits of the numeric portion.
    """
    if len(code) != 5:
        raise ValueError(f"invalid DTC code: {code}")
    letter_map = {"P": 0b00, "C": 0b01, "B": 0b10, "U": 0b11}
    if code[0] not in letter_map:
        raise ValueError(f"unknown DTC letter: {code[0]}")
    nibble_high = letter_map[code[0]] << 6
    digits = int(code[1:], 16)
    word = (nibble_high << 8) | digits
    word |= ((digits >> 12) & 0x03) << 14  # second nibble high bits
    # The above is awkward — simpler computation:
    digits = int(code[1:], 16) & 0x3FFF
    high = (letter_map[code[0]] << 6) | ((digits >> 8) & 0x3F)
    low = digits & 0xFF
    return bytes([high, low])


def _pack_dtc_list(dtcs: list[str]) -> bytes:
    """
    Mode 03/07/0A response data: count byte + 2 bytes per DTC.

    The count byte is the *number of DTCs* (some implementations use
    bytes-of-DTC-data; SAE J1979 uses count of DTCs for modern CAN).
    """
    body = b"".join(_dtc_code_to_bytes(c) for c in dtcs)
    return bytes([len(dtcs)]) + body


@dataclass
class ScenarioState:
    """
    Mutable snapshot the ECU emulator answers from. All fields are
    optional — missing values cause negative-response codes.
    """

    vin: str | None = None
    calibration_id: str | None = None
    ecu_name: str | None = None
    live: dict[str, float | int | str] = field(default_factory=dict)
    dtcs_stored: list[str] = field(default_factory=list)
    dtcs_pending: list[str] = field(default_factory=list)
    dtcs_permanent: list[str] = field(default_factory=list)
    monitor_status: int = 0x00  # mode 01 PID 01: byte A; bytes B-D follow below
    monitor_b: int = 0x07
    monitor_c: int = 0xFF
    monitor_d: int = 0x00
    freeze_frame: dict[str, float | int | str] = field(default_factory=dict)
    freeze_dtc: str | None = None

    def supported_pid_keys(self) -> set[str]:
        """All PIDs we can answer mode 0x01 for, given the live data."""
        keys = set(self.live.keys())
        # PID 0x01 (monitor status) and 0x00 (supported bitmap groups) are always supported
        keys.add("0101")
        return {k.upper() for k in keys if k.startswith("01")}

    def clear_dtcs(self) -> None:
        self.dtcs_stored = []
        self.dtcs_pending = []
        # Permanent DTCs are NOT cleared by mode 0x04 per SAE J1979 — only
        # cleared after the underlying fault is fixed and the monitor passes.
        self.freeze_frame = {}
        self.freeze_dtc = None


def _negative(service: int, nrc: int) -> bytes:
    return bytes([0x7F, service & 0xFF, nrc])


class EcuEmulator:
    def __init__(self, state: ScenarioState | None = None) -> None:
        self.state = state or ScenarioState()

    def load(self, state: ScenarioState) -> None:
        self.state = state

    # ---------- public dispatch ----------

    def handle(self, request: bytes) -> bytes:
        if not request:
            return _negative(0x00, NRC_SERVICE_NOT_SUPPORTED)
        service = request[0]
        try:
            if service == 0x01:
                return self._mode01(request[1:])
            if service == 0x02:
                return self._mode02(request[1:])
            if service == 0x03:
                return self._mode03()
            if service == 0x04:
                return self._mode04()
            if service == 0x07:
                return self._mode07()
            if service == 0x09:
                return self._mode09(request[1:])
            if service == 0x0A:
                return self._mode0A()
        except Exception:
            return _negative(service, NRC_REQUEST_OUT_OF_RANGE)
        return _negative(service, NRC_SERVICE_NOT_SUPPORTED)

    # ---------- mode handlers ----------

    def _mode01(self, args: bytes) -> bytes:
        if not args:
            return _negative(0x01, NRC_REQUEST_OUT_OF_RANGE)
        pid = args[0]
        # Supported PID bitmap groups: 0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0
        if pid in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0):
            answerable = encodable_pids() & self.state.supported_pid_keys()
            bitmap = supported_pid_bitmap(answerable, pid)
            return bytes([0x41, pid]) + bitmap

        if pid == 0x01:
            # Monitor status: 4 bytes A B C D
            return bytes([0x41, 0x01,
                            self.state.monitor_status & 0xFF,
                            self.state.monitor_b & 0xFF,
                            self.state.monitor_c & 0xFF,
                            self.state.monitor_d & 0xFF])

        key = f"01{pid:02X}"
        value = self.state.live.get(key)
        data = encode_pid(key, value)
        if data is None:
            return _negative(0x01, NRC_REQUEST_OUT_OF_RANGE)
        return bytes([0x41, pid]) + data

    def _mode02(self, args: bytes) -> bytes:
        # Format: PID + frame# (we only support frame 0)
        if len(args) < 2:
            return _negative(0x02, NRC_REQUEST_OUT_OF_RANGE)
        pid = args[0]
        # Frame 0 PID 02 returns the freeze-DTC code
        if pid == 0x02:
            if not self.state.freeze_dtc:
                return _negative(0x02, NRC_REQUEST_OUT_OF_RANGE)
            return bytes([0x42, 0x02, 0x00]) + _dtc_code_to_bytes(self.state.freeze_dtc)
        # Otherwise return the freeze-frame snapshot of that live PID
        key = f"01{pid:02X}"
        value = self.state.freeze_frame.get(key) or self.state.freeze_frame.get(self._friendly_name(key))
        data = encode_pid(key, value)
        if data is None:
            return _negative(0x02, NRC_REQUEST_OUT_OF_RANGE)
        return bytes([0x42, pid, 0x00]) + data

    def _mode03(self) -> bytes:
        return bytes([0x43]) + _pack_dtc_list(self.state.dtcs_stored)

    def _mode04(self) -> bytes:
        self.state.clear_dtcs()
        return bytes([0x44])

    def _mode07(self) -> bytes:
        return bytes([0x47]) + _pack_dtc_list(self.state.dtcs_pending)

    def _mode09(self, args: bytes) -> bytes:
        if not args:
            return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
        pid = args[0]
        # PID 0x00: supported PIDs bitmap (we support 0x02 VIN, 0x04 cal id, 0x0A ECU name)
        if pid == 0x00:
            return bytes([0x49, 0x00, 0x54, 0x00, 0x00, 0x00])
        if pid == 0x02:
            if not self.state.vin:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            vin = self.state.vin.encode("ascii")
            if len(vin) > 17:
                vin = vin[:17]
            elif len(vin) < 17:
                vin = vin.rjust(17, b"\x00")
            # 0x49 0x02 NODI=1 + 17 ASCII bytes = 20 bytes total
            return bytes([0x49, 0x02, 0x01]) + vin
        if pid == 0x04:
            if not self.state.calibration_id:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            cal = self.state.calibration_id.encode("ascii")[:16].ljust(16, b"\x00")
            return bytes([0x49, 0x04, 0x01]) + cal
        if pid == 0x0A:
            if not self.state.ecu_name:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            name = self.state.ecu_name.encode("ascii")[:20].ljust(20, b"\x00")
            return bytes([0x49, 0x0A, 0x01]) + name
        return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)

    def _mode0A(self) -> bytes:
        return bytes([0x4A]) + _pack_dtc_list(self.state.dtcs_permanent)

    # ---------- helpers ----------

    @staticmethod
    def _friendly_name(key: str) -> str:
        # The freeze-frame dict on disk uses friendly names ("RPM", "SPEED").
        # Best-effort lookup so we accept both forms.
        mapping = {
            "010C": "RPM", "010D": "SPEED", "0105": "COOLANT_TEMP",
            "0111": "THROTTLE_POS", "0104": "ENGINE_LOAD", "0110": "MAF",
            "010F": "INTAKE_TEMP", "0114": "O2_B1S1_VOLTAGE",
        }
        return mapping.get(key, key)
