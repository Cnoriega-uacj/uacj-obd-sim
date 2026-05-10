# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

OBD-II training simulator for UACJ (Universidad Autónoma de Ciudad Juárez). Two cooperating roles in one Python package:

- **Laptop side** — captures real vehicle data over USB (ELM327 / STN2120), stores it, and provides a FastAPI dashboard for instructors to author "scenarios" (modified versions of captured sessions).
- **Pi simulator side** — Raspberry Pi running the same package in `simulator` mode. Replays scenarios over CAN (ISO 15765) and K-Line (KWP2000) so student scan tools see a synthetic ECU through a real OBD-II port.

Same code, two entry points (`uacj-obd serve` vs. `uacj-obd simulator`).

## Common commands

```bash
# Setup (Python 3.11+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest -v                                  # full suite
pytest tests/test_ecu.py -v                # one file
pytest tests/test_ecu.py::test_name -v     # one test
pytest -k "iso_tp"                         # by keyword

# Lint
ruff check .                               # configured in pyproject.toml (line 100, py311)

# Run
uacj-obd --data data capture --adapter mock --duration 10    # offline capture
uacj-obd --data data serve                                   # dashboard at :8000
uacj-obd simulator --no-can --no-kline                       # Pi server only (HTTP :8765)
python scripts/demo.py                                       # end-to-end pipeline demo
```

`--data` defaults to `./data/`; sessions go to `data/sessions/{VIN}_{make}_{model}_{year}/{session_id}/` and metadata to `data/uacj.db` (SQLite).

## Architecture

### Two FastAPI apps

- `uacj_obd/api/app.py` (`create_app`) — laptop dashboard + REST. Owns DB, SessionStore, scenarios, push-to-board.
- `uacj_obd/simulator/server.py` (`make_simulator_server`) — Pi receiver. Owns the live `EcuEmulator` instance; accepts `POST /api/sim/load` from the laptop.

The laptop's `POST /api/scenarios/{id}/push` performs **live-baseline merging**: it pulls the latest value per PID from the source session's `live_data.jsonl`, attaches it as `live_baseline`, and ships the whole payload to the Pi. So a scenario only needs to specify what *changes* — the simulator answers every PID the original car answered.

### Hardware Abstraction Layer (HAL)

`uacj_obd/adapters/` — every external data source implements `Adapter` (base.py). Adding a new physical interface = one new subclass; nothing else moves.

| Adapter | Purpose |
|---|---|
| `MockAdapter` | In-memory 2015 Civic for offline dev. Tests run against this. |
| `Elm327Adapter` | Real serial/Bluetooth ELM327 / STN2120 via `python-obd`. |
| `ReplayAdapter` | Replays a saved session as if live; accepts `scenario_overrides` for the modify→replay self-test. |

`open_adapter("auto")` tries ELM327 then falls back to mock.

### Acquisition flow

`AcquisitionSession` (`uacj_obd/acquisition/session.py`) orchestrates: connect → static reads (VIN, DTCs, monitors, freeze frame) → continuous live stream → auto-reconnect with exponential backoff → close. The thread that runs `sess.run()` is owned by the FastAPI handler in `api/app.py` (`state["current"]`); only one capture/replay can run at a time (409 otherwise).

### Simulator stack (Pi side)

```
scan tool ─┬─ CAN  ──► CanRuntime  (python-can / SocketCAN)  ─┐
           └─ K-Line ► KlineRuntime (pyserial @ L9637D UART)  ┴─► IsoTpFramer / kline frame ─► EcuEmulator ─► ScenarioState
                                                                                                    ▲
                                                                          POST /api/sim/load ───────┘ (from laptop)
```

- `EcuEmulator` (`simulator/ecu.py`) is **stateless wrt I/O** — pure request-bytes → response-bytes. All mutable state is in `ScenarioState`. This is why every supported service has unit tests without any bus hardware.
- `IsoTpFramer` (`simulator/iso_tp.py`) — SF/FF/CF/FC, padding 0xAA per SAE J1979.
- KWP2000 K-Line (`simulator/kline.py`, `kline_runtime.py`) — short/long form + arithmetic checksum + UART length probing. Includes 5-baud slow-init handshake (`slow_init_step`) handled transparently before frame decoding.
- `can_runtime.scenario_to_state(payload)` is the canonical scenario-payload → `ScenarioState` converter; it's used by both the simulator HTTP load handler and the ReplayAdapter.

Every request the ECU answers is appended to a bounded ring buffer (`EcuEmulator._log`, default 500). `GET /api/sim/log` exposes it, and the laptop dashboard proxies it for the classroom view.

### PID registry

`uacj_obd/pids/registry.py`. PIDs are defined in YAML (`uacj_obd/pids/data/`), loaded automatically by `load_default_registry()`. A PID definition is `(mode, pid, name, unit, formula, bytes_expected, manufacturer)`. The `formula` is a sandboxed `eval` over a tuple `b` of response bytes — e.g. RPM is `(b[0] * 256 + b[1]) / 4`. Mode 0x22 (manufacturer) PIDs use a 16-bit pid; standard J1979 (mode 0x01/0x02/0x05) uses 8-bit. Key format: `{mode:02X}{pid:02X or 04X}` (e.g. `010C`, `22115C`).

To add manufacturer PIDs: drop a YAML file into `uacj_obd/pids/data/`. To make the simulator *answer* a new mfg PID (not just decode it), add an encoder in `simulator/encoders.py` and register it in `ecu._mode22`.

### Scenarios & presets

- `Scenario` (models.py): label + vehicle + dtcs + monitors + freeze_frame + `live_overrides` (dict of PID-key → value).
- `uacj_obd/presets.py` — six built-in classroom cases (P0420, P0171, P0301+P0300, P0455, drive-cycle incomplete, U0100). `POST /api/presets/{id}/instantiate` builds a scenario by overlaying the preset's DTCs/freeze-frame/monitor-override on a chosen source session's vehicle + monitor baseline.

### Diff

`uacj_obd/diff.py` (`diff_sessions(folder_a, folder_b)`) — read-only structured comparison: vehicle identity, DTCs (added/removed/common), monitor changes, per-PID stats (n, min, max, mean, median, delta-mean and delta% with a "shifted" flag at >5%). Surfaced via `GET /api/diff?a=&b=` and `/diff.html`.

## Conventions specific to this codebase

- **Python 3.11+ only.** `from __future__ import annotations` at the top of every module; `str | None` style unions are used everywhere.
- **Pydantic v2** for all wire models (`models.py`). `model_dump(mode="json")` for serialization.
- **Pure-data modules are testable without hardware.** `iso_tp.py`, `kline.py`, `ecu.py`, `encoders.py`, `diff.py` all have zero I/O dependencies — bus and serial objects are duck-typed in the runtime wrappers. New simulator logic should follow the same split: parsing/encoding/state in the pure module, I/O loop in the `*_runtime.py` wrapper.
- **PID keys are uppercase hex strings**, not ints — `"010C"`, not `0x010C`. The registry normalizes via `.upper()` on lookup.
- **DTC codes are SAE J2012 strings** like `"P0420"` (5 chars, letter + 4 hex). `_dtc_code_to_bytes` packs them; mode 03/07/0A response is `count_byte + 2*N`.
- **Scope discipline.** v0.3.0 explicitly defers multi-ECU emulation (BCM/immobilizer) and PDF reports pending legal review — see CHANGELOG. Don't add either without asking. J1850 VPW/PWM is also out of scope (needs MC33390 transmitter hardware).
- The static dashboard (`web/`) is mounted last on `/` and is plain HTML+JS — no build step. There's no frontend framework.

## Where to look

- New REST endpoint → `uacj_obd/api/app.py`
- New simulator service code (e.g. mode 0x05 O2 sensor) → `uacj_obd/simulator/ecu.py` + tests in `tests/test_ecu.py`
- New adapter (e.g. Bluetooth-only ELM) → `uacj_obd/adapters/`, subclass `Adapter`, register in `factory.open_adapter`
- New built-in classroom scenario → `uacj_obd/presets.py`
- Pi provisioning / boot config → `scripts/setup_pi.sh` (idempotent; SPI/MCP2515 overlay, UART, can0 via systemd-networkd)
- Wiring/BOM/connector pinout → `docs/wiring.md`; instructor workflow → `docs/instructor.md`; scan-tool compatibility methodology → `docs/compatibility.md`
