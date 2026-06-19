"""
Pi-side HTTP server: receives scenario push from the laptop and updates
the live ECU emulator state.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from .ecu import EcuEmulator
from .can_runtime import scenario_to_state
from .replay_engine import ReplayEngine
from .scenario_persistence import (
    DEFAULT_PERSISTENCE_PATH,
    clear_last_scenario,
    load_last_scenario,
    persistence_status,
    save_last_scenario,
)
from .. import __version__ as PKG_VERSION

log = logging.getLogger(__name__)


def _apply_scenario(
    payload: dict,
    ecu: EcuEmulator,
    replay: dict[str, ReplayEngine | None],
) -> tuple[int, str | None]:
    """
    Shared scenario-load logic used by both the `/api/sim/load` HTTP
    handler and the boot-time auto-restore. Returns
    `(samples_loaded, vin)`. Raises on malformed payloads.
    """
    new_state = scenario_to_state(payload)
    old_engine = replay["engine"]
    if old_engine is not None:
        old_engine.stop()
        replay["engine"] = None
    ecu.load(new_state)
    timeline = list(new_state.live_timeseries)
    if timeline:
        engine = ReplayEngine(
            state=new_state,
            samples=timeline,
            loop=new_state.live_timeseries_loop,
        )
        engine.start()
        replay["engine"] = engine
    return len(timeline), new_state.vin


def make_simulator_server(
    ecu: EcuEmulator,
    persistence_path: Path | str | None = DEFAULT_PERSISTENCE_PATH,
    auto_restore: bool = False,
) -> FastAPI:
    """
    Build the Pi-side FastAPI app.

    `persistence_path` controls where the last-loaded scenario payload
    is mirrored to disk. Pass `None` to disable persistence entirely
    (useful in tests so the dev machine's home dir stays clean).

    `auto_restore`, when True, looks for a persisted scenario on disk
    and re-applies it (including the replay engine) before the server
    starts handling requests. Used by the `uacj-obd simulator` CLI on
    boot so a Pi reboot mid-class is invisible to students.
    """
    app = FastAPI(
        title="UACJ Simulator Board",
        version="0.1.0",
        description="Pi-side scenario receiver for the UACJ OBD-II simulator.",
    )

    replay: dict[str, ReplayEngine | None] = {"engine": None}
    persist_path: Path | None = Path(persistence_path) if persistence_path is not None else None

    if auto_restore and persist_path is not None:
        restored = load_last_scenario(persist_path)
        if restored is not None:
            try:
                samples, vin = _apply_scenario(restored, ecu, replay)
                log.info(
                    "auto-restore: scenario re-applied VIN=%s replay=%d samples",
                    vin, samples,
                )
            except Exception as exc:
                log.warning("auto-restore failed: %s", exc)

    @app.get("/api/sim/health")
    def health() -> dict:
        return {
            "ok": True,
            "version": PKG_VERSION,
            "vin": ecu.state.vin,
            "stored_dtcs": ecu.state.dtcs_stored,
        }

    @app.get("/api/sim/state")
    def state() -> dict:
        s = ecu.state
        eng = replay["engine"]
        return {
            "vin": s.vin,
            "calibration_id": s.calibration_id,
            "ecu_name": s.ecu_name,
            "stored_dtcs": s.dtcs_stored,
            "pending_dtcs": s.dtcs_pending,
            "permanent_dtcs": s.dtcs_permanent,
            "live_pids": list(s.live.keys()),
            "monitor_status": {
                "A": s.monitor_status, "B": s.monitor_b,
                "C": s.monitor_c, "D": s.monitor_d,
            },
            "replay": {
                "running": eng.is_running if eng else False,
                "duration_seconds": eng.duration_seconds if eng else 0.0,
                "samples_applied": eng.samples_applied if eng else 0,
                "iterations": eng.iterations if eng else 0,
                "loop": eng.loop if eng else None,
            },
        }

    @app.post("/api/sim/load")
    def load(payload: dict) -> dict:
        """
        Receive a scenario payload (the same shape as the API's Scenario.payload)
        and atomically swap the ECU state. If the payload includes a
        `live_timeseries` field, a `ReplayEngine` is started that mutates
        `state.live` according to the captured cadence.

        v0.6.7: the payload is mirrored to disk so a Pi reboot mid-class
        does not lose the loaded scenario.
        """
        samples, vin = _apply_scenario(payload, ecu, replay)
        log.info(
            "scenario loaded: VIN=%s DTCs=%d replay=%d samples",
            vin, len(ecu.state.dtcs_stored), samples,
        )
        persisted = False
        if persist_path is not None:
            persisted = save_last_scenario(payload, persist_path)
        return {
            "loaded": True,
            "vin": vin,
            "replay_samples": samples,
            "persisted": persisted,
        }

    @app.post("/api/sim/clear")
    def clear() -> dict:
        ecu.state.clear_dtcs()
        return {"cleared": True}

    @app.post("/api/sim/replay/stop")
    def stop_replay() -> dict:
        """Stop dynamic replay if running; keep the most-recent live
        values frozen on state."""
        eng = replay["engine"]
        if eng is None:
            return {"stopped": False, "reason": "no replay running"}
        eng.stop()
        replay["engine"] = None
        return {"stopped": True}

    @app.get("/api/sim/log")
    def recent_log(limit: int = 100) -> list[dict]:
        """Recent scan-tool requests this board has seen."""
        return ecu.recent_log(limit=limit)

    @app.get("/api/sim/persistence")
    def persistence_info() -> dict:
        """
        v0.6.7: report whether the Pi has a saved scenario on disk that
        would be restored on next boot. The dashboard exposes this so
        the instructor can verify before walking away from the bench.
        """
        if persist_path is None:
            return {"enabled": False}
        info = persistence_status(persist_path)
        info["enabled"] = True
        return info

    @app.post("/api/sim/persistence/clear")
    def persistence_clear() -> dict:
        """Remove the saved scenario so the next reboot starts blank."""
        if persist_path is None:
            return {"enabled": False, "cleared": False}
        ok = clear_last_scenario(persist_path)
        return {"enabled": True, "cleared": ok}

    return app
