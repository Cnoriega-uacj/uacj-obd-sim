"""
Pi-side HTTP server: receives scenario push from the laptop and updates
the live ECU emulator state.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .ecu import EcuEmulator
from .can_runtime import scenario_to_state
from .replay_engine import ReplayEngine

log = logging.getLogger(__name__)


def make_simulator_server(ecu: EcuEmulator) -> FastAPI:
    app = FastAPI(
        title="UACJ Simulator Board",
        version="0.1.0",
        description="Pi-side scenario receiver for the UACJ OBD-II simulator.",
    )

    # v0.5.0: one replay engine at a time, swapped on scenario load.
    replay: dict[str, ReplayEngine | None] = {"engine": None}

    @app.get("/api/sim/health")
    def health() -> dict:
        return {"ok": True, "vin": ecu.state.vin, "stored_dtcs": ecu.state.dtcs_stored}

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
        """
        new_state = scenario_to_state(payload)
        # Stop the previous engine before swapping state — otherwise its
        # writes race with the new scenario's static `live_overrides`.
        old_engine = replay["engine"]
        if old_engine is not None:
            old_engine.stop()
            replay["engine"] = None
        ecu.load(new_state)
        # Start a new engine if the scenario carried a timeline.
        timeline = list(new_state.live_timeseries)
        if timeline:
            engine = ReplayEngine(
                state=new_state,
                samples=timeline,
                loop=new_state.live_timeseries_loop,
            )
            engine.start()
            replay["engine"] = engine
            log.info(
                "scenario loaded: VIN=%s DTCs=%d replay=%d samples (loop=%s)",
                new_state.vin, len(new_state.dtcs_stored),
                len(timeline), new_state.live_timeseries_loop,
            )
        else:
            log.info("scenario loaded: VIN=%s DTCs=%d (static live data)",
                     new_state.vin, len(new_state.dtcs_stored))
        return {
            "loaded": True,
            "vin": new_state.vin,
            "replay_samples": len(timeline),
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

    return app
