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


# SAE J1979 Mode 01 PID 01 monitor bit layout.
#
# Byte B (continuous monitors) upper nibble = "not complete" bits:
#   bit 4 = Misfire monitor not complete
#   bit 5 = Fuel system monitor not complete
#   bit 6 = Comprehensive Components monitor (CCM) not complete
#
# Byte D (non-continuous monitors) = "not complete" bits:
#   bit 0 = Catalyst (CAT)
#   bit 1 = Heated Catalyst (HCAT) / catalyst bank 2
#   bit 2 = Evaporative System (EVAP)
#   bit 3 = Secondary Air System (AIR)
#   bit 4 = A/C Refrigerant (deprecated, kept for layout)
#   bit 5 = Oxygen Sensor (O2S)
#   bit 6 = Oxygen Sensor Heater (HTR)
#   bit 7 = EGR System
#
# When a stored DTC exists for a given monitor, we mark that monitor as
# "not complete" so scan tools that cross-check DTC presence against
# readiness (Innova 5210 confirmed) render the readiness page sensibly
# instead of suppressing it as inconsistent.
#
# Each entry maps a 4-char DTC prefix → (target_byte_letter, bit_index).
_DTC_PREFIX_TO_MONITOR_BIT: dict[str, tuple[str, int]] = {
    # Continuous monitors (byte B upper nibble)
    "P000": ("B", 6),  # Generic powertrain — comprehensive components
    "P020": ("B", 6),  # Fuel/air metering (injector circuits) — CCM
    "P021": ("B", 6),  # Fuel/air metering — CCM
    "P022": ("B", 6),  # Throttle/pedal — CCM
    "P016": ("B", 5),  # Fuel volume regulator / fuel system
    "P017": ("B", 5),  # Fuel trim (lean/rich) — fuel system
    "P018": ("B", 5),  # Fuel composition / fuel system
    "P019": ("B", 5),  # Fuel rail pressure — fuel system
    "P030": ("B", 4),  # Random misfire (P0300) / cylinder N (P0301-0309) — misfire monitor
    # Non-continuous monitors (byte D)
    "P003": ("D", 6),  # O2 sensor heater bank 1
    "P005": ("D", 6),  # O2 sensor heater bank 2
    "P013": ("D", 5),  # O2 sensor circuit (bank 1 sensor 1/2)
    "P014": ("D", 5),  # O2 sensor circuit (bank 2)
    "P015": ("D", 5),  # O2 sensor circuit (bank 1 sensor 2/heater control)
    "P040": ("D", 7),  # EGR system
    "P041": ("D", 3),  # Secondary air system
    "P042": ("D", 0),  # Catalyst efficiency (bank 1)
    "P043": ("D", 1),  # Catalyst efficiency (bank 2) / heated catalyst
    "P044": ("D", 2),  # EVAP (purge flow, leak detected)
    "P045": ("D", 2),  # EVAP (vent control, gross leak)
    "P046": ("D", 2),  # Fuel level / EVAP related
}


def _derived_monitor_bytes(
    base_b: int, base_d: int, stored_dtcs: list[str]
) -> tuple[int, int]:
    """
    Return (byte_b, byte_d) for Mode 01 PID 01 derived from stored DTCs.

    Starts from the scenario's `monitor_b` / `monitor_d`, then ORs in
    "not complete" bits for any monitor that owns a stored DTC. The
    scenario's existing bits are preserved — this only ever ADDS
    not-complete bits, never clears them.
    """
    derived_b = base_b & 0xFF
    derived_d = base_d & 0xFF
    for code in stored_dtcs:
        prefix = code[:4].upper()
        target = _DTC_PREFIX_TO_MONITOR_BIT.get(prefix)
        if target is None:
            # Unknown DTC range — default to CCM not complete (byte B bit 6)
            # so the monitor row still renders rather than appearing fully
            # complete-but-with-DTCs (which scan tools treat as suspicious).
            derived_b |= 1 << 6
            continue
        byte, bit = target
        if byte == "B":
            derived_b |= 1 << bit
        elif byte == "D":
            derived_d |= 1 << bit
    return derived_b & 0xFF, derived_d & 0xFF


def _clean_ascii_field(value) -> str:
    """
    Sanitise a string-like value for Mode 09 ASCII transmission.

    Captures from v0.4.0 through v0.4.9 stored VIN / calibration ID /
    ECU name as the Python repr of a bytearray (e.g.
    ``"bytearray(b'JM1BL1L72C1627697')"``) because the adapter did not
    decode python-obd's bytearray response. Fixed forward in v0.4.10,
    but the simulator still needs to play back those legacy sessions
    cleanly. Steps:

    1. If passed bytes/bytearray, decode to ASCII.
    2. If passed a Python repr string ("bytearray(b'...')" / "b'...'"),
       peel the wrapper.
    3. Decode `\\x00` and similar escape sequences (legacy reprs of
       null padding flow through as literal escape characters when
       stored to JSON).
    4. Drop non-VIN-shape characters (nulls, whitespace, control codes).

    The output is always a plain ASCII string with only printable
    characters and surrounding whitespace trimmed.
    """
    import codecs

    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("ascii", errors="replace")
        except Exception:
            return ""
    else:
        text = str(value)
    text = text.strip()
    # Strip the legacy "bytearray(b'...')" wrapper if it is the entire value.
    wrappers = (
        ("bytearray(b'", "')"),
        ("bytearray(b\"", "\")"),
        ("b'", "'"),
        ("b\"", "\""),
    )
    for prefix, suffix in wrappers:
        if text.startswith(prefix) and text.endswith(suffix):
            text = text[len(prefix):-len(suffix)]
            break
    # If literal escape sequences came through (\x00, \n, \t, ...),
    # interpret them as their bytes-form so we can strip them.
    if "\\x" in text or "\\n" in text or "\\t" in text or "\\r" in text:
        try:
            decoded, _ = codecs.escape_decode(text.encode("ascii", errors="replace"))
            text = decoded.decode("ascii", errors="replace")
        except Exception:
            pass
    # Keep only ASCII printable characters (0x20-0x7E). This drops nulls,
    # control codes, and any decode replacements (`�` becomes a
    # non-printable when re-encoded to ASCII).
    text = "".join(c for c in text if 0x20 <= ord(c) <= 0x7E)
    return text.strip()


# v0.4.13: special TIDs that return the supported-MIDs bitmap for the
# next 32 MIDs (instead of test result data). Same convention as Mode 01
# PID 0x00 / 0x20 / 0x40 / ... — strict scan tools query these first.
_MODE06_BITMAP_TIDS: frozenset[int] = frozenset({
    0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0,
})


def _mode06_supported_bitmap(implemented_mids: set[int], start_tid: int) -> bytes:
    """Build the 4-byte supported-MIDs bitmap for Mode 06 starting at
    `start_tid`. Bit 7 of byte A = MID (start_tid+1); bit 0 of byte D =
    MID (start_tid+32). The implemented set is the scenario's
    `obd_test_results` keys — single source of truth, no drift risk."""
    bitmap = 0
    for mid in implemented_mids:
        if start_tid < mid <= start_tid + 0x20:
            offset = mid - start_tid  # 1..32
            bit = 32 - offset
            bitmap |= 1 << bit
    return bytes([
        (bitmap >> 24) & 0xFF,
        (bitmap >> 16) & 0xFF,
        (bitmap >> 8) & 0xFF,
        bitmap & 0xFF,
    ])


# v0.4.13: single source of truth for "what Mode 09 PIDs the dispatcher
# implements." The supported-PIDs bitmap (PID 0x00) is derived from
# this set so it can never drift out of sync.
_MODE09_IMPLEMENTED_PIDS: frozenset[int] = frozenset({
    0x01,  # VIN message count
    0x02,  # VIN
    0x03,  # Cal ID message count
    0x04,  # Cal ID
    0x05,  # CVN message count
    0x06,  # CVN
    0x0A,  # ECU name
})


def _mode09_supported_bitmap() -> bytes:
    """Return 4-byte bitmap advertising which Mode 09 PIDs we answer.

    Per SAE J1979 the bitmap covers PIDs 0x01-0x20. Bit 7 of byte A is
    PID 1; bit 0 of byte A is PID 8; bit 7 of byte B is PID 9; bit 0 of
    byte B is PID 16; etc. We never advertise PID 0x00 itself (the
    bitmap is implicitly always supported).
    """
    bitmap = 0
    for pid in _MODE09_IMPLEMENTED_PIDS:
        if 1 <= pid <= 0x20:
            bit = 32 - pid  # PID 1 → bit 31; PID 0x20 → bit 0
            bitmap |= 1 << bit
    return bytes([
        (bitmap >> 24) & 0xFF,
        (bitmap >> 16) & 0xFF,
        (bitmap >> 8) & 0xFF,
        bitmap & 0xFF,
    ])


def _parse_cvn(value) -> bytes | None:
    """
    Parse a Calibration Verification Number into the 4 raw bytes the
    scan tool expects on Mode 09 PID 0x06.

    Accepts several shapes commonly seen on capture sources:
      - "CDA08E85"        (8 hex chars, no separators)
      - "CD A0 8E 85"     (space-separated bytes — Innova display style)
      - "CD-A0-8E-85"     (dash-separated)
      - "0xCDA08E85"      (with 0x prefix)
      - bytes(b"\\xCD\\xA0\\x8E\\x85") (raw bytes pass-through)
      - None / "" / unparseable → returns None (caller emits NRC)

    Always returns exactly 4 bytes on success.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (bytes, bytearray)):
        if len(value) == 4:
            return bytes(value)
        if len(value) > 4:
            return bytes(value[:4])
        return bytes(value).rjust(4, b"\x00")
    text = _clean_ascii_field(value)
    if not text:
        return None
    text = text.replace("0x", "").replace("0X", "")
    text = text.replace(" ", "").replace("-", "").replace(":", "")
    if len(text) < 8:
        text = text.rjust(8, "0")
    elif len(text) > 8:
        text = text[:8]
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


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
    cvn: str | None = None  # Calibration Verification Number, 4-byte hex (e.g. "CDA08E85")
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
    # v0.5.0: optional captured time-series for dynamic replay. When
    # non-empty the simulator server runs a `ReplayEngine` that mutates
    # `live` according to these samples at the recorded cadence. Type
    # is `list[TimedSample]` (imported lazily to avoid a circular
    # dependency between ecu.py and replay_engine.py).
    live_timeseries: list = field(default_factory=list)
    live_timeseries_loop: bool = True

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
            #
            # Byte A — fully derived: bit 7 = MIL on if any stored DTC,
            # bits 0-6 = stored DTC count (saturating at 0x7F). Scenario
            # `monitor_status` is ignored to avoid inconsistencies with
            # Mode 03.
            #
            # Bytes B/D — start from the scenario, then OR in "not complete"
            # bits for any monitor that owns a stored DTC (see
            # `_DTC_PREFIX_TO_MONITOR_BIT`). Without this derivation, the
            # default scenario state (all monitors complete) combined with
            # stored DTCs is internally inconsistent and the Innova 5210
            # silently drops the monitor-badges row. With the derivation,
            # the row renders with the relevant monitor flagged not-complete,
            # which is what a real vehicle with that DTC would show.
            #
            # Byte C — availability bitmap. We never derive this; only the
            # scenario controls which monitors the vehicle is reported to
            # have at all (e.g. some vehicles don't have secondary air or
            # heated catalyst).
            stored_count = min(len(self.state.dtcs_stored), 0x7F)
            mil_on = 0x80 if self.state.dtcs_stored else 0x00
            byte_a = mil_on | stored_count
            byte_b, byte_d = _derived_monitor_bytes(
                self.state.monitor_b, self.state.monitor_d,
                self.state.dtcs_stored,
            )
            return bytes([0x41, 0x01,
                            byte_a,
                            byte_b,
                            self.state.monitor_c & 0xFF,
                            byte_d])

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
        # v0.4.13: Mode 09 PID 0x00 (supported PIDs bitmap) is now derived
        # dynamically from `_MODE09_IMPLEMENTED_PIDS` — the SAME source the
        # dispatcher uses. This eliminates the Pattern E drift bug where
        # the static bitmap could advertise PIDs the dispatcher did not
        # implement (or vice versa). To add a new Mode 09 PID, just add
        # its handler and put the PID in the implemented set — the
        # bitmap updates itself and the symmetry test stays passing.
        if pid == 0x00:
            return bytes([0x49, 0x00]) + _mode09_supported_bitmap()
        # PID 0x01: VIN message count (1 ECU = 1 VIN data item).
        # PID 0x03: Calibration ID message count.
        # PID 0x05: CVN message count.
        # These are queried by strict scan tools BEFORE the actual data
        # read. Per SAE J1979 the response is one byte = number of data
        # messages. We always answer 1 because we emulate a single ECU.
        if pid in (0x01, 0x03, 0x05):
            return bytes([0x49, pid, 0x01])
        if pid == 0x02:
            vin = _clean_ascii_field(self.state.vin)
            if not vin:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            raw = vin.encode("ascii", errors="replace")
            if len(raw) > 17:
                raw = raw[:17]
            elif len(raw) < 17:
                raw = raw.rjust(17, b"\x00")
            # 0x49 0x02 NODI=1 + 17 ASCII bytes = 20 bytes total
            return bytes([0x49, 0x02, 0x01]) + raw
        if pid == 0x04:
            cal = _clean_ascii_field(self.state.calibration_id)
            if not cal:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            raw = cal.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
            return bytes([0x49, 0x04, 0x01]) + raw
        if pid == 0x06:
            # CVN — Calibration Verification Number. Per SAE J1979,
            # one 4-byte hash per Cal ID. We store CVN as either an
            # 8-char hex string ("CDA08E85") or a space-separated
            # 4-byte form ("CD A0 8E 85"). Default to zero-CVN if
            # the scenario does not carry one.
            cvn_bytes = _parse_cvn(self.state.cvn)
            if cvn_bytes is None:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            return bytes([0x49, 0x06, 0x01]) + cvn_bytes
        if pid == 0x0A:
            name = _clean_ascii_field(self.state.ecu_name)
            if not name:
                return _negative(0x09, NRC_REQUEST_OUT_OF_RANGE)
            raw = name.encode("ascii", errors="replace")[:20].ljust(20, b"\x00")
            return bytes([0x49, 0x0A, 0x01]) + raw
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

        Special TIDs (the "supported MIDs" bitmap pre-queries strict scan
        tools issue before reading individual results — same shape as
        Mode 01 PID 0x00):
          0x00 / 0x20 / 0x40 / 0x60 / 0x80 / 0xA0 / 0xC0 / 0xE0
          → 4-byte bitmap for the next 32 MIDs in that range, derived
            dynamically from `self.state.obd_test_results` keys so there
            is no Pattern-E drift between what we advertise and what the
            dispatcher actually answers.

        Response layout for a normal MID:
          0x46 [TID CID UASID Value(2) Min(2) Max(2)]*

        UASID (Unit and Scaling ID) is fixed to 0x00 for the simulator —
        scan tools that don't honour UASID will still display the raw 16-bit
        Value and bracket. Test IDs are scenario-defined; if the scenario
        provides no results for a known MID, we answer with the service byte
        alone (the "supported but no data" convention).
        """
        results = self.state.obd_test_results
        if args:
            tid = args[0]
            # Supported-MIDs bitmap branch (Pattern E source-of-truth).
            if tid in _MODE06_BITMAP_TIDS:
                return bytes([0x46, tid]) + _mode06_supported_bitmap(
                    set(results.keys()), tid
                )
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
