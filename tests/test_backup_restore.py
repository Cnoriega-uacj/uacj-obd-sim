"""
Backup and restore endpoints. The user can move to a new laptop and
recover full state with one ZIP.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from uacj_obd.api import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_root=tmp_path)), tmp_path


def _seed_minimal_session(data_root):
    sessions_root = data_root / "sessions"
    session_dir = sessions_root / "TESTVIN_Mock_Make_Mock_Model_2020" / "session-x"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text('{"session_id":"session-x"}')
    (session_dir / "live_data.jsonl").write_text(
        '{"ts":"2026-04-29T00:00:00Z","pid":"010C","name":"RPM","value":700,"unit":"rpm"}\n'
    )
    (session_dir / "dtcs.json").write_text("[]")


def test_backup_returns_zip_with_data(client):
    c, data_root = client
    _seed_minimal_session(data_root)
    r = c.post("/api/backup")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert "BACKUP_INFO.json" in names
    assert any("session-x/metadata.json" in n for n in names)


def test_round_trip_backup_then_restore(client, tmp_path):
    c, data_root = client
    _seed_minimal_session(data_root)
    backup = c.post("/api/backup").content

    # Wipe the data dir entirely
    sessions_root = data_root / "sessions"
    for f in sessions_root.rglob("*"):
        if f.is_file():
            f.unlink()
    # Sanity: no metadata.json left
    assert not list(sessions_root.rglob("metadata.json"))

    # Restore from the backup we just took
    r = c.post(
        "/api/restore",
        files={"file": ("backup.zip", backup, "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True
    assert body["sessions"] >= 1
    # The original metadata is back at the same path
    assert (sessions_root / "TESTVIN_Mock_Make_Mock_Model_2020" / "session-x" / "metadata.json").exists()


def test_restore_rejects_non_uacj_zip(client):
    c, _ = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("not-a-backup.txt", "hi")
    r = c.post("/api/restore", files={"file": ("not-uacj.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    assert "BACKUP_INFO" in r.text


def test_restore_rejects_zip_slip(client):
    c, _ = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BACKUP_INFO.json", "{}")
        zf.writestr("../escape.txt", "evil")
    r = c.post("/api/restore", files={"file": ("evil.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    assert "unsafe path" in r.text


def test_restore_rejects_non_zip_upload(client):
    c, _ = client
    r = c.post("/api/restore", files={"file": ("oops.txt", b"plain text", "text/plain")})
    assert r.status_code == 400
