"""
v0.6.7 — Pi-side scenario persistence.

When the laptop pushes a scenario to the Pi via `/api/sim/load`, the Pi
writes the payload atomically to a JSON file. On simulator startup
(systemd unit restart, power blip, manual reboot mid-class), the loader
reads it back and re-applies it, so students see the same synthetic ECU
they were scanning before the reboot.

Pure I/O helpers — no FastAPI dependency, no global state. The simulator
server module is responsible for calling save/load/clear at the right
points in the request lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_PERSISTENCE_PATH = Path.home() / ".uacj-sim-last-scenario.json"
"""
Default persistence path. Lives in the home directory of whichever user
runs `uacj-obd simulator` — typically `pi` on the Raspberry Pi, which
makes it survive package upgrades that touch `/opt/uacj-obd-sim/`.
"""

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
"""
Sanity cap. A real scenario with a 10-minute, 100-PID @ 10 Hz timeline
is well under 5 MB even uncompressed. Above this we assume corruption
or someone trying to push something other than a scenario and refuse.
"""


def save_last_scenario(payload: dict, path: Path | str = DEFAULT_PERSISTENCE_PATH) -> bool:
    """
    Atomically write `payload` to `path` so a later `load_last_scenario`
    can recover it. Returns True on success, False otherwise — never
    raises into the caller's HTTP handler.

    Atomicity matters: the Pi can lose power mid-write. We write to a
    sibling tempfile, fsync, then rename. The rename is atomic on the
    same filesystem, so a reader will see either the old content or the
    new content but never a half-written truncation.
    """
    if not isinstance(payload, dict):
        log.warning("scenario persistence: refusing to save non-dict payload")
        return False

    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("scenario persistence: cannot create parent dir %s: %s", target.parent, exc)
        return False

    try:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        log.warning("scenario persistence: payload is not JSON-serializable: %s", exc)
        return False

    if len(encoded) > MAX_PAYLOAD_BYTES:
        log.warning(
            "scenario persistence: payload too large (%d bytes > %d cap)",
            len(encoded), MAX_PAYLOAD_BYTES,
        )
        return False

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".",
            suffix=".tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(encoded)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        log.warning("scenario persistence: write failed: %s", exc)
        return False

    log.info("scenario persistence: saved to %s (%d bytes)", target, len(encoded))
    return True


def load_last_scenario(path: Path | str = DEFAULT_PERSISTENCE_PATH) -> dict | None:
    """
    Read back the most recently saved scenario payload. Returns None
    if no payload is on disk, the file is unreadable, the JSON is
    corrupt, or the decoded value isn't a dict.

    Corruption recovery: a corrupt persistence file gets quarantined
    (renamed to `<name>.corrupt`) so the next save can succeed without
    overwriting forensic evidence.
    """
    target = Path(path)
    if not target.exists():
        return None

    try:
        raw = target.read_bytes()
    except OSError as exc:
        log.warning("scenario persistence: cannot read %s: %s", target, exc)
        return None

    if len(raw) > MAX_PAYLOAD_BYTES:
        log.warning(
            "scenario persistence: stored payload too large (%d bytes); quarantining",
            len(raw),
        )
        _quarantine(target)
        return None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("scenario persistence: corrupt JSON in %s: %s; quarantining", target, exc)
        _quarantine(target)
        return None

    if not isinstance(payload, dict):
        log.warning("scenario persistence: stored payload is not a dict; quarantining")
        _quarantine(target)
        return None

    log.info("scenario persistence: restored from %s", target)
    return payload


def clear_last_scenario(path: Path | str = DEFAULT_PERSISTENCE_PATH) -> bool:
    """
    Remove the persisted scenario. Returns True if a file was removed
    OR no file existed (idempotent), False if removal failed for a
    reason other than absence.
    """
    target = Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        log.warning("scenario persistence: cannot remove %s: %s", target, exc)
        return False
    log.info("scenario persistence: cleared %s", target)
    return True


def persistence_status(path: Path | str = DEFAULT_PERSISTENCE_PATH) -> dict:
    """
    Lightweight status for `/api/sim/persistence` — does a saved
    scenario exist, how big, when was it written, and what VIN does
    it carry. The VIN read is best-effort; corrupt files report
    `exists=True, vin=None`.
    """
    target = Path(path)
    if not target.exists():
        return {"exists": False, "path": str(target)}

    info: dict = {"exists": True, "path": str(target)}
    try:
        st = target.stat()
        info["size_bytes"] = st.st_size
        info["mtime"] = st.st_mtime
    except OSError:
        pass

    payload = load_last_scenario(target)
    if isinstance(payload, dict):
        vehicle = payload.get("vehicle")
        if isinstance(vehicle, dict):
            info["vin"] = vehicle.get("vin")
        else:
            info["vin"] = payload.get("vin")
    return info


def _quarantine(target: Path) -> None:
    """Rename a corrupt file to `<name>.corrupt` for later inspection."""
    quarantine = target.with_suffix(target.suffix + ".corrupt")
    try:
        os.replace(target, quarantine)
    except OSError as exc:
        log.warning("scenario persistence: could not quarantine %s: %s", target, exc)
