"""
v0.5.0 — Tests for the live-data time-series replay engine.

Verify that:
- Sample lists in either compact or LiveSample form are normalised correctly
- Out-of-order samples get sorted
- Multiple input timestamp shapes (float seconds, ISO strings) are accepted
- Empty timelines are a clean no-op
- The deterministic `step(current_time)` API walks samples in order
- The threaded `start()/stop()` lifecycle mutates state and stops cleanly
- Loop vs non-loop behaviour
- ScenarioState integration: scenarios with `live_timeseries` propagate
  cleanly through `scenario_to_state` and the engine
"""

from __future__ import annotations

import threading
import time

from uacj_obd.simulator.can_runtime import scenario_to_state
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.replay_engine import (
    ReplayEngine,
    TimedSample,
    _normalise_samples,
)


# ---------------------------------------------------------------------------
# _normalise_samples
# ---------------------------------------------------------------------------

def test_normalise_compact_form() -> None:
    samples = [
        {"t": 0.0, "pid": "010C", "value": 750},
        {"t": 0.5, "pid": "010C", "value": 800},
        {"t": 1.0, "pid": "010C", "value": 900},
    ]
    out = _normalise_samples(samples)
    assert len(out) == 3
    assert out[0] == TimedSample(0.0, "010C", 750)
    assert out[2] == TimedSample(1.0, "010C", 900)


def test_normalise_livesample_form_with_iso_timestamps() -> None:
    samples = [
        {"ts": "2026-06-18T10:00:00Z", "pid": "010C", "name": "RPM", "value": 750},
        {"ts": "2026-06-18T10:00:01Z", "pid": "010C", "name": "RPM", "value": 850},
    ]
    out = _normalise_samples(samples)
    assert len(out) == 2
    assert out[0].t_offset == 0.0
    assert out[1].t_offset == 1.0
    assert out[0].value == 750
    assert out[1].value == 850


def test_normalise_sorts_out_of_order_samples() -> None:
    samples = [
        {"t": 1.0, "pid": "010C", "value": 900},
        {"t": 0.0, "pid": "010C", "value": 750},
        {"t": 0.5, "pid": "010C", "value": 800},
    ]
    out = _normalise_samples(samples)
    assert [s.t_offset for s in out] == [0.0, 0.5, 1.0]


def test_normalise_lowercase_pid_becomes_uppercase() -> None:
    out = _normalise_samples([{"t": 0.0, "pid": "010c", "value": 750}])
    assert out[0].pid_key == "010C"


def test_normalise_drops_invalid_entries() -> None:
    samples = [
        {"t": 0.0, "pid": "010C", "value": 750},
        {"t": 0.5, "value": 800},  # missing pid
        {"pid": "010D", "value": 0},  # missing timestamp
        "not a dict",  # wrong type
        {"t": 1.0, "pid": "010C", "value": None},  # missing value
    ]
    out = _normalise_samples(samples)
    assert len(out) == 1


def test_normalise_empty_returns_empty_list() -> None:
    assert _normalise_samples([]) == []
    assert _normalise_samples([{}]) == []


# ---------------------------------------------------------------------------
# ReplayEngine.step() — deterministic, no threads
# ---------------------------------------------------------------------------

def _state_and_engine(samples: list[TimedSample], loop: bool = True) -> tuple[ScenarioState, ReplayEngine]:
    state = ScenarioState()
    engine = ReplayEngine(state=state, samples=samples, loop=loop)
    return state, engine


def test_step_applies_no_samples_before_first_timestamp() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.5, "010C", 1000),
    ])
    applied = engine.step(0.0)
    assert applied == 0
    assert "010C" not in state.live


def test_step_applies_first_sample_when_time_reaches_offset() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.5, "010C", 1000),
    ])
    applied = engine.step(0.5)
    assert applied == 1
    assert state.live["010C"] == 1000


def test_step_applies_only_due_samples_in_one_call() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(0.5, "010C", 1000),
        TimedSample(1.0, "010C", 1500),
    ])
    applied = engine.step(0.7)
    assert applied == 2
    assert state.live["010C"] == 1000


def test_step_walks_multiple_pids() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(0.0, "010D", 0),
        TimedSample(0.0, "0105", 88),
    ])
    engine.step(0.0)
    assert state.live["010C"] == 750
    assert state.live["010D"] == 0
    assert state.live["0105"] == 88


# ---------------------------------------------------------------------------
# ReplayEngine threaded lifecycle
# ---------------------------------------------------------------------------

def test_start_stop_clean_lifecycle() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(0.01, "010C", 1000),
    ], loop=False)
    engine.start()
    # Give the thread a moment to run.
    time.sleep(0.1)
    engine.stop()
    assert not engine.is_running
    # The final value should be the last sample.
    assert state.live["010C"] == 1000


def test_empty_timeline_does_not_start_thread() -> None:
    state, engine = _state_and_engine([])
    engine.start()
    assert not engine.is_running


def test_start_twice_is_idempotent() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(10.0, "010C", 1000),  # long delay to avoid completion
    ], loop=False)
    engine.start()
    first_thread = engine._thread
    engine.start()  # second call should not spawn another thread
    assert engine._thread is first_thread
    engine.stop()


def test_non_loop_finishes_after_one_pass() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(0.05, "010C", 1000),
    ], loop=False)
    engine.start()
    time.sleep(0.15)  # generous so the loop definitely finishes
    assert not engine.is_running
    assert engine.iterations == 1


def test_loop_restarts_from_beginning() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(0.02, "010C", 1000),
    ], loop=True)
    engine.start()
    time.sleep(0.1)  # enough time for several full passes
    engine.stop()
    # Iteration count should be >= 1 (engine completed at least one full pass)
    assert engine.iterations >= 1
    assert engine.samples_applied >= 4  # 2 samples * >=2 iterations


def test_stop_during_long_wait_returns_promptly() -> None:
    state, engine = _state_and_engine([
        TimedSample(0.0, "010C", 750),
        TimedSample(10.0, "010C", 1000),  # long wait
    ], loop=False)
    engine.start()
    time.sleep(0.05)  # let it apply first sample and start waiting
    t0 = time.monotonic()
    engine.stop()
    elapsed = time.monotonic() - t0
    # stop() should return well under the 10-second wait
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# scenario_to_state integration
# ---------------------------------------------------------------------------

def test_scenario_to_state_propagates_live_timeseries() -> None:
    payload = {
        "vehicle": {"vin": "TEST1234567890123"},
        "live_timeseries": [
            {"t": 0.0, "pid": "010C", "value": 750},
            {"t": 0.5, "pid": "010C", "value": 1500},
        ],
        "live_timeseries_loop": False,
    }
    state = scenario_to_state(payload)
    assert len(state.live_timeseries) == 2
    assert state.live_timeseries_loop is False


def test_scenario_to_state_no_timeseries_yields_empty_list() -> None:
    payload = {"vehicle": {"vin": "TEST1234567890123"}}
    state = scenario_to_state(payload)
    assert state.live_timeseries == []
    # Default loop should be True (matches the ReplayEngine default)
    assert state.live_timeseries_loop is True


def test_scenario_with_timeseries_and_ecu_query_returns_replayed_value() -> None:
    """End-to-end: load a scenario with a timeseries, start a replay,
    query RPM through the ECU — should return the most recently applied
    value, not the static default."""
    payload = {
        "vehicle": {"vin": "TEST1234567890123"},
        "live_timeseries": [
            {"t": 0.0, "pid": "010C", "value": 750},
            {"t": 0.05, "pid": "010C", "value": 2000},
            {"t": 0.1, "pid": "010C", "value": 3000},
        ],
        "live_timeseries_loop": False,
    }
    state = scenario_to_state(payload)
    engine = ReplayEngine(state=state, samples=state.live_timeseries, loop=False)
    engine.start()
    time.sleep(0.2)  # let the timeline complete
    engine.stop()

    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x0C]))
    assert resp[0] == 0x41
    assert resp[1] == 0x0C
    raw = (resp[2] << 8) | resp[3]
    rpm = raw / 4
    assert rpm == 3000  # the last sample


def test_replay_does_not_touch_pids_not_in_timeseries() -> None:
    """Static `live_overrides` for PIDs NOT in the time-series should
    survive the replay running. This means instructors can use replay
    for moving values + overrides for fixed teaching states."""
    payload = {
        "vehicle": {"vin": "TEST1234567890123"},
        "live_overrides": {"0105": 88},  # static coolant temp
        "live_timeseries": [
            {"t": 0.0, "pid": "010C", "value": 750},  # only RPM in timeline
            {"t": 0.05, "pid": "010C", "value": 1500},
        ],
        "live_timeseries_loop": False,
    }
    state = scenario_to_state(payload)
    # Coolant should still be in state.live before any replay
    assert state.live["0105"] == 88
    engine = ReplayEngine(state=state, samples=state.live_timeseries, loop=False)
    engine.start()
    time.sleep(0.15)
    engine.stop()
    # Coolant still untouched after replay
    assert state.live["0105"] == 88
    # RPM is the last replayed value
    assert state.live["010C"] == 1500
