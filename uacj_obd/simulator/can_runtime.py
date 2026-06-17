"""
SocketCAN runtime: thin loop that ties CAN frames to the EcuEmulator
through ISO-TP framing.

This module is purposely simple — the heavy logic is in `ecu.py` and
`iso_tp.py`, both of which are testable without hardware. Use the
`run_can_loop()` entry point on the Pi.
"""

from __future__ import annotations

import logging
import threading

from .ecu import EcuEmulator
from .iso_tp import CanFrame, IsoTpError, IsoTpFramer

log = logging.getLogger(__name__)


# 11-bit OBD-II IDs
ID_FUNCTIONAL_REQUEST = 0x7DF
ID_PHYSICAL_REQUEST_BASE = 0x7E0
ID_PHYSICAL_RESPONSE_BASE = 0x7E8


class CanRuntime:
    """
    Listens on a CAN bus, dispatches each request through the EcuEmulator,
    and sends back ISO-TP-framed responses.

    The runtime is hardware-agnostic — pass in a `bus` object with
    `recv(timeout)` and `send(msg)` methods (compatible with python-can).
    """

    def __init__(self, ecu: EcuEmulator, bus, response_id: int = ID_PHYSICAL_RESPONSE_BASE) -> None:
        self.ecu = ecu
        self.bus = bus
        self.response_id = response_id
        self._framer = IsoTpFramer(tx_id=response_id, rx_id=ID_FUNCTIONAL_REQUEST)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle_request_frame(self, frame: CanFrame) -> list[CanFrame]:
        """
        Public entry-point used by tests. Returns the list of CAN frames
        that should be sent back, or [] if no response is ready yet
        (e.g. while assembling a multi-frame request).
        """
        if frame.arbitration_id not in (ID_FUNCTIONAL_REQUEST,
                                          ID_PHYSICAL_REQUEST_BASE,
                                          ID_PHYSICAL_REQUEST_BASE + 1):
            return []
        try:
            payload = self._framer.decode(frame)
        except IsoTpError as exc:
            log.warning("ISO-TP decode error: %s", exc)
            return []
        if payload is None:
            return []
        response = self.ecu.handle(payload)
        if not response:
            return []
        return self._framer.encode(response)

    def run(self) -> None:
        """Blocking loop. Use `stop()` from another thread to exit."""
        while not self._stop.is_set():
            msg = self.bus.recv(timeout=0.2)
            if msg is None:
                continue
            frame = CanFrame(arbitration_id=msg.arbitration_id, data=bytes(msg.data))
            responses = self.handle_request_frame(frame)
            for r in responses:
                self.bus.send(_to_canmsg(r))

    @classmethod
    def open_socketcan(cls, ecu: EcuEmulator, channel: str = "can0",
                         response_id: int = ID_PHYSICAL_RESPONSE_BASE) -> "CanRuntime":
        """Convenience: open a python-can SocketCAN bus and return a runtime."""
        try:
            import can  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError(f"python-can not available: {exc}") from exc
        bus = can.interface.Bus(channel=channel, interface="socketcan")
        return cls(ecu, bus, response_id=response_id)


def _to_canmsg(frame: CanFrame):  # type: ignore[no-untyped-def]
    """Convert a domain CanFrame to a python-can Message lazily."""
    import can  # type: ignore[import-not-found]
    return can.Message(arbitration_id=frame.arbitration_id, data=list(frame.data),
                        is_extended_id=frame.arbitration_id > 0x7FF)


# SAE J1979 Mode 01 PID 01 monitor name → (category, bit position).
#
# Category "continuous" → byte B (bits 0-2 supported / bits 4-6 not-complete).
# Category "non_continuous" → byte C (supported) + byte D (not-complete) at the
# same bit index.
#
# Names are matched case-insensitively after trimming, so the encoder accepts
# the preset's display names ("Evaporative System") and the abbreviations the
# Innova prints on screen ("EVAP", "O2S", "HTR").
_MONITOR_NAME_TO_POSITION: dict[str, tuple[str, int]] = {
    # Continuous monitors → byte B
    "misfire": ("continuous", 0),
    "mis": ("continuous", 0),
    "fuel system": ("continuous", 1),
    "fuel": ("continuous", 1),
    "fue": ("continuous", 1),
    "comprehensive components": ("continuous", 2),
    "components": ("continuous", 2),
    "ccm": ("continuous", 2),
    # Non-continuous monitors → bytes C/D
    "catalyst": ("non_continuous", 0),
    "cat": ("non_continuous", 0),
    "heated catalyst": ("non_continuous", 1),
    "hcat": ("non_continuous", 1),
    "hca": ("non_continuous", 1),
    "evaporative system": ("non_continuous", 2),
    "evap": ("non_continuous", 2),
    "eva": ("non_continuous", 2),
    "secondary air system": ("non_continuous", 3),
    "secondary air": ("non_continuous", 3),
    "air": ("non_continuous", 3),
    "a/c system refrigerant": ("non_continuous", 4),
    "a/c refrigerant": ("non_continuous", 4),
    "a/c": ("non_continuous", 4),
    "ac": ("non_continuous", 4),
    "oxygen sensor": ("non_continuous", 5),
    "o2 sensor": ("non_continuous", 5),
    "o2s": ("non_continuous", 5),
    "o2": ("non_continuous", 5),
    "ozs": ("non_continuous", 5),
    "oxygen sensor heater": ("non_continuous", 6),
    "o2 sensor heater": ("non_continuous", 6),
    "o2s heater": ("non_continuous", 6),
    "o2 heater": ("non_continuous", 6),
    "heater": ("non_continuous", 6),
    "htr": ("non_continuous", 6),
    "egr system": ("non_continuous", 7),
    "egr": ("non_continuous", 7),
}


def _encode_monitors_per_j1979(monitors: list[dict]) -> tuple[int, int, int]:
    """
    Encode a scenario `monitors[]` array into Mode 01 PID 01 bytes B, C, D
    per SAE J1979.

    Each monitor entry is a dict with `id` or `name` plus boolean `supported`
    and `ready` fields. Bit semantics per spec:

    - "Supported" bit = 1 means the vehicle has that monitor at all.
    - "Not complete" bit = 1 means the monitor has not run-to-completion since
      the last DTC clear (i.e. NOT ready).

    Unknown monitor names are silently ignored — the caller can still rely on
    the scenario's default `monitor_b` / `monitor_c` / `monitor_d` for any
    bits the array doesn't touch.

    Returns (byte_b, byte_c, byte_d). Byte A is derived elsewhere from DTCs.
    """
    byte_b = 0
    byte_c = 0
    byte_d = 0
    for m in monitors:
        raw_name = m.get("id") or m.get("name") or ""
        key = raw_name.strip().lower()
        position = _MONITOR_NAME_TO_POSITION.get(key)
        if position is None:
            continue
        category, bit = position
        supported = bool(m.get("supported", False))
        ready = bool(m.get("ready", False))
        if category == "continuous":
            if supported:
                byte_b |= 1 << bit         # bits 0-2: continuous supported
                if not ready:
                    byte_b |= 1 << (bit + 4)  # bits 4-6: continuous not-complete
        else:  # non_continuous
            if supported:
                byte_c |= 1 << bit         # byte C: non-continuous supported
                if not ready:
                    byte_d |= 1 << bit     # byte D: non-continuous not-complete
    return byte_b, byte_c, byte_d


def scenario_to_state(scenario_payload: dict, source_session: dict | None = None):
    """
    Convert an API scenario payload (and optionally the source session
    metadata) into a ScenarioState the ECU emulator can use.

    Live data merging order (later wins):
      1. scenario_payload["live_baseline"] — typically the latest value
         per-PID from the source session, populated by the laptop before
         pushing so the simulator can answer any PID the original car
         answered.
      2. source_session["live_latest"] (if passed separately).
      3. scenario_payload["live_overrides"] — instructor's edits.
    """
    from .ecu import ScenarioState

    vehicle = scenario_payload.get("vehicle") or {}
    live: dict[str, float | int | str] = {}
    if scenario_payload.get("live_baseline"):
        live.update({k.upper(): v for k, v in scenario_payload["live_baseline"].items()})
    if source_session and source_session.get("live_latest"):
        live.update({k.upper(): v for k, v in source_session["live_latest"].items()})
    live.update({k.upper(): v for k, v in (scenario_payload.get("live_overrides") or {}).items()})

    state = ScenarioState(
        vin=vehicle.get("vin"),
        calibration_id=vehicle.get("calibration_id"),
        ecu_name=vehicle.get("ecu_name"),
        live=live,
        dtcs_stored=[d["code"] for d in scenario_payload.get("dtcs", []) if d.get("status") == "stored"],
        dtcs_pending=[d["code"] for d in scenario_payload.get("dtcs", []) if d.get("status") == "pending"],
        dtcs_permanent=[d["code"] for d in scenario_payload.get("dtcs", []) if d.get("status") == "permanent"],
        freeze_frame=(scenario_payload.get("freeze_frame") or {}).get("pids", {}),
        freeze_dtc=(scenario_payload.get("freeze_frame") or {}).get("dtc"),
    )
    raw_tests = scenario_payload.get("obd_test_results") or {}
    if raw_tests:
        # Accept {tid_hex_or_int: [cid, val, min, max]} or {...: {...}} from JSON.
        normalized: dict[int, tuple[int, int, int, int]] = {}
        for k, entry in raw_tests.items():
            tid = int(k, 16) if isinstance(k, str) else int(k)
            if isinstance(entry, dict):
                vals = (entry["cid"], entry["value"], entry["min"], entry["max"])
            else:
                vals = tuple(entry)  # type: ignore[assignment]
            normalized[tid] = (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]))
        state.obd_test_results = normalized

    monitors = scenario_payload.get("monitors") or []
    if monitors:
        # Per SAE J1979, byte B carries continuous monitors (MIS / Fuel / CCM)
        # in bits 0-2 (supported) + 4-6 (not complete); bytes C and D carry
        # non-continuous monitors (CAT / HCAT / EVAP / AIR / A/C / O2S / HTR /
        # EGR) in the same bit indices 0-7 — byte C for supported, byte D for
        # not complete. Match monitor entries against `_MONITOR_NAME_TO_POSITION`
        # by `id` or `name`. Unknown names are silently skipped.
        byte_b, byte_c, byte_d = _encode_monitors_per_j1979(monitors)
        state.monitor_b = byte_b
        state.monitor_c = byte_c
        state.monitor_d = byte_d
    return state
