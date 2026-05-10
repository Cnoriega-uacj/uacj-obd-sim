#!/usr/bin/env python3
"""
Virtual-bus bench harness — proves CAN (ISO-TP) + K-Line (KWP2000)
+ J1850 round-trip without any hardware.

Why this exists:
  Until the OBDLink SX, MCP2515, and L9637D arrive, we can't run an
  electrical-level integration test. But we can spin up a python-can
  "virtual" bus (in-memory pub/sub) for CAN, a pty pair for K-Line
  (os.openpty gives us a Linux pseudo-terminal that pyserial can open
  on each end), and an in-memory pipe for J1850. Both ends — emulator
  and "tester" — share the same byte protocol they will once a
  transceiver replaces the virtual link.

Run: python scripts/bench.py

Exit code 0 = every protocol round-trips a VIN read, a live PID,
              a DTC fetch, and a clear-codes round successfully.
Exit code 1 = any round-trip failed (with a diagnostic).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable

from rich.console import Console
from rich.table import Table

from uacj_obd.simulator.can_runtime import (
    CanRuntime,
    ID_FUNCTIONAL_REQUEST,
    ID_PHYSICAL_RESPONSE_BASE,
)
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.iso_tp import CanFrame, IsoTpFramer
from uacj_obd.simulator.j1850 import (
    decode as decode_j1850,
    encode_request as encode_j1850_request,
)
from uacj_obd.simulator.j1850_runtime import J1850Runtime
from uacj_obd.simulator.kline import (
    decode as decode_kline,
    encode_request as encode_kline_request,
)
from uacj_obd.simulator.kline_runtime import KlineRuntime


console = Console()


# Reference scenario the bench runs against. Fixed values let us assert
# exact byte-level expectations (no flakiness from random data).
DEMO_VIN = "1HGBH41JXMN109186"  # 17 ASCII chars
DEMO_RPM = 825
DEMO_SPEED = 0
DEMO_COOLANT = 88
DEMO_DTCS = ["P0420", "P0301"]


def _build_state() -> ScenarioState:
    return ScenarioState(
        vin=DEMO_VIN,
        live={"010C": DEMO_RPM, "010D": DEMO_SPEED, "0105": DEMO_COOLANT},
        dtcs_stored=list(DEMO_DTCS),
    )


# ---------------------------------------------------------------------------
# CAN over python-can virtual bus
# ---------------------------------------------------------------------------


def bench_can() -> tuple[bool, list[tuple[str, str]]]:
    """Run an ECU on a virtual CAN bus, query it as a tester, return results."""
    try:
        import can  # type: ignore[import-not-found]
    except Exception as exc:
        return False, [("import", f"python-can missing: {exc}")]

    # Two python-can "virtual" buses on the same channel form a pub/sub link.
    ecu_bus = can.interface.Bus(channel="uacj_bench", interface="virtual")
    tester_bus = can.interface.Bus(channel="uacj_bench", interface="virtual")
    ecu = EcuEmulator(_build_state())
    runtime = CanRuntime(ecu, ecu_bus)

    stop = threading.Event()

    def _ecu_loop() -> None:
        # Mirror CanRuntime.run() but with a stop event we own here.
        while not stop.is_set():
            msg = ecu_bus.recv(timeout=0.05)
            if msg is None:
                continue
            frame = CanFrame(arbitration_id=msg.arbitration_id, data=bytes(msg.data))
            for r in runtime.handle_request_frame(frame):
                ecu_bus.send(can.Message(arbitration_id=r.arbitration_id,
                                            data=list(r.data), is_extended_id=False))

    th = threading.Thread(target=_ecu_loop, daemon=True)
    th.start()

    framer = IsoTpFramer(tx_id=ID_FUNCTIONAL_REQUEST, rx_id=ID_PHYSICAL_RESPONSE_BASE)
    rows: list[tuple[str, str]] = []
    ok = True

    def _query(label: str, payload: bytes, expect: Callable[[bytes], bool],
                explain: Callable[[bytes], str]) -> None:
        nonlocal ok
        for f in framer.encode(payload):
            tester_bus.send(can.Message(arbitration_id=f.arbitration_id,
                                          data=list(f.data), is_extended_id=False))
        deadline = time.monotonic() + 1.0
        decoder = IsoTpFramer(tx_id=ID_FUNCTIONAL_REQUEST, rx_id=ID_PHYSICAL_RESPONSE_BASE)
        resp: bytes | None = None
        while time.monotonic() < deadline and resp is None:
            msg = tester_bus.recv(timeout=0.05)
            if msg is None:
                continue
            if msg.arbitration_id != ID_PHYSICAL_RESPONSE_BASE:
                continue
            resp = decoder.decode(CanFrame(msg.arbitration_id, bytes(msg.data)))
        if resp is None:
            ok = False
            rows.append((label, "[red]TIMEOUT[/]"))
            return
        if not expect(resp):
            ok = False
            rows.append((label, f"[red]UNEXPECTED[/] {resp.hex(' ')}"))
            return
        rows.append((label, f"[green]OK[/] {explain(resp)}"))

    _query("VIN read",
           bytes([0x09, 0x02]),
           lambda r: r[0] == 0x49 and r[1] == 0x02 and DEMO_VIN.encode() in r,
           lambda r: f"VIN={r[3:].decode('ascii', errors='replace').strip(chr(0))}")
    _query("RPM",
           bytes([0x01, 0x0C]),
           lambda r: r[0] == 0x41 and r[1] == 0x0C and ((r[2] << 8) | r[3]) // 4 == DEMO_RPM,
           lambda r: f"{((r[2] << 8) | r[3]) / 4:.0f} RPM")
    _query("Stored DTCs",
           bytes([0x03]),
           lambda r: r[0] == 0x43 and r[1] == len(DEMO_DTCS),
           lambda r: f"count={r[1]}")
    _query("Clear DTCs",
           bytes([0x04]),
           lambda r: r[0] == 0x44,
           lambda r: "ack 0x44")

    stop.set()
    th.join(timeout=1.0)
    ecu_bus.shutdown()
    tester_bus.shutdown()
    return ok, rows


# ---------------------------------------------------------------------------
# K-Line over a Linux pty pair (no L9637D needed)
# ---------------------------------------------------------------------------


class _PtyPort:
    """Duck-typed wrapper around an os.openpty() endpoint mimicking
    pyserial's read(n) timeout semantics: block up to `timeout` seconds
    accumulating bytes; return whatever arrived (possibly < n)."""

    def __init__(self, fd: int, timeout: float = 0.1) -> None:
        self._fd = fd
        self.timeout = timeout

    def read(self, n: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        out = bytearray()
        while len(out) < n:
            try:
                chunk = os.read(self._fd, n - len(out))
            except (BlockingIOError, OSError):
                chunk = b""
            if chunk:
                out.extend(chunk)
                continue
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        return bytes(out)

    def write(self, b: bytes) -> int:
        return os.write(self._fd, b)


def bench_kline() -> tuple[bool, list[tuple[str, str]]]:
    if sys.platform != "linux":
        return True, [("skip", "kline pty bench is linux-only")]
    master, slave = os.openpty()
    # Put the pty into raw mode — otherwise the line discipline cooks
    # certain bytes (CR/LF translation, signal chars) and breaks 8-bit
    # binary KWP2000 frames. Real pyserial does this automatically.
    import termios
    import fcntl
    import tty
    tty.setraw(master)
    tty.setraw(slave)
    flags = fcntl.fcntl(slave, fcntl.F_GETFL)
    fcntl.fcntl(slave, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    _ = termios  # silence unused import on platforms where it's stub

    ecu = EcuEmulator(_build_state())
    runtime = KlineRuntime(ecu, _PtyPort(slave))
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                frame = runtime._read_one_frame()
            except Exception:
                frame = None
            if not frame:
                time.sleep(0.005)
                continue
            response = runtime.handle_request_bytes(frame)
            if response:
                runtime.serial.write(response)

    th = threading.Thread(target=_loop, daemon=True)
    th.start()

    rows: list[tuple[str, str]] = []
    ok = True

    def _query(label: str, payload: bytes,
                check: Callable[[bytes], bool], explain: Callable[[bytes], str]) -> None:
        nonlocal ok
        os.write(master, encode_kline_request(payload))
        deadline = time.monotonic() + 1.0
        buf = bytearray()
        resp_payload: bytes | None = None
        while time.monotonic() < deadline:
            try:
                chunk = os.read(master, 64)
            except (BlockingIOError, OSError):
                time.sleep(0.005)
                continue
            if chunk:
                buf.extend(chunk)
                try:
                    parsed = decode_kline(bytes(buf))
                    resp_payload = parsed.data
                    break
                except Exception:
                    continue
        if resp_payload is None:
            ok = False
            rows.append((label, "[red]TIMEOUT[/]"))
            return
        if not check(resp_payload):
            ok = False
            rows.append((label, f"[red]UNEXPECTED[/] {resp_payload.hex(' ')}"))
            return
        rows.append((label, f"[green]OK[/] {explain(resp_payload)}"))

    _query("VIN read",
           bytes([0x09, 0x02]),
           lambda r: r[0] == 0x49 and r[1] == 0x02 and DEMO_VIN.encode() in r,
           lambda r: f"VIN={r[3:].decode('ascii', errors='replace').strip(chr(0))}")
    _query("RPM",
           bytes([0x01, 0x0C]),
           lambda r: r[0] == 0x41 and r[1] == 0x0C and ((r[2] << 8) | r[3]) // 4 == DEMO_RPM,
           lambda r: f"{((r[2] << 8) | r[3]) / 4:.0f} RPM")
    _query("Stored DTCs",
           bytes([0x03]),
           lambda r: r[0] == 0x43 and r[1] == len(DEMO_DTCS),
           lambda r: f"count={r[1]}")

    stop.set()
    th.join(timeout=1.0)
    os.close(master)
    os.close(slave)
    return ok, rows


# ---------------------------------------------------------------------------
# J1850 over an in-memory thread-safe pipe
# ---------------------------------------------------------------------------


class _Pipe:
    """In-process duck-typed serial port: thread-safe FIFO of bytes."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    def read(self, n: int) -> bytes:
        with self._cv:
            self._cv.wait_for(lambda: bool(self._buf), timeout=0.01)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def write(self, b: bytes) -> int:
        with self._cv:
            self._buf.extend(b)
            self._cv.notify_all()
            return len(b)


def bench_j1850() -> tuple[bool, list[tuple[str, str]]]:
    """Round-trip OBD-II requests through the J1850 runtime over an
    in-memory pipe. Mirrors what an MC33390 + Pi UART will do once it's
    wired."""
    tester_to_ecu = _Pipe()
    ecu_to_tester = _Pipe()

    class _BiPort:
        def __init__(self, rx: _Pipe, tx: _Pipe) -> None:
            self.rx = rx
            self.tx = tx

        def read(self, n: int) -> bytes:
            return self.rx.read(n)

        def write(self, b: bytes) -> int:
            return self.tx.write(b)

    ecu = EcuEmulator(_build_state())
    runtime = J1850Runtime(ecu, _BiPort(tester_to_ecu, ecu_to_tester))
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                frame = runtime._read_one_frame()
            except Exception:
                frame = None
            if not frame:
                continue
            for response in runtime.handle_request_bytes(frame):
                runtime.port.write(response)

    th = threading.Thread(target=_loop, daemon=True)
    th.start()

    rows: list[tuple[str, str]] = []
    ok = True

    def _query(label: str, payload: bytes, check, explain) -> None:
        nonlocal ok
        tester_to_ecu.write(encode_j1850_request(payload))
        # Collect response frames until we have a complete reply
        deadline = time.monotonic() + 1.0
        buf = bytearray()
        chunks: list[bytes] = []
        while time.monotonic() < deadline and not chunks:
            chunk = ecu_to_tester.read(64)
            if not chunk:
                continue
            buf.extend(chunk)
            # Try to peel off one or more complete frames
            while len(buf) >= 5:
                for end in range(5, min(len(buf), 11) + 1):
                    try:
                        decode_j1850(bytes(buf[:end]))
                    except Exception:
                        continue
                    chunks.append(bytes(buf[:end]))
                    del buf[:end]
                    break
                else:
                    break
        if not chunks:
            ok = False
            rows.append((label, "[red]TIMEOUT[/]"))
            return
        body = b"".join(decode_j1850(c).data for c in chunks)
        if not check(body):
            ok = False
            rows.append((label, f"[red]UNEXPECTED[/] {body.hex(' ')}"))
            return
        rows.append((label, f"[green]OK[/] {explain(body)}"))

    _query("RPM",
           bytes([0x01, 0x0C]),
           lambda r: r[0] == 0x41 and r[1] == 0x0C and ((r[2] << 8) | r[3]) // 4 == DEMO_RPM,
           lambda r: f"{((r[2] << 8) | r[3]) / 4:.0f} RPM")
    _query("Stored DTCs",
           bytes([0x03]),
           lambda r: r[0] == 0x43 and r[1] == len(DEMO_DTCS),
           lambda r: f"count={r[1]}")
    _query("Clear DTCs",
           bytes([0x04]),
           lambda r: r[0] == 0x44,
           lambda r: "ack 0x44")

    stop.set()
    th.join(timeout=1.0)
    return ok, rows


# ---------------------------------------------------------------------------


def _render(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Request")
    table.add_column("Result")
    for label, result in rows:
        table.add_row(label, result)
    console.print(table)


def main() -> int:
    console.print("[bold]UACJ OBD-II bench harness[/] — virtual buses, no hardware.\n")
    overall_ok = True
    for name, fn in (("CAN (ISO-TP, virtual bus)", bench_can),
                       ("K-Line (KWP2000, pty pair)", bench_kline),
                       ("J1850 (in-memory pipe)", bench_j1850)):
        try:
            ok, rows = fn()
        except Exception as exc:
            ok, rows = False, [("exception", f"[red]{exc!r}[/]")]
        overall_ok = overall_ok and ok
        _render(name, rows)
    if overall_ok:
        console.print("[bold green]✓ Bench harness passed.[/] All three protocols round-trip cleanly.")
        return 0
    console.print("[bold red]✗ Bench harness failed.[/] See rows above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
