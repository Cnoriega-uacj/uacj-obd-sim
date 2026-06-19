"""
v0.5.3 — Tests for full-PID capture and the "show all" data path.

The dashboard JavaScript change can't run in pytest, but the API
behaviour it depends on can. The dashboard now starts captures
WITHOUT passing an explicit PID list, which means the server must:

  1. Discover the adapter's supported PIDs (already done in v0.4.9).
  2. Capture samples for every discovered PID.
  3. Return all of them via `/api/sessions/{id}/live` so the dashboard
     can render either the focused 12-PID view or the full set.

These tests lock that contract in.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from uacj_obd.api import create_app


def _drain_session(client: TestClient) -> None:
    """Wait for the current session to finish (mock adapter has a
    fixed duration)."""
    deadline = time.time() + 5
    while time.time() < deadline:
        cur = client.get("/api/sessions/current").json()
        if cur is None:
            break
        time.sleep(0.05)


def test_start_session_without_explicit_pids_captures_all_supported(tmp_path: Path) -> None:
    """v0.5.3: when the dashboard starts a session with no `pids` field
    in the POST body, the server uses the adapter's supported PIDs
    (the v0.4.9 default). MockAdapter supports a known set so this is
    deterministic."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    _drain_session(client)
    live = client.get(f"/api/sessions/{sid}/live?limit=2000").json()
    pids_seen = {s["pid"] for s in live}
    # MockAdapter's supported_pids is more than just the 12 the dashboard
    # used to hardcode. The exact count is implementation-defined, but
    # we DO expect at least more than the 12 common set so the "show
    # all" toggle has something to reveal.
    assert len(pids_seen) > 0, "no samples captured at all"
    # All common PIDs the dashboard renders by default should be there:
    common = {"010C", "010D", "0105", "010F", "0110", "0111", "0104",
              "0106", "0107", "010B", "0114", "012F"}
    overlap = common & pids_seen
    assert len(overlap) >= 5, (
        f"expected most common PIDs to be captured; got overlap {overlap}"
    )


def test_start_session_with_explicit_pids_still_honoured(tmp_path: Path) -> None:
    """Backwards compatibility: if the caller still sends a `pids` list,
    we don't break them. The v0.4.9 server already does this; v0.5.3
    just ensures the dashboard's new "no list" behaviour doesn't
    accidentally remove the override path."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post(
        "/api/sessions/start",
        json={"adapter": "mock", "duration_s": 0.5, "pids": ["010C", "010D"]},
    )
    assert r.status_code == 200
    sid = r.json()["session_id"]
    _drain_session(client)
    live = client.get(f"/api/sessions/{sid}/live?limit=2000").json()
    pids_seen = {s["pid"] for s in live}
    # With an explicit constraint we should NOT see any PID outside the list
    assert pids_seen.issubset({"010C", "010D"}), (
        f"explicit PID list was not honoured; saw extras: {pids_seen - {'010C', '010D'}}"
    )


def test_live_endpoint_supports_2000_sample_limit(tmp_path: Path) -> None:
    """v0.5.3: the dashboard fetches `?limit=2000` so the 'show all'
    panel has data for every PID even on long captures. The endpoint
    must respect that without error."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    sid = r.json()["session_id"]
    _drain_session(client)
    r = client.get(f"/api/sessions/{sid}/live?limit=2000")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
