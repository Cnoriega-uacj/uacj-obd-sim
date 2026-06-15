# v0.4.3 — Derive monitor-not-complete bits from stored DTCs

**Release date:** 2026-06-15
**Status:** Code fix discovered during the UACJ on-site install, same session as v0.4.1 / v0.4.2

After v0.4.2 made Mode 01 PID 01 byte A consistent with Mode 03, the
client verified against a real Kia (5 stored DTCs, P0300 visible) that
the Innova 5210 **does** render the monitor-badges row alongside DTC
info — the badges appeared with CAT and EVAP red (not complete) and
all others green. So our earlier suspicion that the Innova suppresses
badges whenever DTCs are present was wrong; the Innova suppresses them
only when the readiness data doesn't match the DTC story.

## What was inconsistent

Our default `ScenarioState` reports:

- Byte B = `0x07` → MIS / Fuel / CCM supported AND complete
- Byte D = `0x00` → all 8 non-continuous monitors complete

Scenarios load stored DTCs but don't tell the simulator that the
affected monitor failed to complete. Scan tools cross-check this:
**"a P0420 is stored but the catalyst monitor reports complete"** is
not a real vehicle state. The Innova 5210 detects the inconsistency
and silently drops the badges row rather than show nonsense.

## Fix

In `EcuEmulator._mode01`, bytes B and D are now derived on every
dispatch by OR-ing the scenario's bytes with monitor-not-complete bits
inferred from `dtcs_stored`. A new module-level constant maps DTC
prefixes to the byte/bit pair they affect:

| DTC range | Monitor | Byte / bit |
|-----------|---------|------------|
| P0030–P0059 | O2 sensor heater | D bit 6 |
| P0130–P0159 | O2 sensor | D bit 5 |
| P0160–P0199 | Fuel system | B bit 5 |
| P0200–P0229 | CCM (fuel/air, throttle) | B bit 6 |
| P0300–P0309 | Misfire | B bit 4 |
| P0400–P0409 | EGR | D bit 7 |
| P0410–P0419 | Secondary air | D bit 3 |
| P0420–P0429 | Catalyst bank 1 | D bit 0 |
| P0430–P0439 | Catalyst bank 2 / heated | D bit 1 |
| P0440–P0469 | EVAP | D bit 2 |

DTCs outside these ranges (e.g. P0700 transmission, P1xxx
manufacturer-specific, U-network) fall back to **CCM not complete**
(byte B bit 6). The reasoning: in a generic-OBD-II context the scan
tool doesn't care which monitor specifically; what matters is that the
row renders rather than reporting "fully complete + DTCs stored".

The derivation is **additive** — if a scenario already sets
not-complete bits via `monitor_b` / `monitor_d`, those are preserved.

Byte C (monitor availability) is **not** derived. Only the scenario
controls which monitors the vehicle has (e.g. some Toyotas don't have
secondary air; pre-2002 vehicles don't have all eight non-continuous).

## Tests

7 new tests in `tests/test_ecu.py`:

- `test_mode01_pid01_p0420_derives_cat_not_complete`
- `test_mode01_pid01_p0455_derives_evap_not_complete`
- `test_mode01_pid01_p0300_derives_misfire_not_complete`
- `test_mode01_pid01_unknown_dtc_falls_back_to_ccm`
- `test_mode01_pid01_derivation_preserves_existing_bits`
- `test_mode01_pid01_multiple_dtcs_set_multiple_bits`
- `test_mode01_pid01_no_dtcs_leaves_bytes_bd_at_scenario_values`

Total test count: 123 (v0.4.2) → 130 (v0.4.3). No regressions.

## On-site confirmation

After `git pull` on the Pi and `systemctl restart uacj-obd-sim`, the
Innova 5210 rendered the I/M Monitor Status badges row alongside the
stored P0420 — CAT shown as not-complete (red), all other monitors
shown as complete (green). Matches the UX of a real vehicle with a
catalyst code.

## Upgrade path

In-place upgrade on the Pi:

```bash
ssh pi@uacj-sim.local
cd /opt/uacj-obd-sim
sudo git pull
sudo systemctl restart uacj-obd-sim
```

No config changes, no Pi reboot required, no laptop changes.
