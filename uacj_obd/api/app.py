from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import io
import shutil
import tempfile
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.models import DTC, FreezeFrame, Monitor, Scenario, VehicleInfo
from uacj_obd.pids import load_default_registry
from uacj_obd.presets import apply_monitors_override, get_preset, list_presets
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
    # v0.5.0 — dynamic replay opt-in.
    replay: bool = False
    replay_loop: bool = True


class ScenarioUpdateRequest(BaseModel):
    label: str | None = None
    dtcs: list[DTC] | None = None
    monitors: list[Monitor] | None = None
    freeze_frame: FreezeFrame | None = None
    live_overrides: dict[str, Any] | None = None
    replay: bool | None = None
    replay_loop: bool | None = None


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
        """List captured vehicles. v0.5.2: each entry is enriched with
        an offline-decoded `decoded_make` / `decoded_year` / `region`
        if the VIN parses, so the dashboard shows useful info even
        when the captured session never received make/model/year from
        the ECU (some adapters don't populate those fields)."""
        from uacj_obd.vin_decoder import decode_vin
        out = []
        for v in db.list_vehicles():
            v = dict(v)
            vin = v.get("vin")
            if vin:
                result = decode_vin(vin)
                v["decoded_make"] = result.make
                v["decoded_year"] = result.model_year
                v["decoded_region"] = result.region
                v["vin_valid"] = result.valid
            out.append(v)
        return out

    @app.get("/api/vin/decode")
    def decode_vin_endpoint(vin: str) -> dict:
        """v0.5.2: standalone endpoint the dashboard can call from a
        scenario editor to auto-fill make/year fields after the user
        types or pastes a VIN."""
        from uacj_obd.vin_decoder import decode_vin
        result = decode_vin(vin)
        return {
            "vin": result.vin,
            "valid": result.valid,
            "make": result.make,
            "region": result.region,
            "model_year": result.model_year,
            "plant_code": result.plant_code,
            "error": result.error,
        }

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
            replay=req.replay,
            replay_loop=req.replay_loop,
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

    @app.get("/api/presets")
    def list_preset_endpoint() -> list[dict]:
        return list_presets()

    @app.post("/api/presets/{preset_id}/instantiate")
    def instantiate_preset(preset_id: str, source_session_id: str | None = None) -> dict:
        """
        Build a new scenario from a preset on top of a saved session.
        The session provides the vehicle identity and the live baseline;
        the preset provides DTCs, freeze frame, and any live overrides.
        """
        preset = get_preset(preset_id)
        if not preset:
            raise HTTPException(404, "preset not found")

        source = db.get_session(source_session_id) if source_session_id else None
        vehicle = VehicleInfo(vin=source["vin"]) if source else VehicleInfo()

        # Pull saved monitors and apply preset's monitor overrides if any
        monitors: list[dict] = []
        if source:
            mon_path = Path(source["folder"]) / "monitors.json"
            if mon_path.exists():
                monitors = json.loads(mon_path.read_text())
        if preset.get("monitors_override"):
            monitors = apply_monitors_override(monitors, preset["monitors_override"])

        scenario_id = uuid.uuid4().hex
        ts = datetime.now(timezone.utc).isoformat()
        scenario = Scenario(
            scenario_id=scenario_id,
            label=preset["label"],
            source_session_id=source_session_id,
            vehicle=vehicle,
            dtcs=[DTC(**d) for d in preset.get("dtcs", [])],
            monitors=[Monitor(**m) for m in monitors],
            freeze_frame=FreezeFrame(**preset["freeze_frame"]) if preset.get("freeze_frame") else None,
            live_overrides=preset.get("live_overrides") or {},
        )
        db.upsert_scenario(
            scenario_id=scenario_id,
            label=scenario.label,
            source_session_id=source_session_id,
            vin=vehicle.vin,
            payload=scenario.model_dump(mode="json"),
            created_at=ts,
            updated_at=ts,
        )
        return scenario.model_dump(mode="json")

    @app.post("/api/scenarios/{scenario_id}/push")
    def push_scenario(scenario_id: str, sim_url: str = "http://uacj-sim.local:8765") -> dict:
        """
        Push a scenario to the Pi simulator over HTTP.

        Pre-merges the *latest value per PID* from the source session as
        a live baseline; the scenario's live_overrides ride on top. This
        means the simulator answers every PID the original car answered,
        not just the ones the instructor explicitly modified.

        v0.5.0: if the scenario opts in by setting `replay: true` in its
        payload, the FULL captured time-series is shipped to the
        simulator as `live_timeseries`. The Pi-side `ReplayEngine` then
        mutates state.live at the recorded cadence — RPM bounces, speed
        rises and falls, every value moves like the real car did during
        capture. `replay_loop` (default true) controls whether the
        timeline restarts after reaching the end.
        """
        scenario = db.get_scenario(scenario_id)
        if not scenario:
            raise HTTPException(404, "scenario not found")
        payload = dict(scenario["payload"])

        source_id = payload.get("source_session_id")
        replay_enabled = bool(payload.get("replay", False))
        # v0.5.0: hard cap to keep pathological captures from blowing
        # the HTTP request size. ~50k samples is roughly 10 min of
        # 100-PID-per-cycle capture, far more than any classroom demo.
        replay_max_samples = int(payload.get("replay_max_samples", 50_000))

        if source_id:
            source = db.get_session(source_id)
            if source:
                live_path = Path(source["folder"]) / "live_data.jsonl"
                if live_path.exists():
                    latest: dict[str, Any] = {}
                    timeseries: list[dict[str, Any]] = []
                    with live_path.open() as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            obj = json.loads(line)
                            pid = obj.get("pid")
                            value = obj.get("value")
                            ts = obj.get("ts")
                            if pid and value is not None:
                                latest[pid] = value
                                if replay_enabled and ts is not None:
                                    if len(timeseries) >= replay_max_samples:
                                        continue
                                    timeseries.append({
                                        "ts": ts,
                                        "pid": pid,
                                        "value": value,
                                    })
                    payload["live_baseline"] = latest
                    if replay_enabled:
                        payload["live_timeseries"] = timeseries
                        payload["live_timeseries_loop"] = bool(
                            payload.get("replay_loop", True)
                        )

        try:
            import httpx

            with httpx.Client(timeout=10.0) as client:
                r = client.post(f"{sim_url.rstrip('/')}/api/sim/load", json=payload)
                r.raise_for_status()
                return {
                    "pushed": True,
                    "baseline_pids": len(payload.get("live_baseline") or {}),
                    "replay_samples": len(payload.get("live_timeseries") or []),
                    "sim_response": r.json(),
                }
        except Exception as exc:
            raise HTTPException(502, f"simulator push failed: {exc}") from exc

    @app.get("/api/diff")
    def diff_two_sessions(a: str, b: str) -> dict:
        """Compare two captured sessions: DTC delta, monitor delta, per-PID stats."""
        from uacj_obd.diff import diff_sessions

        row_a = db.get_session(a)
        row_b = db.get_session(b)
        if not row_a or not row_b:
            raise HTTPException(404, "session not found")
        return diff_sessions(Path(row_a["folder"]), Path(row_b["folder"]))

    @app.get("/api/sim/log")
    def sim_log(sim_url: str = "http://uacj-sim.local:8765", limit: int = 100) -> list[dict]:
        """
        Proxy the simulator board's request log to the laptop dashboard.
        Lets the instructor see every request a student's scan tool sent
        to the board, with timestamp, service, PID, and short summary.
        """
        try:
            import httpx

            with httpx.Client(timeout=3.0) as client:
                r = client.get(f"{sim_url.rstrip('/')}/api/sim/log", params={"limit": limit})
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            raise HTTPException(502, f"simulator log fetch failed: {exc}") from exc

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

    # --- backup / restore --------------------------------------------

    @app.post("/api/backup")
    def backup() -> StreamingResponse:
        """Stream a ZIP containing the SQLite metadata DB plus every
        session folder. Restoring this file on another laptop reproduces
        the entire state."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            db_path = root / "uacj.db"
            if db_path.exists():
                zf.write(db_path, arcname="uacj.db")
            sessions_root = root / "sessions"
            if sessions_root.exists():
                for path in sessions_root.rglob("*"):
                    if path.is_file():
                        zf.write(path, arcname=str(path.relative_to(root)))
            zf.writestr("BACKUP_INFO.json", json.dumps({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": "1",
                "source": "uacj-obd-sim /api/backup",
            }, indent=2))
        buf.seek(0)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        headers = {"Content-Disposition": f'attachment; filename="uacj-backup-{stamp}.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    @app.post("/api/restore")
    async def restore(file: UploadFile = File(...)) -> dict:
        """Replace the current data directory with the contents of a
        previously-saved backup ZIP. The current data is moved to a
        timestamped backup folder (root/.restore-backup-*) before
        the new files are written, so a bad upload is recoverable."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="upload must be a .zip file")
        raw = await file.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail=f"not a valid zip: {exc}")
        # Validate that this looks like one of our backups before touching disk
        names = set(zf.namelist())
        if "BACKUP_INFO.json" not in names:
            raise HTTPException(status_code=400, detail="missing BACKUP_INFO.json — not a UACJ backup")
        # Reject paths attempting to escape root (zip slip).
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise HTTPException(status_code=400, detail=f"unsafe path in zip: {name}")
        # Snapshot existing state side-aside before extracting.
        snapshot_dir = root / f".restore-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for name in ("uacj.db", "sessions"):
            src = root / name
            if src.exists():
                shutil.move(str(src), str(snapshot_dir / name))
        # Extract into root.
        session_count = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extractall(tmpdir)
            for src in Path(tmpdir).rglob("*"):
                if not src.is_file():
                    continue
                rel = src.relative_to(tmpdir)
                if str(rel) == "BACKUP_INFO.json":
                    continue
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                if rel.parts and rel.parts[0] == "sessions" and src.name == "metadata.json":
                    session_count += 1
        return {"restored": True, "sessions": session_count, "snapshot": str(snapshot_dir.name)}

    # --- static dashboard --------------------------------------------

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
