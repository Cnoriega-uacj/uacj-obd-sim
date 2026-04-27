from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(data_root=tmp_path)
    return TestClient(app)


def test_health(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_pid_list(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/pids")
    assert r.status_code == 200
    pids = r.json()
    assert any(p["name"] == "RPM" for p in pids)


def test_session_lifecycle(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5,
                                              "pids": ["010C", "010D"]})
    assert r.status_code == 200
    sid = r.json()["session_id"]

    # let the background thread record some samples then finish
    deadline = time.time() + 5
    while time.time() < deadline:
        cur = c.get("/api/sessions/current").json()
        if not cur.get("active"):
            break
        time.sleep(0.1)

    r = c.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert "metadata" in body
    assert "dtcs" in body

    r = c.get(f"/api/sessions/{sid}/live?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_scenario_crud(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/scenarios", json={
        "label": "P0420 catalyst lesson",
        "vehicle": {"vin": "2HGFC2F59FH123456", "make": "Honda", "model": "Civic", "year": 2015},
        "dtcs": [{"code": "P0420", "status": "stored", "description": "Catalyst"}],
        "monitors": [],
        "live_overrides": {"010C": 1850},
    })
    assert r.status_code == 200
    sid = r.json()["scenario_id"]

    r = c.get(f"/api/scenarios/{sid}")
    assert r.status_code == 200
    assert r.json()["label"] == "P0420 catalyst lesson"

    r = c.patch(f"/api/scenarios/{sid}", json={"label": "renamed"})
    assert r.status_code == 200
    assert r.json()["label"] == "renamed"

    r = c.get("/api/scenarios")
    assert any(s["scenario_id"] == sid for s in r.json())

    r = c.delete(f"/api/scenarios/{sid}")
    assert r.status_code == 200
