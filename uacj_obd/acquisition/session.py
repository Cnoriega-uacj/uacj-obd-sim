from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from uacj_obd.adapters import Adapter, AdapterError, ConnectionState
from uacj_obd.models import (
    LiveSample,
    SessionMetadata,
    VehicleInfo,
)
from uacj_obd.pids import PidRegistry
from uacj_obd.storage import Database, SessionStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionConfig:
    pids: list[str] = field(default_factory=lambda: [
        "010C", "010D", "0105", "010F", "0110", "0111", "0104",
        "0106", "0107", "010B", "0114", "012F",
    ])
    sample_interval_s: float = 0.1
    max_reconnects: int = 10
    notes: str = ""


class AcquisitionSession:
    """
    Orchestrates a full capture: vehicle info, DTCs, monitors, freeze frame,
    and a continuous live-data stream into the SessionStore.

    Resilient to adapter drop: on read failure it backs off and reconnects
    up to `max_reconnects` times, falling through to the flat-file raw log
    so no captured data is lost.
    """

    def __init__(
        self,
        adapter: Adapter,
        store: SessionStore,
        db: Database,
        pid_registry: PidRegistry | None = None,
        config: SessionConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.db = db
        self.pid_registry = pid_registry
        self.config = config or SessionConfig()
        self._stop = threading.Event()
        self._writer = None
        self.meta: SessionMetadata | None = None

    def _new_session_id(self) -> str:
        return _now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]

    def _capture_static(self, writer) -> None:
        """One-shot reads done at session start: VIN, DTCs, monitors, freeze frame."""
        try:
            dtcs = self.adapter.read_dtcs()
            writer.write_dtcs(dtcs)
        except AdapterError as exc:
            log.warning("DTC read failed: %s", exc)

        try:
            monitors = self.adapter.read_monitors()
            writer.write_monitors(monitors)
        except AdapterError as exc:
            log.warning("monitor read failed: %s", exc)

        try:
            ff = self.adapter.read_freeze_frame()
            writer.write_freeze_frame(ff)
        except AdapterError as exc:
            log.warning("freeze frame read failed: %s", exc)

    def _connect(self) -> VehicleInfo:
        status = self.adapter.connect()
        if status.state != ConnectionState.CONNECTED:
            raise AdapterError(f"adapter not connected: {status}")
        return self.adapter.read_vehicle_info()

    def start(self) -> SessionMetadata:
        vehicle = self._connect()
        status = self.adapter.status()
        meta = SessionMetadata(
            session_id=self._new_session_id(),
            started_at=_now(),
            protocol=status.protocol,
            adapter=status.adapter_name,
            vehicle=vehicle,
            notes=self.config.notes,
        )
        self.meta = meta
        writer = self.store.open_session(meta)
        self._writer = writer
        ts = _now().isoformat()
        if vehicle.vin:
            self.db.upsert_vehicle(vehicle.vin, vehicle.make, vehicle.model, vehicle.year, ts)
        self.db.insert_session(
            session_id=meta.session_id,
            vin=vehicle.vin,
            started_at=meta.started_at.isoformat(),
            ended_at=None,
            protocol=meta.protocol.value,
            adapter=meta.adapter,
            sample_count=0,
            folder=str(writer.dir),
            notes=meta.notes,
        )
        self._capture_static(writer)
        return meta

    def run(self, duration_s: float | None = None) -> int:
        """Run the live-data stream synchronously. Returns sample count."""
        if self._writer is None or self.meta is None:
            raise RuntimeError("call start() first")
        end = time.monotonic() + duration_s if duration_s else None
        reconnects = 0
        sample_count = 0
        pids = list(self.config.pids)
        while not self._stop.is_set():
            try:
                for pid in pids:
                    if self._stop.is_set():
                        break
                    sample = self.adapter.read_pid(pid)
                    if sample is not None:
                        self._writer.write_sample(sample)
                        sample_count += 1
                if end is not None and time.monotonic() >= end:
                    break
                time.sleep(self.config.sample_interval_s)
            except AdapterError as exc:
                self._writer.write_raw(f"adapter error: {exc}")
                reconnects += 1
                if reconnects > self.config.max_reconnects:
                    log.error("max reconnects exceeded; ending session")
                    break
                backoff = min(2 ** reconnects, 30)
                log.warning("adapter dropped, reconnecting in %ss (attempt %d/%d)",
                             backoff, reconnects, self.config.max_reconnects)
                time.sleep(backoff)
                try:
                    self.adapter.connect()
                except AdapterError as exc2:
                    self._writer.write_raw(f"reconnect failed: {exc2}")
        return sample_count

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> Path:
        if self._writer is None or self.meta is None:
            raise RuntimeError("session not started")
        self._writer.close()
        self.db.update_session(
            self.meta.session_id,
            ended_at=self._writer.meta.ended_at.isoformat() if self._writer.meta.ended_at else None,
            sample_count=self._writer.meta.sample_count,
        )
        try:
            self.adapter.disconnect()
        except Exception:
            pass
        return self._writer.dir
