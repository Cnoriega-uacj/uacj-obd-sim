"""
v0.6.7 — Session retention policy.

The Pi and the laptop are both space-constrained (16-32 GB SD card on
the Pi, often-full Windows laptops in the classroom). Captures pile up
over a semester and the dashboard list becomes unscannable. Worse: a
silently-full disk corrupts the next capture.

This module provides two policies, separately and together:

- `prune_empty(...)` — drop sessions that ended with zero samples.
  These are failed captures: adapter wouldn't open, scan tool not
  connected, instructor hit Stop too fast. They have a row in the
  DB and an empty folder on disk, and they only confuse the UI.

- `prune_old(...)` — drop sessions older than `max_age_days`, keeping
  at least `keep_minimum` sessions regardless of age. The keep-minimum
  floor protects against an end-of-semester cleanup that wipes the
  entire history.

Both helpers return a `PruneResult` so the API can show what happened.
Pure SQL + filesystem — no FastAPI dependency.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import Database

log = logging.getLogger(__name__)


DEFAULT_MAX_AGE_DAYS = 90
"""
Default retention window: one academic semester plus margin. Captures
older than this are unlikely to be re-used as scenario sources and the
classroom benefits more from the disk space.
"""

DEFAULT_KEEP_MINIMUM = 10
"""
Always keep at least this many of the most-recent sessions, even if
they're past the max-age window. This protects the dashboard from
starting empty after a long break.
"""


@dataclass
class PruneResult:
    deleted_session_ids: list[str] = field(default_factory=list)
    deleted_folders: list[str] = field(default_factory=list)
    skipped_session_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.deleted_session_ids)


def prune_empty(db: Database) -> PruneResult:
    """
    Delete sessions with sample_count == 0. The folder on disk is also
    removed if it still exists; if it doesn't, that's not an error.
    """
    result = PruneResult(reason="empty captures (sample_count==0)")
    for row in db.list_sessions():
        if row.get("sample_count", 0) != 0:
            continue
        _delete_one(db, row, result)
    return result


def prune_old(
    db: Database,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    keep_minimum: int = DEFAULT_KEEP_MINIMUM,
    now: datetime | None = None,
) -> PruneResult:
    """
    Delete sessions older than `max_age_days`, but always keep at least
    `keep_minimum` of the most-recent ones.

    `now` is injectable so tests can run deterministically against fixed
    timestamps; production callers pass None and get UTC now.
    """
    if max_age_days < 1:
        raise ValueError("max_age_days must be >= 1")
    if keep_minimum < 0:
        raise ValueError("keep_minimum must be >= 0")

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    result = PruneResult(
        reason=f"older than {max_age_days}d (keep_minimum={keep_minimum})",
    )

    all_rows = db.list_sessions()
    if len(all_rows) <= keep_minimum:
        log.info(
            "retention: only %d sessions, below keep_minimum=%d; nothing to prune",
            len(all_rows), keep_minimum,
        )
        return result

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _sort_key(row: dict) -> datetime:
        # Treat unparseable timestamps as "very old" so they're not
        # accidentally protected by the keep_minimum floor.
        return _parse_iso(row.get("started_at")) or epoch

    rows_sorted = sorted(all_rows, key=_sort_key, reverse=True)
    protected_ids = {r["session_id"] for r in rows_sorted[:keep_minimum]}

    for row in all_rows:
        if row["session_id"] in protected_ids:
            continue
        ts = _parse_iso(row.get("started_at"))
        if ts is None:
            result.skipped_session_ids.append(row["session_id"])
            continue
        if ts >= cutoff:
            continue
        _delete_one(db, row, result)

    return result


def _delete_one(db: Database, row: dict, result: PruneResult) -> None:
    """Delete the DB row and (best-effort) the on-disk folder."""
    session_id = row["session_id"]
    folder = row.get("folder")
    if folder:
        folder_path = Path(folder)
        try:
            if folder_path.exists():
                shutil.rmtree(folder_path)
                result.deleted_folders.append(str(folder_path))
        except OSError as exc:
            log.warning("retention: could not remove %s: %s", folder_path, exc)
            result.skipped_session_ids.append(session_id)
            return
    if db.delete_session(session_id):
        result.deleted_session_ids.append(session_id)
    else:
        result.skipped_session_ids.append(session_id)


def _parse_iso(value: str | None) -> datetime | None:
    """
    Parse an ISO-8601 timestamp like the ones stored by SessionStore.
    Returns None for empty/malformed values so the caller can skip
    rather than crash on legacy data.
    """
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts
