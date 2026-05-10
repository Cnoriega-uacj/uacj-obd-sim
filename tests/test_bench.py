"""
Wrap scripts/bench.py so CI catches regressions in the virtual-bus
round-trip. The bench is the closest thing we have to integration
testing without the OBDLink SX / Pi / MCP2515 / L9637D in hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bench_harness_passes():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "bench.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bench.py failed (exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "Bench harness passed" in result.stdout
