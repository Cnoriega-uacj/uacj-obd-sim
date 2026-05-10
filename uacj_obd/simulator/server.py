"""
Pi-side HTTP server: receives scenario push from the laptop and updates
the live ECU emulator state.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .ecu import EcuEmulator
from .can_runtime import scenario_to_state

log = logging.getLogger(__name__)


def make_simulator_server(ecu: EcuEmulator) -> FastAPI:
    app = FastAPI(
        title="UACJ Simulator Board",
        version="0.1.0",
        description="Pi-side scenario receiver for the UACJ OBD-II simulator.",
    )

    @app.get("/api/sim/health")
    def health() -> dict:
        return {"ok": True, "vin": ecu.state.vin, "stored_dtcs": ecu.state.dtcs_stored}

    @app.get("/api/sim/state")
    def state() -> dict:
        s = ecu.state
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
        }

    @app.post("/api/sim/load")
    def load(payload: dict) -> dict:
        """
        Receive a scenario payload (the same shape as the API's Scenario.payload)
        and atomically swap the ECU state.
        """
        new_state = scenario_to_state(payload)
        ecu.load(new_state)
        log.info("scenario loaded: VIN=%s DTCs=%d", new_state.vin, len(new_state.dtcs_stored))
        return {"loaded": True, "vin": new_state.vin}

    @app.post("/api/sim/clear")
    def clear() -> dict:
        ecu.state.clear_dtcs()
        return {"cleared": True}

    @app.get("/api/sim/log")
    def log(limit: int = 100) -> list[dict]:
        """Recent scan-tool requests this board has seen."""
        return ecu.recent_log(limit=limit)

    return app
