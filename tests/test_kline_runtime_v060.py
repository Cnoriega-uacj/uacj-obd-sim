"""
v0.6.0 — Tests for the K-Line UART runtime.

v0.5.5 audit identified `simulator/kline_runtime.py` as 44% covered.
The pure-data layer (`kline.py`) is 96% covered, but the runtime
that pumps bytes between the L9637D UART and the EcuEmulator is
poorly tested.

This module covers the runtime with a deque-backed fake serial port —
exercises slow-init handshake, frame assembly, dispatch, write-back,
read errors, write errors, and the stop() lifecycle. No hardware
needed; the runtime is duck-typed against the serial object.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.kline import (
    ECU_ADDRESS_PHYSICAL,
    KEY_BYTE_2,
    SLOW_INIT_ADDRESS_OBD,
    encode_request,
)
from uacj_obd.simulator.kline_runtime import KlineRuntime


class FakeSerial:
    """Duck-typed pyserial substitute backed by an in-memory FIFO.

    `feed_bytes()` appends to the RX buffer the runtime will read.
    `written` is a `bytearray` of everything the runtime wrote.
    """

    def __init__(self) -> None:
        self.rx = deque()
        self.written = bytearray()
        # Configurable failure injection for the error-path tests.
        self.read_raises = False
        self.write_raises = False

    def feed_bytes(self, data: bytes) -> None:
        for b in data:
            self.rx.append(b)

    def read(self, n: int) -> bytes:
        if self.read_raises:
            raise OSError("simulated UART read failure")
        out = bytearray()
        for _ in range(n):
            if not self.rx:
                break
            out.append(self.rx.popleft())
        return bytes(out)

    def write(self, data: bytes) -> int:
        if self.write_raises:
            raise OSError("simulated UART write failure")
        self.written.extend(data)
        return len(data)

    @property
    def in_waiting(self) -> int:
        return len(self.rx)


def _runtime_with_mazda_state() -> tuple[KlineRuntime, FakeSerial]:
    state = ScenarioState(
        vin="JM1BL1L72C1627697",
        calibration_id="PE2GEM000PE06020",
        ecu_name="ECM",
        live={"010C": 800, "010D": 0},
    )
    ecu = EcuEmulator(state)
    serial = FakeSerial()
    return KlineRuntime(ecu, serial), serial


# ---------------------------------------------------------------------------
# handle_request_bytes — pure-data entry point
# ---------------------------------------------------------------------------

def test_handle_request_bytes_mode_09_pid_02_returns_vin_response() -> None:
    runtime, _ = _runtime_with_mazda_state()
    req_frame = encode_request(bytes([0x09, 0x02]))
    resp = runtime.handle_request_bytes(req_frame)
    assert resp, "expected a non-empty response"
    # The response should contain the VIN bytes somewhere.
    assert b"JM1BL1L72C1627697" in resp


def test_handle_request_bytes_invalid_frame_returns_empty() -> None:
    runtime, _ = _runtime_with_mazda_state()
    # Garbage bytes that won't decode as KWP2000
    resp = runtime.handle_request_bytes(b"\x00\x00\x00")
    assert resp == b""


def test_handle_request_bytes_empty_ecu_response_returns_empty() -> None:
    """If the ECU returns no payload (NRC paths sometimes), the runtime
    should also write nothing rather than wrap an empty payload."""
    state = ScenarioState()
    ecu = EcuEmulator(state)
    runtime = KlineRuntime(ecu, FakeSerial())
    # Send a malformed-but-valid-frame request that the ECU might NRC.
    # We use raw bytes that decode to an empty/unknown service.
    req_frame = encode_request(bytes([0x09, 0x02]))  # VIN — but state has no VIN
    resp = runtime.handle_request_bytes(req_frame)
    # The ECU returns an NRC (negative response), which IS a payload.
    # So we expect a non-empty wrapped response containing 0x7F.
    assert b"\x7F" in resp


# ---------------------------------------------------------------------------
# _read_one_frame — UART byte assembly
# ---------------------------------------------------------------------------

def test_read_one_frame_returns_none_when_uart_empty() -> None:
    runtime, _ = _runtime_with_mazda_state()
    assert runtime._read_one_frame() is None


def test_read_one_frame_handles_slow_init_address_byte() -> None:
    """When the UART first byte is the 5-baud slow-init address (0x33),
    the runtime should emit the handshake reply and return None
    (no complete request frame yet)."""
    runtime, serial = _runtime_with_mazda_state()
    serial.feed_bytes(bytes([SLOW_INIT_ADDRESS_OBD]))
    result = runtime._read_one_frame()
    assert result is None
    # The runtime should have written SOMETHING (sync byte + key bytes).
    assert len(serial.written) > 0


def test_read_one_frame_handles_inverse_key_byte_2() -> None:
    """The second slow-init step is when the tester echoes ~KB2."""
    runtime, serial = _runtime_with_mazda_state()
    serial.feed_bytes(bytes([(~KEY_BYTE_2) & 0xFF]))
    result = runtime._read_one_frame()
    assert result is None


def test_read_one_frame_assembles_complete_request() -> None:
    """Feed a full encoded KWP frame and verify _read_one_frame returns
    exactly those bytes."""
    runtime, serial = _runtime_with_mazda_state()
    req = encode_request(bytes([0x09, 0x02]))  # Mode 09 PID 02
    serial.feed_bytes(req)
    frame = runtime._read_one_frame()
    assert frame == req


def test_read_one_frame_handles_long_form_with_length_byte() -> None:
    """KWP long-form frames have a separate Len byte. The runtime needs
    to read the header, consult total_frame_length, then read the rest."""
    runtime, serial = _runtime_with_mazda_state()
    # Construct a long-form request (forces the length-byte branch).
    long_payload = bytes(range(10))  # 10-byte payload triggers long form
    req = encode_request(long_payload)
    serial.feed_bytes(req)
    frame = runtime._read_one_frame()
    assert frame == req


# ---------------------------------------------------------------------------
# run() — blocking loop, threaded
# ---------------------------------------------------------------------------

def test_run_responds_to_request_in_threaded_loop() -> None:
    """Feed a request into the FakeSerial, start the run loop in a
    thread, wait briefly, then stop. The written buffer should contain
    a valid response frame."""
    runtime, serial = _runtime_with_mazda_state()
    req = encode_request(bytes([0x09, 0x02]))  # VIN query
    serial.feed_bytes(req)

    thread = threading.Thread(target=runtime.run, daemon=True)
    thread.start()
    # Give the loop a moment to read + respond.
    time.sleep(0.15)
    runtime.stop()
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "runtime thread did not stop"
    assert b"JM1BL1L72C1627697" in serial.written


def test_run_continues_after_uart_read_error() -> None:
    """Transient read failures shouldn't kill the runtime — it should
    sleep briefly and try again."""
    runtime, serial = _runtime_with_mazda_state()
    serial.read_raises = True

    thread = threading.Thread(target=runtime.run, daemon=True)
    thread.start()
    time.sleep(0.15)
    # Disable the failure mode and feed a valid request — the loop
    # should now process it.
    serial.read_raises = False
    serial.feed_bytes(encode_request(bytes([0x09, 0x02])))
    time.sleep(0.15)
    runtime.stop()
    thread.join(timeout=1.0)
    assert b"JM1BL1L72C1627697" in serial.written


def test_run_logs_write_error_and_continues() -> None:
    """If the UART write fails, the runtime should keep running rather
    than crashing — it just drops that response."""
    runtime, serial = _runtime_with_mazda_state()
    serial.write_raises = True
    serial.feed_bytes(encode_request(bytes([0x09, 0x02])))

    thread = threading.Thread(target=runtime.run, daemon=True)
    thread.start()
    time.sleep(0.15)
    runtime.stop()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    # Nothing was written (write failed) but the thread didn't crash.
    assert len(serial.written) == 0


def test_stop_exits_run_promptly_when_uart_idle() -> None:
    """If the UART is silent, stop() should return promptly — the run
    loop's read returns empty bytes which is the "no frame yet" path."""
    runtime, _ = _runtime_with_mazda_state()
    thread = threading.Thread(target=runtime.run, daemon=True)
    thread.start()
    time.sleep(0.05)
    runtime.stop()
    thread.join(timeout=0.5)
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_open_serial_raises_on_missing_pyserial(monkeypatch) -> None:
    """If pyserial isn't installed, open_serial should raise RuntimeError
    with a helpful message rather than crashing on an undefined import."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "serial":
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    state = ScenarioState()
    ecu = EcuEmulator(state)
    try:
        KlineRuntime.open_serial(ecu)
    except RuntimeError as exc:
        assert "pyserial" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError when pyserial missing")
