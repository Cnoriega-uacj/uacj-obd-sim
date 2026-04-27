from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from uacj_obd.models import (
    DTC,
    FreezeFrame,
    LiveSample,
    Monitor,
    SessionMetadata,
    VehicleInfo,
)


_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _slug(s: str | None, fallback: str = "unknown") -> str:
    if not s:
        return fallback
    return _SAFE.sub("_", str(s)).strip("_") or fallback


def vehicle_folder_name(v: VehicleInfo) -> str:
    parts = [
        _slug(v.vin, "novin"),
        _slug(v.make),
        _slug(v.model),
        str(v.year) if v.year else "unknown",
    ]
    return "_".join(parts)


class SessionStore:
    """
    On-disk session storage. One folder per vehicle, one subfolder per session.

    Layout:
        sessions/
          {VIN}_{make}_{model}_{year}/
            {session_id}/
              metadata.json
              live_data.jsonl
              dtcs.json
              monitors.json
              freeze_frame.json
              raw.log
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, vehicle: VehicleInfo, session_id: str) -> Path:
        return self.root / vehicle_folder_name(vehicle) / session_id

    def open_session(self, meta: SessionMetadata) -> "SessionWriter":
        d = self.session_dir(meta.vehicle, meta.session_id)
        d.mkdir(parents=True, exist_ok=True)
        return SessionWriter(d, meta)

    def list_session_dirs(self) -> list[Path]:
        out: list[Path] = []
        for vehicle_dir in sorted(self.root.iterdir()):
            if not vehicle_dir.is_dir():
                continue
            for session_dir in sorted(vehicle_dir.iterdir()):
                if session_dir.is_dir():
                    out.append(session_dir)
        return out


class SessionWriter:
    def __init__(self, directory: Path, meta: SessionMetadata) -> None:
        self.dir = directory
        self.meta = meta
        self._live = (self.dir / "live_data.jsonl").open("a", buffering=1)
        self._raw = (self.dir / "raw.log").open("a", buffering=1)
        self._sample_count = meta.sample_count
        self._save_metadata()

    # --- public api ----------------------------------------------------

    def write_sample(self, sample: LiveSample) -> None:
        self._live.write(sample.model_dump_json() + "\n")
        self._sample_count += 1

    def write_samples(self, samples: Iterable[LiveSample]) -> int:
        n = 0
        for s in samples:
            self.write_sample(s)
            n += 1
        return n

    def write_dtcs(self, dtcs: list[DTC]) -> None:
        (self.dir / "dtcs.json").write_text(
            json.dumps([d.model_dump() for d in dtcs], indent=2)
        )

    def write_monitors(self, monitors: list[Monitor]) -> None:
        (self.dir / "monitors.json").write_text(
            json.dumps([m.model_dump() for m in monitors], indent=2)
        )

    def write_freeze_frame(self, ff: FreezeFrame | None) -> None:
        if ff is None:
            return
        (self.dir / "freeze_frame.json").write_text(
            json.dumps(ff.model_dump(), indent=2)
        )

    def write_raw(self, line: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self._raw.write(f"{ts} {line}\n")

    def close(self) -> None:
        self.meta.ended_at = datetime.now(timezone.utc)
        self.meta.sample_count = self._sample_count
        self._save_metadata()
        try:
            self._live.close()
        finally:
            self._raw.close()

    # --- exporters -----------------------------------------------------

    def export_csv(self) -> Path:
        out = self.dir / "live_data.csv"
        with (self.dir / "live_data.jsonl").open() as src, out.open("w", newline="") as dst:
            writer = csv.writer(dst)
            writer.writerow(["ts", "pid", "name", "value", "unit"])
            for line in src:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                writer.writerow([obj.get("ts"), obj.get("pid"), obj.get("name"),
                                  obj.get("value"), obj.get("unit")])
        return out

    def export_json(self) -> Path:
        out = self.dir / "session.json"
        bundle = {
            "metadata": self.meta.model_dump(mode="json"),
            "live_data": [],
            "dtcs": [],
            "monitors": [],
            "freeze_frame": None,
        }
        live = self.dir / "live_data.jsonl"
        if live.exists():
            with live.open() as fh:
                bundle["live_data"] = [json.loads(line) for line in fh if line.strip()]
        for key, fname in (("dtcs", "dtcs.json"), ("monitors", "monitors.json"),
                            ("freeze_frame", "freeze_frame.json")):
            p = self.dir / fname
            if p.exists():
                bundle[key] = json.loads(p.read_text())
        out.write_text(json.dumps(bundle, indent=2, default=str))
        return out

    # --- internal ------------------------------------------------------

    def _save_metadata(self) -> None:
        (self.dir / "metadata.json").write_text(
            self.meta.model_dump_json(indent=2)
        )
