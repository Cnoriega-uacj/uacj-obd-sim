#!/usr/bin/env python3
"""
Build the laptop-installer ZIP that gets handed to UACJ.

Contents:
  - Source code (uacj_obd/, web/, scripts/, pyproject.toml, README,
    docs/) — needed for `pip install -e .`
  - installer/start_uacj.bat (Windows one-click launcher)
  - installer/start_uacj.sh (macOS/Linux one-click launcher)
  - installer/README_INSTALLER.md
  - CHANGELOG.md, LICENSE (if present)

Excludes: .venv/, .git/, data/, __pycache__/, .pytest_cache/, .ruff_cache/

Usage:
    python scripts/build_installer_zip.py [output-path]
Default output: dist/uacj-obd-sim-installer-vX.Y.Z.zip
"""

from __future__ import annotations

import sys
import tomllib
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_PARTS = {".venv", ".git", "__pycache__", ".pytest_cache",
                    ".ruff_cache", "data", "dist", ".github"}
INCLUDE_TOP = {
    "uacj_obd", "web", "scripts", "installer", "docs", "tests",
    "pyproject.toml", "README.md", "CHANGELOG.md", "CLAUDE.md",
}


def _is_excluded(rel: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in rel.parts)


def _read_version() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return pyproject["project"]["version"]


def main() -> int:
    version = _read_version()
    out = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) > 1
        else REPO_ROOT / "dist" / f"uacj-obd-sim-installer-v{version}.zip"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for top in INCLUDE_TOP:
            src = REPO_ROOT / top
            if not src.exists():
                continue
            if src.is_file():
                zf.write(src, arcname=src.name)
                file_count += 1
                continue
            for path in src.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(REPO_ROOT)
                if _is_excluded(rel):
                    continue
                zf.write(path, arcname=str(rel))
                file_count += 1
        zf.writestr("INSTALLER_VERSION.txt",
                     f"uacj-obd-sim laptop installer\n"
                     f"version: {version}\n"
                     f"files: {file_count}\n")
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"Built {out} ({file_count} files, {size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
