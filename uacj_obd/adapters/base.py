from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from uacj_obd.models import DTC, FreezeFrame, LiveSample, Monitor, Protocol, VehicleInfo


class AdapterError(Exception):
    pass


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LOST = "lost"


@dataclass
class AdapterStatus:
    state: ConnectionState
    protocol: Protocol
    adapter_name: str
    last_error: str | None = None


class Adapter(ABC):
    """
    Abstract base for any OBD-II adapter (ELM327, STN2120, mock, file-replay).

    The HAL is intentionally narrow so that swapping in a custom hardware
    board later does not require touching acquisition or storage code.
    """

    @abstractmethod
    def connect(self) -> AdapterStatus: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def status(self) -> AdapterStatus: ...

    @abstractmethod
    def supported_pids(self) -> set[str]:
        """Standard mode 0x01 PIDs reported as supported by the ECU."""

    @abstractmethod
    def read_pid(self, pid: str) -> LiveSample | None: ...

    @abstractmethod
    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        """Yield samples in a loop. Stops on disconnect or external interrupt."""

    @abstractmethod
    def read_vehicle_info(self) -> VehicleInfo: ...

    @abstractmethod
    def read_dtcs(self) -> list[DTC]: ...

    @abstractmethod
    def clear_dtcs(self) -> bool: ...

    @abstractmethod
    def read_freeze_frame(self) -> FreezeFrame | None: ...

    @abstractmethod
    def read_monitors(self) -> list[Monitor]: ...

    def read_metrics(self) -> dict[str, int]:
        """
        v0.6.16: optional capture-side counters surfaced for diagnostics.
        Subclasses may override to report adapter-specific telemetry like
        raw-passthrough attempts and successes. Default = no telemetry.
        """
        return {}

    @abstractmethod
    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        """Send a raw OBD service request; for mode 0x22 manufacturer PIDs."""
