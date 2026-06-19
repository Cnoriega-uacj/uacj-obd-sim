"""
v0.6.6 — Operational safeguards.

After v0.6.5 closed the static coverage gaps, the remaining risk
surface was operational — things that the code is correct about but
that real-world usage can still get wrong:

1. Cristopher forgets to deploy to BOTH laptop and Pi → version mismatch
   causes confusing partial-feature behaviour.

2. Disk fills up from accumulated captures → silent truncation / I/O errors
   when the dashboard tries to write the next sample.

3. A session runs unbounded because Cristopher walked away → the JSONL
   keeps growing, and only an explicit Stop call terminates it.

This module exposes pure helpers that the API + CLI use to enforce
these guardrails. All functions are side-effect-free and individually
testable.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


# --- session duration cap ---------------------------------------------------

# Hard cap on `duration_s` accepted by /api/sessions/start. Default is one
# hour — enough for any realistic single-session classroom capture, well
# below what would risk exhausting disk space or growing into hours of
# replay timeline. The cap is enforced server-side so the dashboard's UI
# can't override it; users who genuinely need longer sessions can pass
# `--duration` to the CLI or do multiple back-to-back captures.
MAX_SESSION_DURATION_S = 3600.0


def normalize_session_duration(duration_s: float | None) -> float | None:
    """Coerce a user-supplied session duration into a safe range.

    - ``None`` → ``None`` (the acquisition session interprets this as
      "indefinite, stop on Ctrl+C / button click"). This is fine because
      the API layer also enforces a cap when the caller is the dashboard.
    - Negative or zero values → ``None`` (treat as "indefinite").
    - Values above ``MAX_SESSION_DURATION_S`` → clamped down.
    """
    if duration_s is None:
        return None
    if duration_s <= 0:
        return None
    if duration_s > MAX_SESSION_DURATION_S:
        return MAX_SESSION_DURATION_S
    return float(duration_s)


# --- disk-space pre-flight check --------------------------------------------

# We refuse to start a new capture if the data root has less than this much
# free disk space. Tuned so a full hour of 100-PID-per-cycle capture (worst
# case ~150 MB of JSONL) fits with margin.
MIN_FREE_DISK_BYTES = 200 * 1024 * 1024  # 200 MB

# We warn (but don't refuse) when free space is between MIN_FREE_DISK_BYTES
# and this number. The warning shows up in the dashboard so Cristopher
# notices before the kit silently truncates.
LOW_FREE_DISK_WARN_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


@dataclass(frozen=True)
class DiskStatus:
    """Result of a pre-flight disk check."""

    total_bytes: int
    free_bytes: int
    ok: bool          # False → refuse to start the capture
    warn: bool        # True → start, but tell the user to clean up
    message: str      # human-readable explanation


def check_disk_space(data_root: Path | str) -> DiskStatus:
    """Run a pre-flight disk space check for the given data root.

    Returns a DiskStatus the caller can either surface to the dashboard
    or use to refuse the operation. Uses ``shutil.disk_usage`` so it
    works on Pi (Linux) and Windows alike.
    """
    root = Path(data_root)
    # Walk up to the first existing parent if the path doesn't exist yet —
    # `disk_usage` requires an existing path.
    target = root
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except Exception as exc:  # pragma: no cover - extremely unusual
        return DiskStatus(
            total_bytes=0, free_bytes=0, ok=True, warn=True,
            message=f"could not query disk usage: {exc}",
        )
    total = usage.total
    free = usage.free
    if free < MIN_FREE_DISK_BYTES:
        return DiskStatus(
            total_bytes=total, free_bytes=free, ok=False, warn=True,
            message=(
                f"refusing to start capture: only {free // (1024*1024)} MB free on "
                f"{target} (need at least {MIN_FREE_DISK_BYTES // (1024*1024)} MB). "
                f"Delete old sessions or back them up off the device first."
            ),
        )
    if free < LOW_FREE_DISK_WARN_BYTES:
        return DiskStatus(
            total_bytes=total, free_bytes=free, ok=True, warn=True,
            message=(
                f"low disk space: {free // (1024*1024)} MB free on {target}. "
                f"Captures will run but consider cleaning up sessions soon."
            ),
        )
    return DiskStatus(
        total_bytes=total, free_bytes=free, ok=True, warn=False,
        message=f"{free // (1024*1024)} MB free of {total // (1024*1024)} MB",
    )


# --- version compatibility check --------------------------------------------

def compare_versions(laptop: str, pi: str) -> str:
    """Return a short human-readable verdict comparing the laptop's
    package version against the Pi simulator's reported version.

    Returns one of:
    - "match"   — laptop and Pi are on the same version
    - "mismatch — Pi is older. Update with: cd /opt/uacj-obd-sim && sudo git pull"
    - "mismatch — Pi is newer than laptop. Update the laptop with: git pull"
    - "unknown" — one side didn't report a version

    Comparison is permissive: we treat the version as a dotted tuple of
    integers, stopping at the first non-integer segment. So "0.6.5"
    compares correctly against "0.6.6", and "0.7.0-dev" parses as
    (0, 7, 0) (stopping at "-dev").
    """
    if not laptop or not pi:
        return "unknown"
    if laptop == pi:
        return "match"

    def _parts(v: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in v.split("."):
            digits = ""
            for c in chunk:
                if c.isdigit():
                    digits += c
                else:
                    break
            if not digits:
                break
            out.append(int(digits))
        return tuple(out)

    lp, pp = _parts(laptop), _parts(pi)
    if lp == pp:
        return "match"
    if pp < lp:
        return (
            "mismatch — Pi is older. Update with: "
            "cd /opt/uacj-obd-sim && sudo git pull && "
            "sudo systemctl restart uacj-obd-sim"
        )
    return (
        "mismatch — Pi is newer than laptop. Update the laptop with: "
        "cd C:\\uacj && git pull"
    )
