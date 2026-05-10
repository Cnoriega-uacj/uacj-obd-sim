# Scan Tool Compatibility Log

This document lists scan tools that have been verified to work with the
UACJ OBD-II Training Simulator, plus known limitations and quirks. It
will be updated as tools are tested against the simulator board.

> Status legend: ✅ verified working · ⚠ works with caveats · ❓ not yet tested · ❌ does not work · 🔮 prediction (no hardware yet)

Each row's "predicted" column is a best-effort forecast based on the
tool's published protocol support and our simulator's confirmed
implementation surface. Predictions are replaced with verified results
once the hardware is in hand.

## Methodology

For each tool we verify:
1. **Connect** — tool establishes session with the simulator board
2. **VIN read** — mode 0x09 PID 0x02 returns the loaded scenario's VIN
3. **DTC read** — modes 0x03 / 0x07 / 0x0A return loaded codes
4. **Live data** — mode 0x01 PIDs match scenario's overrides + baseline
5. **Clear codes** — mode 0x04 wipes stored DTCs (scenario reload restores them)
6. **Freeze frame** — mode 0x02 returns the scenario's freeze data

Pass criteria: every step matches what the laptop dashboard shows.

---

## Tools we expect to test

Predictions assume the v0.4.0 simulator (CAN ✅, K-Line ✅ incl. 5-baud
slow-init, J1850 framing ✅ but transmitter chip not yet in BOM).

| Tool | Protocols (per spec) | Predicted | Notes |
|---|---|---|---|
| Generic ELM327 USB/BT clone (v1.5) | CAN, K-Line, ISO 9141-2 | 🔮 ✅ | Should work — these clones implement the standard ISO-TP request/response we already round-trip in the bench. Expect occasional retry on first connect (clones often need ATZ/ATSP0 twice). |
| OBDLink SX (STN1110) | CAN, K-Line, ISO 9141-2, J1850 (read) | 🔮 ✅ on CAN+K-Line | Same as ELM327 clone, plus the STN-tuned init path we added in v0.4.0 (ST commands silently ignored if absent). On J1850 vehicles, the SX *reads* fine — but it talks to a real car, not the simulator, so this is acquisition-side coverage only. |
| OBDLink MX+ (STN2120) | CAN, K-Line, ISO 9141-2, J1850 (read) | 🔮 ✅ on CAN+K-Line | Same as SX. J1850 simulator-side will return nothing until the MC33390 add-on is wired. |
| Autel AutoLink AL319 | CAN | 🔮 ✅ | Entry-level CAN-only tool. Reads VIN, DTCs, freeze frame, monitors — all in our service set. Most-likely classroom tool for UACJ. |
| Autel MaxiCheck MX808 | CAN, K-Line | 🔮 ✅ on basic OBD; ⚠ on advanced | Bidirectional/active tests are out of scope for the simulator — MX808 will show those features as unavailable. Standard generic-OBD pages work. |
| Launch CRP123 | CAN, K-Line, ISO 9141-2 | 🔮 ✅ | Generic OBD scope only on CRP123 — matches our service set. |
| Innova 3100 / 5610 | CAN | 🔮 ✅ | Reads VIN/DTC/freeze frame/monitors. Innova firmware historically fussy about NRC formatting; we use ISO 14229 NRCs unchanged, so it should accept them. |
| BlueDriver BluePro | CAN | 🔮 ✅ on standard PIDs; ⚠ on enhanced | BlueDriver's "enhanced data" uses manufacturer-specific PIDs we don't all encode (only six in v0.3.0). Standard PIDs and DTCs work. |
| Snap-on MODIS Edge | CAN, K-Line, J1850 | 🔮 ⚠ | Generic-OBD mode should work on CAN/K-Line. Snap-on enhanced/factory pages will fail because the simulator only emulates one ECU and a subset of mfg PIDs — not a regression, just a scope limit instructors should know. |
| FORScan (Ford-only) | K-Line, CAN | 🔮 ⚠ | Reads generic OBD fine. FORScan-specific module browsing requires multi-ECU presence and Ford UDS routines — out of scope. |
| Torque Pro (Android, ELM327 BT) | CAN | 🔮 ✅ | Heavy mode 01 polling; our 0.05 s inter-poll cadence is faster than Torque's default. Should display gauges live. |
| Carista (iOS/Android) | CAN | 🔮 ✅ on diagnostics | Adaptation/coding features not applicable; basic diagnostics will work. |
| Snap-on MODIS Edge (J1850) | J1850 VPW | 🔮 ❌ | Will not work until MC33390 transceiver is added. Documented as v2. |

---

## Known limitations of the v0.x simulator (apply to all tools)

These are board-side, not tool-side:

- **J1850 VPW / PWM** (pre-CAN GM/Ford 2004–2007): framing layer is
  implemented and unit-tested as of v0.4.0; the electrical-side
  transceiver (MC33390 for VPW, dual-wire driver for PWM) is **not in
  the v1 BOM**. Once a transceiver is wired to the Pi UART, the same
  J1850Runtime answers requests with no further code changes.
  Workaround until then: any 2008+ vehicle in the scenario library, or
  any 2003+ vehicle that uses CAN or K-Line.
- **5-baud slow-init**: implemented in v0.3.0. Most modern adapters use
  KWP fast-init (CARB EOBD), but a few older units may still rely on
  the slower 5-baud sequence — we now answer it.
- **Multi-ECU scenarios**: only one ECU is emulated (responds on
  0x7E8). Scan tools that probe the full 0x7E8–0x7EF range will see
  responses only from 0x7E8.
- **Mode 0x06 on-board monitoring test results**: implemented in v0.4.0.
  Scenarios may attach an `obd_test_results` map (TID → CID, value, min,
  max). Tools that lean heavily on this mode (CARB compliance testers,
  IM240 emulators) read pass/fail brackets correctly. Empty scenarios
  answer with the "no data yet" service byte rather than an NRC, which
  matches real-world vehicles whose monitors haven't completed.

---

## How to add a tool to this list

When testing a new scan tool against the simulator:

1. Push a known scenario to the board (e.g. the `p0420_catalyst` preset
   on top of a captured Civic).
2. Run the tool through the six-step methodology above.
3. Record results below with date and tool firmware version if
   visible.

Template:

```
| Tool name (firmware vX.Y) | CAN, K-Line | ✅ | All six steps pass.
   Tested 2026-MM-DD against scenario `p0420_catalyst` on Civic capture. |
```

If a step fails, document:
- which step
- what the tool showed vs what the laptop dashboard showed
- whether the simulator's `/api/sim/log` recorded the request
- the request hex and response hex from the log

This last item is the critical one — if the request reached the board
and the board answered, the gap is in the tool's parsing. If the
request never appeared in the log, the gap is in the simulator's
electrical path (transceiver, termination, bus voltage).

---

## Changelog

- **2026-04-27** — initial template, no entries verified yet (hardware
  not yet in hand).
