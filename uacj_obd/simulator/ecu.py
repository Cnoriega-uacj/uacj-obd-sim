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

import time
from collections import deque
from dataclasses import dataclass, field

from .encoders import (
    encodable_pids,
    encode_mfg_pid,
    encode_pid,
    supported_pid_bitmap,
)


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
    # Mode 0x06 on-board monitoring test results.
    # Map of test_id (int) → (component_id, value, min, max).
    # Values are in raw 16-bit units; the dispatch packs them per SAE J1979 mode 0x06.
    obd_test_results: dict[int, tuple[int, int, int, int]] = field(default_factory=dict)

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


_SERVICE_NAMES = {
    0x01: "current data", 0x02: "freeze frame",
    0x03: "stored DTCs", 0x04: "clear DTCs",
    0x06: "OBD monitoring test results",
    0x07: "pending DTCs", 0x09: "vehicle info",
    0x0A: "permanent DTCs", 0x22: "mfg PID",
}


def _summarize(request: bytes, response: bytes) -> str:
    """Short human-readable description of one OBD-II request/response pair."""
    if not request:
        return "empty request"
    service = request[0]
    name = _SERVICE_NAMES.get(service, f"service 0x{service:02X}")
    if response and response[0] == 0x7F:
        return f"{name} → NRC 0x{response[2]:02X}"
    if service == 0x01 and len(request) >= 2:
        return f"{name} PID 0x{request[1]:02X}"
    if service == 0x09 and len(request) >= 2:
        return f"{name} PID 0x{request[1]:02X}"
    if service == 0x22 and len(request) >= 3:
        return f"{name} 0x{request[1]:02X}{request[2]:02X}"
    if service == 0x03:
        n = response[1] if len(response) >= 2 and response[0] == 0x43 else 0
        return f"{name} (count={n})"
    if service == 0x04:
        return "clear DTCs"
    return name


class EcuEmulator:
    def __init__(self, state: ScenarioState | None = None, log_capacity: int = 500) -> None:
        self.state = state or ScenarioState()
        # Bounded ring buffer of recent (request, response) interactions
        # so the laptop can show "what did the student try?" in the
        # classroom view. Bounded so a long class doesn't grow unbounded.
        self.log: deque[dict] = deque(maxlen=log_capacity)

    def load(self, state: ScenarioState) -> None:
        self.state = state
        # Don't clear the log on scenario reload — instructors want to
        # see what the student did across scenario changes.

    # ---------- public dispatch ----------

    def handle(self, request: bytes) -> bytes:
        if not request:
            response = _negative(0x00, NRC_SERVICE_NOT_SUPPORTED)
            self._log_interaction(request, response)
            return response
        service = request[0]
        try:
            response = self._dispatch(service, request)
        except Exception:
            response = _negative(service, NRC_REQUEST_OUT_OF_RANGE)
        if response is None:
            response = _negative(service, NRC_SERVICE_NOT_SUPPORTED)
        self._log_interaction(request, response)
        return response

    def _dispatch(self, service: int, request: bytes) -> bytes | None:
        if service == 0x01:
            return self._mode01(request[1:])
        if service == 0x02:
            return self._mode02(request[1:])
        if service == 0x03:
            return self._mode03()
        if service == 0x04:
            return self._mode04()
        if service == 0x06:
            return self._mode06(request[1:])
        if service == 0x07:
            return self._mode07()
        if service == 0x09:
            return self._mode09(request[1:])
        if service == 0x0A:
            return self._mode0A()
        if service == 0x22:
            return self._mode22(request[1:])
        return None

    def _log_interaction(self, request: bytes, response: bytes) -> None:
        self.log.append({
            "ts": time.time(),
            "service": request[0] if request else None,
            "request": request.hex(),
            "response": response.hex(),
            "summary": _summarize(request, response),
        })

    def recent_log(self, limit: int = 100) -> list[dict]:
        return list(self.log)[-limit:]

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
            # Monitor status: 4 bytes A B C D per SAE J1979.
            # Byte A is fully derived from current state: bit 7 = MIL on if
            # any stored DTC exists; bits 0-6 = stored DTC count (saturating
            # at 0x7F). Any `monitor_status` value on the scenario is
            # ignored — scenarios that try to set "MIL off, 0 DTCs" while
            # also storing a DTC produced an inconsistency that some
            # scan tools (e.g. Innova 5210) detected by refusing to render
            # the readiness page. Bytes B/C/D continue to come from the
            # scenario (the available/complete bits for the continuous and
            # non-continuous monitors).
            stored_count = min(len(self.state.dtcs_stored), 0x7F)
            mil_on = 0x80 if self.state.dtcs_stored else 0x00
            byte_a = mil_on | stored_count
            return bytes([0x41, 0x01,
                            byte_a,
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

    def _mode06(self, args: bytes) -> bytes:
        """
        Mode 0x06 — on-board monitoring test results (CAN ECUs after 2002).

        Request layout per SAE J1979:
          0x06 TID                 — return all components for this test ID
          0x06                     — return all configured tests (some tools
                                      send a bare 0x06 to enumerate)

        Response layout:
          0x46 [TID CID UASID Value(2) Min(2) Max(2)]*

        UASID (Unit and Scaling ID) is fixed to 0x00 for the simulator —
        scan tools that don't honour UASID will still display the raw 16-bit
        Value and bracket. Test IDs are scenario-defined; if the scenario
        provides no results, we answer with an empty result set rather than
        an NRC, mirroring vehicles where mode 06 is supported but the
        monitor hasn't completed yet.
        """
        results = self.state.obd_test_results
        if args:
            tid = args[0]
            entry = results.get(tid)
            if entry is None:
                # No data for this TID — vehicles typically respond with the
                # service byte alone, indicating "supported but no data".
                return bytes([0x46])
            cid, value, mn, mx = entry
            return bytes([
                0x46, tid, cid & 0xFF, 0x00,
                (value >> 8) & 0xFF, value & 0xFF,
                (mn >> 8) & 0xFF, mn & 0xFF,
                (mx >> 8) & 0xFF, mx & 0xFF,
            ])
        # Bare 0x06 → enumerate every configured test.
        out = bytearray([0x46])
        for tid, (cid, value, mn, mx) in sorted(results.items()):
            out.extend([
                tid & 0xFF, cid & 0xFF, 0x00,
                (value >> 8) & 0xFF, value & 0xFF,
                (mn >> 8) & 0xFF, mn & 0xFF,
                (mx >> 8) & 0xFF, mx & 0xFF,
            ])
        return bytes(out)

    def _mode22(self, args: bytes) -> bytes:
        """Manufacturer-specific PID read (16-bit PID, ISO 14229 service 0x22)."""
        if len(args) < 2:
            return _negative(0x22, NRC_REQUEST_OUT_OF_RANGE)
        pid = (args[0] << 8) | args[1]
        key = f"22{pid:04X}"
        value = self.state.live.get(key)
        data = encode_mfg_pid(key, value)
        if data is None:
            return _negative(0x22, NRC_REQUEST_OUT_OF_RANGE)
        return bytes([0x62, args[0], args[1]]) + data

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
