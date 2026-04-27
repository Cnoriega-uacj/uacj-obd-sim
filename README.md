# UACJ OBD-II Training Simulator

A complete OBD-II acquisition, modification, and replay system for the UACJ (Universidad Autónoma de Ciudad Juárez) automotive program.

**Phase 1 (this milestone):** acquire live vehicle data via a USB OBD-II adapter, save organized per-vehicle session folders, and expose a dashboard + REST API.

**Phase 2 (next):** modification interface for DTCs / monitors / VIN / sensor values, and the Raspberry Pi simulator firmware that replays scenarios to student scan tools.

---

## Quick start (offline, no vehicle needed)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# capture 10 seconds against the built-in mock vehicle
uacj-obd --data data capture --adapter mock --duration 10

# launch the dashboard at http://127.0.0.1:8000
uacj-obd --data data serve
```

## With a real vehicle

```bash
# Linux: ELM327 / STN2120 typically appears as /dev/ttyUSB0 or /dev/rfcomm0
uacj-obd --data data capture --adapter elm327 --port /dev/ttyUSB0 --duration 60
```

The system auto-detects the OBD-II protocol on connect (CAN, KWP2000, ISO 9141-2, J1850 VPW, J1850 PWM when supported by the adapter) and reads:

- Live mode 0x01 PIDs (RPM, speed, temperatures, MAF, throttle, O2, fuel trims, etc.)
- VIN, calibration ID, ECU name (mode 0x09)
- DTCs — stored, pending, permanent (modes 0x03, 0x07, 0x0A)
- Readiness monitors (mode 0x01 PID 0x01)
- Freeze frame (mode 0x02)
- Mode 0x22 manufacturer-specific PIDs via pluggable definition files

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

## REST API (running)

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
| GET/POST/PATCH/DELETE | `/api/scenarios[...]` | scenario CRUD (Phase 2) |

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

## Tests

```bash
pip install -e ".[dev]"
pytest -v
```

## Architecture

The HAL (Hardware Abstraction Layer) in `uacj_obd/adapters/` isolates adapter-specific code. Switching from an ELM327 to a custom hardware board later means writing one new `Adapter` subclass — no other module changes.

```
adapters/         HAL: ELM327, mock, replay, (future: custom board)
acquisition/      session orchestration: connect → capture → reconnect → close
storage/          SQLite metadata + per-vehicle session folders
pids/             PID registry, YAML-driven, hot-extensible
api/              FastAPI REST + static dashboard
cli.py            click-based CLI (capture, vehicles, sessions, serve)
```

## Roadmap

- **Phase 2 — Modification & simulator board**
  - Web modification panel (DTCs, monitors, VIN, live overrides per scenario)
  - Scenario push to Raspberry Pi simulator over Wi-Fi
  - Pi firmware: ECU response engine on CAN (ISO-TP framing) and K-Line (KWP2000)
  - Wiring guide with photos for student-facing OBD-II port assembly
