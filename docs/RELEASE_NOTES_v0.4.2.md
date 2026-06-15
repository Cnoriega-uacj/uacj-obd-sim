# v0.4.2 — Derived Mode 01 PID 01 byte A from DTC state

**Release date:** 2026-06-15
**Status:** Code fix discovered during the UACJ on-site install, same session as v0.4.1

After v0.4.1 fixed the CAN bus termination prerequisite, the Innova 5210
read VIN, P0420 with description, and live data correctly — but the
dedicated I/M Monitor (readiness) page rendered blank instead of the
green/yellow/grey monitor indicators it had displayed earlier in the
session (before a scenario with stored DTCs was pushed).

## Root cause

SAE J1979 Mode 01 PID 01 returns four bytes:

- **Byte A** — bit 7 = MIL state, bits 0-6 = number of stored DTCs
- **Byte B** — continuous-monitor availability and completeness (MIS / Fuel / CCM)
- **Byte C** — non-continuous-monitor availability (Catalyst, EVAP, O2S, …)
- **Byte D** — non-continuous-monitor completeness

The v0.4.0 / v0.4.1 simulator returned byte A directly from
`ScenarioState.monitor_status`, which defaults to `0x00` (no MIL, no
DTCs). Scenarios that loaded a stored DTC didn't set `monitor_status`
explicitly, so byte A continued to claim "no MIL, no DTCs" — while
Mode 03 separately returned the stored DTC. The Innova 5210 noticed
the contradiction and refused to render the readiness page.

## Fix

In `EcuEmulator._mode01`, byte A is now derived on every dispatch from
the *current* DTC state, not stored on the scenario:

```python
stored_count = min(len(self.state.dtcs_stored), 0x7F)
mil_on = 0x80 if self.state.dtcs_stored else 0x00
byte_a = mil_on | stored_count
```

Bytes B/C/D continue to come from `ScenarioState` (where the scenario's
`monitors[]` array writes them). Pending DTCs do not turn the MIL on —
per SAE J1979, only stored/confirmed DTCs illuminate the MIL.

The `ScenarioState.monitor_status` field is now effectively vestigial
on output and we leave the default 0x00 in place; future scenarios that
need to test edge cases (e.g. permanent DTCs without stored ones) can
still construct the byte directly, but for the common case of "loaded
a DTC, want the MIL on", the derivation Just Works.

## Tests

5 new focused tests in `tests/test_ecu.py`:

- `test_mode01_pid01_byte_a_no_dtcs` — byte A = 0x00 when no DTCs
- `test_mode01_pid01_byte_a_one_stored_dtc_turns_mil_on` — byte A = 0x81 with one stored
- `test_mode01_pid01_byte_a_dtc_count_saturates_at_127` — saturation at 0x7F with bit 7 set = 0xFF
- `test_mode01_pid01_byte_a_pending_only_does_not_turn_mil_on` — pending DTCs don't set MIL
- `test_mode01_pid01_bytes_bcd_come_from_scenario_state` — bytes B/C/D still scenario-driven

Total test count: 118 (v0.4.0) → 123 (v0.4.2). No regressions in any
other module.

## On-site confirmation

After `git pull` on the Pi and `systemctl restart uacj-obd-sim`, the
Innova 5210 displayed the I/M Monitor (readiness) page correctly
alongside the stored P0420. End-to-end smoke test (link → VIN → DTC →
description → live data → readiness) passes for the Innova 5210.

## Upgrade path

In-place upgrade on the Pi:

```bash
ssh pi@uacj-sim.local
cd /opt/uacj-obd-sim
sudo git pull
sudo systemctl restart uacj-obd-sim
```

No config changes, no Pi reboot required, no laptop changes.
