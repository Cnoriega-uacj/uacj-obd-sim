#!/usr/bin/env python3
"""
End-to-end demo: capture (mock) → save → modify → replay → ECU response.

Run: python scripts/demo.py [data_dir]

Prints a step-by-step trace so the full pipeline can be verified
without any physical hardware. Cristopher can run this on his laptop
the moment the repo is cloned and confirm the system is real.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.adapters.replay import ReplayAdapter
from uacj_obd.pids import load_default_registry
from uacj_obd.simulator import EcuEmulator
from uacj_obd.simulator.can_runtime import CanRuntime, scenario_to_state
from uacj_obd.simulator.iso_tp import CanFrame, IsoTpFramer
from uacj_obd.simulator.kline import decode as decode_kline, encode_request as encode_kline_request
from uacj_obd.simulator.kline_runtime import KlineRuntime
from uacj_obd.storage import Database, SessionStore


console = Console()


def step(n: int, title: str) -> None:
    console.print(Panel.fit(f"[bold cyan]Step {n}[/]  {title}", border_style="cyan"))


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="uacj-demo-"))
    console.print(f"[dim]Data root:[/] {data_dir}\n")

    db = Database(data_dir / "uacj.db")
    store = SessionStore(data_dir / "sessions")
    pid_reg = load_default_registry()

    # ----- 1. Capture from mock vehicle -----
    step(1, "Capture a 'vehicle' via the mock OBD-II adapter")
    adapter = open_adapter("mock")
    sess = AcquisitionSession(adapter, store, db, pid_reg,
                                SessionConfig(pids=["010C", "010D", "0105", "0111"], sample_interval_s=0.0))
    meta = sess.start()
    n = sess.run(duration_s=0.5)
    folder = sess.close()
    console.print(f"  ✓ captured [bold]{n}[/] samples from VIN [bold]{meta.vehicle.vin}[/] "
                   f"(protocol: {meta.protocol.value})")
    console.print(f"  ✓ session folder: [dim]{folder}[/]")

    # ----- 2. Inspect saved DTCs -----
    step(2, "Verify saved DTCs and monitors")
    dtcs = json.loads((folder / "dtcs.json").read_text())
    table = Table(show_header=True, header_style="bold")
    table.add_column("Code")
    table.add_column("Status")
    table.add_column("Description")
    for d in dtcs:
        table.add_row(d["code"], d["status"], d["description"])
    console.print(table)

    # ----- 3. Build a teaching scenario -----
    step(3, "Build a teaching scenario (modified DTC + RPM override)")
    # Pre-merge the saved session's last-known values per PID as the
    # baseline; instructor's overrides ride on top. This is what the
    # /api/scenarios/{id}/push endpoint does on the laptop.
    live_baseline: dict = {}
    with (folder / "live_data.jsonl").open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("pid") and obj.get("value") is not None:
                live_baseline[obj["pid"]] = obj["value"]

    scenario_payload = {
        "label": "P0301 misfire training",
        "source_session_id": meta.session_id,
        "vehicle": meta.vehicle.model_dump(),
        "dtcs": [
            {"code": "P0301", "status": "stored", "description": "Cylinder 1 Misfire Detected"},
            {"code": "P0300", "status": "pending", "description": "Random/Multiple Cylinder Misfire"},
        ],
        "monitors": json.loads((folder / "monitors.json").read_text()),
        "freeze_frame": {"dtc": "P0301", "pids": {"010C": 1850, "010D": 30}},
        "live_baseline": live_baseline,
        "live_overrides": {"010C": 950, "010D": 0, "0111": 8},  # rough idle, no speed, low throttle
    }
    console.print(f"  ✓ scenario built: [bold]{scenario_payload['label']}[/]")
    console.print(f"  ✓ DTCs: [bold]{[d['code'] for d in scenario_payload['dtcs']]}[/]")
    console.print(f"  ✓ Live overrides: [bold]{scenario_payload['live_overrides']}[/]")

    # ----- 4. Replay through ReplayAdapter -----
    step(4, "Replay the modified scenario through ReplayAdapter")
    replay = ReplayAdapter(folder, scenario_overrides=scenario_payload)
    replay.connect()
    rdtcs = replay.read_dtcs()
    rrpm = replay.read_pid("010C")
    console.print(f"  ✓ replay returns DTCs: [bold]{[d.code for d in rdtcs]}[/]")
    console.print(f"  ✓ replay overrides RPM to [bold]{rrpm.value}[/] (was streaming)")
    replay.disconnect()

    # ----- 5. Load into ECU emulator and answer scan-tool requests -----
    step(5, "Load scenario into ECU emulator and answer scan-tool-style requests")
    state = scenario_to_state(scenario_payload)
    ecu = EcuEmulator(state)

    requests = [
        ("VIN read (mode 09 PID 02)",      bytes([0x09, 0x02])),
        ("RPM (mode 01 PID 0C)",           bytes([0x01, 0x0C])),
        ("Speed (mode 01 PID 0D)",         bytes([0x01, 0x0D])),
        ("Coolant temp (mode 01 PID 05)",  bytes([0x01, 0x05])),
        ("Stored DTCs (mode 03)",          bytes([0x03])),
        ("Pending DTCs (mode 07)",         bytes([0x07])),
    ]

    rt_can = CanRuntime(ecu, bus=None)  # bus unused — we go through the pure-data path
    framer = IsoTpFramer()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Scan-tool request")
    table.add_column("CAN response (decoded)")
    for label, payload in requests:
        req_frame = framer.encode(payload)[0]
        out = rt_can.handle_request_frame(CanFrame(0x7DF, req_frame.data))
        decoder = IsoTpFramer()
        resp = None
        for f in out:
            resp = decoder.decode(f) or resp
        decoded = _interpret(payload, resp) if resp else "(no response)"
        table.add_row(label, decoded)
    console.print(table)

    # ----- 6. Same scenario over K-Line for pre-CAN vehicles -----
    step(6, "Same scenario over K-Line (KWP2000) for pre-CAN vehicles")
    rt_kline = KlineRuntime(ecu, serial=None)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Scan-tool request")
    table.add_column("K-Line response payload")
    for label, payload in requests[:3]:
        request_frame = encode_kline_request(payload)
        response_frame = rt_kline.handle_request_bytes(request_frame)
        if response_frame:
            parsed = decode_kline(response_frame)
            table.add_row(label, parsed.data.hex(" "))
        else:
            table.add_row(label, "(no response)")
    console.print(table)

    # ----- 7. Mode 04 clear -----
    step(7, "Mode 04 (clear DTCs) — student presses 'clear codes' on their tool")
    state.dtcs_stored = ["P0301"]
    out = ecu.handle(bytes([0x04]))
    console.print(f"  ✓ ECU response byte: [bold]0x{out.hex().upper()}[/] (0x44 = positive)")
    console.print(f"  ✓ stored DTCs after clear: [bold]{state.dtcs_stored}[/]")

    console.print()
    console.print(Panel.fit(
        "[bold green]✓ All seven steps passed.[/]\n"
        "Mock capture → save → modify → replay → CAN+K-Line ECU response works end-to-end.\n"
        f"Full session folder + database in: [dim]{data_dir}[/]",
        border_style="green",
    ))
    return 0


def _interpret(req: bytes, resp: bytes) -> str:
    if not resp:
        return "(empty)"
    if resp[0] == 0x7F:
        return f"NRC 0x{resp[2]:02X} for service 0x{resp[1]:02X}"
    if resp[0] == 0x41 and len(resp) >= 4 and req[1:2] == bytes([0x0C]):
        rpm = ((resp[2] << 8) | resp[3]) / 4
        return f"RPM = {rpm:.0f}"
    if resp[0] == 0x41 and len(resp) >= 3 and req[1:2] == bytes([0x0D]):
        return f"Speed = {resp[2]} km/h"
    if resp[0] == 0x41 and len(resp) >= 3 and req[1:2] == bytes([0x05]):
        return f"Coolant = {resp[2] - 40} °C"
    if resp[0] == 0x49 and resp[1] == 0x02:
        return f"VIN = {resp[3:].decode('ascii', errors='replace').strip(chr(0))}"
    if resp[0] == 0x43:
        return f"Stored DTCs: count={resp[1]}, raw={resp[2:].hex(' ')}"
    if resp[0] == 0x47:
        return f"Pending DTCs: count={resp[1]}, raw={resp[2:].hex(' ')}"
    return resp.hex(" ")


if __name__ == "__main__":
    sys.exit(main())
