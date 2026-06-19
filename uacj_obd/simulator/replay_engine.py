"""
v0.5.0 — Live-data time-series replay engine.

When a captured session is pushed as a scenario, the time-series of
recorded `(timestamp, pid, value)` samples can ride along in the
payload. The `ReplayEngine` then mutates `ScenarioState.live` at the
recorded cadence so a scan tool querying the simulator sees RPM
bouncing, speed rising and falling, MAF moving — exactly like the
real car was doing during capture. When the timeline runs out the
engine optionally loops back to the start (for classroom demos).

Without `live_timeseries` in the scenario payload the engine simply
isn't started, and the simulator keeps the static `live_overrides`
behaviour from v0.4.x. So this is a purely additive feature — no
existing scenario shape breaks.

The engine is purposely separate from the ECU emulator: the ECU is
stateless w.r.t. I/O and reads from `state.live` for every dispatch.
The engine is the only producer mutating that dict. Single-key writes
+ single-key reads under the GIL are atomic, so no lock is needed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimedSample:
    """One (t_offset, pid_key, value) point on the replay timeline.

    `t_offset` is seconds from the start of the timeline (the timeline
    is always normalised so the first sample is at t=0). `pid_key` is
    an uppercase OBD-II key like ``"010C"``. `value` is the raw value
    the original adapter recorded — units stay implicit (same as the
    static `live_overrides` shape).
    """

    t_offset: float
    pid_key: str
    value: float | int | str


def _normalise_samples(samples: Iterable[dict]) -> list[TimedSample]:
    """Convert a list of `live_data.jsonl`-shape dicts into normalised
    `TimedSample` records. Accepts both the wire-friendly compact form
    ``{"t": 0.5, "pid": "010C", "value": 1500}`` and the LiveSample
    dump-shape that includes `name` / `unit` (those fields are dropped
    here — the replay only needs t/pid/value)."""
    out: list[TimedSample] = []
    raw: list[tuple[float, str, float | int | str]] = []
    for entry in samples:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid") or entry.get("PID")
        if not pid:
            continue
        # Accept either compact `t` or the LiveSample-style `ts` (ISO
        # string or float seconds-since-epoch).
        t = entry.get("t")
        if t is None:
            ts = entry.get("ts")
            if isinstance(ts, (int, float)):
                t = float(ts)
            elif isinstance(ts, str):
                # Best-effort: parse ISO timestamps to a float
                try:
                    from datetime import datetime
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
        if t is None:
            continue
        value = entry.get("value")
        if value is None:
            continue
        raw.append((float(t), str(pid).upper(), value))
    if not raw:
        return out
    # Sort by timestamp and normalise so the first sample is at t=0.
    raw.sort(key=lambda r: r[0])
    t0 = raw[0][0]
    for t, pid, value in raw:
        out.append(TimedSample(t_offset=t - t0, pid_key=pid, value=value))
    return out


class ReplayEngine:
    """Background thread that mutates `state.live` according to a
    captured time-series. Stops cleanly via `stop()`.

    The engine is exposed as a class rather than a free function so
    the simulator server can swap engines when a new scenario is
    loaded (stop the old one, start the new one). A scenario without
    a `live_timeseries` field results in an empty timeline and the
    engine never starts a thread.
    """

    def __init__(
        self,
        state,  # ScenarioState, not annotated to avoid circular import
        samples: list[TimedSample],
        loop: bool = True,
        time_fn=time.monotonic,
        sleep_fn=None,
    ) -> None:
        self.state = state
        self._timeline = list(samples)
        self.loop = loop
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn  # injected for tests; None → use self._stop.wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Diagnostic counters useful in tests and operator logs.
        self.iterations = 0          # number of full loops completed
        self.samples_applied = 0     # total samples mutated into state

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def duration_seconds(self) -> float:
        """Length of one loop through the timeline (0.0 if empty)."""
        if not self._timeline:
            return 0.0
        return self._timeline[-1].t_offset

    def start(self) -> None:
        """Launch the replay thread. Safe no-op for empty timelines."""
        if not self._timeline:
            log.debug("ReplayEngine.start: empty timeline, nothing to do")
            return
        if self.is_running:
            log.debug("ReplayEngine.start: already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="uacj-replay", daemon=True
        )
        self._thread.start()
        log.info(
            "ReplayEngine started: %d samples, %.1fs loop, loop=%s",
            len(self._timeline), self.duration_seconds, self.loop,
        )

    def stop(self, timeout: float = 2.0) -> None:
        """Signal stop and wait for the thread to exit."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    # ---- step API for tests --------------------------------------------

    def step(self, current_time: float) -> int:
        """Apply every sample whose t_offset <= current_time but greater
        than the last step's current_time. Returns count applied.

        Used by deterministic tests to walk the engine forward without
        threads or sleeps. Production code calls `start()` / `stop()`
        instead.
        """
        # Walk the timeline from where we left off; mutate state.live
        # and bump the counter. This is a simple linear scan rather
        # than a binary search because the timelines are short enough
        # that the overhead is negligible.
        applied = 0
        already = self.samples_applied % len(self._timeline) if self._timeline else 0
        for sample in self._timeline[already:]:
            if sample.t_offset > current_time:
                break
            self.state.live[sample.pid_key] = sample.value
            self.samples_applied += 1
            applied += 1
        return applied

    # ---- thread body ---------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                start_t = self._time_fn()
                for sample in self._timeline:
                    if self._stop.is_set():
                        return
                    target = start_t + sample.t_offset
                    wait = target - self._time_fn()
                    if wait > 0:
                        # `Event.wait` returns True if the event was set
                        # during the wait — stop promptly in that case.
                        if self._stop.wait(wait):
                            return
                    self.state.live[sample.pid_key] = sample.value
                    self.samples_applied += 1
                self.iterations += 1
                if not self.loop:
                    log.info("ReplayEngine finished one pass; loop=False")
                    return
        except Exception:
            log.exception("ReplayEngine crashed; halting replay")
