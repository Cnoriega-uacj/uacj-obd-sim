from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
    # PID list to capture. Default (empty) means "ask the adapter for the
    # full set of PIDs the connected vehicle reports as supported, then
    # capture all of them" — this is what real-world classroom use wants
    # (a 2012 Mazda3 supports ~113 PIDs; the old hardcoded 14-PID default
    # was throwing away 99 of them). Provide an explicit list to capture
    # a curated subset instead.
    pids: list[str] = field(default_factory=list)
    manufacturer_pids: list[str] = field(default_factory=list)
    # Sleep BETWEEN full sweep cycles. Set to 0 to pull as fast as the
    # adapter can; >0 to pace the rate. The old default of 0.1 was
    # meaningless when capturing 100+ PIDs (a single sweep already takes
    # 5-22 s on an OBDLink SX), so v0.4.11 makes 0 the default and lets
    # the acquisition loop add a tiny sleep only when the sweep itself
    # was very fast (small PID set on a fast adapter).
    sample_interval_s: float = 0.0
    max_reconnects: int = 10
    notes: str = ""
    # Minimum wall-clock time per sweep cycle. When a sweep finishes
    # faster than this, the acquisition loop sleeps the difference so a
    # 4-PID capture against the mock doesn't burn 100% CPU. When a sweep
    # takes longer than this (e.g. 113 PIDs on a real car), no extra
    # sleep — back to back queries.
    min_cycle_seconds: float = 0.5

    # Fallback list used if the adapter cannot report supported PIDs
    # (e.g. partial connect, mock with no PID set populated). Kept as a
    # class-level constant rather than the default factory so we never
    # silently fall back to it on a real car — the live capture loop
    # logs which path it took.
    _FALLBACK_PIDS = (
        "010C", "010D", "0105", "010F", "0110", "0111", "0104",
        "0106", "0107", "010B", "0114", "012F", "0103", "011F",
    )


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

    def _read_manufacturer_pid(self, key: str) -> LiveSample | None:
        """Read a mode 0x22 manufacturer PID by key (e.g. '22115C') and decode."""
        if self.pid_registry is None:
            return None
        defn = self.pid_registry.get(key)
        if defn is None or defn.mode != 0x22:
            return None
        try:
            data = self.adapter.read_raw(defn.mode, defn.pid)
        except AdapterError:
            return None
        if not data:
            return None
        value = self.pid_registry.decode(key, data)
        if value is None:
            return None
        return LiveSample(pid=key, name=defn.name, value=value, unit=defn.unit)

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
        # PID-list resolution per v0.4.9: if the caller passed an explicit
        # list, honour it verbatim. Otherwise ask the adapter for the full
        # set of supported PIDs (typically ~50-130 on a modern car). If the
        # adapter can't enumerate (mock without a PID set, partial connect,
        # adapter error), fall back to the curated 14-PID safe list — but
        # log which path we took so a silent fallback is debuggable.
        if self.config.pids:
            pids = list(self.config.pids)
            self.meta.pid_resolution_source = "explicit"
            self.meta.discovered_pids = []
            log.info("acquisition using explicit PID list (%d PIDs)", len(pids))
        else:
            try:
                discovered = sorted(self.adapter.supported_pids())
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("supported_pids() raised %s; using fallback list", exc)
                discovered = []
            if discovered:
                pids = discovered
                self.meta.pid_resolution_source = "discovered"
                self.meta.discovered_pids = list(discovered)
                log.info("acquisition discovered %d supported PIDs from vehicle", len(pids))
            else:
                pids = list(self.config._FALLBACK_PIDS)
                self.meta.pid_resolution_source = "fallback"
                self.meta.discovered_pids = []
                log.info("acquisition using fallback PID list (%d PIDs)", len(pids))
        # v0.6.11: persist the source-of-truth before the first cycle so
        # the diagnostic is visible even if the capture crashes early.
        if self._writer is not None:
            self._writer._save_metadata()
        mfg_pids = list(self.config.manufacturer_pids)
        while not self._stop.is_set():
            cycle_start = time.monotonic()
            try:
                for pid in pids:
                    if self._stop.is_set():
                        break
                    sample = self.adapter.read_pid(pid)
                    if sample is not None:
                        self._writer.write_sample(sample)
                        sample_count += 1
                for mfg_key in mfg_pids:
                    if self._stop.is_set():
                        break
                    sample = self._read_manufacturer_pid(mfg_key)
                    if sample is not None:
                        self._writer.write_sample(sample)
                        sample_count += 1
                if end is not None and time.monotonic() >= end:
                    break
                # Adaptive cycle pacing (v0.4.11):
                # - If the explicit sample_interval_s is set, honour it.
                # - Otherwise, sleep only enough to bring the total cycle
                #   time up to `min_cycle_seconds`. On a slow real-car
                #   sweep this adds zero delay; on a fast mock sweep it
                #   keeps the loop from spinning at 100% CPU.
                elapsed = time.monotonic() - cycle_start
                if self.config.sample_interval_s > 0:
                    time.sleep(self.config.sample_interval_s)
                elif elapsed < self.config.min_cycle_seconds:
                    time.sleep(self.config.min_cycle_seconds - elapsed)
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
