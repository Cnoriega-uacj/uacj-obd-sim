# UACJ OBD-II Training Simulator

[![ci](https://github.com/lightstar226/uacj-obd-sim/actions/workflows/ci.yml/badge.svg)](https://github.com/lightstar226/uacj-obd-sim/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A complete OBD-II acquisition, modification, and replay system for the
**UACJ (Universidad Autónoma de Ciudad Juárez)** automotive program.

Two cooperating parts:

- **Laptop side** — captures real vehicle data over USB (ELM327 / STN2120),
  stores it per-vehicle, and provides a FastAPI dashboard for instructors
  to build "scenarios" (modified copies of captured sessions).
- **Pi simulator side** — Raspberry Pi running the same package in
  simulator mode. Replays scenarios over CAN (ISO 15765) and K-Line
  (KWP2000) so student scan tools see a synthetic ECU through a real
  OBD-II port.

Same code, two entry points: `uacj-obd serve` vs. `uacj-obd simulator`.

---

## What's in v0.4.0

**118 automated tests, ruff-clean, CI on every push.**

- **Acquisition** for all 5 OBD-II protocols: CAN, KWP2000, ISO 9141-2,
  J1850 VPW, J1850 PWM (acquisition side via OBDLink SX/STN2120)
- **Simulator** answers students' scan tools on CAN + K-Line.
  J1850 framing is implemented and unit-tested; ships dormant until
  a transceiver chip is wired (separate add-on)
- **Per-vehicle session storage** organized by VIN, make, model, year
- **Modification interface** for DTCs, monitors, VIN, freeze frame,
  and any live sensor value (all stored locally; never written back
  to a real vehicle)
- **6 built-in classroom presets**: P0420 catalyst, P0171 lean,
  P0301+P0300 misfire, P0455 EVAP leak, drive-cycle incomplete,
  U0100 lost-comm
- **5 pre-loaded sample vehicles** (Civic, Silverado, Corolla, F-150,
  Sentra) so the dashboard isn't empty on first boot
- **Mode 0x06 on-board monitoring test results** for CARB-style
  emissions-readiness diagnosis
- **Manufacturer PID library** covering Honda (6), Ford (5), GM (5),
  Toyota (3), Nissan (3)
- **Classroom view** with live request log — instructors see every
  scan-tool query in real time
- **Session diff tool** — side-by-side compare of two captures
- **Spanish/English UI toggle** (auto-detects es-MX browser locale)
- **One-click backup/restore** — entire system state as a ZIP

---

## Quick start (instructor laptop)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .

# Open the dashboard at http://localhost:8000
uacj-obd --data data serve
```

Windows users can double-click `installer/start_uacj.bat` instead —
creates the venv, installs everything, seeds the samples, and opens
the browser automatically.

## Capture from a real vehicle

```bash
# Linux: ELM327 / STN2120 typically appears as /dev/ttyUSB0 or /dev/rfcomm0
uacj-obd --data data capture --adapter elm327 --port /dev/ttyUSB0 --duration 60

# Windows: COM3, COM4, etc.
uacj-obd --data data capture --adapter elm327 --port COM3 --duration 60
```

The system auto-detects the OBD-II protocol on connect and reads:

- Live mode 0x01 PIDs (RPM, speed, temperatures, MAF, throttle, O2,
  fuel trims, etc.)
- VIN, calibration ID, ECU name (mode 0x09)
- DTCs — stored, pending, permanent (modes 0x03, 0x07, 0x0A)
- Readiness monitors (mode 0x01 PID 0x01)
- Freeze frame (mode 0x02)
- Mode 0x06 on-board monitoring test results
- Mode 0x22 manufacturer-specific PIDs via pluggable definition files

## Pi simulator setup

Wiring guide and BOM: [docs/wiring.md](docs/wiring.md)

After wiring per the guide:

```bash
# On a fresh Raspberry Pi OS Lite
sudo git clone https://github.com/lightstar226/uacj-obd-sim.git /opt/uacj-obd-sim
sudo chown -R pi:pi /opt/uacj-obd-sim
cd /opt/uacj-obd-sim
sudo bash scripts/setup_pi.sh
sudo reboot
```

After reboot, the simulator is running as a `systemd` service on port
8765. Verify with:

```bash
systemctl status uacj-obd-sim
ip -details link show can0          # should be UP at 500 kbps
curl http://localhost:8765/api/sim/health
```

## Storage layout

```
data/
  uacj.db                              # SQLite metadata (vehicles, sessions, scenarios)
  sessions/
    {VIN}_{make}_{model}_{year}/       # one folder per vehicle
      {YYYYMMDDTHHMMSSZ}-{hash}/       # one folder per session
        metadata.json
        live_data.jsonl
        dtcs.json
        monitors.json
        freeze_frame.json
        raw.log
```

## REST API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | health check |
| POST | `/api/sessions/start` | start a capture |
| POST | `/api/sessions/stop` | stop the active capture |
| GET | `/api/sessions/current` | active session metadata |
| GET | `/api/sessions` | list sessions (optional `?vin=`) |
| GET | `/api/sessions/{id}` | session metadata + DTCs + monitors |
| GET | `/api/sessions/{id}/live` | live samples (JSON) |
| GET | `/api/sessions/{id}/export.csv` | CSV export |
| GET | `/api/vehicles` | list vehicles seen |
| GET | `/api/pids` | PID registry |
| GET/POST/PATCH/DELETE | `/api/scenarios[...]` | scenario CRUD |
| GET | `/api/presets` | list built-in presets |
| POST | `/api/presets/{id}/instantiate` | apply preset on top of a session |
| POST | `/api/scenarios/{id}/push` | push to the Pi simulator |
| GET | `/api/diff?a=&b=` | diff two captured sessions |
| POST | `/api/backup` | download full backup ZIP |
| POST | `/api/restore` | restore from a backup ZIP |

## Adding manufacturer PIDs

Drop a YAML file into `uacj_obd/pids/data/`:

```yaml
- mode: 0x22
  pid: 0x1234
  name: MY_CUSTOM_PID
  manufacturer: Toyota
  unit: "°C"
  bytes_expected: 2
  formula: "(b[0] * 256 + b[1]) * 0.1 - 40"
```

It will be loaded automatically on startup. No code changes required.

To make the simulator *answer* a new mfg PID (not just decode it), add
an encoder in `uacj_obd/simulator/encoders.py` and register it in
`ecu._mode22`.

## Tests

```bash
pip install -e ".[dev]"
pytest -v
ruff check .
```

The bench harness (`scripts/bench.py`) round-trips RPM / VIN / DTC /
clear-codes through python-can virtual buses, a pty pair, and an
in-memory pipe — exercising CAN, K-Line, and J1850 without any
hardware.

## Architecture

The HAL (Hardware Abstraction Layer) in `uacj_obd/adapters/` isolates
adapter-specific code. Switching from an ELM327 to a custom hardware
board later means writing one new `Adapter` subclass — no other module
changes.

```
adapters/         HAL: ELM327 (STN-aware), mock, replay
acquisition/      session orchestration: connect → capture → reconnect → close
storage/          SQLite metadata + per-vehicle session folders
pids/             PID registry, YAML-driven, hot-extensible
simulator/        ECU emulator, ISO-TP / KWP2000 / J1850 framing, CAN / K-Line / J1850 runtimes
api/              FastAPI REST + static dashboard
cli.py            click-based CLI (capture, vehicles, sessions, serve, simulator)
```

## Documentation

- [docs/wiring.md](docs/wiring.md) — assembly guide with full BOM, GPIO
  pinout, OBD-II connector pinout, plus optional GM J1850 VPW and
  Ford J1850 PWM add-on schematics
- [docs/install.md](docs/install.md) — TeamViewer-day install runbook
  (SD-card flash through smoke-test) with troubleshooting table
- [docs/instructor.md](docs/instructor.md) — 30-minute end-to-end
  tutorial for instructors
- [docs/instructor_quickstart.md](docs/instructor_quickstart.md) /
  [docs/instructor_quickstart_es.md](docs/instructor_quickstart_es.md) —
  one-page classroom cheat sheets (English / Español)
- [docs/compatibility.md](docs/compatibility.md) — scan-tool compatibility
  predictions for 13 common tools
- [CHANGELOG.md](CHANGELOG.md) — full release history

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 Universidad Autónoma de
Ciudad Juárez (UACJ).
