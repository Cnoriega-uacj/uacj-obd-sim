# v0.4.5 — SAE J1979-correct monitor bitmap encoding for scenarios

**Release date:** 2026-06-15
**Status:** Code fix discovered during the same UACJ on-site install as v0.4.1 / v0.4.2 / v0.4.3 / v0.4.4

After v0.4.4 unblocked the dashboard's push-to-Pi path (`httpx` now in
main deps), the client pushed his first preset-built scenario from the
laptop dashboard to the Pi simulator. The Innova displayed the right
VIN (Honda), the right DTC (P0420 with the full official description
"Catalyst System Efficiency Below Threshold (Bank 1)"), and the MIL on
— but only one monitor badge rendered in the I/M Monitor row. The
hand-written scenarios we used earlier (no `monitors[]` field) had
shown a full row of badges via v0.4.3's DTC-derivation; the dashboard's
preset includes a `monitors_override` that ships as a `monitors[]`
array on the wire, and that array hit a different code path with the
wrong bit layout.

## Root cause

`scenario_to_state` in `can_runtime.py` packed `monitors[]` entries
into byte B / byte C bits 0-7 in **array order**, ignoring the SAE
J1979 byte layout:

```python
# Old, incorrect encoder
for i, m in enumerate(monitors[:8]):
    if not m.get("supported", False):
        mb |= (1 << i)        # wrong byte AND wrong bit
    if not m.get("ready", False):
        mc |= (1 << i)        # byte D was never set at all
state.monitor_b = mb
state.monitor_c = mc
```

This produced bytes that no scan tool could interpret correctly:

- A preset entry for "Catalyst" at array index 3 wrote into bit 3 of
  byte B (which J1979 reserves) instead of bit 0 of bytes C/D.
- "Misfire" at array index 0 wrote into bit 0 of byte B, but the
  encoder set that bit when the monitor was *not* supported (inverted
  semantics from spec).
- Byte D was never written, so non-continuous monitors with stored
  DTCs but no DTC-derivation hit (rare) would always appear complete.

The Innova handled this by hiding badges it couldn't make sense of.

## Fix

New `_encode_monitors_per_j1979()` helper that:

1. Maps monitor names to (category, bit) via a lookup table covering
   preset display names ("Catalyst", "Evaporative System") and the
   abbreviations scan tools render ("CAT", "EVAP", "O2S", "HTR").
2. Writes bytes B / C / D per SAE J1979:
   - **Byte B** — continuous monitors. Bits 0-2 = supported (MIS /
     Fuel / CCM). Bits 4-6 = not-complete for the same three.
   - **Byte C** — non-continuous monitors supported (CAT / HCAT / EVAP
     / AIR / A/C / O2S / HTR / EGR in bits 0-7).
   - **Byte D** — same bit indices as C, set when the monitor is
     supported AND not ready.
3. Silently skips unknown monitor names so future preset extensions
   don't break older simulators.

Unsupported monitors contribute zero bits — even if `"ready": false`
is set on them, since "not complete" only makes sense when the monitor
exists.

## Tests

9 new tests in `tests/test_simulator_integration.py`:

- `test_encode_monitors_continuous_supported_and_complete`
- `test_encode_monitors_continuous_supported_not_ready_sets_upper_nibble`
- `test_encode_monitors_catalyst_supported_not_ready`
- `test_encode_monitors_evap_not_ready`
- `test_encode_monitors_unsupported_does_not_set_any_bit`
- `test_encode_monitors_accepts_id_and_abbreviation`
- `test_encode_monitors_unknown_name_is_silently_ignored`
- `test_encode_monitors_full_typical_pre_2008_vehicle`
- `test_scenario_to_state_propagates_encoded_monitor_bytes`

Total tests: 130 (v0.4.4) → 139 (v0.4.5). No regressions.

## Combined with v0.4.2 / v0.4.3

This series of three patches now makes the simulator's Mode 01 PID 01
response match what a real vehicle in the same fault state would emit
end-to-end:

| Version | Fix |
|---------|-----|
| v0.4.2 | Byte A derived from stored DTC count + MIL on |
| v0.4.3 | Bytes B/D not-complete bits derived from stored DTC ranges (DTC→monitor mapping) |
| v0.4.5 | Bytes B/C/D from scenario `monitors[]` array encoded per SAE J1979 bit layout |

Hand-written scenarios (no `monitors[]`) and dashboard preset
scenarios (with `monitors[]`) now produce identically correct readiness
displays.

## Upgrade path

In-place on the Pi:

```bash
ssh pi@uacj-sim.local
cd /opt/uacj-obd-sim
sudo git pull
sudo systemctl restart uacj-obd-sim
```

On the laptop (only needed if the laptop is older than v0.4.4):

```powershell
cd C:\uacj
git pull
pip install -e . --upgrade
```

No config changes, no Pi reboot required.
