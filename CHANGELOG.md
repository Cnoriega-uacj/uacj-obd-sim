# Changelog

## 0.2.0 — 2026-04-27

Polish and classroom features layered on top of v0.1.0. No protocol or
storage breaking changes.

### Scenario presets
- Six built-in training cases (`uacj_obd/presets.py`):
  P0420 catalyst, P0171 lean, P0301+P0300 misfire, P0455 EVAP large
  leak, drive-cycle incomplete, U0100 lost-comm.
- `GET /api/presets` and `POST /api/presets/{id}/instantiate` —
  instantiate a preset on top of any saved session in one click.
  The session provides the vehicle identity and live baseline; the
  preset provides DTCs, freeze frame, and any monitor / live overrides.
- Scenarios dashboard gains a "From preset" panel.

### Manufacturer-specific PID simulation
- Mode 0x22 dispatch added to the ECU (`ecu._mode22`).
- Six manufacturer PID encoders mirroring the YAML decoders:
  Ford trans-oil temp, Ford key-on runtime, GM oil life, GM trans
  fluid temp, Toyota engine runtime, Honda ATF temp.
- Scenarios that include 0x22 PID values in `live_overrides` (key
  format `22XXXX`) are now answered by the simulator, completing the
  manufacturer-PID loop end-to-end.

### Classroom view
- ECU keeps a bounded ring buffer (default 500 entries) of every
  request → response pair, with timestamp, decoded service/PID, and
  short summary.
- `GET /api/sim/log` on the simulator board surfaces it; the laptop
  proxies the same path so the dashboard works when the laptop is on
  a different subnet.
- New `/classroom.html` page: 1-second auto-refreshing tail of every
  scan-tool request the board has seen, grouped by service with a
  quick-scan pill (NRCs warn, clear-DTCs flagged red).

### Tests
- 10 new tests covering preset instantiation including monitor
  overrides, mfg PID encode round-trip, mode 0x22 dispatch including
  NRC paths, request log capture and bounding.
- 55 tests total, all passing.

## 0.1.0 — 2026-04-27

First milestone release. Phase 1 + Phase 2 features both delivered ahead
of the two-week budget.

### Acquisition (Phase 1)
- Hardware abstraction layer (`uacj_obd/adapters/`):
  - `Elm327Adapter` — real ELM327 / STN1110 / STN2120 via python-obd
  - `MockAdapter` — drop-in 2015 Honda Civic for offline development
  - `ReplayAdapter` — saved sessions replayed as if live, with optional
    scenario overrides
- `AcquisitionSession` orchestrator: connect, static reads (VIN, DTCs,
  monitors, freeze frame), continuous live stream, auto-reconnect with
  exponential backoff, flat-file fallback log
- Storage:
  - SQLite metadata for vehicles, sessions, scenarios
  - Per-vehicle, per-session folder layout: `{VIN}_{make}_{model}_{year}/{session_id}/`
  - JSONL live data, CSV export
- Pluggable PID registry:
  - Standard SAE J1979 mode 0x01 PIDs (RPM, speed, temps, MAF, throttle,
    O2, fuel trims, EGR, oil temp, etc.)
  - Mode 0x22 manufacturer-specific PIDs with starter map for Ford / GM /
    Toyota / Honda — instructors extend via YAML drops
- FastAPI + static dashboard:
  - Live capture controls, gauges, DTC and monitor display
  - Per-session detail page: time-series chart per PID, raw event log,
    CSV download
  - Scenarios page: full edit (DTCs, monitors, vehicle, live overrides),
    push to simulator board, replay-into-session smoke test
- CLI: `uacj-obd capture`, `vehicles`, `sessions`, `serve`, `simulator`

### Simulator board (Phase 2)
- ECU response engine (`uacj_obd/simulator/ecu.py`):
  - Mode 0x01 (live PIDs + supported-PID bitmaps + monitor status)
  - Mode 0x02 (freeze frame)
  - Mode 0x03 / 0x07 / 0x0A (stored / pending / permanent DTCs)
  - Mode 0x04 (clear DTCs)
  - Mode 0x09 (VIN, calibration ID, ECU name)
  - DTC packing per SAE J2012, negative-response codes per ISO 14229
- ISO-TP framing (`iso_tp.py`): single-frame + first-frame +
  consecutive-frame, padding 0xAA per SAE J1979
- KWP2000 framing (`kline.py`): short and long form, arithmetic
  checksum, frame-length probing for the UART loop
- Hardware glue:
  - SocketCAN runtime via python-can
  - K-Line UART runtime via pyserial (L9637D transceiver)
  - Bus and serial objects are duck-typed so all logic is testable
    without hardware
- Pi-side HTTP service for scenario push
- `scripts/setup_pi.sh` — idempotent provisioner: SPI/MCP2515 overlay,
  UART, can0 via systemd-networkd, virtualenv, systemd unit

### Live-data merging
- Pushing a scenario merges the saved session's last-known value per
  PID as a baseline; instructor's `live_overrides` ride on top. The
  simulator answers every PID the original car answered, not just the
  ones explicitly modified.

### Documentation
- `README.md` — quick start, REST API summary, architecture
- `docs/wiring.md` — 10–15 min assembly guide with full BOM, Pi GPIO
  pinout, MCP2515 + L9637 wiring, OBD-II connector pinout, bench-test
  commands
- `docs/instructor.md` — 30-minute end-to-end tutorial including a
  starter scenario library

### Tests
- 45 tests covering: adapter lifecycle, session capture, DB layout,
  PID decode, manufacturer PIDs, replay round-trip with overrides,
  ISO-TP framing, KWP2000 framing, ECU dispatch (every supported mode
  + NRC paths), runtime integration including multi-frame VIN, full
  capture-to-scan-tool E2E over both CAN and K-Line
- `scripts/demo.py` — runnable end-to-end demo that prints proof of
  every pipeline step

### Out of scope (deferred to v2)
- SAE J1850 VPW / PWM (pre-CAN GM/Ford 2004–2007) — needs additional
  transceiver hardware (MC33390); STN2120-based acquisition adapter
  handles the read side, simulator needs an extra transmitter
- Multi-ECU emulation (ABS, BCM, transmission)
- 5-baud slow-init for ISO 9141-2 (KWP fast-init covers most 2003+
  vehicles)
