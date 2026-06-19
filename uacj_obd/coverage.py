"""
v0.6.10 — Scenario coverage reporting.

Cristopher's bench: pushed a Mazda3 capture, scan tool listed only 10
mode-01 live PIDs. Two possible causes — paging on the Innova (1/10
might mean position-1-of-10), or a genuine bitmap gap. He has no way
to tell the difference from the UI.

This module computes "what will the simulator actually answer for a
given scenario payload": intersects the captured live_baseline keys
with the simulator's encoder registry and the dashboard's PID
registry (for human-readable names), and reports the mode breakdown.
The dashboard surfaces this BEFORE pushing so Cristopher can confirm
"yes, the Pi will respond to 38 of the 47 captured mode-01 PIDs"
instead of guessing from a flat sample count.

Pure helpers — no FastAPI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pids.registry import PidRegistry
from .simulator.encoders import _try_raw_passthrough, encodable_pids


@dataclass(frozen=True)
class PidEntry:
    key: str
    name: str
    unit: str
    answerable: bool
    via_raw: bool = False  # v0.6.14: answerable via raw passthrough?


@dataclass
class CoverageReport:
    total_pids: int = 0
    mode01_total: int = 0
    mode01_answerable: int = 0
    mode01_via_raw: int = 0  # v0.6.14: of answerable, how many via raw passthrough
    mode09_present: list[str] = field(default_factory=list)
    mode22_total: int = 0
    entries: list[PidEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def mode01_unanswered(self) -> int:
        return self.mode01_total - self.mode01_answerable

    @property
    def mode01_via_formula(self) -> int:
        return self.mode01_answerable - self.mode01_via_raw


_MODE09_VEHICLE_FIELDS: dict[int, str] = {
    0x02: "vin",
    0x04: "calibration_id",
    0x06: "cvn",
    0x0A: "ecu_name",
}


def compute_coverage(payload: dict, pid_reg: PidRegistry) -> CoverageReport:
    """
    Walk the scenario payload and report what the simulator will
    actually answer for it.

    `payload` is the wire shape that gets POSTed to `/api/sim/load`:
    `vehicle`, `live_baseline`, `live_overrides`, `live_timeseries`,
    `dtcs`, etc. The helper merges `live_baseline` + `live_overrides`
    + any pids referenced in the timeseries the same way
    `scenario_to_state` does at push time.
    """
    report = CoverageReport()
    keys = _merged_pid_keys(payload)
    report.total_pids = len(keys)
    encodable = encodable_pids()
    by_mode01 = sorted(k for k in keys if k.startswith("01"))
    by_mode22 = sorted(k for k in keys if k.startswith("22"))
    report.mode01_total = len(by_mode01)
    report.mode22_total = len(by_mode22)

    baseline = (payload.get("live_baseline") or {})
    overrides = (payload.get("live_overrides") or {})
    merged_values: dict[str, object] = {}
    for src in (baseline, overrides):
        for k, v in src.items():
            if isinstance(k, str):
                merged_values[k.upper()] = v
    for key in by_mode01:
        defn = pid_reg.get(key)
        name = defn.name if defn else key
        unit = defn.unit if defn else ""
        has_encoder = key in encodable
        via_raw = _try_raw_passthrough(merged_values.get(key)) is not None
        answerable = has_encoder or via_raw
        report.entries.append(PidEntry(
            key=key, name=name, unit=unit,
            answerable=answerable, via_raw=via_raw,
        ))
    report.mode01_answerable = sum(1 for e in report.entries if e.answerable)
    report.mode01_via_raw = sum(1 for e in report.entries if e.via_raw)

    vehicle = payload.get("vehicle") or {}
    for pid_byte, attr in _MODE09_VEHICLE_FIELDS.items():
        if vehicle.get(attr):
            report.mode09_present.append(f"09{pid_byte:02X}")

    if report.mode01_total == 0:
        report.notes.append(
            "no mode-01 PIDs in baseline — the scan tool will see "
            "an empty 'PIDs supported' bitmap"
        )
    elif report.mode01_unanswered > 0:
        report.notes.append(
            f"{report.mode01_unanswered} captured mode-01 PIDs have no "
            f"encoder in this simulator — the scan tool will not see them"
        )
    if "vin" not in (k for k in vehicle if vehicle.get(k)):
        report.notes.append(
            "no VIN on the scenario — Mode 09 PID 02 will NRC"
        )
    if not vehicle.get("calibration_id"):
        report.notes.append(
            "no calibration_id — Mode 09 PID 04 will NRC (Cal ID empty on scan tool)"
        )
    if not vehicle.get("cvn"):
        report.notes.append(
            "no CVN — Mode 09 PID 06 will NRC (CVN empty on scan tool)"
        )
    return report


def _merged_pid_keys(payload: dict) -> set[str]:
    """
    Reproduce the PID-key set that `scenario_to_state` will end up with
    in `state.live`. Keys are uppercased to match the registry's key
    format.
    """
    keys: set[str] = set()
    for k in (payload.get("live_baseline") or {}):
        if isinstance(k, str):
            keys.add(k.upper())
    for k in (payload.get("live_overrides") or {}):
        if isinstance(k, str):
            keys.add(k.upper())
    for entry in (payload.get("live_timeseries") or []):
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid") or entry.get("PID")
        if isinstance(pid, str):
            keys.add(pid.upper())
    return keys
