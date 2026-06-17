from __future__ import annotations

import logging
import time
from typing import Iterable

from uacj_obd.models import (
    DTC,
    DTCStatus,
    FreezeFrame,
    LiveSample,
    Monitor,
    Protocol,
    VehicleInfo,
)

from .base import Adapter, AdapterError, AdapterStatus, ConnectionState

log = logging.getLogger(__name__)

# python-obd is loaded lazily so the rest of the system runs without it
# (e.g. on the Pi simulator side, or in CI).
try:
    import obd as pyobd  # type: ignore[import-not-found]
    _HAS_OBD = True
except Exception:  # pragma: no cover
    pyobd = None  # type: ignore[assignment]
    _HAS_OBD = False


_PROTOCOL_MAP = {
    "1": Protocol.SAE_J1850_PWM,
    "2": Protocol.SAE_J1850_VPW,
    "3": Protocol.ISO_9141_2,
    "4": Protocol.ISO_14230_4_KWP_5BAUD,
    "5": Protocol.ISO_14230_4_KWP_FAST,
    "6": Protocol.ISO_15765_4_CAN_11_500,
    "7": Protocol.ISO_15765_4_CAN_29_500,
    "8": Protocol.ISO_15765_4_CAN_11_250,
    "9": Protocol.ISO_15765_4_CAN_29_250,
}


class Elm327Adapter(Adapter):
    """
    Real ELM327 / STN1110 / STN2120 adapter via python-obd.

    Auto-detects protocol on the vehicle. Reconnects on transient failures.

    STN2120 (OBDLink SX) extensions: when `stn_mode=True` (or auto-detected
    via the ATI / STI banner), the adapter sends a small set of ST*
    commands after connect to enable the chip's faster init, larger RX
    buffer, and improved timing. These are silently ignored by a plain
    ELM327 clone, so the same code path works for both — but on a real
    STN2120 we get the speedup. Reference: OBDLink ST command reference,
    rev 4.20.
    """

    # STN2120 init sequence (sent post-connect, ignored by plain ELM327):
    #   STSBR 38400 — set serial baud (effective on next reset; we set
    #     the python-obd baudrate explicitly to match)
    #   STPRS — print current protocol search order
    #   STN — print firmware version (used to confirm STN chip vs clone)
    # We do NOT change non-volatile settings. Each session is stateless.
    _STN_PROBE_COMMANDS = ("STI", "STDI")  # banner / device-info
    # NOTE: post-connect runtime commands intentionally empty after on-site
    # testing on a 2012 Mazda3 with an OBDLink SX. The previous set re-sent
    # ATSP0 (re-triggers protocol auto-detection) and STCSEGR 1 (changes
    # how the chip frames multi-frame responses) AFTER python-obd had
    # already negotiated the protocol cleanly. Both rewrote the chip's
    # working state, leaving subsequent PID queries with no response.
    # python-obd's defaults handle the OBDLink SX correctly; STN-specific
    # tuning should be opt-in via constructor args, not on by default.
    _STN_RUNTIME_COMMANDS: tuple[str, ...] = ()

    def __init__(
        self,
        portstr: str | None = None,
        baudrate: int | None = None,
        fast: bool = False,
        # Real OBD-II queries against a car commonly take 200-1000 ms each.
        # python-obd's own default is 0.1 s, which silently times out almost
        # every query against a real vehicle (the call just returns None and
        # the acquisition loop sees empty data — no error, no log line, just
        # nothing). 2.0 s gives every query enough time without making
        # interactive UI feel slow.
        timeout: float = 2.0,
        stn_mode: bool | None = None,
    ) -> None:
        if not _HAS_OBD:
            raise AdapterError(
                "python-obd is not installed; install with `pip install obd` "
                "or use the mock adapter for offline development"
            )
        self._portstr = portstr
        self._baudrate = baudrate
        self._fast = fast
        self._timeout = timeout
        # None = auto-detect via STI banner; True = force STN init; False = plain ELM
        self._stn_mode = stn_mode
        self._is_stn: bool | None = None  # set by connect()
        self._stn_banner: str | None = None
        self._conn: "pyobd.OBD | None" = None  # type: ignore[name-defined]
        self._last_error: str | None = None

    # --- lifecycle -----------------------------------------------------

    def connect(self) -> AdapterStatus:
        try:
            self._conn = pyobd.OBD(
                portstr=self._portstr,
                baudrate=self._baudrate,
                fast=self._fast,
                timeout=self._timeout,
            )
            if not self._conn.is_connected():
                raise AdapterError(f"adapter did not connect: {self._conn.status()}")
        except Exception as exc:
            self._last_error = str(exc)
            raise AdapterError(f"connect failed: {exc}") from exc
        # Probe for STN-class chip and apply tuned init if so. Failures
        # here are non-fatal — we want the adapter usable as a plain
        # ELM327 if probing or tuning misbehaves.
        try:
            self._apply_stn_init_if_present()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("STN init skipped: %s", exc)
        return self.status()

    def _apply_stn_init_if_present(self) -> None:
        """Detect an STN-class chip (OBDLink SX/MX, STN1110/STN2120) and
        send its tuned post-connect command set if found.

        On a plain ELM327 clone the probe returns no STN signature and we
        leave python-obd's defaults in place. Single round-trip cost on
        the happy path: one ATI/STI exchange, ~50 ms.
        """
        is_stn = self._stn_mode
        if is_stn is None:
            banner = self._send_raw("STI") or self._send_raw("ATI")
            self._stn_banner = banner
            # STN1110/STN2120 banners include "STN" or "OBDLink"; ELM327
            # clones return "ELM327 v..." without those markers.
            is_stn = bool(banner) and ("STN" in banner.upper() or "OBDLINK" in banner.upper())
        self._is_stn = bool(is_stn)
        if not self._is_stn:
            return
        for cmd in self._STN_RUNTIME_COMMANDS:
            self._send_raw(cmd)

    def _send_raw(self, cmd: str) -> str | None:
        """Send an AT/ST command to the adapter and return the trimmed reply.
        Returns None if the underlying interface does not expose a raw
        send-and-parse hook (older python-obd builds).
        """
        if self._conn is None:
            return None
        interface = getattr(self._conn, "interface", None)
        sender = getattr(interface, "send_and_parse", None) if interface else None
        if sender is None:
            return None
        try:
            messages = sender(cmd.encode())
        except Exception:
            return None
        if not messages:
            return None
        # python-obd Message.raw() returns the AT/ST text reply
        raw = getattr(messages[0], "raw", lambda: None)()
        if raw is None:
            return None
        return raw.strip()

    @property
    def is_stn(self) -> bool | None:
        """True if the adapter has been identified as STN1110/STN2120/OBDLink."""
        return self._is_stn

    @property
    def stn_banner(self) -> str | None:
        """The raw STI/ATI banner returned at connect, for diagnostics."""
        return self._stn_banner

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def status(self) -> AdapterStatus:
        if self._conn is None or not self._conn.is_connected():
            return AdapterStatus(
                state=ConnectionState.DISCONNECTED,
                protocol=Protocol.UNKNOWN,
                adapter_name="ELM327",
                last_error=self._last_error,
            )
        proto_id = getattr(self._conn, "protocol_id", lambda: None)()
        protocol = _PROTOCOL_MAP.get(str(proto_id), Protocol.UNKNOWN)
        return AdapterStatus(
            state=ConnectionState.CONNECTED,
            protocol=protocol,
            adapter_name=f"ELM327 on {self._conn.port_name()}",
        )

    # --- data ----------------------------------------------------------

    def _ensure(self) -> "pyobd.OBD":  # type: ignore[name-defined]
        if self._conn is None or not self._conn.is_connected():
            raise AdapterError("adapter is not connected")
        return self._conn

    def supported_pids(self) -> set[str]:
        c = self._ensure()
        out: set[str] = set()
        for cmd in c.supported_commands:
            mode = getattr(cmd, "mode", None)
            pid = getattr(cmd, "pid", None)
            if mode is not None and pid is not None:
                out.add(f"{int(mode):02X}{int(pid):02X}")
        return out

    def read_pid(self, pid: str) -> LiveSample | None:
        c = self._ensure()
        # pid is a 4-hex-char "MMPP" string; resolve via python-obd's command table
        mode = int(pid[:2], 16)
        pid_num = int(pid[2:], 16)
        cmd = None
        for candidate in c.supported_commands:
            if getattr(candidate, "mode", None) == mode and getattr(candidate, "pid", None) == pid_num:
                cmd = candidate
                break
        if cmd is None:
            return None
        try:
            response = c.query(cmd, force=True)
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if response is None or response.is_null():
            return None
        value = response.value
        unit = None
        try:
            unit = str(value.units) if hasattr(value, "units") else None
            magnitude = float(value.magnitude) if hasattr(value, "magnitude") else value
        except Exception:
            magnitude = value
        return LiveSample(pid=pid, name=cmd.name, value=magnitude, unit=unit)

    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        pids = list(pids)
        while True:
            for pid in pids:
                sample = self.read_pid(pid)
                if sample is not None:
                    yield sample
            if self._conn is None or not self._conn.is_connected():
                return
            time.sleep(0.05)

    def read_vehicle_info(self) -> VehicleInfo:
        c = self._ensure()
        info = VehicleInfo()
        for attr_name, cmd_name in (
            ("vin", "VIN"),
            ("calibration_id", "CALIBRATION_ID"),
            ("ecu_name", "ECU_NAME"),
        ):
            cmd = getattr(pyobd.commands, cmd_name, None)
            if cmd is None:
                continue
            try:
                resp = c.query(cmd, force=True)
                if resp and not resp.is_null():
                    setattr(info, attr_name, str(resp.value))
            except Exception as exc:  # pragma: no cover
                log.debug("vehicle info %s failed: %s", cmd_name, exc)
        return info

    def read_dtcs(self) -> list[DTC]:
        c = self._ensure()
        out: list[DTC] = []
        for cmd_name, status in (
            ("GET_DTC", DTCStatus.STORED),
            ("GET_CURRENT_DTC", DTCStatus.PENDING),
            ("GET_PERMANENT_DTC", DTCStatus.PERMANENT),
        ):
            cmd = getattr(pyobd.commands, cmd_name, None)
            if cmd is None:
                continue
            try:
                resp = c.query(cmd, force=True)
            except Exception as exc:  # pragma: no cover
                log.debug("dtc query %s failed: %s", cmd_name, exc)
                continue
            if not resp or resp.is_null():
                continue
            for entry in resp.value or []:
                code, desc = (entry[0], entry[1]) if isinstance(entry, (list, tuple)) else (str(entry), "")
                out.append(DTC(code=code, status=status, description=desc or ""))
        return out

    def clear_dtcs(self) -> bool:
        c = self._ensure()
        cmd = getattr(pyobd.commands, "CLEAR_DTC", None)
        if cmd is None:
            return False
        try:
            c.query(cmd, force=True)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def read_freeze_frame(self) -> FreezeFrame | None:
        c = self._ensure()
        ff = FreezeFrame()
        cmd = getattr(pyobd.commands, "FREEZE_DTC", None)
        if cmd is not None:
            try:
                resp = c.query(cmd, force=True)
                if resp and not resp.is_null():
                    ff.dtc = str(resp.value)
            except Exception as exc:  # pragma: no cover
                log.debug("freeze dtc query failed: %s", exc)
        return ff if ff.dtc else None

    def read_monitors(self) -> list[Monitor]:
        c = self._ensure()
        cmd = getattr(pyobd.commands, "STATUS", None)
        out: list[Monitor] = []
        if cmd is None:
            return out
        try:
            resp = c.query(cmd, force=True)
        except Exception as exc:  # pragma: no cover
            log.debug("monitor status query failed: %s", exc)
            return out
        if not resp or resp.is_null():
            return out
        status = resp.value
        for name in (
            "MISFIRE_MONITORING", "FUEL_SYSTEM_MONITORING", "COMPONENT_MONITORING",
            "CATALYST_MONITORING", "HEATED_CATALYST_MONITORING",
            "EVAPORATIVE_SYSTEM_MONITORING", "SECONDARY_AIR_SYSTEM_MONITORING",
            "AC_SYSTEM_REFRIGERANT_MONITORING", "OXYGEN_SENSOR_MONITORING",
            "OXYGEN_SENSOR_HEATER_MONITORING", "EGR_VVT_SYSTEM_MONITORING",
        ):
            test = getattr(status, name, None)
            if test is None:
                continue
            out.append(Monitor(
                name=name.replace("_MONITORING", "").replace("_", " ").title(),
                supported=bool(getattr(test, "available", False)),
                ready=not bool(getattr(test, "incomplete", True)),
            ))
        return out

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        c = self._ensure()
        # Compose raw OBD message and send through ELM327 directly.
        if pid is None:
            cmd_str = f"{mode:02X}"
        else:
            cmd_str = f"{mode:02X}{pid:02X}"
        try:
            messages = c.interface.send_and_parse(cmd_str.encode())
            if not messages:
                return None
            data = messages[0].data
            return bytes(data) if data else None
        except Exception as exc:
            self._last_error = str(exc)
            return None
