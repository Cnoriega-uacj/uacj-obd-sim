# v0.4.0 — Pre-arrival hardening + classroom polish

**Release date:** 2026-05-10
**Status:** Production — installed at UACJ on 2026-05-11

This is the delivery release. It bundles everything from v0.1.0 through
v0.3.0 plus a pre-hardware-arrival hardening pass and classroom-ready
polish.

## Highlights

- **118 automated tests passing**, ruff-clean, CI on every push
- **5 OBD-II protocols on the acquisition side**: CAN, KWP2000, ISO 9141-2,
  J1850 VPW, J1850 PWM (the last two via OBDLink SX / STN2120)
- **CAN + K-Line simulator** answers any student scan tool;
  J1850 framing is built and unit-tested (transceiver add-on optional)
- **6 built-in classroom presets** ready to use on day one
- **5 pre-loaded sample vehicles** so the dashboard isn't empty
- **22 manufacturer PIDs** across Honda, Ford, GM, Toyota, Nissan
- **Mode 0x06 on-board monitoring** for CARB-style emissions readiness
- **Spanish/English UI toggle**, auto-detects es-MX locale
- **One-click backup/restore** of the entire system state

## What's new since v0.3.0

### Pre-CAN J1850 framing (GM VPW + Ford PWM)

`uacj_obd/simulator/j1850.py` — SAE J1850 framing, CRC-8 (poly 0x1D),
segmented response packing for multi-frame replies (VIN, DTC lists).

`uacj_obd/simulator/j1850_runtime.py` — duck-typed transceiver wrapper
that reads complete frames, dispatches through `EcuEmulator`, and writes
responses back. Same hardware-free pattern as `kline_runtime.py`.

15 unit tests covering CRC, framing, NRC paths, and round-trip RPM /
VIN / DTC / clear-codes via the runtime.

Wiring schematics for both add-ons (LM358 + 2N7000 for GM, AM26LS31 +
AM26LS32 for Ford) are in `docs/wiring.md`.

### STN2120 (OBDLink SX) tuning

`Elm327Adapter` probes the chip's ATI/STI banner at connect. When an
STN-class chip is detected, the adapter sends ST-prefixed init commands
(segmented-response auto-reassembly, ISO-TP flow-control padding) that
a plain ELM327 clone silently ignores. 5 tests with a fake `pyobd`
object verifying STN init runs only when expected.

### Mode 0x06 on-board monitoring test results

`EcuEmulator._mode06` answers SAE J1979 mode 0x06 with packed test
records (TID, CID, UASID, value, min, max). Bare `0x06` enumerates
every configured test; specific TID returns just that test or the
"no data" service-byte-only reply for unknown TIDs.

### 5 pre-loaded sample vehicle sessions

`scripts/seed_sample_sessions.py` writes a Civic / Silverado / Corolla /
F-150 / Sentra session to disk so the dashboard isn't empty on first
boot. Each has 60 seconds of synthetic-but-plausible live data; all
flagged in `metadata.notes` as `synthetic sample`.

### Extended manufacturer PID library

22 PIDs total across the default bank:
- **Honda (6):** VTEC oil pressure, target idle, knock retard, fuel
  pressure, brake switch, ATF temp
- **Ford (5):** trans oil temp, key-on runtime, A/C compressor command,
  fuel pump duty, commanded gear
- **GM (5):** engine oil life, transmission fluid temp, fuel tank
  pressure, commanded gear, baro
- **Toyota (3):** engine runtime, hybrid SOC, inverter temp
- **Nissan (3):** CVT fluid temp (default), plus CVT ratio + target AFR
  in the opt-in Nissan bank

`select_make()` switches the active encoder bank to resolve key
collisions between makes.

### Spanish/English UI toggle

`web/i18n.js` auto-detects browser language (es-MX → Spanish), injects
an EN/ES toggle button into every page header, persists choice in
`localStorage`, broadcasts a `uacj:lang-changed` event so dashboard JS
can re-render dynamic content. All five HTML pages carry `data-i18n`
annotations.

### Backup / restore

`POST /api/backup` streams a single ZIP containing `uacj.db` + every
session folder + a `BACKUP_INFO.json` schema marker.

`POST /api/restore` validates the ZIP shape, rejects zip-slip attacks,
snapshots the existing data dir as `.restore-backup-*` before
overwriting.

### Virtual-bus bench harness

`scripts/bench.py` round-trips RPM / VIN / DTC / clear-codes through:
- python-can virtual bus (CAN, ISO-TP)
- `os.openpty()` raw-mode pty pair (K-Line, KWP2000)
- in-memory pipe (J1850)

Runs in CI as a regression gate via `tests/test_bench.py`.

### Documentation

- `docs/install.md` — complete TeamViewer-day install runbook
- `docs/compatibility.md` — predicted compatibility for 13 common scan
  tools
- `docs/instructor_quickstart.md` + `_es.md` — single-page classroom
  cheat sheets, A4/Letter ready

### Engineering

- GitHub Actions CI on Python 3.11 and 3.12, every push and PR
- Ruff-clean across the whole repo (29 → 0 issues)
- Test count: 63 → 118
- MIT LICENSE added

## Upgrade notes

This release is the initial production install — no upgrade path needed.

For future updates inside the 30-day adjustment window:

```bash
ssh pi@uacj-sim.local
cd /opt/uacj-obd-sim
sudo git pull
sudo systemctl restart uacj-obd-sim
```

On the laptop:
```bash
cd C:\uacj
git pull
pip install -e .          # only if dependencies changed
```

## Known limitations

- **J1850 simulator-side** requires the optional transceiver add-on
  per `docs/wiring.md`. Without it, the framing code is present but
  no electrical signal is emitted.
- **Multi-ECU emulation** (ABS, BCM, TCM) deferred pending legal
  review of immobilizer/anti-theft risk.
- **Pre-2002 mode 0x06** (UASID addressing variant) not implemented;
  post-2002 CAN form per SAE J1979 is fully supported.
- **Mode 0x22 key collisions** between makes (e.g. Ford 0x221101 vs
  Nissan 0x221101) resolved via `select_make()` opt-in banks.
