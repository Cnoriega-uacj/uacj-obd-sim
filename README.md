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

## Current capabilities

**661 automated tests, ruff-clean, ~89% code coverage, CI on every push.**

### Core acquisition + simulator (v0.3 — v0.4)

- **Acquisition** for all 5 OBD-II protocols: CAN, KWP2000, ISO 9141-2,
  J1850 VPW, J1850 PWM (acquisition side via OBDLink SX/STN2120)
- **Simulator** answers students' scan tools on CAN + K-Line.
  J1850 framing is implemented and unit-tested; J1850 transceiver
  add-on instructions in [docs/wiring_walkthrough_stage2.md](docs/wiring_walkthrough_stage2.md)
- **Per-vehicle session storage** organized by VIN, make, model, year
- **Modification interface** for DTCs, monitors, VIN, freeze frame,
  and any live sensor value (all stored locally; never written back
  to a real vehicle)
- **6 built-in classroom presets**: P0420 catalyst, P0171 lean,
  P0301+P0300 misfire, P0455 EVAP leak, drive-cycle incomplete,
  U0100 lost-comm
- **Mode 0x06 on-board monitoring test results** for CARB-style
  emissions-readiness diagnosis
- **Manufacturer PID library** covering Honda (6), Ford (5), GM (5),
  Toyota (3), Nissan (3)
- **Classroom view** with live request log — instructors see every
  scan-tool query in real time
- **Session diff tool** — side-by-side compare of two captures
- **Spanish/English UI toggle** (auto-detects es-MX browser locale)
- **One-click backup/restore** — entire system state as a ZIP

### Wide-coverage capture (v0.5 — v0.6.16)

- **Raw bitmap probe** (v0.6.12) — queries Mode 01 PID 0x00/0x20/0x40/...
  directly and parses the response bytes, bypassing python-obd's
  command-table filter so PIDs python-obd has no decoder for still
  get discovered
- **Raw-bytes capture fallback** (v0.6.13, v0.6.16) — for PIDs python-obd
  can't decode, the adapter reads the raw response bytes via
  `c.interface.send_and_parse()` and stores them as `"raw:HEX"`. The
  simulator's pass-through encoder replays them verbatim, so any PID
  the car answered shows up on the scan tool
- **VIN, Cal ID, CVN, ECU name** (v0.6.9) — full vehicle-info capture
  via Mode 09 PIDs 0x02 / 0x04 / 0x06 / 0x0A
- **Dynamic live-data replay** (v0.5.0) — the captured time-series
  (RPM bouncing, speed rising, etc.) is shipped to the simulator and
  the values move in real time on the scan tool
- **VIN decoder** (v0.5.2) — WMI table for 85+ makes, year disambiguation
- **Pi access-point mode** (v0.5.1) — bench-side WiFi network from
  the Pi for self-contained classroom use

### Operational visibility (v0.6.6 — v0.6.16)

- **Capture diagnostics** — `GET /api/sessions/{id}/diagnostics`
  reports adapter-discovered PID count vs unique PIDs that landed,
  numeric vs raw passthrough split, list of "missing after capture"
  PIDs, plus adapter telemetry (raw fallback rate). Dashboard's
  session page renders all of it inline
- **Scenario coverage preview** — `GET /api/scenarios/{id}/coverage`
  reports what the simulator will actually answer for the scenario:
  formula-encoded count vs raw-passthrough count, per-PID name + unit
  + answerable flag, human-readable notes for missing fields. New
  "Preview coverage" button on the scenario editor pops the report
  before push
- **Pi-status panel** — dashboard polls a consolidated
  `/api/sim/state-proxy` every 10s and renders VIN loaded, DTC count,
  replay running state, persistence status. Offline Pi shows OFFLINE
  pill instead of console errors
- **Version-mismatch + disk-space + duration-cap guardrails** —
  `/api/sim/version-check` flags laptop ↔ Pi version drift;
  `/api/sessions/start` refuses if data root has < 200 MB free;
  duration > 3600 s clamps to one hour

### Reliability + retention (v0.6.7)

- **Pi scenario persistence** — every `POST /api/sim/load` mirrors
  the payload atomically to `~/.uacj-sim-last-scenario.json`. On
  simulator startup the server re-applies it (including re-arming
  the replay engine) so a Pi reboot mid-class is invisible to students
- **Session retention** — `DELETE /api/sessions/{id}` removes a
  capture; `POST /api/sessions/cleanup?mode={empty,old,both}`
  prunes empty captures and captures older than 90 days while
  keeping at least the 10 most-recent

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
| GET | `/api/sessions/{id}/diagnostics` | capture-side telemetry (discovered/captured/raw rates) |
| DELETE | `/api/sessions/{id}` | delete a session (DB row + folder) |
| POST | `/api/sessions/cleanup` | prune empty / old sessions |
| GET | `/api/vehicles` | list vehicles seen |
| GET | `/api/vin/decode` | VIN decode lookup |
| GET | `/api/pids` | PID registry |
| GET | `/api/disk` | data root disk-space status |
| GET/POST/PATCH/DELETE | `/api/scenarios[...]` | scenario CRUD |
| GET | `/api/scenarios/{id}/coverage` | what the simulator will answer for this scenario |
| GET | `/api/presets` | list built-in presets |
| POST | `/api/presets/{id}/instantiate` | apply preset on top of a session |
| POST | `/api/scenarios/{id}/push` | push to the Pi simulator |
| GET | `/api/sim/version-check` | flag laptop ↔ Pi version mismatch |
| GET | `/api/sim/state-proxy` | merged Pi state + persistence info |
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
- [docs/wiring_walkthrough.md](docs/wiring_walkthrough.md) — Stage 1
  step-by-step (plain-language, photo-confirmation gates per step)
- [docs/wiring_walkthrough_stage2.md](docs/wiring_walkthrough_stage2.md) —
  Stage 2 step-by-step for the J1850 VPW + PWM pre-CAN add-ons
- [docs/install.md](docs/install.md) — TeamViewer-day install runbook
  (SD-card flash through smoke-test) with troubleshooting table
- [docs/instructor.md](docs/instructor.md) — 30-minute end-to-end
  tutorial for instructors
- [docs/instructor_quickstart.md](docs/instructor_quickstart.md) /
  [docs/instructor_quickstart_es.md](docs/instructor_quickstart_es.md) —
  one-page classroom cheat sheets (English / Español)
- [docs/compatibility.md](docs/compatibility.md) — scan-tool compatibility
  predictions for 13 common tools
- [docs/wifi_hotspot.md](docs/wifi_hotspot.md) — Pi access-point mode
  for self-contained classroom networks
- [CHANGELOG.md](CHANGELOG.md) — full release history

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 Universidad Autónoma de
Ciudad Juárez (UACJ).
