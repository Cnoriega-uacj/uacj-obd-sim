"""
Smoke test for scripts/seed_sample_sessions.py.

The five sample vehicles are shipped so the dashboard isn't empty on
first boot. CI catches if the seed script breaks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_seed_script_creates_five_sessions(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "seed_sample_sessions.py"), str(tmp_path)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"seed_sample_sessions.py failed:\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )
    sessions_root = tmp_path / "sessions"
    session_dirs = [p for p in sessions_root.rglob("sample_*") if p.is_dir()]
    assert len(session_dirs) == 5, f"expected 5 sample sessions, got {len(session_dirs)}"
    for session_dir in session_dirs:
        # Each session must have the canonical files
        assert (session_dir / "metadata.json").exists()
        assert (session_dir / "dtcs.json").exists()
        assert (session_dir / "monitors.json").exists()
        assert (session_dir / "live_data.jsonl").exists()
        # Live data must be valid JSONL with at least 60 records
        lines = [
            json.loads(line) for line in (session_dir / "live_data.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(lines) >= 60, f"{session_dir.name}: only {len(lines)} samples"
        for sample in lines[:5]:
            assert "ts" in sample and "pid" in sample and "value" in sample
    # Database row was created
    assert (tmp_path / "uacj.db").exists()


def test_seed_script_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(repo / "scripts" / "seed_sample_sessions.py"), str(tmp_path)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
    # Still exactly 5 session directories after a re-run
    sessions_root = tmp_path / "sessions"
    session_dirs = [p for p in sessions_root.rglob("sample_*") if p.is_dir()]
    assert len(session_dirs) == 5
