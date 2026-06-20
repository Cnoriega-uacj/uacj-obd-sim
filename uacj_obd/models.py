from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Protocol(str, Enum):
    AUTO = "auto"
    ISO_15765_4_CAN_11_500 = "iso_15765_4_can_11_500"
    ISO_15765_4_CAN_29_500 = "iso_15765_4_can_29_500"
    ISO_15765_4_CAN_11_250 = "iso_15765_4_can_11_250"
    ISO_15765_4_CAN_29_250 = "iso_15765_4_can_29_250"
    ISO_14230_4_KWP_FAST = "iso_14230_4_kwp_fast"
    ISO_14230_4_KWP_5BAUD = "iso_14230_4_kwp_5baud"
    ISO_9141_2 = "iso_9141_2"
    SAE_J1850_VPW = "sae_j1850_vpw"
    SAE_J1850_PWM = "sae_j1850_pwm"
    UNKNOWN = "unknown"


class DTCStatus(str, Enum):
    STORED = "stored"
    PENDING = "pending"
    PERMANENT = "permanent"


class Monitor(BaseModel):
    name: str
    supported: bool
    ready: bool


class FreezeFrame(BaseModel):
    dtc: str | None = None
    pids: dict[str, float | int | str] = Field(default_factory=dict)


class DTC(BaseModel):
    code: str
    status: DTCStatus
    description: str = ""


class VehicleInfo(BaseModel):
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    calibration_id: str | None = None
    cvn: str | None = None
    ecu_name: str | None = None


class LiveSample(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    pid: str
    name: str
    value: float | int | str | None
    unit: str | None = None


class SessionMetadata(BaseModel):
    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    protocol: Protocol = Protocol.UNKNOWN
    adapter: str = ""
    vehicle: VehicleInfo = Field(default_factory=VehicleInfo)
    sample_count: int = 0
    notes: str = ""
    # v0.6.11: capture-side diagnostics. `discovered_pids` is what
    # `adapter.supported_pids()` returned at the start of the run
    # (the universe of PIDs the adapter said the car could answer).
    # `pid_resolution_source` records which branch we took:
    # "explicit" (caller-supplied list), "discovered" (adapter scan),
    # "fallback" (curated 14-PID safe list). Together these let an
    # instructor diagnose the "real car reports 44 PIDs but capture
    # only got 10" gap without re-running the session.
    discovered_pids: list[str] = Field(default_factory=list)
    pid_resolution_source: str = ""
    # v0.6.16: adapter-side telemetry. `raw_attempts` is how many times
    # the raw-passthrough fallback fired (because python-obd had no
    # decoder or returned null); `raw_successes` is how many of those
    # produced data the simulator can replay. The gap between them
    # indicates a bus-silent / adapter-stalled condition the bitmap
    # probe alone can't explain.
    adapter_metrics: dict[str, int] = Field(default_factory=dict)


class Scenario(BaseModel):
    """A modifiable copy of a captured session, used to feed the simulator."""

    scenario_id: str
    label: str
    source_session_id: str | None = None
    vehicle: VehicleInfo
    dtcs: list[DTC] = Field(default_factory=list)
    monitors: list[Monitor] = Field(default_factory=list)
    freeze_frame: FreezeFrame | None = None
    live_overrides: dict[str, float | int | str] = Field(default_factory=dict)
    # v0.5.0: when True, /api/scenarios/{id}/push attaches the captured
    # session's full live time-series (from live_data.jsonl) as
    # `live_timeseries` in the simulator payload, and the Pi-side
    # `ReplayEngine` mutates state.live at the recorded cadence.
    replay: bool = False
    replay_loop: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
