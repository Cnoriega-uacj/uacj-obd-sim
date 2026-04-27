"""
Session diff: compare two captured sessions and surface the deltas
that matter for diagnosis — DTC changes, monitor state changes, and
per-PID statistical comparison.

All inputs are read-only; nothing in this module writes to the database
or to the saved session files.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _pid_stats(samples: list[dict]) -> dict[str, dict]:
    by_pid: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    units: dict[str, str | None] = {}
    for s in samples:
        pid = s.get("pid")
        v = s.get("value")
        if pid is None or not isinstance(v, (int, float)):
            continue
        by_pid.setdefault(pid, []).append(float(v))
        names.setdefault(pid, s.get("name", pid))
        units.setdefault(pid, s.get("unit"))
    out = {}
    for pid, vals in by_pid.items():
        out[pid] = {
            "name": names[pid],
            "unit": units[pid],
            "n": len(vals),
            "min": min(vals),
            "max": max(vals),
            "mean": statistics.fmean(vals),
            "median": statistics.median(vals),
        }
    return out


def diff_sessions(folder_a: Path, folder_b: Path) -> dict[str, Any]:
    """
    Compute the structured diff between two saved sessions on disk.

    Returns a dict with sub-diffs for vehicle, dtcs, monitors, and
    per-PID summary statistics (no row-by-row alignment — these
    sessions usually weren't recorded at the same sample rate).
    """
    folder_a = Path(folder_a)
    folder_b = Path(folder_b)

    def _read_json(folder: Path, name: str) -> Any:
        p = folder / name
        return json.loads(p.read_text()) if p.exists() else None

    meta_a = _read_json(folder_a, "metadata.json") or {}
    meta_b = _read_json(folder_b, "metadata.json") or {}

    # Vehicle diff
    vehicle_a = (meta_a.get("vehicle") or {})
    vehicle_b = (meta_b.get("vehicle") or {})
    vehicle = {}
    for key in ("vin", "make", "model", "year", "calibration_id", "ecu_name"):
        if vehicle_a.get(key) != vehicle_b.get(key):
            vehicle[key] = {"a": vehicle_a.get(key), "b": vehicle_b.get(key)}

    # DTC diff
    dtcs_a = {(d["code"], d["status"]): d for d in (_read_json(folder_a, "dtcs.json") or [])}
    dtcs_b = {(d["code"], d["status"]): d for d in (_read_json(folder_b, "dtcs.json") or [])}
    dtcs = {
        "added":   [dtcs_b[k] for k in dtcs_b.keys() - dtcs_a.keys()],
        "removed": [dtcs_a[k] for k in dtcs_a.keys() - dtcs_b.keys()],
        "common":  [dtcs_a[k] for k in dtcs_a.keys() & dtcs_b.keys()],
    }

    # Monitor diff
    monitors_a = {m["name"]: m for m in (_read_json(folder_a, "monitors.json") or [])}
    monitors_b = {m["name"]: m for m in (_read_json(folder_b, "monitors.json") or [])}
    monitors_changed = []
    for name in monitors_a.keys() & monitors_b.keys():
        a, b = monitors_a[name], monitors_b[name]
        if a["supported"] != b["supported"] or a["ready"] != b["ready"]:
            monitors_changed.append({"name": name, "a": a, "b": b})

    # Live data: per-PID stats comparison
    live_a = _load_jsonl(folder_a / "live_data.jsonl")
    live_b = _load_jsonl(folder_b / "live_data.jsonl")
    stats_a = _pid_stats(live_a)
    stats_b = _pid_stats(live_b)
    pids_changed = []
    for pid in sorted(set(stats_a.keys()) | set(stats_b.keys())):
        sa, sb = stats_a.get(pid), stats_b.get(pid)
        if sa is None:
            pids_changed.append({"pid": pid, "name": (sb or {}).get("name", pid),
                                   "a": None, "b": sb, "status": "added"})
            continue
        if sb is None:
            pids_changed.append({"pid": pid, "name": sa["name"],
                                   "a": sa, "b": None, "status": "removed"})
            continue
        # Highlight statistically significant shifts: mean differs by >5%
        # of the larger value, or monitor flag changes — instructor decides
        # what to make of small drift.
        denom = max(abs(sa["mean"]), abs(sb["mean"]), 1e-9)
        delta_pct = abs(sa["mean"] - sb["mean"]) / denom * 100
        pids_changed.append({
            "pid": pid, "name": sa["name"],
            "a": sa, "b": sb,
            "delta_mean": sb["mean"] - sa["mean"],
            "delta_pct": delta_pct,
            "status": "shifted" if delta_pct > 5 else "stable",
        })

    return {
        "session_a": meta_a.get("session_id"),
        "session_b": meta_b.get("session_id"),
        "vehicle": vehicle,
        "dtcs": dtcs,
        "monitors_changed": monitors_changed,
        "pids": pids_changed,
    }
