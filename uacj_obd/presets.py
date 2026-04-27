"""
Built-in scenario presets — common automotive training cases that
instructors can instantiate with one click.

Each preset is a self-contained dict matching the Scenario payload
shape. Instantiating a preset clones it onto a saved session (so the
preset's overrides ride on top of the captured car's live data) and
saves it as a new editable scenario.

Adding more presets is purely a data exercise — append to PRESETS.
"""

from __future__ import annotations

from typing import Any


PRESETS: dict[str, dict[str, Any]] = {
    "p0420_catalyst": {
        "label": "P0420 — Catalyst Inefficiency (Bank 1)",
        "description": (
            "Stored P0420. Live data normal. Teaches the bank-1 catalyst "
            "code: O2 sensor comparison, freeze-frame interpretation, "
            "drive-cycle re-test."
        ),
        "dtcs": [
            {"code": "P0420", "status": "stored",
              "description": "Catalyst System Efficiency Below Threshold (Bank 1)"},
        ],
        "freeze_frame": {
            "dtc": "P0420",
            "pids": {"010C": 1850, "010D": 64, "0105": 91, "0111": 18, "0104": 42},
        },
        "live_overrides": {},
    },
    "p0171_lean": {
        "label": "P0171 — System Too Lean (Bank 1)",
        "description": (
            "Stored P0171 with elevated LTFT (+18%). Teaches fuel trim "
            "diagnosis: vacuum-leak path, MAF contamination, fuel pressure."
        ),
        "dtcs": [
            {"code": "P0171", "status": "stored",
              "description": "System Too Lean (Bank 1)"},
        ],
        "freeze_frame": {
            "dtc": "P0171",
            "pids": {"010C": 800, "010D": 0, "0107": 18, "0106": 12},
        },
        "live_overrides": {"0107": 18, "0106": 12, "0114": 0.15},
    },
    "p0301_misfire": {
        "label": "P0301 + P0300 — Cylinder 1 Misfire",
        "description": (
            "Stored P0301 + pending P0300, rough idle. Teaches multi-code "
            "interpretation, ignition vs fuel diagnosis, monitor effects."
        ),
        "dtcs": [
            {"code": "P0301", "status": "stored",
              "description": "Cylinder 1 Misfire Detected"},
            {"code": "P0300", "status": "pending",
              "description": "Random/Multiple Cylinder Misfire"},
        ],
        "freeze_frame": {
            "dtc": "P0301",
            "pids": {"010C": 850, "010D": 0, "0111": 8, "0104": 32},
        },
        "live_overrides": {"010C": 920, "010D": 0, "0111": 8},
    },
    "p0455_evap": {
        "label": "P0455 — EVAP Large Leak",
        "description": (
            "Stored P0455 with EVAP monitor incomplete. Teaches EVAP "
            "smoke testing, gas cap inspection, monitor drive-cycle."
        ),
        "dtcs": [
            {"code": "P0455", "status": "stored",
              "description": "Evaporative Emission System Leak Detected (Large)"},
        ],
        "freeze_frame": {
            "dtc": "P0455",
            "pids": {"010C": 1500, "010D": 50, "0105": 88},
        },
        "live_overrides": {},
        "monitors_override": {"Evaporative System": {"supported": True, "ready": False}},
    },
    "monitors_incomplete": {
        "label": "Drive-Cycle Incomplete (no DTCs)",
        "description": (
            "No DTCs but multiple readiness monitors not ready. Teaches "
            "why a car can fail emissions without any codes set, and the "
            "drive-cycle conditions needed to complete each monitor."
        ),
        "dtcs": [],
        "freeze_frame": None,
        "live_overrides": {},
        "monitors_override": {
            "Catalyst": {"supported": True, "ready": False},
            "Evaporative System": {"supported": True, "ready": False},
            "Oxygen Sensor": {"supported": True, "ready": False},
        },
    },
    "u0100_lost_comm": {
        "label": "U0100 — Lost Communication with ECM",
        "description": (
            "Stored U0100 from BCM perspective. Teaches CAN-bus diagnosis: "
            "checking bus voltages, identifying which module reports the "
            "loss, reading bus traffic."
        ),
        "dtcs": [
            {"code": "U0100", "status": "stored",
              "description": "Lost Communication With ECM/PCM"},
        ],
        "freeze_frame": None,
        "live_overrides": {},
    },
}


def list_presets() -> list[dict]:
    return [
        {"id": pid, "label": p["label"], "description": p["description"]}
        for pid, p in PRESETS.items()
    ]


def get_preset(preset_id: str) -> dict | None:
    return PRESETS.get(preset_id)


def apply_monitors_override(saved_monitors: list[dict], override: dict[str, dict]) -> list[dict]:
    """
    Take the saved session's monitor list and apply per-monitor overrides
    by name. Returns a new list, leaves the input unchanged.
    """
    out = []
    for m in saved_monitors:
        edit = override.get(m.get("name", ""))
        if edit:
            new = {**m, **edit}
        else:
            new = dict(m)
        out.append(new)
    return out
