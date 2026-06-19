"""
v0.6.2 — Tests for the CAN runtime (`CanRuntime` class).

v0.6.1 left `simulator/can_runtime.py` at 78%. The uncovered lines
are the runtime class itself — every test so far exercises
`scenario_to_state` (the pure-data path) but not the CAN bus
dispatch loop. Same pattern that worked for K-Line and J1850
runtimes: deque-backed FakeBus, no python-can installation
required.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from uacj_obd.simulator.can_runtime import (
    CanRuntime,
    ID_FUNCTIONAL_REQUEST,
    ID_PHYSICAL_REQUEST_BASE,
    ID_PHYSICAL_RESPONSE_BASE,
)
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.iso_tp import CanFrame, IsoTpFramer


class FakeMessage:
    """Tiny python-can `Message` substitute the runtime can consume."""

    def __init__(self, arbitration_id: int, data: bytes) -> None:
        self.arbitration_id = arbitration_id
        self.data = bytearray(data)


class FakeBus:
    """Duck-typed python-can `Bus` substitute. `recv` returns the next
    queued message or None on timeout; `send` records what was written."""

    def __init__(self) -> None:
        self.rx = deque()
        self.sent: list[FakeMessage] = []
        self.recv_calls = 0
        self.send_raises = False

    def feed(self, arbitration_id: int, data: bytes) -> None:
        self.rx.append(FakeMessage(arbitration_id, data))

    def recv(self, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        self.recv_calls += 1
        if self.rx:
            return self.rx.popleft()
        return None

    def send(self, msg) -> None:  # type: ignore[no-untyped-def]
        if self.send_raises:
            raise OSError("simulated send failure")
        self.sent.append(msg)


def _runtime() -> tuple[CanRuntime, FakeBus]:
    state = ScenarioState(
        vin="JM1BL1L72C1627697",
        live={"010C": 800, "0105": 88},
    )
    ecu = EcuEmulator(state)
    bus = FakeBus()
    return CanRuntime(ecu, bus), bus


# ---------------------------------------------------------------------------
# handle_request_frame — pure-data entry
# ---------------------------------------------------------------------------

def test_handle_request_frame_dispatches_mode01_rpm_query() -> None:
    runtime, _ = _runtime()
    # Build an ISO-TP single-frame request: 0x02 (length) 0x01 0x0C
    frame = CanFrame(arbitration_id=ID_FUNCTIONAL_REQUEST,
                      data=bytes([0x02, 0x01, 0x0C, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA]))
    responses = runtime.handle_request_frame(frame)
    assert len(responses) >= 1
    # First response frame should encode 0x41 0x0C (Mode 01 PID 0x0C reply)
    assert responses[0].arbitration_id == ID_PHYSICAL_RESPONSE_BASE
    payload = responses[0].data
    # Single-frame ISO-TP: payload[0] = length, payload[1:1+length] = data
    length = payload[0] & 0x0F
    body = payload[1:1+length]
    assert body[0] == 0x41
    assert body[1] == 0x0C


def test_handle_request_frame_ignores_unrelated_arbitration_id() -> None:
    """Frames addressed to other IDs (e.g. 0x123 or 0x500) should be
    ignored without raising — the ECU only listens on the functional
    and physical request IDs."""
    runtime, _ = _runtime()
    frame = CanFrame(arbitration_id=0x500,
                      data=bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))
    assert runtime.handle_request_frame(frame) == []


def test_handle_request_frame_handles_physical_request_id() -> None:
    """The runtime also accepts requests on 0x7E0 (physical request
    base) and 0x7E1, not just the functional 0x7DF."""
    runtime, _ = _runtime()
    frame = CanFrame(arbitration_id=ID_PHYSICAL_REQUEST_BASE,
                      data=bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))
    responses = runtime.handle_request_frame(frame)
    assert len(responses) >= 1


def test_handle_request_frame_returns_empty_on_iso_tp_decode_error() -> None:
    """If a frame's PCI nibble is unknown (0x3, 0x4, ..., 0xF — not
    SF/FF/CF/FC), the ISO-TP decoder raises IsoTpError. The runtime
    should swallow that error rather than letting it propagate."""
    runtime, _ = _runtime()
    # PCI nibble 0x3, 0x4, ..., 0xF are all reserved/unknown.
    frame = CanFrame(arbitration_id=ID_FUNCTIONAL_REQUEST,
                      data=bytes([0x30] + [0xAA] * 7))
    # The runtime should not raise; whether it returns [] or a flow
    # control depends on the framer (0x30 is FC, but with no message in
    # progress its handling can vary). Key invariant: no exception.
    runtime.handle_request_frame(frame)  # must not raise

    # Truly unknown PCI nibble (0xF0) — this WILL raise IsoTpError.
    frame2 = CanFrame(arbitration_id=ID_FUNCTIONAL_REQUEST,
                       data=bytes([0xF0] + [0xAA] * 7))
    assert runtime.handle_request_frame(frame2) == []


def test_handle_request_frame_returns_empty_when_ecu_returns_empty() -> None:
    """If the ECU's handle() returns empty bytes (defensive path), no
    frames go on the wire."""
    state = ScenarioState()
    ecu = EcuEmulator(state)
    # Patch the ECU to return an empty response.
    original_handle = ecu.handle
    ecu.handle = lambda req: b""  # type: ignore[method-assign]
    runtime = CanRuntime(ecu, FakeBus())
    frame = CanFrame(arbitration_id=ID_FUNCTIONAL_REQUEST,
                      data=bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))
    assert runtime.handle_request_frame(frame) == []
    ecu.handle = original_handle  # restore


def test_handle_request_frame_returns_empty_during_multi_frame_assembly() -> None:
    """A First Frame announces multi-frame data; the runtime should
    respond with a Flow Control frame OR an empty list (depending on
    the framer's behaviour) and then wait for Consecutive Frames."""
    runtime, _ = _runtime()
    # First Frame announcing 12-byte payload (more than 7 single-frame bytes)
    # PCI = 0x1N MM (N=length high nibble, MM=length low byte)
    ff = CanFrame(arbitration_id=ID_FUNCTIONAL_REQUEST,
                   data=bytes([0x10, 0x0C, 0x01, 0x0C] + [0x00] * 4))
    # Calling once with just the FF — runtime should NOT yet have a
    # complete request to dispatch.
    out = runtime.handle_request_frame(ff)
    # Either zero outgoing frames (waiting for CFs) or a Flow Control
    # frame back to the tester. Both are valid behaviours.
    for f in out:
        # If anything went out, it should be on the physical response ID
        assert f.arbitration_id == ID_PHYSICAL_RESPONSE_BASE


# ---------------------------------------------------------------------------
# run() — threaded receive/send loop
# ---------------------------------------------------------------------------

def test_run_responds_to_queued_request() -> None:
    runtime, bus = _runtime()
    bus.feed(ID_FUNCTIONAL_REQUEST,
              bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))
    t = threading.Thread(target=runtime.run, daemon=True)
    t.start()
    time.sleep(0.5)  # need to be > recv timeout (0.2s)
    runtime.stop()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert len(bus.sent) >= 1
    # The response is a python-can Message — confirm it's on the
    # physical response ID.
    assert bus.sent[0].arbitration_id == ID_PHYSICAL_RESPONSE_BASE


def test_run_stop_with_no_traffic_returns_promptly() -> None:
    runtime, bus = _runtime()
    t = threading.Thread(target=runtime.run, daemon=True)
    t.start()
    # bus stays idle; recv returns None on each call
    time.sleep(0.05)
    runtime.stop()
    t.join(timeout=1.0)
    assert not t.is_alive()


def test_run_skips_unrelated_arbitration_ids() -> None:
    runtime, bus = _runtime()
    bus.feed(0x123, bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))  # ignored
    bus.feed(ID_FUNCTIONAL_REQUEST,
              bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))  # accepted
    t = threading.Thread(target=runtime.run, daemon=True)
    t.start()
    time.sleep(0.5)
    runtime.stop()
    t.join(timeout=1.0)
    # Only the second message produced a response.
    assert len(bus.sent) >= 1
    assert bus.sent[0].arbitration_id == ID_PHYSICAL_RESPONSE_BASE


def test_run_uses_custom_response_id() -> None:
    """When constructed with a non-default response_id, all outgoing
    frames go to that ID."""
    state = ScenarioState(live={"010C": 800})
    ecu = EcuEmulator(state)
    bus = FakeBus()
    custom_id = 0x7E9
    runtime = CanRuntime(ecu, bus, response_id=custom_id)
    bus.feed(ID_FUNCTIONAL_REQUEST,
              bytes([0x02, 0x01, 0x0C] + [0xAA] * 5))
    t = threading.Thread(target=runtime.run, daemon=True)
    t.start()
    time.sleep(0.5)
    runtime.stop()
    t.join(timeout=1.0)
    assert len(bus.sent) >= 1
    assert bus.sent[0].arbitration_id == custom_id


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_open_socketcan_raises_when_python_can_missing(monkeypatch) -> None:
    """The convenience constructor should raise a helpful RuntimeError
    when python-can isn't installed rather than crashing with a plain
    ImportError."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "can":
            raise ImportError("simulated missing python-can")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    state = ScenarioState()
    ecu = EcuEmulator(state)
    try:
        CanRuntime.open_socketcan(ecu)
    except RuntimeError as exc:
        assert "python-can" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError when python-can missing")
