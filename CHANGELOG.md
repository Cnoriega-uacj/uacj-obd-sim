# Changelog

## 0.6.1 — 2026-06-19

**Continued hardening pass.** Two more modules lifted to high coverage,
keeping the same v0.6.0 strategy: exercise user-facing methods + every
defensive error path with deterministic fixtures, no hardware required.

### Coverage improvements

| Module | v0.6.0 | v0.6.1 |
|--------|--------|--------|
| `acquisition/session.py` | 76% | **86%** |
| `simulator/j1850_runtime.py` | 60% | **98%** |
| **Project total** | 82% | **84%** |

### `tests/test_acquisition_v060.py` (+14 tests)

Covers the `AcquisitionSession`'s defensive paths with a `BadAdapter`
whose individual methods can be configured to raise `AdapterError`:

- `_capture_static` independently tolerates DTC / monitor / freeze
  frame read failures (4 tests; each failure mode is its own test
  + a combined-failures test).
- `_connect` raises `AdapterError` when status says not connected.
- `run()` called without `start()` raises `RuntimeError`.
- `run()` falls back to the curated 14-PID list when adapter
  reports zero supported PIDs OR when `supported_pids()` raises.
- `run()` recovers from `AdapterError` mid-loop via reconnect with
  capped exponential backoff.
- `run()` stops when `max_reconnects` is exceeded (vs. spinning
  forever).
- `stop()` signal breaks the loop promptly.
- `_read_manufacturer_pid` returns None for: missing registry,
  unknown PID, adapter error during `read_raw`.

### `tests/test_j1850_runtime_v060.py` (+12 tests)

Same pattern as the K-Line runtime tests in v0.6.0. A deque-backed
`FakePort` substitutes for an MC33390-style transceiver:

- `handle_request_bytes` Mode 09 round-trip (VIN reassembled from
  segmented multi-frame response), invalid frames return empty,
  NRC payloads still wrap.
- `_read_one_frame` empty port, complete frame assembly, partial
  frame on idle timeout.
- `run()` loop responds in a thread, recovers from read/write
  errors, stops promptly when idle.
- Custom `source_address` honoured; default is `SRC_ECU_DEFAULT`.

### Remaining lower-coverage modules

| Module | Coverage | Path forward |
|--------|----------|--------------|
| `adapters/elm327.py` | 37% | Hardware-dependent; covered by on-site integration. |
| `cli.py` | 68% | Remaining 32% is `serve`/`simulator` long-running server bodies; FastAPI-level tests already exercise the underlying logic. |
| `api/app.py` | 85% | Remaining 15% is rarely-hit error branches and a few admin endpoints. |
| `simulator/encoders.py` | 83% | Edge-case formulas in newer PID encoders; not yet exercised by every direction. |

Total tests 352 → 378 (+26). No regressions. Project coverage 82% → 84%.

## 0.6.0 — 2026-06-19

**Hardening pass for the v0.5.5 audit's deferred coverage items.**
Three more modules lifted to high coverage by exercising user-facing
methods with deterministic fixtures (no hardware required).

### Coverage improvements

| Module | v0.5.5 | v0.6.0 |
|--------|--------|--------|
| `storage/session_store.py` | 61% | **100%** |
| `simulator/kline_runtime.py` | 44% | **89%** |
| `adapters/factory.py` | 33% | **100%** |
| **Project total** | 79% | **82%** |

### `tests/test_session_store_v060.py` (+16 tests)

Closes every uncovered path in the session storage layer:

- `SessionStore.list_session_dirs` — empty store, one session, stray
  non-directory files at the root, multiple vehicles.
- `SessionWriter.write_samples` — bulk write returns the right count;
  empty iterable returns 0.
- `SessionWriter.write_raw` — appends timestamped lines, accepts
  arbitrary string payloads.
- `SessionWriter.export_csv` — round-trips live samples, tolerates
  blank lines in the source JSONL, header-only on empty sessions.
- `SessionWriter.export_json` — bundles metadata + live data + DTCs +
  monitors + freeze frame into a single dictionary; correctly omits
  missing pieces; `write_freeze_frame(None)` is a clean no-op.

### `tests/test_kline_runtime_v060.py` (+13 tests)

Exercises the K-Line UART runtime with a `FakeSerial` (deque-backed
duck-typed pyserial substitute, no hardware needed):

- `handle_request_bytes` — Mode 09 PID 02 round-trip, invalid frames
  return empty, NRC payloads still wrap correctly.
- `_read_one_frame` — empty UART returns None, 5-baud slow-init
  address (0x33) triggers handshake reply, ~KB2 inverse byte handled,
  full frames assembled correctly, long-form frames with separate
  length byte handled.
- `run()` blocking loop — responds to requests in a thread, recovers
  from UART read errors, survives UART write errors without crashing,
  stops promptly when idle.
- `open_serial()` raises a helpful RuntimeError when pyserial isn't
  installed.

### `tests/test_factory_v060.py` (+8 tests)

Covers `open_adapter()`'s dispatch logic:

- `"mock"` returns MockAdapter; kwargs pass through to the constructor.
- Kind matching is case-insensitive (`"MOCK"`, `"Mock"`, `"mock"`).
- Unknown kinds raise a clean `ValueError` with the right message.
- `"elm327"` / `"stn2120"` / `"real"` all alias to `Elm327Adapter`
  (3 separate construction calls verified).
- `"replay"` returns ReplayAdapter from a real on-disk session
  directory.
- `"auto"` falls back to MockAdapter when ELM327 construction raises
  (mocked: no hardware needed).
- `"auto"` uses ELM327 when construction succeeds.

### Remaining lower-coverage modules (truly hardware-dependent)

| Module | Coverage | Why |
|--------|----------|-----|
| `adapters/elm327.py` | 37% | Path through python-obd + real serial. Covered by on-site integration on the client's Mazda3. |
| `simulator/j1850_runtime.py` | 60% | J1850 transceiver is optional add-on; covered by virtual-bus bench harness. |
| `cli.py` | 68% | Remaining 32% is `serve` / `simulator` long-running server bodies, exercised by the FastAPI-level tests. |
| `acquisition/session.py` | 76% | Error-recovery branches need PTY-based testing scaffolding (planned for v0.6.x continuation). |

Total tests 315 → 352 (+37). No regressions.

## 0.5.5 — 2026-06-19

**Audit-driven test coverage release.** Ran `pytest --cov` over the
whole project to surface coverage gaps that the on-site bug pattern
would have classified as Pattern A risk (untested code paths can
break silently). Two modules had **0% coverage** despite being on
the critical path; this release closes both gaps to **100%** and
**68%** respectively, lifting overall project coverage from 74% to
79% and total tests from 293 to 315.

### Critical gap #1: `simulator/server.py` (0% → 100%)

The FastAPI app running on the Pi to receive `/api/sim/load` from
the dashboard had zero direct tests. `scenario_to_state` and
`EcuEmulator` were heavily tested in isolation; the HTTP routes that
glue them together were not. A silent regression in `/api/sim/load`
(e.g. v0.5.0's ReplayEngine wiring) would have shipped without any
test failing.

11 new tests in `tests/test_simulator_server_v055.py`:
- `/api/sim/health` returns ok with default state
- `/api/sim/state` returns the full snapshot including replay sub-dict
- `/api/sim/load` with VIN updates ECU state
- `/api/sim/load` with `live_timeseries` starts the replay engine
- Loading a second scenario STOPS the previous replay (v0.5.0
  no-race invariant)
- Empty payload doesn't crash
- `/api/sim/clear` clears DTCs
- `/api/sim/replay/stop` when nothing running is clean
- `/api/sim/log` returns empty list on fresh ECU
- ECU requests populate the log
- `?limit=N` parameter respected

### Critical gap #2: `cli.py` (0% → 68%)

The click-based CLI is the entry point Cristopher actually invokes
(`uacj-obd serve`, `uacj-obd simulator`, `uacj-obd capture`). Zero
tests meant a silent break in argument parsing or subcommand
dispatch would only be caught after deploy.

11 new tests in `tests/test_cli_v055.py`:
- Help text is available at every level
- `capture --adapter mock` runs end-to-end and persists a session
- `sessions`, `vehicles`, `pids` subcommands exist or fail cleanly
- `serve --help` and `simulator --help` work
- `-v` verbose flag accepted
- `--data` creates the directory tree lazily

The remaining 32% is the `serve` and `simulator` command bodies
which would start long-running servers — exercised at runtime via
the existing FastAPI-level tests, not the CLI shell.

### Remaining coverage gaps (deferred)

| Module | Coverage | Why deferred |
|--------|----------|--------------|
| `adapters/elm327.py` | 37% | Hardware-dependent paths; covered by integration testing on the client's actual Mazda3. |
| `adapters/factory.py` | 33% | `open_adapter("auto")` requires hardware probing. |
| `simulator/kline_runtime.py` | 44% | K-Line is a real protocol but the runtime wrapper needs PTY-based testing scaffolding to exercise without hardware. |
| `simulator/j1850_runtime.py` | 60% | J1850 transceiver is optional add-on; runtime is partially covered by virtual-bus bench tests. |
| `storage/session_store.py` | 61% | Backup/restore filesystem paths need temp-dir fixtures; v0.6.x cleanup. |

These are tracked for v0.6.0 hardening pass.

### Project-wide audit conclusions

- No TODO / FIXME / XXX / HACK markers in the codebase (`grep -rn`).
- No circular imports (Python startup clean).
- Pattern E symmetry tests for Mode 06 and Mode 09 in place.
- Pattern C `_decode_string_response` / `_clean_ascii_field` applied
  at every adapter boundary identified in the v0.4.11 sweep.

Total tests: 293 → 315. No regressions. Coverage 74% → 79%.

## 0.5.4 — 2026-06-19

**Scenario-editor quick-setup buttons for monitors.** Last item from
Cristopher's punch list: "doesn't show CCM and EGR even if I input
them manually." The dashboard's monitor table only renders entries
already in `active.monitors` — if the captured session never received
monitor info from the ECU (some adapters don't return Mode 01 PID
0x01 reliably), the table is empty and there's no obvious way for the
instructor to add the standard 11 monitors by name.

### Three new buttons above the monitor table

- **Set up standard monitors** — populates the 11 standard SAE J1979
  monitors in their canonical order, with sensible defaults
  (Misfire/Fuel/CCM/Catalyst/HCAT/EVAP/O2/HTR/EGR supported and
  ready; Secondary Air + A/C Refrigerant unsupported because most
  modern vehicles don't have either).
- **All ready** — marks every currently-listed monitor as supported
  AND ready. Useful for the "healthy vehicle" baseline scenario.
- **All incomplete** — marks every currently-listed monitor as
  supported but NOT ready. Mirrors the "DTCs were just cleared"
  state — drive-cycle pending.

The buttons mutate the in-memory scenario; the instructor still has
to click Save to persist.

### Tests prevent JS ↔ simulator drift

The 11 monitor names in the JS `STANDARD_MONITORS` array MUST match
the keys the simulator's `_MONITOR_NAME_TO_POSITION` recognises —
otherwise the bit-packing silently drops monitors and the Innova
hides badges (exactly the bug Cristopher chased for hours on
v0.4.5). 5 new tests in `tests/test_v054_monitor_quick_setup.py`:

1. Every name in the Python source-of-truth list is in
   `_MONITOR_NAME_TO_POSITION`.
2. The 11-monitor preset packs into the expected J1979 byte
   patterns (continuous supported/complete, CAT through EGR
   supported, AIR + A/C unsupported, byte D zero).
3. The JS file actually contains every expected monitor name —
   if you edit the Python source-of-truth without updating the JS,
   this fails.
4. "All ready" button semantics produce byte B upper nibble = 0
   AND byte D = 0.
5. "All incomplete" button semantics produce byte D = byte C
   exactly (every supported monitor also not-complete).

Total tests 288 → 293. No regressions.

### Why it took until v0.5.4 to add this

v0.4.5 fixed the encoder so MANUAL monitor entries with the right
names would encode correctly. But "the right names" was implicit
knowledge the instructor had to know. v0.5.4 makes that knowledge
explicit (in the JS preset) AND enforced by tests (so it can't drift).

## 0.5.3 — 2026-06-19

**Dashboard show-all-captured-PIDs toggle + capture all by default.**
Client captured 113 PIDs from his Mazda3 but the dashboard's LIVE
PARAMETERS panel only showed 12. Two bugs in one — capture was
constraining to a hardcoded list, and even if it hadn't been, the
display was hardcoded to render only those 12. v0.5.3 fixes both.

### Capture: server discovers, dashboard doesn't override

The dashboard's `start` button used to POST a 12-PID list to
`/api/sessions/start`, which overrode v0.4.9's "auto-discover all
supported PIDs from the adapter" behaviour. Removed — the dashboard
now sends no list, so the server uses everything the connected car
supports (the client's Mazda3 reports 113).

Backwards compatible: callers who explicitly pass a `pids` list (CLI,
scripts, custom tooling) still get the constraint they asked for.

### Display: toggle between focused view and full view

New "Show all captured PIDs" checkbox above the LIVE PARAMETERS
panel:

- **Unchecked (default)** — render the curated 12-PID common set
  (RPM, speed, coolant, throttle, MAF, fuel trim, etc.) so the panel
  stays scannable for general use.
- **Checked** — render EVERY PID for which we have a sample,
  alphabetical by PID key. Catalyst temps, individual O2 sensors,
  EGR position, EVAP pressures, accelerator pedal positions, fuel
  rate, oil temp, ambient air temp — everything the car emits.

A small counter pill shows `<rendered> / <total available>` so the
operator can see at a glance how much they're showing vs hiding.

### Data fetch tuning

The dashboard now fetches `/api/sessions/{id}/live?limit=2000` instead
of `?limit=200`. With 100+ PIDs cycling through serially on a real
OBDLink SX, 200 samples can leave the rarer PIDs without any value
yet. 2000 is enough for any classroom capture without bloating the
response.

### Tests

3 new tests in `tests/test_v053_full_pid_capture.py`:
- Capture started without `pids` field uses adapter's full supported
  set.
- Explicit `pids` field is still honoured (backwards compatibility).
- `/api/sessions/{id}/live?limit=2000` returns without error.

Total tests 285 → 288. No regressions.

### i18n

New translation keys: `live.show_all`.
- English: "Show all captured PIDs"
- Spanish: "Mostrar todos los PIDs capturados"

## 0.5.2 — 2026-06-19

**Offline VIN → make / model year / region decoder.** Client
captures repeatedly showed "Unknown vehicle" because the OBD-II VIN
read often returns only the 17-character VIN itself — make, model,
and year aren't on the wire. v0.5.2 derives the make / year /
country-of-assembly from the VIN structure offline (no NHTSA call,
no internet) and exposes them through new dashboard endpoints.

### New `uacj_obd.vin_decoder` module

- `decode_vin(vin)` → `VinDecodeResult` dataclass with `valid`,
  `make`, `region`, `model_year`, `plant_code`, and `error`.
- Validates VIN length (must be 17), allowed character set (ISO 3779
  excludes I / O / Q), and reports clean errors.
- WMI table covers ~85 manufacturer-region codes for the makes the
  client will see in a Mexican automotive school: Honda, Toyota /
  Lexus, Ford, GM (Chevrolet / GMC / Buick / Cadillac / Pontiac),
  Mazda, Nissan, Hyundai, Kia, VW / Audi, BMW, Mercedes-Benz, Subaru,
  Mitsubishi, Dodge / Ram / Chrysler / Jeep — across US / Canada /
  Mexico / Japan / Korea / Germany / Brazil / Hungary build sites.
- Model year decoding uses the ISO 3779 30-character ladder. Real
  VIN data showed position 7 isn't a reliable second-cycle
  disambiguator (different manufacturers used it differently), so the
  decoder picks the most recent cycle that doesn't produce a future
  year (a tool in 2026 reports 'A' as 2010, '8' as 2008, 'C' as 2012).

### Two new endpoints on the dashboard

- **`GET /api/vin/decode?vin=...`** — standalone decoder for the
  scenario editor to auto-fill make/year fields when the user
  pastes a VIN.
- **`GET /api/vehicles`** is now enriched with `decoded_make`,
  `decoded_year`, `decoded_region`, `vin_valid` fields per row. The
  underlying database row is left untouched — the enrichment happens
  at response time, so existing data benefits without any migration.

### Tests

23 new tests in `tests/test_vin_decoder.py`:
- The client's actual Mazda3 VIN decodes to 2012 / Mazda / Japan
- The mock Civic VIN decodes to 2015 / Honda / Canada
- The Silverado smoke-test VIN decodes to 2008 / Chevrolet
- Validation rejections for short / long / I-O-Q chars / None / empty
- Year cycle disambiguation: 'A' picks 2010 not 1980, '8' picks 2008
  not 2038
- Unknown WMIs return None for make rather than guessing
- Lowercase + whitespace tolerance
- `/api/vin/decode` endpoint shape for valid + invalid VINs
- `/api/vehicles` includes the decoded fields

Total tests 262 → 285. No regressions.

### What's still out of scope (NHTSA-territory)

Model name, trim, engine, transmission. Those require the NHTSA vPIC
database (~600 MB) or a paid lookup service. The kit is meant to work
offline; bundling that dataset is v0.6.x scope if there's demand.

## 0.5.1 — 2026-06-18

**Pi as standalone WiFi access point.** Client asked for the kit to
work in places where there is no school WiFi or no internet — the
parking lot, a shop bay, a remote training site. v0.5.1 turns the Pi
into its own WiFi network so the laptop can connect to it directly,
without any external infrastructure.

### Two new shell scripts

- **`scripts/setup_pi_hotspot.sh`** — idempotent installer. Run once
  on the Pi as root. Installs hostapd + dnsmasq, writes their
  configs, configures static IP on wlan0, enables/starts both
  services. Defaults to SSID `UACJ-SIM`, password `uacj1234`, Pi IP
  `192.168.50.1`, DHCP pool `192.168.50.10-50`. Override any of those
  via environment variables (e.g. `UACJ_AP_SSID=Custom UACJ_AP_PASS=...`).

- **`scripts/revert_pi_hotspot.sh`** — restores the pre-AP-mode
  configs from a backup directory the setup script created. Returns
  the Pi to client-mode WiFi (joining whatever wpa_supplicant
  configures). Idempotent.

The setup script wraps its dhcpcd block in marker comments so the
revert script can strip exactly that block without touching anything
else in dhcpcd.conf.

### New `uacj_obd.pi_hotspot` Python module

The bash scripts are operator-facing; the Python module is the
testable layer. `HotspotSettings` dataclass holds every knob, with
constructor-time validation:

- WPA2 passphrase length 8-63 chars (raises ValueError otherwise)
- 2.4 GHz channel range 1-14
- ISO 3166-1 alpha-2 country code (exactly 2 letters)
- SSID length 1-32 chars

`render_hostapd_conf()` / `render_dnsmasq_conf()` /
`render_dhcpcd_block()` return the exact text those config files
should contain. Plus `dashboard_url_for_clients()` — small helper for
the future dashboard UI that will tell the instructor "your Pi
broadcasts WiFi `X`, dashboard reachable at `Y`".

### Tests

20 new tests in `tests/test_pi_hotspot.py` covering:
- Defaults match the bash script's defaults (lock-in)
- WPA2 length / channel / country / SSID validation
- hostapd.conf includes every required directive AND no WPA1/WEP
- dnsmasq.conf binds to wlan0, advertises Pi as gateway + DNS
- dhcpcd block has revert markers and disables wpa_supplicant hook
- dashboard URL helper for default and custom ports

Total tests: 242 → 262 (+20). No regressions.

### New documentation

`docs/wifi_hotspot.md` — complete walkthrough: defaults, setup,
laptop connection flow, revert, troubleshooting table, network
architecture diagram. Calls out the SSH-over-WiFi disconnect
gotcha so operators don't get stranded mid-setup.

### Combined with v0.5.0

A captured Mazda3 drive can now run end-to-end in a field with zero
external infrastructure:
1. Pi boots, broadcasts `UACJ-SIM` WiFi.
2. Laptop joins that WiFi, opens dashboard at localhost:8000.
3. Push the Mazda3 scenario (with `replay: true`) to
   `http://192.168.50.1:8765`.
4. ReplayEngine animates the captured PIDs at original cadence.
5. Student plugs Innova into the simulator board — sees a living,
   breathing engine instead of static values.

No school WiFi, no router, no internet. Truly portable kit.

## 0.5.0 — 2026-06-18

**Dynamic time-series live-data replay.** Client requested this feature
during on-site validation: when a captured session is pushed as a
scenario, the simulator should play back the recorded RPM / speed /
throttle / fuel-trim values as they changed over time, instead of
freezing at one static value. v0.5.0 ships this end-to-end through the
dashboard → simulator pipeline with no UI change required.

### Pi-side replay engine

New `uacj_obd/simulator/replay_engine.py` contains:

- `TimedSample` — `(t_offset, pid_key, value)` dataclass for one
  point on the replay timeline.
- `_normalise_samples()` — converts wire-format dicts into normalised
  `TimedSample` records. Accepts compact form
  `{"t": 0.5, "pid": "010C", "value": 1500}`, LiveSample dump-shape
  `{"ts": "2026-06-18T...", "pid": "...", "value": ...}` (ISO
  timestamp), and out-of-order or partial entries (sorted and
  filtered automatically).
- `ReplayEngine` — background thread that mutates `state.live` at the
  recorded cadence. Supports both `loop=True` (default — restarts
  from beginning, ideal for classroom demos) and `loop=False` (one
  pass). Also exposes a deterministic `step(current_time)` API for
  tests that need to walk the timeline without real wall clock.

The engine is the only producer mutating `state.live`. The ECU
dispatcher remains pure read-from-state, so no locking is needed
(single-key dict operations are atomic under CPython's GIL).

### Pi-side server lifecycle

`make_simulator_server()`:

- Stops any previous engine before swapping state on `/api/sim/load`
  (so old writes can never race with the new scenario's static
  `live_overrides`).
- Starts a new engine if the scenario carries `live_timeseries`.
- Exposes `/api/sim/replay/stop` for the dashboard to halt replay
  while keeping the most-recent values frozen on state.
- Surfaces replay status in `/api/sim/state` — `running`,
  `duration_seconds`, `samples_applied`, `iterations`, `loop`.

### Dashboard opt-in

Scenarios now carry `replay: bool` (default `False`) and
`replay_loop: bool` (default `True`) fields on `Scenario` and
`ScenarioCreateRequest`. When `replay=True`, `/api/scenarios/{id}/push`
reads every line of the source session's `live_data.jsonl` and
attaches the full time-series as `live_timeseries` in the payload.

Defaults are preserved for v0.4.x compatibility — scenarios that don't
opt in keep the static-value behaviour with no extra overhead.

### Safety rails

- `replay_max_samples` config field (default 50,000 ≈ 10 minutes of
  100-PID capture) caps how many samples the dashboard ships per
  push. Pathological captures can't blow the HTTP request size.
- HTTP push timeout raised from 5 s to 10 s to accommodate a large
  payload upload.
- Time-series samples in a stopped/swapped scenario are dropped
  cleanly — no leftover thread mutating the new state.

### Tests

- 20 unit tests in `tests/test_replay_engine.py` — normalisation
  shapes, step API, thread lifecycle (start, stop, idempotent start,
  prompt stop during long waits), loop vs non-loop behaviour, full
  `scenario_to_state` integration, and the static-overrides-don't-get-
  touched invariant.
- 3 integration tests in `tests/test_v05_push_with_replay.py` — full
  dashboard push endpoint, no-replay path leaves payload untouched,
  replay path attaches the time-series, round-trip through
  `scenario_to_state` produces a non-empty `ScenarioState.live_timeseries`.

Total tests: 219 → 242 (+23).

### What this enables for the client

In the classroom, an instructor captures a 60-second drive of any car
(idle → cruise → acceleration → brake → idle). Pushed with `replay:
true`, the Innova then sees RPM, speed, MAF, throttle, and every other
captured PID move exactly as the real car was moving. Loops
indefinitely so a single capture drives a whole lecture.

Static scenarios (P0420 frozen at idle for teaching catalyst
diagnosis) keep working unchanged.

## 0.4.14 — 2026-06-18

Extends the Pattern E symmetry recipe from v0.4.13 to Mode 06 (on-board
monitoring test results). Pattern E is now architecturally impossible
to recur in either Mode 09 or Mode 06.

### Mode 06 supported-MIDs bitmap

Per SAE J1979, Mode 06 TIDs 0x00 / 0x20 / 0x40 / 0x60 / 0x80 / 0xA0 /
0xC0 / 0xE0 are the "supported MIDs" range bitmaps strict scan tools
query first. Each returns a 4-byte bitmap advertising which of the
next 32 MIDs are supported.

Previously the simulator NRC'd these bitmap TIDs because the
dispatcher only knew about specific MIDs (or returned the bare
service byte for unknown ones). Strict scan tools that respect the
bitmap would never bother asking for any MID's actual data.

Fix: `_mode06_supported_bitmap()` derives the bitmap dynamically from
the keys of `state.obd_test_results` — the same source the dispatcher
uses to answer individual MID queries. Adding a MID to the scenario
automatically advertises it.

### Mode 22 audit outcome

Mode 22 (manufacturer-specific PIDs) was audited and does NOT need
this treatment. Mode 22's PID space is 16-bit and manufacturer-
defined; there is no standard "supported PIDs" structure to drift.
The dispatcher already answers any PID in `state.live` and NRCs
unknown ones — no static advertisement to keep in sync.

### Five new symmetry tests for Mode 06

Same pattern as the Mode 09 tests in v0.4.13:

1. `test_mode06_bitmap_advertises_implemented_mids_in_range_0x00` —
   bitmap at TID 0x00 matches configured MIDs in the 0x01-0x20 range.
2. `test_mode06_bitmap_advertises_implemented_mids_in_range_0x20` —
   same for the 0x21-0x40 range.
3. `test_mode06_bitmap_empty_when_no_results_configured` — clean
   "nothing supported" advertisement.
4. `test_mode06_bitmap_symmetry_for_every_advertised_range` —
   bitmap → advertised MIDs → dispatcher must answer each. Catches
   "advertised but not implemented" drift.
5. `test_mode06_dispatcher_matches_state_obd_test_results` —
   configured MIDs → at least one bitmap range must advertise each.
   Catches "implemented but not advertised" drift.

Total tests: 214 → 219.

### Pattern E status after v0.4.14

| Mode | Pattern E status |
|------|------------------|
| Mode 01 | Already had dynamic bitmap (pre-existing). |
| Mode 06 | **Eliminated v0.4.14.** Dynamic bitmap + 5 symmetry tests. |
| Mode 09 | **Eliminated v0.4.13.** Dynamic bitmap + 5 symmetry tests. |
| Mode 22 | Not applicable — no static advertisement to drift. |

Drift can no longer ship silently for any mode that has an
advertised "supported" list.

## 0.4.13 — 2026-06-18

Eliminates the **Pattern E** root cause discovered while shipping
v0.4.12: static "supported-PIDs" bitmaps drifting away from the
dispatcher's actual implementation. Also closes a Mode 09 spec gap
that v0.4.12 missed.

### Pattern E root-cause fix

Mode 09 PID 0x00 (supported-PIDs bitmap) was a hand-coded constant.
Every time a new Mode 09 PID was added, the constant had to be
updated by hand or it would drift out of sync. v0.4.12 found the
drift the hard way — the bitmap was advertising 0x06 (CVN, which we
hadn't implemented yet) and NOT advertising 0x0A (ECU name, which
we did implement). Strict scan tools that only query advertised
PIDs would never read ECU name on the simulator.

Fix: a new constant `_MODE09_IMPLEMENTED_PIDS` is the single source
of truth, and `_mode09_supported_bitmap()` derives the response
bytes from it dynamically. Adding a Mode 09 PID now requires
updating ONE place (the set), and the bitmap follows automatically.

### Mode 09 spec gap closed

python-obd's command table lists Mode 09 PIDs 0x01, 0x03, 0x05 —
the "message count" pre-queries strict scan tools issue before
reading VIN / Calibration ID / CVN. These tell the tool how many
data items to expect. v0.4.12 left them as NRC, which on a strict
tool could short-circuit the actual data read.

v0.4.13 adds them all (always answering count=1, since we emulate a
single ECU). After this release, every Mode 09 PID python-obd knows
about plus PID 0x0A (ECU name, non-standard but common) is
implemented and advertised.

### Test layer that prevents drift recurring (`test_spec_symmetry.py`)

The fix above closes today's drift, but a future contributor could
re-introduce the same Pattern E bug by adding a dispatcher branch
without updating the set, or vice versa. Three new symmetry tests
make that impossible to ship silently:

1. `test_mode09_bitmap_advertises_exactly_what_is_implemented` —
   decodes the bitmap and asserts it matches the implemented set.
2. `test_mode09_every_advertised_pid_returns_positive_response` —
   probes every advertised PID; any that returns NRC is a drift.
3. `test_mode09_every_implemented_pid_is_in_advertised_set` —
   probes every PID in the bitmap range; any positive response not
   in the implemented set is also a drift.

Plus:
4. `test_mode09_advertised_pid_set_matches_real_scan_tools` — locks
   in v0.4.12's empirical truth (Innova 5210 queries PIDs 0x02,
   0x04, 0x06, 0x0A).
5. `test_mode09_message_count_pids_return_one` — locks in the
   new 0x01/0x03/0x05 responses.

Total tests: 209 → 214 (+5 symmetry).

### What this means for Pattern D recurrence

The earlier audits (v0.4.11) and individual patches (v0.4.2, v0.4.3,
v0.4.5, v0.4.11, v0.4.12) all addressed **Pattern D: SAE J1979
implementation incomplete** by finding individual missing Mode/PID
combinations and adding them. They patched instances. None
eliminated the root cause.

v0.4.13 takes a different approach for Mode 09: the symmetry test
turns "spec completeness" into a property the codebase ENFORCES
rather than something contributors have to remember. It does not
backfill missing Modes (Mode 06's 88 PIDs, Mode 02's freeze-frame
variants, Mode 22's manufacturer PIDs are still partial), but it
establishes the pattern for closing those gaps in v0.5.0 — add the
same symmetry test per mode, derive bitmaps dynamically, declare an
implemented set as the source of truth.

## 0.4.12 — 2026-06-18

Adds Mode 09 PID 0x06 (CVN — Calibration Verification Number)
support and fixes a related bitmap inconsistency. Surfaced when the
client's Innova 5210 displayed his real 2012 Mazda3's CVN
(`CD A0 8E 85`) and the simulator could not match it because the
service wasn't implemented.

### New: CVN (Mode 09 PID 0x06)

- `ScenarioState.cvn` field added.
- New `_parse_cvn` helper accepts CVN in every common shape:
  - `"CDA08E85"` — 8 hex chars, no separators
  - `"CD A0 8E 85"` — space-separated bytes (Innova display style)
  - `"CD-A0-8E-85"` / `"CD:A0:8E:85"` — dash- or colon-separated
  - `"0xCDA08E85"` — with hex prefix
  - `bytes(b"\\xCD\\xA0\\x8E\\x85")` — raw bytes pass-through
- Short values are zero-padded, long values are truncated to 4 bytes,
  unparseable values return NRC. Always produces a well-formed 7-byte
  response (0x49 0x06 0x01 + 4 bytes).
- `scenario_to_state` now propagates the CVN field through the
  capture → scenario → simulator pipeline.

### Fix: Mode 09 PID 0x00 supported-PIDs bitmap

The bitmap returned `0x54 0x00 0x00 0x00` — advertising PIDs 0x02
(VIN), 0x04 (Cal ID), and 0x06 (CVN) but **not** PID 0x0A (ECU
name). The dispatcher still answered PID 0x0A regardless, so the
mismatch was silent on tolerant scan tools — but a strict scan tool
that only queries advertised PIDs would never read the ECU name.

Now byte B = 0x40 correctly advertises PID 0x0A.

### Tests

9 new tests:
- 7 for CVN (round-trip, every accepted input shape, NRC paths,
  short-value padding, invalid-char rejection)
- 1 for the corrected supported-PIDs bitmap
- 1 real-vehicle integration test using the client's actual Mazda3
  values (VIN `JM1BL1L72C1627697`, Cal ID `PE2GEM000PE06020`, CVN
  `CD A0 8E 85`, ECU `ECM`) — all four Mode 09 PIDs verified
  end-to-end through `scenario_to_state` → `EcuEmulator`.

Total tests 200 → 209.

## 0.4.11 — 2026-06-18

**Audit-driven stabilization release.** The previous ten patches
(v0.4.1 through v0.4.10) all addressed real bugs surfaced during
on-site validation, but each was reactive. Rather than continue
patching one bug at a time, this release does a systematic audit of
the four root-cause patterns those bugs revealed and fixes every
related issue found, then closes the meta-cause by adding a
real-vehicle round-trip integration test harness that exercises the
full pipeline against the actual data shapes python-obd returns from
real hardware.

### Audit findings + fixes

The ten earlier patches clustered into four patterns:

| Pattern | Description | Example bugs |
|---|---|---|
| A | Assumption without verification | v0.4.1 (terminator), v0.4.5 (SAE bit layout), v0.4.7 (STN init "safe") |
| B | Default chosen for dev convenience, not production | v0.4.4 (httpx dev-only), v0.4.6 / v0.4.8 (timeout), v0.4.9 (14-PID list) |
| C | Python library boundary not normalized | v0.4.10 (bytearray VIN) |
| D | SAE J1979 implementation incomplete | v0.4.2, v0.4.3, v0.4.11 (encoder coverage) |

A systematic sweep of the codebase for each pattern surfaced five
additional latent issues fixed in this release:

1. **Pattern C** — `Elm327Adapter._read_freeze_frame()` stored the DTC
   reference as `str(resp.value)`, leaking the Python repr of a
   bytearray exactly like the VIN bug fixed in v0.4.10. Same root
   cause; now routes through `_decode_string_response`.
2. **Pattern C** — `Elm327Adapter.read_dtcs()` unpacked python-obd's
   DTC tuples without normalizing the code field. On chips that return
   the code as `bytes` rather than `str`, the captured session ended
   up with `b'P0420'` written as a DTC code. Now decoded.
3. **Pattern C** — `Elm327Adapter.read_pid()` fell through to storing
   `value` as-is when python-obd's response had no `.magnitude`. If
   that value was a `bytearray` (some status-byte PIDs), it broke
   JSON serialization downstream. Now sanitized through
   `_decode_string_response`.
4. **Pattern A** — `EcuEmulator._mode09` (VIN / calibration ID / ECU
   name) called `.encode("ascii")` directly on whatever the scenario
   carried. Legacy sessions captured before v0.4.10 carry the
   bytearray-repr wrapper string (`"bytearray(b'JM1...')"`) and the
   simulator would have transmitted that garbage as the VIN. New
   `_clean_ascii_field` helper peels legacy wrappers, decodes
   `\x00`-style escape sequences, and filters to printable ASCII —
   so legacy captures replay correctly without any data migration.
5. **Pattern B** — `SessionConfig.sample_interval_s` defaulted to 0.1
   s. With v0.4.9's all-supported-PIDs capture (typically 50-130 PIDs
   per car), a single sweep takes 5-22 s on an OBDLink SX, making the
   sleep meaningless. The default is now 0 with an adaptive
   `min_cycle_seconds=0.5` floor — fast adapters don't burn 100% CPU,
   slow real-car sweeps add zero overhead.

### Simulator PID encoder expansion (Pattern D)

The Mode 01 dispatch had encoders for 17 PIDs. The client's 2012
Mazda3 reports 113 supported PIDs through python-obd; OBDwiz showed it
reading ~30 commonly-tested ones. The simulator could only re-emit 17,
so the Innova displayed a fraction of what the real car would.

Expanded to 60+ PIDs covering everything OBDwiz read on the Mazda3 plus
the common shop-floor diagnostics: timing advance (0x0E), distance with
MIL on (0x21), wide-range O2 sensors (0x24-0x2B), commanded EGR / EGR
error (0x2C-0x2D), commanded EVAP purge (0x2E), warm-ups + distance
since codes cleared (0x30-0x31), EVAP vapor pressure (0x32), catalyst
temps for both banks (0x3C-0x3F), absolute load (0x43), commanded AFR
(0x44), relative throttle position (0x45), ambient air temperature
(0x46), absolute throttle position B/C (0x47-0x48), accelerator pedal
positions D/E/F (0x49-0x4B), commanded throttle actuator (0x4C), fuel
type (0x51), ethanol percentage (0x52), secondary O2 trims (0x55-0x58),
relative accelerator pedal position (0x5A), hybrid battery remaining
life (0x5B), engine oil temperature (0x5C), engine fuel rate (0x5E),
plus extra O2 voltage sensors (0x16-0x1B) and fuel rail pressures
(0x22-0x23).

Each encoder mirrors the SAE J1979 decode formula exactly. Every newly
added PID has a dedicated round-trip test that locks the formula in.

### Real-vehicle round-trip integration test harness

The meta-cause behind every Pattern A/B/C bug was that we never tested
real-against-real before on-site install. All 145 prior tests use
`MockAdapter`. v0.4.11 adds `tests/test_real_vehicle_round_trip.py`
with fixtures shaped like the actual python-obd return values from the
client's hardware (bytearray VINs, multi-segment VINs, tuple-of-bytes
DTC entries, the real 33-PID Mazda3 idle dump) and 17 tests that walk
the full pipeline:

    raw python-obd-style data
        → Elm327Adapter._decode_string_response / DTC decode / live PID
        → SessionStore-format payload
        → scenario_to_state
        → EcuEmulator dispatch
        → Mode 01 / 03 / 09 response bytes match SAE J1979

If any future change breaks a real-vehicle code path, this harness
catches it before the client sees it.

### Test count

Total tests: 145 (v0.4.10) → 200 (v0.4.11).

- +38 unit-level tests covering the 5 audit fixes and the 28 new
  encoders
- +17 end-to-end integration tests against the real Mazda3 data shape

No regressions; all 200 pass.

### Migration path

Drop-in upgrade. Legacy captures from v0.4.0-v0.4.9 (bytearray-repr
VINs) now replay cleanly without any data migration thanks to the
simulator-side `_clean_ascii_field` helper. New captures use the
clean code path end-to-end.

## 0.4.10 — 2026-06-18

Client's Mazda3 captures showed VIN as `bytearray(b'JM1BL1L72C1627697')`
in the dashboard's VEHICLES panel and PAST SESSIONS list. The on-disk
session folder names had the same Python repr leaking through, breaking
the documented `{VIN}_{make}_{model}_{year}/` layout. Vehicle make /
model / year never decoded so every session showed "Unknown vehicle".

Root cause: `Elm327Adapter.read_vehicle_info()` called `str(resp.value)`
on python-obd's response. python-obd returns VIN / Calibration ID / ECU
Name as `bytearray` (or sometimes a list of `bytearray` segments for
multi-frame VINs). `str(bytearray(b'...'))` formats as the Python repr
`"bytearray(b'...')"` rather than decoding the bytes to text.

Fix: new module-level `_decode_string_response()` helper that normalises
`bytes` / `bytearray` / `list-of-bytearray` / pass-through `str` to a
clean ASCII string with nulls and surrounding whitespace stripped.
Applied to VIN, calibration ID, and ECU name reads.

Future captures will save with clean VINs (`"JM1BL1L72C1627697"`) and
proper folder layout. Existing captures from before this patch have the
bad strings baked in — they continue to work but display oddly until
manually renamed.

6 new tests in `tests/test_elm327_stn.py`: bytearray VIN, plain bytes,
null/whitespace strip, str pass-through, None/empty, and the
multi-segment list case. Total tests 139 → 145.

## 0.4.9 — 2026-06-18

Client reported that captures of his 2012 Mazda3 only included ~10
live PIDs even though python-obd's direct test against the same car
showed 113 supported PIDs. Root cause: `SessionConfig.pids` defaulted
to a hardcoded curated list of 14 PIDs (RPM, speed, coolant temp,
throttle, MAF, fuel trim, intake air temp, MAP, runtime, O2 voltage,
fuel level, ambient air temp, etc.). The acquisition loop iterated
only over that list, ignoring everything else the vehicle could
report (catalyst temps, advance angle, individual O2 sensors, EGR
position, EVAP pressures, knock retard, etc.).

Fix: `SessionConfig.pids` now defaults to an empty list, and the
acquisition loop interprets that as "ask the adapter what PIDs the
connected vehicle supports and capture all of them." The adapter
interface already exposed `supported_pids()` (Elm327Adapter wraps
python-obd's `supported_commands`; MockAdapter returns its mock
set) — the acquisition session simply wasn't calling it.

A curated 14-PID list is kept as `SessionConfig._FALLBACK_PIDS` and
used only when the adapter can't enumerate (mock without a populated
PID set, partial connect, etc.). Each branch logs which path was
taken so silent fallbacks are debuggable.

Explicit PID lists passed through the REST API still work as before —
this only changes what "no list specified" means.

No tests change semantically — existing tests either pass an explicit
PID list or use a mock that populates `supported_pids()` already.
139 tests pass.

## 0.4.8 — 2026-06-17

Companion to v0.4.6 / v0.4.7. The default `Elm327Adapter` timeout is
bumped from 2.0 s to 5.0 s to match the parameters the client
confirmed worked in his direct python-obd test against the 2012 Mazda3
(`obd.OBD('COM3', timeout=5)` returned Status: Car Connected,
Protocol ISO 15765-4 CAN 11/500, 113 PIDs supported, live RPM 752.75).

v0.4.6 raised the timeout from 0.1 to 2.0 — enough for per-query
reads on an established connection but not always enough for the
initial 0100 protocol-detection query on a cold connect through an
OBDLink SX. The chip walks through several ISO 15765-4 variants
during auto-detect and the cumulative wait can reach 2-4 seconds
before any response. python-obd's connect path returns
`is_connected() == False` on protocol-detect timeout — silently, with
no exception raised — so the acquisition loop kept polling against a
disconnected interface and the dashboard saw empty data forever.

5.0 s matches the value the client's direct test used and is well
below any UI latency the user would notice (since the slow path is
just the cold connect; subsequent queries return in <50 ms once the
protocol is locked).

No tests change (the fake-obd test harness uses an in-memory
interface, no real timeouts). All 139 tests still pass.

## 0.4.7 — 2026-06-17

The actual fix for the silent zero-data capture from v0.4.6. The
timeout bump was necessary but not sufficient — the deeper bug was
that our STN-tuning post-connect commands were breaking the
already-established python-obd connection.

Client confirmed by running `obd.OBD('COM3', timeout=5)` directly
against his 2012 Mazda3 + OBDLink SX combination: status returned
`Car Connected`, protocol detected as `ISO 15765-4 (CAN 11/500)`,
113 PIDs supported, RPM reading 752.75 in real time. So the chain
laptop ↔ OBDLink ↔ car works perfectly via plain python-obd.

The breakage: after python-obd connects cleanly, our
`_apply_stn_init_if_present()` re-sent:

- `ATSP0` — re-triggers protocol auto-detection (overwrites the
  protocol python-obd just negotiated)
- `STCSEGR 1` — changes how the chip frames multi-frame responses
  (python-obd then can't parse subsequent replies)
- `STCFCPA` — also changes flow-control behaviour python-obd assumed

The original intent was harmless STN-only tuning. In practice the
commands rewrote the chip's working state and subsequent PID queries
returned nothing.

Fix: `_STN_RUNTIME_COMMANDS` is now an empty tuple. The STI/ATI
banner probe still runs so `adapter.is_stn` and `adapter.stn_banner`
remain available for diagnostics, but no commands modify the chip's
runtime state after connect. python-obd's defaults handle the
OBDLink SX correctly out of the box (the client's direct test
proved this). STN-specific tuning should be opt-in via constructor
args, not on by default.

Two existing STN tests in `tests/test_elm327_stn.py` were rewritten
to assert the new "probe only, no runtime commands" behaviour. All
139 tests pass.

## 0.4.6 — 2026-06-17

One more bug fix from the client's first real-vehicle capture attempt
the morning after the on-site install. With a 2012 Mazda3 (CAN-OBD-II)
plus the kit's OBDLink SX adapter on Windows, the dashboard recorded a
session, polled live data every second, and got HTTP 200 OK on every
poll — but no PIDs ever populated. VIN stayed empty, vehicle stayed
"Unknown". The OBDLink's LINK LED was solid green throughout (adapter
powered and ready); OBDwiz (the manufacturer's own diagnostic app)
read VIN and live data fine against the same car on the same port.
So adapter and car were healthy — our dashboard was broken.

Root cause: `Elm327Adapter` defaulted `timeout=0.1` (100 ms) when
constructing the python-obd `OBD()` client. Real OBD-II queries against
a vehicle commonly take 200-1000 ms each. python-obd treats a per-query
timeout by returning `None` (not raising) — so the acquisition loop
saw every PID query "succeed" with empty data, no errors logged, no
exception thrown. The dashboard could not distinguish "query timed out"
from "vehicle doesn't support this PID". Result: silent zero-data
capture.

Fix: default `Elm327Adapter` timeout raised from 0.1 to 2.0 seconds.
Plenty for any real-world query, still keeps interactive UI snappy.
Existing tests use `MockAdapter` and are unaffected — 139 tests still
pass. The fix is a single-line constant change with a comment
explaining why.

Upgrade on the laptop:

```powershell
cd C:\uacj
git pull
pip install -e . --upgrade
# Stop + restart the dashboard
```

The same captured session that was returning zero data before should
now populate with VIN, DTCs, monitors, and live PIDs streaming from
the car.

## 0.4.5 — 2026-06-15

Third small bug fix from the same UACJ on-site install — discovered the
moment the client pushed his first preset-built scenario from the
laptop dashboard (after v0.4.4 unblocked the push). The Innova showed
the right VIN, DTC, description, and MIL state, but the I/M Monitor
badges row rendered with the wrong monitors flagged: the preset's
`monitors_override` (which the dashboard ships as a `monitors[]`
array on the wire) was encoded with non-SAE-J1979 bit positions.

Root cause: `scenario_to_state` packed all `monitors[]` entries into
byte B / byte C bits 0-7 in array order, ignoring the SAE J1979 byte
layout. Per J1979:

- **Byte B (continuous)** — MIS / Fuel / CCM in bits 0-2 (supported)
  and bits 4-6 (not complete). All other bits reserved/zero.
- **Byte C (non-continuous supported)** — CAT / HCAT / EVAP / AIR /
  A/C / O2S / HTR / EGR in bits 0-7.
- **Byte D (non-continuous not complete)** — same bit indices as C.

The old encoder wrote the wrong bits and never set byte D at all, so
the Innova interpreted scenarios as "MIS not complete" or "Fuel not
complete" depending on array order — even when the scenario said the
*catalyst* monitor was incomplete.

Fix: new `_encode_monitors_per_j1979()` helper in `can_runtime.py`
that maps monitor names (`"Catalyst"`, `"Evaporative System"`,
`"EVAP"`, `"O2S"`, `"HTR"`, …) to the correct SAE J1979 byte/bit and
populates B/C/D faithfully. Accepts both preset display names and
the abbreviations scan tools use (Innova / Autel / generic ELM327).
Unknown monitor names are silently skipped so future preset
extensions don't break old simulators.

9 new tests in `tests/test_simulator_integration.py` covering the
encoder directly (all continuous complete, continuous incomplete,
CAT-only, EVAP-only, unsupported→no bits set, alias matching,
unknown-name graceful skip, a full typical post-2008 vehicle, and
the propagation through `scenario_to_state`). Total tests 130 → 139.

Combined with v0.4.2 (byte A from DTC count) and v0.4.3 (B/D
not-complete bits derived from stored DTC ranges), the simulator now
produces a Mode 01 PID 01 response that matches what a real vehicle
in the same fault state would emit, end-to-end.

## 0.4.4 — 2026-06-15

Tiny dependency-declaration fix discovered when the client installed the
laptop dashboard fresh and pushed his first scenario from the UI. The
dashboard's `POST /api/scenarios/{id}/push` endpoint uses `httpx` to
talk to the Pi simulator, but `httpx` was mis-tagged as a dev-only
dependency in `pyproject.toml`. A `pip install -e .` install (without
`[dev]`) therefore omitted it, and the first push attempt failed with
`502 {"detail":"simulator push failed: No module named 'httpx'"}`.

Fix: moved `httpx>=0.27.0` from `[project.optional-dependencies].dev`
to the main `[project].dependencies` list. No code changes.

Upgrade path on an existing install: `pip install httpx` works as a
one-line workaround; or `pip install -e . --upgrade` after pulling.

## 0.4.3 — 2026-06-15

Second small bug fix landed during the same UACJ on-site install. After
v0.4.2 made the Mode 01 PID 01 byte A (MIL state + DTC count) consistent
with Mode 03, the client confirmed against a real Kia that the Innova
5210 **does** show the monitor-badges row when DTCs are present — it
just won't show it when the readiness data and the DTC story are
mutually inconsistent.

Root cause: our default ScenarioState reports all monitors complete
(byte B upper nibble = 0, byte D = 0x00) but scenarios load stored
DTCs without telling the simulator that the affected monitor failed
to complete. Scan tools cross-check this — "a P0420 is stored but the
catalyst monitor reports complete" is an impossible vehicle state — and
the Innova suppresses the badges rather than display nonsense.

Fix in `EcuEmulator._mode01`: bytes B and D are now derived per
dispatch by OR-ing the scenario's bytes with monitor-not-complete bits
inferred from `dtcs_stored`. A new constant `_DTC_PREFIX_TO_MONITOR_BIT`
maps DTC prefix ranges to the byte/bit they affect, covering the common
generic powertrain codes:

| DTC range | Monitor | Byte/bit |
|-----------|---------|----------|
| P0030-P0059 | O2 sensor heater | D bit 6 |
| P0130-P0159 | O2 sensor | D bit 5 |
| P0160-P0199 | Fuel system | B bit 5 |
| P0200-P0229 | Fuel/air metering (CCM) | B bit 6 |
| P0300-P0309 | Misfire | B bit 4 |
| P0400-P0409 | EGR | D bit 7 |
| P0410-P0419 | Secondary air | D bit 3 |
| P0420-P0429 | Catalyst bank 1 | D bit 0 |
| P0430-P0439 | Catalyst bank 2 / heated | D bit 1 |
| P0440-P0469 | EVAP | D bit 2 |

DTCs outside these ranges (transmission, body, U-network, etc.) fall
back to "CCM not complete" so the badges row renders rather than
appearing fully complete-but-with-DTCs. Scenarios that already set
not-complete bits via `monitor_b` / `monitor_d` keep them — the
derivation is purely additive.

7 new tests in `tests/test_ecu.py`: P0420 → CAT, P0455 → EVAP, P0300 →
MIS, unmapped DTC → CCM fallback, additive preservation, multi-DTC
multi-bit, and the no-DTC pass-through case. Total tests 123 → 130.

Confirmed on site: with v0.4.3 deployed, the Innova 5210 renders the
monitor-badges row on the I/M Monitor Status page even with the
stored P0420, showing CAT as not-complete (red) and all other monitors
as complete (green) — same UX as a real vehicle with a catalyst code.

## 0.4.2 — 2026-06-15

Tiny bug fix discovered immediately after v0.4.1 during the same on-site
install. The Innova 5210 displayed VIN, DTC, and live data correctly but
refused to render the I/M Monitor readiness page once a stored DTC was
loaded. Root cause: Mode 01 PID 01's byte A (MIL state + stored DTC
count) was being returned verbatim from `ScenarioState.monitor_status`,
which defaults to 0x00 (no MIL, no DTCs). When a scenario also loaded a
stored DTC, the scan tool saw an inconsistency — Mode 03 returned one
DTC but byte A claimed zero — and refused to render readiness.

Fix in `EcuEmulator._mode01`: byte A is now derived dynamically from
`self.state.dtcs_stored` on every Mode 01 PID 01 dispatch. Bit 7 = 1 if
any stored DTC exists; bits 0-6 = stored DTC count (saturating at
0x7F). Bytes B/C/D continue to come from `ScenarioState` (the monitor
availability/completeness bitmaps that scenarios populate via the
`monitors[]` array). Pending DTCs do not turn the MIL on, per SAE J1979.

Added 5 focused tests in `tests/test_ecu.py` covering the derivation
(no DTCs, one stored DTC, count saturation, pending-only, and
preservation of bytes B/C/D from scenario state). All 123 tests pass.

Confirmed on site: Innova 5210 rendered the readiness page correctly
immediately after the simulator service was restarted with the patch.

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
