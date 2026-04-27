from __future__ import annotations

import json
import time
from pathlib import Path
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


class ReplayAdapter(Adapter):
    """
    Replays a previously saved session as if it were a live vehicle.

    Used by the simulator firmware to feed the on-device ECU emulator,
    and useful for instructor demos without a real car connected.
    """

    def __init__(
        self,
        session_dir: str | Path,
        scenario_overrides: dict | None = None,
    ) -> None:
        """
        scenario_overrides: optional dict matching the Scenario model
            (dtcs, monitors, freeze_frame, live_overrides, vehicle).
            When set, overrides replace or augment the saved session
            so the simulator can replay a *modified* version of it.
        """
        self._dir = Path(session_dir)
        if not self._dir.exists():
            raise AdapterError(f"session not found: {self._dir}")
        self._state = ConnectionState.DISCONNECTED
        self._t0 = 0.0
        self._meta = json.loads((self._dir / "metadata.json").read_text())
        self._dtcs_raw = json.loads((self._dir / "dtcs.json").read_text()) if (self._dir / "dtcs.json").exists() else []
        self._monitors_raw = json.loads((self._dir / "monitors.json").read_text()) if (self._dir / "monitors.json").exists() else []
        self._ff_raw = json.loads((self._dir / "freeze_frame.json").read_text()) if (self._dir / "freeze_frame.json").exists() else None
        self._samples: list[dict] = []
        live_path = self._dir / "live_data.jsonl"
        if live_path.exists():
            with live_path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._samples.append(json.loads(line))

        ov = scenario_overrides or {}
        self._override_dtcs = ov.get("dtcs")
        self._override_monitors = ov.get("monitors")
        self._override_freeze_frame = ov.get("freeze_frame")
        self._override_vehicle = ov.get("vehicle")
        self._live_overrides: dict[str, float | int | str] = ov.get("live_overrides") or {}

    def connect(self) -> AdapterStatus:
        self._state = ConnectionState.CONNECTED
        self._t0 = time.monotonic()
        return self.status()

    def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    def status(self) -> AdapterStatus:
        proto = Protocol(self._meta.get("protocol", Protocol.UNKNOWN.value))
        return AdapterStatus(
            state=self._state,
            protocol=proto,
            adapter_name=f"Replay({self._dir.name})",
        )

    def supported_pids(self) -> set[str]:
        pids = {s["pid"] for s in self._samples}
        pids.update(self._live_overrides.keys())
        return pids

    def _override_sample(self, pid: str) -> LiveSample | None:
        if pid not in self._live_overrides:
            return None
        # Use the most recent saved sample's name/unit metadata if available
        name = pid
        unit = None
        for s in reversed(self._samples):
            if s["pid"] == pid:
                name = s.get("name", pid)
                unit = s.get("unit")
                break
        return LiveSample(pid=pid, name=name, value=self._live_overrides[pid], unit=unit)

    def read_pid(self, pid: str) -> LiveSample | None:
        if pid in self._live_overrides:
            return self._override_sample(pid)
        if not self._samples:
            return None
        candidates = [s for s in self._samples if s["pid"] == pid]
        if not candidates:
            return None
        return LiveSample(**candidates[-1])

    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        wanted = set(pids)
        for sample in self._samples:
            if not (sample["pid"] in wanted and self._state == ConnectionState.CONNECTED):
                continue
            if sample["pid"] in self._live_overrides:
                ov = self._override_sample(sample["pid"])
                if ov is not None:
                    yield ov
            else:
                yield LiveSample(**sample)
            time.sleep(0.05)

    def read_vehicle_info(self) -> VehicleInfo:
        if self._override_vehicle:
            return VehicleInfo(**self._override_vehicle)
        v = self._meta.get("vehicle", {})
        return VehicleInfo(**v)

    def read_dtcs(self) -> list[DTC]:
        if self._override_dtcs is not None:
            return [DTC(**d) for d in self._override_dtcs]
        return [DTC(**d) for d in self._dtcs_raw]

    def clear_dtcs(self) -> bool:
        # Clearing applies to the in-memory override stack only — saved
        # session is preserved on disk. Modeled the same way a scan tool
        # clear works on a real ECU: codes return on next ignition cycle
        # unless the underlying fault is fixed.
        if self._override_dtcs is not None:
            self._override_dtcs = []
        else:
            self._dtcs_raw = []
        return True

    def read_freeze_frame(self) -> FreezeFrame | None:
        ff_raw = self._override_freeze_frame if self._override_freeze_frame is not None else self._ff_raw
        return FreezeFrame(**ff_raw) if ff_raw else None

    def read_monitors(self) -> list[Monitor]:
        if self._override_monitors is not None:
            return [Monitor(**m) for m in self._override_monitors]
        return [Monitor(**m) for m in self._monitors_raw]

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        return None
