from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.models import DTC, FreezeFrame, Monitor, Scenario, VehicleInfo
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database, SessionStore

log = logging.getLogger(__name__)


class StartSessionRequest(BaseModel):
    adapter: str = "auto"
    portstr: str | None = None
    pids: list[str] | None = None
    manufacturer_pids: list[str] | None = None
    duration_s: float | None = None
    notes: str = ""


class ScenarioCreateRequest(BaseModel):
    label: str
    source_session_id: str | None = None
    vehicle: VehicleInfo | None = None
    dtcs: list[DTC] = []
    monitors: list[Monitor] = []
    freeze_frame: FreezeFrame | None = None
    live_overrides: dict[str, Any] = {}


class ScenarioUpdateRequest(BaseModel):
    label: str | None = None
    dtcs: list[DTC] | None = None
    monitors: list[Monitor] | None = None
    freeze_frame: FreezeFrame | None = None
    live_overrides: dict[str, Any] | None = None


def create_app(data_root: str | Path = "data") -> FastAPI:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "uacj.db")
    store = SessionStore(root / "sessions")
    pid_reg = load_default_registry()

    app = FastAPI(
        title="UACJ OBD-II Training Simulator",
        version="0.1.0",
        description=(
            "Acquisition, storage, modification, and replay for the UACJ "
            "automotive training program."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state: dict[str, Any] = {"current": None, "thread": None}

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "now": datetime.now(timezone.utc).isoformat()}

    @app.get("/api/pids")
    def list_pids() -> list[dict]:
        return [d.__dict__ for d in pid_reg.all()]

    # --- session lifecycle --------------------------------------------

    @app.post("/api/sessions/start")
    def start_session(req: StartSessionRequest) -> dict:
        if state["current"] is not None:
            raise HTTPException(409, "a session is already running")
        adapter = open_adapter(req.adapter, portstr=req.portstr) if req.portstr else open_adapter(req.adapter)
        cfg = SessionConfig(
            pids=req.pids or SessionConfig().pids,
            manufacturer_pids=req.manufacturer_pids or [],
            notes=req.notes,
        )
        sess = AcquisitionSession(adapter, store, db, pid_reg, cfg)
        try:
            meta = sess.start()
        except Exception as exc:
            raise HTTPException(500, f"start failed: {exc}") from exc

        def _runner() -> None:
            try:
                sess.run(duration_s=req.duration_s)
            finally:
                sess.close()
                state["current"] = None
                state["thread"] = None

        thread = threading.Thread(target=_runner, daemon=True, name=f"acq-{meta.session_id}")
        state["current"] = sess
        state["thread"] = thread
        thread.start()
        return {"session_id": meta.session_id, "vehicle": meta.vehicle.model_dump()}

    @app.post("/api/sessions/stop")
    def stop_session() -> dict:
        sess = state["current"]
        if sess is None:
            raise HTTPException(404, "no active session")
        sess.stop()
        return {"stopping": True, "session_id": sess.meta.session_id if sess.meta else None}

    @app.get("/api/sessions/current")
    def current_session() -> dict:
        sess = state["current"]
        if sess is None or sess.meta is None:
            return {"active": False}
        return {"active": True, "metadata": sess.meta.model_dump(mode="json")}

    @app.get("/api/sessions")
    def list_sessions(vin: str | None = None) -> list[dict]:
        return db.list_sessions(vin=vin)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        row = db.get_session(session_id)
        if not row:
            raise HTTPException(404, "session not found")
        d = Path(row["folder"])
        out = {"metadata": row, "dtcs": [], "monitors": [], "freeze_frame": None}
        for key, fname in (("dtcs", "dtcs.json"),
                            ("monitors", "monitors.json"),
                            ("freeze_frame", "freeze_frame.json")):
            p = d / fname
            if p.exists():
                out[key] = json.loads(p.read_text())
        return out

    @app.get("/api/sessions/{session_id}/live")
    def get_session_live(session_id: str, limit: int = 1000) -> list[dict]:
        row = db.get_session(session_id)
        if not row:
            raise HTTPException(404, "session not found")
        live = Path(row["folder"]) / "live_data.jsonl"
        if not live.exists():
            return []
        with live.open() as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    @app.get("/api/sessions/{session_id}/export.csv")
    def export_csv(session_id: str) -> FileResponse:
        row = db.get_session(session_id)
        if not row:
            raise HTTPException(404, "session not found")
        d = Path(row["folder"])
        live = d / "live_data.csv"
        if not live.exists():
            jsonl = d / "live_data.jsonl"
            import csv as _csv
            with jsonl.open() as src, live.open("w", newline="") as dst:
                w = _csv.writer(dst)
                w.writerow(["ts", "pid", "name", "value", "unit"])
                for line in src:
                    if not line.strip():
                        continue
                    o = json.loads(line)
                    w.writerow([o.get("ts"), o.get("pid"), o.get("name"),
                                 o.get("value"), o.get("unit")])
        return FileResponse(live, media_type="text/csv", filename=f"{session_id}.csv")

    # --- vehicles -----------------------------------------------------

    @app.get("/api/vehicles")
    def list_vehicles() -> list[dict]:
        return db.list_vehicles()

    # --- scenarios (modification) -------------------------------------

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict]:
        return db.list_scenarios()

    @app.post("/api/scenarios")
    def create_scenario(req: ScenarioCreateRequest) -> dict:
        scenario_id = uuid.uuid4().hex
        ts = datetime.now(timezone.utc).isoformat()
        vehicle = req.vehicle
        if not vehicle and req.source_session_id:
            row = db.get_session(req.source_session_id)
            if row and row.get("vin"):
                vehicle = VehicleInfo(vin=row["vin"])
        scenario = Scenario(
            scenario_id=scenario_id,
            label=req.label,
            source_session_id=req.source_session_id,
            vehicle=vehicle or VehicleInfo(),
            dtcs=req.dtcs,
            monitors=req.monitors,
            freeze_frame=req.freeze_frame,
            live_overrides=req.live_overrides,
        )
        db.upsert_scenario(
            scenario_id=scenario_id,
            label=req.label,
            source_session_id=req.source_session_id,
            vin=vehicle.vin if vehicle else None,
            payload=scenario.model_dump(mode="json"),
            created_at=ts,
            updated_at=ts,
        )
        return scenario.model_dump(mode="json")

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict:
        row = db.get_scenario(scenario_id)
        if not row:
            raise HTTPException(404, "scenario not found")
        return row["payload"]

    @app.patch("/api/scenarios/{scenario_id}")
    def update_scenario(scenario_id: str, req: ScenarioUpdateRequest) -> dict:
        row = db.get_scenario(scenario_id)
        if not row:
            raise HTTPException(404, "scenario not found")
        payload = row["payload"]
        if req.label is not None:
            payload["label"] = req.label
        if req.dtcs is not None:
            payload["dtcs"] = [d.model_dump() for d in req.dtcs]
        if req.monitors is not None:
            payload["monitors"] = [m.model_dump() for m in req.monitors]
        if req.freeze_frame is not None:
            payload["freeze_frame"] = req.freeze_frame.model_dump()
        if req.live_overrides is not None:
            payload["live_overrides"] = req.live_overrides
        ts = datetime.now(timezone.utc).isoformat()
        payload["updated_at"] = ts
        db.upsert_scenario(
            scenario_id=scenario_id,
            label=payload.get("label", ""),
            source_session_id=payload.get("source_session_id"),
            vin=(payload.get("vehicle") or {}).get("vin"),
            payload=payload,
            created_at=row["created_at"],
            updated_at=ts,
        )
        return payload

    @app.delete("/api/scenarios/{scenario_id}")
    def delete_scenario(scenario_id: str) -> dict:
        db.delete_scenario(scenario_id)
        return {"deleted": scenario_id}

    @app.post("/api/scenarios/{scenario_id}/push")
    def push_scenario(scenario_id: str, sim_url: str = "http://uacj-sim.local:8765") -> dict:
        """
        Push a scenario to the Pi simulator over HTTP.

        Pre-merges the *latest value per PID* from the source session as
        a live baseline; the scenario's live_overrides ride on top. This
        means the simulator answers every PID the original car answered,
        not just the ones the instructor explicitly modified.
        """
        scenario = db.get_scenario(scenario_id)
        if not scenario:
            raise HTTPException(404, "scenario not found")
        payload = dict(scenario["payload"])

        source_id = payload.get("source_session_id")
        if source_id:
            source = db.get_session(source_id)
            if source:
                live_path = Path(source["folder"]) / "live_data.jsonl"
                if live_path.exists():
                    latest: dict[str, Any] = {}
                    with live_path.open() as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            obj = json.loads(line)
                            pid = obj.get("pid")
                            value = obj.get("value")
                            if pid and value is not None:
                                latest[pid] = value
                    payload["live_baseline"] = latest

        try:
            import httpx

            with httpx.Client(timeout=5.0) as client:
                r = client.post(f"{sim_url.rstrip('/')}/api/sim/load", json=payload)
                r.raise_for_status()
                return {
                    "pushed": True,
                    "baseline_pids": len(payload.get("live_baseline") or {}),
                    "sim_response": r.json(),
                }
        except Exception as exc:
            raise HTTPException(502, f"simulator push failed: {exc}") from exc

    @app.post("/api/scenarios/{scenario_id}/replay")
    def replay_scenario(scenario_id: str, duration_s: float = 2.0) -> dict:
        """
        Replay a scenario through the ReplayAdapter into a new captured session.
        Used by the simulator workflow as an end-to-end self-test: it proves
        the saved-session → modify → replay loop works without hardware.
        """
        if state["current"] is not None:
            raise HTTPException(409, "another session is already running")
        scenario = db.get_scenario(scenario_id)
        if not scenario:
            raise HTTPException(404, "scenario not found")
        payload = scenario["payload"]
        source_id = payload.get("source_session_id")
        if not source_id:
            raise HTTPException(400, "scenario has no source session to replay from")
        source = db.get_session(source_id)
        if not source:
            raise HTTPException(404, "source session missing")
        from uacj_obd.adapters.replay import ReplayAdapter

        adapter = ReplayAdapter(source["folder"], scenario_overrides=payload)
        sess = AcquisitionSession(adapter, store, db, pid_reg,
                                    SessionConfig(notes=f"replay of scenario {scenario_id}"))
        meta = sess.start()

        def _runner() -> None:
            try:
                sess.run(duration_s=duration_s)
            finally:
                sess.close()
                state["current"] = None
                state["thread"] = None

        thread = threading.Thread(target=_runner, daemon=True, name=f"replay-{meta.session_id}")
        state["current"] = sess
        state["thread"] = thread
        thread.start()
        return {"session_id": meta.session_id, "scenario_id": scenario_id}

    # --- static dashboard --------------------------------------------

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
