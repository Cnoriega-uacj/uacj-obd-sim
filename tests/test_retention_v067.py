"""
v0.6.7 — Tests for the session retention policy.

Covers `uacj_obd.retention` (pure helpers over a real SQLite DB) and
the API endpoints (`DELETE /api/sessions/{id}`, `POST /api/sessions/cleanup`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uacj_obd.api import create_app
from uacj_obd.retention import (
    DEFAULT_KEEP_MINIMUM,
    DEFAULT_MAX_AGE_DAYS,
    prune_empty,
    prune_old,
)
from uacj_obd.storage import Database


def _insert(db: Database, *, session_id: str, started_at: datetime,
            sample_count: int = 100, folder: str | None = None) -> str:
    """Insert a fake session row + (optionally) make a folder on disk."""
    folder = folder or f"/tmp/{session_id}_folder_does_not_need_to_exist"
    vin = f"VIN{session_id[:14]}"
    db.upsert_vehicle(vin, "TestMake", "TestModel", 2020, started_at.isoformat())
    db.insert_session(
        session_id=session_id,
        vin=vin,
        started_at=started_at.isoformat(),
        ended_at=(started_at + timedelta(minutes=5)).isoformat(),
        protocol="ISO_15765_4_CAN_11_500",
        adapter="mock",
        sample_count=sample_count,
        folder=folder,
        notes="",
    )
    return folder


# ---------------------------------------------------------------------------
# prune_empty
# ---------------------------------------------------------------------------

def test_prune_empty_removes_zero_sample_sessions(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    now = datetime.now(timezone.utc)
    _insert(db, session_id="s_empty1", started_at=now, sample_count=0)
    _insert(db, session_id="s_full",   started_at=now, sample_count=42)
    _insert(db, session_id="s_empty2", started_at=now, sample_count=0)

    result = prune_empty(db)
    assert result.count == 2
    assert set(result.deleted_session_ids) == {"s_empty1", "s_empty2"}
    remaining = [r["session_id"] for r in db.list_sessions()]
    assert remaining == ["s_full"]


def test_prune_empty_no_op_when_nothing_to_prune(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    now = datetime.now(timezone.utc)
    _insert(db, session_id="s1", started_at=now, sample_count=10)
    _insert(db, session_id="s2", started_at=now, sample_count=20)
    result = prune_empty(db)
    assert result.count == 0
    assert len(db.list_sessions()) == 2


def test_prune_empty_removes_folder_when_present(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    folder = tmp_path / "session_folder"
    folder.mkdir()
    (folder / "live_data.jsonl").write_text("")
    _insert(
        db, session_id="s_empty", started_at=datetime.now(timezone.utc),
        sample_count=0, folder=str(folder),
    )
    result = prune_empty(db)
    assert result.count == 1
    assert str(folder) in result.deleted_folders
    assert not folder.exists()


def test_prune_empty_handles_missing_folder(tmp_path: Path) -> None:
    """An empty session whose folder was already removed shouldn't error."""
    db = Database(tmp_path / "uacj.db")
    _insert(
        db, session_id="s", started_at=datetime.now(timezone.utc),
        sample_count=0, folder=str(tmp_path / "vanished"),
    )
    result = prune_empty(db)
    assert result.count == 1


# ---------------------------------------------------------------------------
# prune_old
# ---------------------------------------------------------------------------

def test_prune_old_drops_sessions_past_max_age(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert(db, session_id="recent", started_at=now - timedelta(days=10))
    _insert(db, session_id="old1",   started_at=now - timedelta(days=200))
    _insert(db, session_id="old2",   started_at=now - timedelta(days=400))

    # Add filler so we exceed keep_minimum and old sessions become eligible.
    for i in range(DEFAULT_KEEP_MINIMUM):
        _insert(db, session_id=f"filler{i}",
                started_at=now - timedelta(days=1, hours=i))

    result = prune_old(db, max_age_days=90, keep_minimum=2, now=now)
    assert "old1" in result.deleted_session_ids
    assert "old2" in result.deleted_session_ids
    assert "recent" not in result.deleted_session_ids


def test_prune_old_protects_keep_minimum_floor(tmp_path: Path) -> None:
    """Even if every session is past max_age, the most-recent
    `keep_minimum` are kept."""
    db = Database(tmp_path / "uacj.db")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        _insert(db, session_id=f"s{i}",
                started_at=now - timedelta(days=365 + i))

    result = prune_old(db, max_age_days=30, keep_minimum=3, now=now)
    assert result.count == 2
    assert len(db.list_sessions()) == 3


def test_prune_old_no_op_when_below_keep_minimum(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        _insert(db, session_id=f"s{i}", started_at=now - timedelta(days=400))

    result = prune_old(db, max_age_days=30, keep_minimum=10, now=now)
    assert result.count == 0
    assert len(db.list_sessions()) == 3


def test_prune_old_rejects_invalid_args(tmp_path: Path) -> None:
    db = Database(tmp_path / "uacj.db")
    with pytest.raises(ValueError):
        prune_old(db, max_age_days=0)
    with pytest.raises(ValueError):
        prune_old(db, max_age_days=-1)
    with pytest.raises(ValueError):
        prune_old(db, keep_minimum=-1)


def test_prune_old_skips_unparseable_timestamps(tmp_path: Path) -> None:
    """Legacy rows with empty started_at should be skipped, not crash."""
    db = Database(tmp_path / "uacj.db")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Recent rows above keep_minimum so old ones become eligible.
    for i in range(DEFAULT_KEEP_MINIMUM):
        _insert(db, session_id=f"keep{i}",
                started_at=now - timedelta(days=1, hours=i))
    # Insert with a bogus timestamp directly via DB.
    db.upsert_vehicle("VINMALFORMED", None, None, None, now.isoformat())
    db.insert_session(
        session_id="malformed",
        vin="VINMALFORMED",
        started_at="not-a-date",
        ended_at=None,
        protocol=None,
        adapter="mock",
        sample_count=1,
        folder="/tmp/nope",
        notes="",
    )
    result = prune_old(db, max_age_days=30, keep_minimum=DEFAULT_KEEP_MINIMUM, now=now)
    assert "malformed" in result.skipped_session_ids


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_delete_session_endpoint_removes_row_and_folder(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    # Run a quick mock capture so a session exists.
    r = client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 1.0})
    assert r.status_code == 200
    # Wait for the session thread to finish.
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            break

    sessions = client.get("/api/sessions").json()
    assert sessions, "expected at least one captured session"
    session_id = sessions[0]["session_id"]
    folder = Path(sessions[0]["folder"])
    assert folder.exists()

    r = client.delete(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert not folder.exists()


def test_delete_session_404_on_missing(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.delete("/api/sessions/does-not-exist")
    assert r.status_code == 404


def test_delete_session_409_while_running(tmp_path: Path) -> None:
    """Refuse to delete the currently-running capture."""
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 30.0})
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    try:
        r = client.delete(f"/api/sessions/{session_id}")
        assert r.status_code == 409
    finally:
        client.post("/api/sessions/stop")


def test_cleanup_empty_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    # Run two captures, force one to be "empty" by patching its row.
    for _ in range(2):
        client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
        import time
        for _ in range(80):
            time.sleep(0.1)
            if client.get("/api/sessions/current").json().get("active") is False:
                break

    db = Database(tmp_path / "uacj.db")
    rows = db.list_sessions()
    db.update_session(rows[0]["session_id"], sample_count=0)

    r = client.post("/api/sessions/cleanup", params={"mode": "empty"})
    assert r.status_code == 200
    body = r.json()
    assert body["total_deleted"] == 1
    assert body["results"][0]["policy"] == "empty"


def test_cleanup_invalid_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post("/api/sessions/cleanup", params={"mode": "garbage"})
    assert r.status_code == 400


def test_cleanup_409_while_session_running(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 30.0})
    try:
        r = client.post("/api/sessions/cleanup", params={"mode": "empty"})
        assert r.status_code == 409
    finally:
        client.post("/api/sessions/stop")


def test_cleanup_both_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    # Empty session + old session.
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            break

    db = Database(tmp_path / "uacj.db")
    sid = db.list_sessions()[0]["session_id"]
    db.update_session(sid, sample_count=0)

    r = client.post(
        "/api/sessions/cleanup",
        params={"mode": "both", "max_age_days": 30, "keep_minimum": 0},
    )
    assert r.status_code == 200
    body = r.json()
    policies = [r["policy"] for r in body["results"]]
    assert policies == ["empty", "old"]


def test_cleanup_old_validation_passes_through(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path))
    r = client.post(
        "/api/sessions/cleanup",
        params={"mode": "old", "max_age_days": 0},
    )
    assert r.status_code == 400
