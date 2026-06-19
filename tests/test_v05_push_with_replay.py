"""
v0.5.0 — End-to-end test of the dashboard push endpoint with dynamic
replay enabled.

Walks the full pipeline:

  1. Dashboard captures a mock session into live_data.jsonl
  2. A scenario is created over that session with `replay: true`
  3. POST /api/scenarios/{id}/push is invoked
  4. The push endpoint reads live_data.jsonl, builds the time-series,
     and POSTs the full payload to the simulator URL
  5. We capture the payload sent to the simulator and verify:
     - live_timeseries is present and non-empty
     - live_baseline (latest-per-PID) is also present
     - The shape is what scenario_to_state expects
     - scenario_to_state turns it into a ScenarioState with the
       time-series populated
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from uacj_obd.api import create_app
from uacj_obd.simulator.can_runtime import scenario_to_state


def _capture_short_mock_session(client: TestClient) -> str:
    """Run a brief mock-adapter capture and return its session id."""
    r = client.post(
        "/api/sessions/start",
        json={"adapter": "mock", "duration_s": 0.3, "pids": ["010C", "010D", "0105"]},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    deadline = time.time() + 5
    while time.time() < deadline:
        cur = client.get("/api/sessions/current").json()
        if cur is None:
            break
        time.sleep(0.05)
    return sid


def _make_scenario_over_session(client: TestClient, source_session_id: str, replay: bool) -> str:
    """Create a scenario whose payload references the source session.
    Returns the scenario id."""
    req = {
        "label": "v0.5.0 replay test",
        "source_session_id": source_session_id,
        "vehicle": {
            "vin": "TEST1234567890123",
            "make": "Test",
            "model": "Replay",
            "year": 2026,
        },
        "live_overrides": {},
        "replay": replay,
    }
    r = client.post("/api/scenarios", json=req)
    assert r.status_code == 200, r.text
    return r.json()["scenario_id"]


def test_push_without_replay_omits_timeseries(tmp_path: Path, monkeypatch) -> None:
    """Baseline behaviour: scenarios without `replay: true` send only
    `live_baseline`, no `live_timeseries`. Preserves v0.4.x compat."""
    app = create_app(data_root=tmp_path)
    client = TestClient(app)
    sid = _capture_short_mock_session(client)
    scenario_id = _make_scenario_over_session(client, sid, replay=False)

    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"loaded": True, "vin": "TEST1234567890123"}

    class _FakeClient:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a, **kw): ...
        def post(self, url, json=None) -> _FakeResponse:
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    r = client.post(f"/api/scenarios/{scenario_id}/push")
    assert r.status_code == 200, r.text
    assert r.json()["pushed"] is True
    assert r.json().get("replay_samples", 0) == 0

    payload = captured["payload"]
    assert "live_baseline" in payload
    assert "live_timeseries" not in payload


def test_push_with_replay_attaches_full_timeseries(tmp_path: Path, monkeypatch) -> None:
    """When the scenario opts in with `replay: true`, the push endpoint
    reads every line of live_data.jsonl and ships them as
    `live_timeseries`."""
    app = create_app(data_root=tmp_path)
    client = TestClient(app)
    sid = _capture_short_mock_session(client)
    scenario_id = _make_scenario_over_session(client, sid, replay=True)

    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"loaded": True, "vin": "TEST1234567890123"}

    class _FakeClient:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a, **kw): ...
        def post(self, url, json=None) -> _FakeResponse:
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.post(f"/api/scenarios/{scenario_id}/push")
    assert r.status_code == 200, r.text
    payload = captured["payload"]
    assert "live_timeseries" in payload
    assert len(payload["live_timeseries"]) > 0
    # Each entry should have ts + pid + value
    sample = payload["live_timeseries"][0]
    assert "ts" in sample and "pid" in sample and "value" in sample
    # Loop default should ride along
    assert payload.get("live_timeseries_loop") is True
    # Push response reports the count
    assert r.json()["replay_samples"] == len(payload["live_timeseries"])


def test_push_with_replay_payload_round_trips_through_scenario_to_state(tmp_path: Path, monkeypatch) -> None:
    """Final integration check: the payload the push endpoint sends must
    parse cleanly through `scenario_to_state` into a ScenarioState
    whose `live_timeseries` is non-empty and replay-ready."""
    app = create_app(data_root=tmp_path)
    client = TestClient(app)
    sid = _capture_short_mock_session(client)
    scenario_id = _make_scenario_over_session(client, sid, replay=True)

    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"loaded": True, "vin": "TEST1234567890123"}

    class _FakeClient:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a, **kw): ...
        def post(self, url, json=None) -> _FakeResponse:
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    client.post(f"/api/scenarios/{scenario_id}/push")

    payload = captured["payload"]
    # Hand the captured payload to scenario_to_state — this is the
    # exact same path the Pi server takes on /api/sim/load.
    state = scenario_to_state(payload)
    assert len(state.live_timeseries) > 0
    assert state.live_timeseries_loop is True
    # Sanity: the first sample is the earliest in time
    assert state.live_timeseries[0].t_offset == 0.0
