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

    def __init__(self, session_dir: str | Path) -> None:
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
        return {s["pid"] for s in self._samples}

    def read_pid(self, pid: str) -> LiveSample | None:
        # Return the most recent sample for that PID at "current playback time"
        if not self._samples:
            return None
        candidates = [s for s in self._samples if s["pid"] == pid]
        if not candidates:
            return None
        return LiveSample(**candidates[-1])

    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        wanted = set(pids)
        for sample in self._samples:
            if sample["pid"] in wanted and self._state == ConnectionState.CONNECTED:
                yield LiveSample(**sample)
                time.sleep(0.05)

    def read_vehicle_info(self) -> VehicleInfo:
        v = self._meta.get("vehicle", {})
        return VehicleInfo(**v)

    def read_dtcs(self) -> list[DTC]:
        return [DTC(**d) for d in self._dtcs_raw]

    def clear_dtcs(self) -> bool:
        # Replay is read-only by design; clearing is handled at the scenario level
        return False

    def read_freeze_frame(self) -> FreezeFrame | None:
        return FreezeFrame(**self._ff_raw) if self._ff_raw else None

    def read_monitors(self) -> list[Monitor]:
        return [Monitor(**m) for m in self._monitors_raw]

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        return None
