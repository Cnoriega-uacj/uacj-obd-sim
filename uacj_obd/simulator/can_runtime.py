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
        # encode supported & ready bits (mode 01 PID 01 byte B)
        mb = 0
        mc = 0
        # Test bits per SAE J1979 (subset). Bits set means "incomplete" / "test failed".
        for i, m in enumerate(monitors[:8]):
            if not m.get("supported", False):
                mb |= (1 << i)
            if not m.get("ready", False):
                mc |= (1 << i)
        state.monitor_b = mb
        state.monitor_c = mc
    return state
