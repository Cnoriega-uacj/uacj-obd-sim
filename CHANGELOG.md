# Changelog

## 0.4.1 — 2026-06-15

Docs-only patch following the UACJ on-site bring-up. No code change.

### Documented: external 120 Ω CAN terminator is required at the OBD-II connector

During the on-site install with the Innova 5210 scan tool, the bus failed to
exchange OBD-II frames despite the Pi-side CAN stack being healthy
(`ERROR-ACTIVE`, correct 500 kbps timing, MCP2515 driver loaded). `candump can0`
showed a continuous flood of `can0  000  [0]` error frames whenever the scan
tool was plugged in, and went silent the moment it was unplugged.

Root cause: most consumer OBD-II scan tools (Innova 5210, Autel AL319, generic
ELM327 clones) do not carry their own 120 Ω terminator — they assume the car's
wiring provides the second terminator at the gateway/ECM end. Our v0.4.0 wiring
guide implicitly assumed the same, which left the OBD-II end of our pigtail
under-terminated. Signal reflections off the unterminated end caused the
MCP2515 to see continuous bit-level corruption, surfaced by SocketCAN as error
frames with ID 0 and zero payload.

Fix: add a 120 Ω resistor (1/4 W, 1% metal film, brown-red-brown) between OBD-II
pin 6 (CAN-H) and pin 14 (CAN-L), wired right at the connector body — not at
the MCP2515 end of the cable. Confirmed on-site: the Innova 5210 reported
"Linked to CAN" within seconds of installing the terminator, and Mode 01 / 03 /
09 traffic flowed correctly between scan tool and simulator.

### Documentation changes

- **`docs/wiring.md`** — added 120 Ω resistor to the BOM (now part 7), updated
  Connection 1's termination note to call out the OBD-end requirement
  explicitly, added explicit terminator row to Connection 3, and documented a
  multimeter sanity check (~60 Ω across pins 6 ↔ 14 means both terminators are
  in place).
- **`docs/wiring_walkthrough.md`** — added 120 Ω resistor to parts list, added a
  new "Connection 3.5: CAN bus terminator" section with no-solder install steps,
  ladder-rung topology diagram, and the common "in series instead of in
  parallel" mistake to avoid.
- **`docs/install.md`** — added three new troubleshooting entries: the
  `000 [0]` error-frame flood (→ missing terminator), non-standard IDs at the
  wrong bitrate (→ bitrate or crystal mismatch), and `BUS-OFF` /
  `ERROR-PASSIVE` recovery (`ip link set can0 down` + `up` with `restart-ms`).

## 0.4.0 — 2026-04-29

Pre-hardware-arrival hardening pass. The OBDLink SX, Pi 4, MCP2515,
and L9637D have been ordered but not yet delivered. Everything below
was built and validated without that hardware on hand, against
virtual buses and unit tests, so the day the parts arrive the
remaining work is wiring + bring-up rather than fresh code.

### J1850 framing for pre-CAN GM/Ford (no transceiver yet)
- `simulator/j1850.py` — SAE J1850 frame layout, CRC-8 (poly 0x1D,
  init 0xFF, xor-out 0xFF), encode/decode, 7-byte payload limit, and
  segmented response packing for VIN-style multi-frame replies.
- `simulator/j1850_runtime.py` — duck-typed transceiver wrapper that
  reads complete frames, dispatches through `EcuEmulator`, and writes
  responses back. Same hardware-free pattern as `kline_runtime.py`.
- 15 unit tests covering CRC, framing, NRC paths, and round-trip
  RPM / VIN / DTC / clear-codes via the runtime.
- The MC33390 (or equivalent VPW transceiver) is **still not in the
  v1 BOM** — see `docs/compatibility.md` and `docs/client-reply-precan.md`
  for the BOM-clarification thread with Cristopher.

### STN2120 (OBDLink SX) tuning path
- `Elm327Adapter` now probes the chip's ATI/STI banner at connect.
  When an STN-class chip (STN1110, STN2120, OBDLink SX/MX) is
  detected, the adapter sends ST-prefixed init commands (segmented-
  response auto-reassembly, ISO-TP flow-control padding) that a plain
  ELM327 clone silently ignores. `stn_mode=True/False` overrides the
  banner detection.
- 5 tests with a fake `pyobd` object verifying STN init runs only
  when expected — no real adapter required.

### Virtual-bus bench harness
- `scripts/bench.py` — round-trips RPM / VIN / DTC / clear-codes
  through:
    - python-can virtual bus (CAN, ISO-TP)
    - `os.openpty()` raw-mode pty pair (K-Line, KWP2000)
    - in-memory pipe (J1850)
- Runs in CI as a regression gate via `tests/test_bench.py`. This is
  the closest we can get to integration testing without the hardware.

### Honda PID coverage expanded
- Cristopher noted Honda is the most-frequent classroom case at UACJ,
  so `manufacturer_starter.yaml` and `simulator/encoders.py` gained
  five new Honda mode 0x22 PIDs: VTEC oil pressure, brake pedal
  switch, target idle, knock retard, fuel rail pressure.
- 8 tests, including encode → decode round-trip via the YAML registry.

### Documentation
- `docs/install.md` — complete TeamViewer-day install runbook,
  paste-and-go from SD-card flash through scan-tool smoke test, with
  troubleshooting table.
- `docs/compatibility.md` — predicted compatibility for 13 common
  scan tools, replacing the "❓" placeholders with reasoned forecasts
  (marked 🔮 to distinguish from verified ✅).
- `docs/client-reply-precan.md` — the BOM-clarification draft sent
  to Cristopher about the optional pre-CAN add-on.

### Engineering
- GitHub Actions CI workflow (`.github/workflows/ci.yml`): pytest +
  ruff on Python 3.11 and 3.12, every push and PR to main.
- Ruff-clean across the whole repo (29 → 0 issues).
- Test count: 63 → 118, all passing.

### Pre-arrival classroom polish (added 2026-05-01)

While the hardware ships, this set ensures the laptop and class flow
work the moment the parts land — no install-day code surprises.

#### Mode 0x06 — on-board monitoring test results
- `EcuEmulator._mode06` answers SAE J1979 mode 0x06 with packed test
  records (TID, CID, UASID, value, min, max). Bare `0x06` enumerates
  every configured test; specific TID returns just that test or the
  "no data" service-byte-only reply for unknown TIDs.
- `ScenarioState.obd_test_results` carries `{tid: (cid, value, min, max)}`
  per scenario; `scenario_to_state()` accepts both list and dict
  payload shapes.
- 5 tests including the bare-request enumeration path and the
  no-data-yet behaviour that matches real CARB compliance testers.

#### Extended manufacturer PID library
- 18 new mode 0x22 PIDs across Ford (5), GM (5), Toyota (4), Nissan (3)
  alongside the existing Honda 6.
- `select_make()` opt-in encoder banks (`default`, `nissan`, `toyota`)
  cleanly resolve key collisions where two makes use the same PID
  number — instructors switch via one call, no YAML hot-swap.
- 14 round-trip + bank-switching tests.

#### Five pre-loaded sample sessions
- `scripts/seed_sample_sessions.py` writes a Civic / Silverado /
  Corolla / F-150 / Sentra session to disk so the dashboard isn't
  empty on first boot. Each has 60 seconds of synthetic-but-plausible
  live data; all are flagged in `metadata.notes` as `synthetic sample`.
  Idempotent — re-running overwrites the same session IDs.
- 2 tests (creates 5 sessions, idempotent re-run).

#### Spanish/English UI toggle
- `web/i18n.js` — auto-detects browser language (es-MX → Spanish),
  injects an EN/ES toggle button into every page header, persists
  the choice in `localStorage`, and broadcasts a `uacj:lang-changed`
  event so dashboard JS can re-render dynamic content.
- All five HTML pages (`index`, `scenarios`, `classroom`, `diff`,
  `session`) now carry `data-i18n` annotations on their static labels.

#### Backup / restore
- `POST /api/backup` streams a single ZIP containing `uacj.db` +
  every session folder + a `BACKUP_INFO.json` schema marker.
- `POST /api/restore` validates the ZIP shape, rejects zip-slip
  attacks, snapshots the existing data dir as `.restore-backup-*`
  before overwriting, then extracts.
- Dashboard left-rail buttons wire the round trip end-to-end.
- 5 tests including round-trip, malformed zip rejection, and
  zip-slip path rejection.

#### Wiring guide expansion
- `docs/wiring.md` now includes both pre-CAN add-ons end-to-end:
  - GM J1850 VPW DIY transceiver (LM358 + 2N7000 × 2 + R/C kit, ~$10)
  - Ford J1850 PWM (AM26LS31 driver + AM26LS32 receiver + 120 Ω
    terminator, ~$15)
- Each add-on includes pin-by-pin wiring tables, an ASCII schematic,
  bench-test commands, and a "common gotchas" list (5V vs 7V VPW,
  termination resistor placement, MOSFET orientation, dual-UART
  conflict resolution).

#### Laptop installer bundle
- `installer/start_uacj.bat` (Windows) / `start_uacj.sh` (Mac/Linux):
  one-click launchers that create the venv, install the package,
  seed the sample sessions, and open the browser to the dashboard
  on first run.
- `scripts/build_installer_zip.py` packages the source + launchers
  + docs into `dist/uacj-obd-sim-installer-v{version}.zip`.

#### Instructor quick-start
- `docs/instructor_quickstart.md` (English) and
  `docs/instructor_quickstart_es.md` (Spanish) — single-page cheat
  sheets sized for A4/Letter, designed so a substitute teacher can
  run a class with just the printout.

## 0.3.0 — 2026-04-27

Three additions, all reviewed for legal risk before implementation
(per project guidance) and confirmed zero-risk before code was written:

### Session diff view (no risk: read-only on local data)
- New `uacj_obd.diff.diff_sessions(folder_a, folder_b)`: structured
  comparison of two captured sessions covering vehicle identity, DTCs
  (added / removed / common), readiness monitor changes, and per-PID
  summary statistics (n, min, max, mean, median, delta-mean and delta%
  with a "shifted" flag for >5% drift).
- `GET /api/diff?a=...&b=...` exposes it.
- New `/diff.html` page: dropdowns for both sessions, side-by-side
  output with color-coded added / removed / shifted rows.

### 5-baud slow-init (no risk: published ISO 14230-2 standard)
- `simulator/kline.slow_init_step()`: stateless single-byte handler
  for the ISO 9141-2 / KWP2000 5-baud handshake (address byte 0x33
  → sync 0x55 + KB1 + KB2; tester's inverted KB2 → ECU's inverted
  address). Lets the simulator answer scan tools that don't speak
  the more common KWP fast-init.
- `simulator/kline_runtime` recognizes handshake bytes on the UART
  and answers them transparently — the byte never reaches the frame
  decoder.

### Compatibility log (no risk: documentation)
- New `docs/compatibility.md`: methodology for verifying scan-tool
  compatibility against the simulator (six-step procedure: connect,
  VIN, DTCs, live data, clear, freeze frame), template for adding
  entries, list of common student / professional tools to test once
  hardware arrives, known v0.x simulator limitations.

### Items deferred pending legal review (not built):
- Multi-ECU emulation including BCM (immobilizer / anti-theft risk)
- PDF session report (potential misrepresentation as legal inspection)

### Tests
- 8 new tests covering diff (DTCs added/removed/common, PID stats,
  endpoint round-trip, 404 handling) and slow-init (address-byte
  reply, ~KB2 reply, unrelated bytes ignored, runtime handshake
  passthrough). 63 tests total, all passing.

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
