from __future__ import annotations

import math
import random
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


_DEFAULT_PIDS = {
    "010C": ("RPM", "rpm"),
    "010D": ("SPEED", "km/h"),
    "0105": ("COOLANT_TEMP", "°C"),
    "010F": ("INTAKE_TEMP", "°C"),
    "0110": ("MAF", "g/s"),
    "0111": ("THROTTLE_POS", "%"),
    "0104": ("ENGINE_LOAD", "%"),
    "0106": ("SHORT_FUEL_TRIM_1", "%"),
    "0107": ("LONG_FUEL_TRIM_1", "%"),
    "010B": ("INTAKE_PRESSURE", "kPa"),
    "0114": ("O2_B1S1", "V"),
    "012F": ("FUEL_LEVEL", "%"),
}


class MockAdapter(Adapter):
    """
    Drop-in adapter that simulates a 2015 Honda Civic.

    Used for development off-vehicle and as the default in tests.
    Generates plausible time-varying values so the dashboard and
    storage paths can be exercised without real hardware.
    """

    def __init__(
        self,
        vin: str = "2HGFC2F59FH123456",
        make: str = "Honda",
        model: str = "Civic",
        year: int = 2015,
        protocol: Protocol = Protocol.ISO_15765_4_CAN_11_500,
        seed: int = 42,
        dtcs: list[DTC] | None = None,
    ) -> None:
        self._vin = vin
        self._make = make
        self._model = model
        self._year = year
        self._protocol = protocol
        self._rng = random.Random(seed)
        self._state = ConnectionState.DISCONNECTED
        self._t0 = time.monotonic()
        self._dtcs = dtcs if dtcs is not None else [
            DTC(code="P0420", status=DTCStatus.STORED, description="Catalyst System Efficiency Below Threshold (Bank 1)"),
            DTC(code="P0171", status=DTCStatus.PENDING, description="System Too Lean (Bank 1)"),
        ]

    def connect(self) -> AdapterStatus:
        self._state = ConnectionState.CONNECTED
        self._t0 = time.monotonic()
        return self.status()

    def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            state=self._state,
            protocol=self._protocol,
            adapter_name="MockAdapter (2015 Honda Civic)",
        )

    def supported_pids(self) -> set[str]:
        return set(_DEFAULT_PIDS.keys())

    def _ensure(self) -> None:
        if self._state != ConnectionState.CONNECTED:
            raise AdapterError("adapter is not connected")

    def _value_for(self, pid: str) -> float:
        self._ensure()
        t = time.monotonic() - self._t0
        # plausible idle-to-cruise oscillations
        if pid == "010C":  # RPM
            return 800 + 600 * (1 + math.sin(t / 5)) + self._rng.uniform(-50, 50)
        if pid == "010D":  # speed
            return max(0.0, 40 + 25 * math.sin(t / 7) + self._rng.uniform(-2, 2))
        if pid == "0105":  # coolant
            return 88 + 3 * math.sin(t / 30)
        if pid == "010F":  # intake air temp
            return 28 + 2 * math.sin(t / 15)
        if pid == "0110":  # MAF
            return 4 + 3 * (1 + math.sin(t / 4))
        if pid == "0111":  # throttle
            return max(0.0, 12 + 8 * math.sin(t / 6) + self._rng.uniform(-1, 1))
        if pid == "0104":  # load
            return 25 + 15 * (1 + math.sin(t / 8))
        if pid in ("0106", "0107"):
            return self._rng.uniform(-3, 3)
        if pid == "010B":
            return 32 + 5 * math.sin(t / 9)
        if pid == "0114":
            return 0.45 + 0.3 * math.sin(t / 1.5)
        if pid == "012F":
            return 64.0
        return 0.0

    def read_pid(self, pid: str) -> LiveSample | None:
        if pid not in _DEFAULT_PIDS:
            return None
        name, unit = _DEFAULT_PIDS[pid]
        return LiveSample(pid=pid, name=name, value=round(self._value_for(pid), 3), unit=unit)

    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        self._ensure()
        pids = list(pids)
        while self._state == ConnectionState.CONNECTED:
            for pid in pids:
                sample = self.read_pid(pid)
                if sample is not None:
                    yield sample
            time.sleep(0.1)

    def read_vehicle_info(self) -> VehicleInfo:
        self._ensure()
        return VehicleInfo(
            vin=self._vin,
            make=self._make,
            model=self._model,
            year=self._year,
            calibration_id="HND-CIV-2015-A1",
            cvn="CDA08E85",
            ecu_name="ECM",
        )

    def read_dtcs(self) -> list[DTC]:
        self._ensure()
        return list(self._dtcs)

    def clear_dtcs(self) -> bool:
        self._ensure()
        self._dtcs = []
        return True

    def read_freeze_frame(self) -> FreezeFrame | None:
        self._ensure()
        if not self._dtcs:
            return None
        return FreezeFrame(
            dtc=self._dtcs[0].code,
            pids={
                "RPM": 1850,
                "SPEED": 64,
                "COOLANT_TEMP": 91,
                "THROTTLE_POS": 18,
                "ENGINE_LOAD": 42,
            },
        )

    def read_monitors(self) -> list[Monitor]:
        self._ensure()
        names = [
            "Misfire", "Fuel System", "Components",
            "Catalyst", "Heated Catalyst", "Evaporative System",
            "Secondary Air System", "A/C Refrigerant", "Oxygen Sensor",
            "Oxygen Sensor Heater", "EGR System",
        ]
        return [
            Monitor(name=n, supported=True, ready=(n != "Catalyst"))
            for n in names
        ]

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        self._ensure()
        # Mode 0x22 manufacturer PIDs not implemented in mock; return None so
        # higher layers exercise their "unsupported" handling.
        if mode == 0x22:
            return None
        return None
