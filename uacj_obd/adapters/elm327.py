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
    """

    def __init__(
        self,
        portstr: str | None = None,
        baudrate: int | None = None,
        fast: bool = False,
        timeout: float = 0.1,
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
        return self.status()

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
