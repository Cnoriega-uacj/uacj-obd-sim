from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    vin TEXT PRIMARY KEY,
    make TEXT,
    model TEXT,
    year INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    vin TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    protocol TEXT,
    adapter TEXT,
    sample_count INTEGER DEFAULT 0,
    folder TEXT NOT NULL,
    notes TEXT DEFAULT '',
    FOREIGN KEY (vin) REFERENCES vehicles(vin)
);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    source_session_id TEXT,
    vin TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_vin ON sessions(vin);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- vehicles ------------------------------------------------------

    def upsert_vehicle(self, vin: str, make: str | None, model: str | None,
                        year: int | None, ts: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO vehicles (vin, make, model, year, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(vin) DO UPDATE SET
                    make=COALESCE(excluded.make, vehicles.make),
                    model=COALESCE(excluded.model, vehicles.model),
                    year=COALESCE(excluded.year, vehicles.year),
                    last_seen=excluded.last_seen
                """,
                (vin, make, model, year, ts, ts),
            )

    def list_vehicles(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM vehicles ORDER BY last_seen DESC")]

    # --- sessions ------------------------------------------------------

    def insert_session(self, **fields) -> None:
        cols = ",".join(fields.keys())
        placeholders = ",".join("?" * len(fields))
        with self._conn() as c:
            c.execute(
                f"INSERT INTO sessions ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )

    def update_session(self, session_id: str, **fields) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(
                f"UPDATE sessions SET {sets} WHERE session_id=?",
                (*fields.values(), session_id),
            )

    def get_session(self, session_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            return dict(row) if row else None

    def list_sessions(self, vin: str | None = None) -> list[dict]:
        with self._conn() as c:
            if vin:
                rows = c.execute(
                    "SELECT * FROM sessions WHERE vin=? ORDER BY started_at DESC",
                    (vin,),
                )
            else:
                rows = c.execute("SELECT * FROM sessions ORDER BY started_at DESC")
            return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> bool:
        """
        v0.6.7: remove a session row by id. Returns True if a row was
        actually deleted, False if no such session existed. The folder
        on disk is the caller's responsibility (see SessionStore).
        """
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            return cur.rowcount > 0

    # --- scenarios -----------------------------------------------------

    def upsert_scenario(self, scenario_id: str, label: str,
                         source_session_id: str | None, vin: str | None,
                         payload: dict, created_at: str, updated_at: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO scenarios (scenario_id, label, source_session_id, vin, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    label=excluded.label,
                    source_session_id=excluded.source_session_id,
                    vin=excluded.vin,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (scenario_id, label, source_session_id, vin, json.dumps(payload), created_at, updated_at),
            )

    def get_scenario(self, scenario_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM scenarios WHERE scenario_id=?", (scenario_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["payload"] = json.loads(d["payload"])
            return d

    def list_scenarios(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM scenarios ORDER BY updated_at DESC")
            out = []
            for r in rows:
                d = dict(r)
                d["payload"] = json.loads(d["payload"])
                out.append(d)
            return out

    def delete_scenario(self, scenario_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM scenarios WHERE scenario_id=?", (scenario_id,))
