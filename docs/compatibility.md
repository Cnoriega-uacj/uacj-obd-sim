# Scan Tool Compatibility Log

This document lists scan tools that have been verified to work with the
UACJ OBD-II Training Simulator, plus known limitations and quirks. It
will be updated as tools are tested against the simulator board.

> Status legend: ✅ verified working · ⚠ works with caveats · ❓ not yet tested · ❌ does not work

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

## Verified

| Tool | Protocols | Status | Notes |
|---|---|---|---|
| Generic ELM327 USB clone | CAN | ❓ | To be tested with first hardware delivery |
| Generic ELM327 Bluetooth clone | CAN | ❓ | Pending |
| OBDLink SX (STN1110) | CAN, K-Line | ❓ | Pending |
| OBDLink MX+ (STN2120) | CAN, K-Line, J1850 | ❓ | Pending — J1850 will fail until v2 hardware |
| Autel MaxiCheck MX808 | CAN, K-Line | ❓ | Pending |
| Autel AutoLink AL319 | CAN | ❓ | Common entry-level student tool |
| Launch CRP123 | CAN, K-Line | ❓ | Pending |
| Innova 5610 | CAN | ❓ | Pending |
| Bluedriver BlueTooth Pro | CAN | ❓ | Pending |
| Snap-on MODIS Edge | CAN, K-Line | ❓ | Professional reference tool |

---

## Known limitations of the v0.x simulator (apply to all tools)

These are board-side, not tool-side:

- **J1850 VPW / PWM** (pre-CAN GM/Ford 2004–2007): not supported. The
  simulator's BOM does not include MC33390 transceivers in v1.
  Workaround: any 2008+ vehicle in the scenario library, or any 2003+
  vehicle that uses CAN or K-Line.
- **5-baud slow-init**: implemented in v0.3.0. Most modern adapters use
  KWP fast-init (CARB EOBD), but a few older units may still rely on
  the slower 5-baud sequence — we now answer it.
- **Multi-ECU scenarios**: only one ECU is emulated (responds on
  0x7E8). Scan tools that probe the full 0x7E8–0x7EF range will see
  responses only from 0x7E8.
- **Mode 0x06 on-board monitoring test results**: not yet implemented.
  Tools that lean heavily on this mode (CARB compliance testers) will
  show "no data" for monitor test IDs.

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
