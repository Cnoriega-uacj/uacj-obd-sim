# v0.4.1 — Post-install docs patch (CAN bus termination)

**Release date:** 2026-06-15
**Status:** Docs-only — no code change since v0.4.0

This patch closes the documentation gap that cost the UACJ on-site install
several hours of debugging. The simulator code is unchanged; the wiring guide
now correctly specifies the external 120 Ω CAN bus terminator that v0.4.0
omitted.

## What happened on site

During the on-site bring-up with the Innova 5210 scan tool:

- Pi-side CAN stack came up clean: `state UP`, `bitrate 500000`,
  `can state ERROR-ACTIVE`, MCP2515 driver loaded, timing math consistent
  with the 8 MHz crystal.
- Simulator service accepted scenario pushes from the laptop and returned the
  loaded VIN over `GET /api/sim/health`.
- When the Innova was plugged in and AUTO-LINK started, `candump can0` filled
  with a continuous flood of `can0  000  [0]` lines.
- The flood **stopped** the moment the Innova was unplugged. With the Pi
  powered and the simulator running but nothing plugged in, the bus was
  silent.

The `000  [0]` pattern is how SocketCAN surfaces CAN error frames when not in
`-e` mode — they're not real OBD-II frames, they're bit-level corruption being
counted as zero-payload frames with ID 0.

## Root cause

CAN buses are designed for two 120 Ω terminators in parallel, one at each end
of the bus, totalling ~60 Ω. The MCP2515 module has one terminator built in
(at the simulator end). The v0.4.0 wiring guide assumed the scan tool would
provide the second terminator at the OBD-II end — that assumption is true for
*some* tools (factory-spec OBD-II testers, Snap-on platforms, OBDLink series)
but **false** for the entire consumer tier the UACJ classroom is most likely
to use: Innova, Autel AL-series, generic ELM327 clones. These tools were
designed for real vehicles, where the second 120 Ω lives in the car's wiring,
not the scan tool.

Without the second terminator, the Innova's CAN transceiver puts signal on
the bus but the signal reflects off the unterminated OBD-II end, corrupting
bits, which the MCP2515 reports as error frames.

## Fix

Add a single 120 Ω resistor (1/4 W, 1 % metal film, brown-red-brown bands)
between OBD-II pin 6 (CAN-H) and pin 14 (CAN-L), wired right at the connector
body — not at the MCP2515 end of the cable.

Confirmed on site: within seconds of installing the resistor (no-solder
alligator-clip method for the test), the Innova displayed "Linked to CAN"
and standard OBD-II traffic appeared:

```
can0  7DF  [8]  02 01 00 00 00 00 00 00      ← Innova: Mode 01 supported PIDs
can0  7E8  [8]  06 41 00 00 00 00 00 AA      ← Simulator response
can0  7DF  [8]  02 09 02 00 00 00 00 00      ← Innova: VIN request
can0  7E8  [8]  06 49 00 54 00 00 00 AA      ← Simulator response
can0  7DF  [8]  02 03 00 00 00 00 00 00      ← Innova: Mode 03 (stored DTCs)
```

The Innova displayed I/M monitor status and read VIN and DTC count correctly.

## Documentation updates

- `docs/wiring.md` — added 120 Ω resistor to BOM (line item 7), expanded the
  termination note in Connection 1 with the empirical evidence from the May
  install, added an explicit "install the terminator here" paragraph to
  Connection 3, and added a multimeter sanity check (~60 Ω across OBD pins
  6 ↔ 14 means both terminators are in place).
- `docs/wiring_walkthrough.md` — added 120 Ω resistor to the parts list,
  added a new **Connection 3.5: CAN bus terminator** section with no-solder
  install steps, a ladder-rung topology diagram (resistor bridges H↔L at one
  end, does **not** run along the cable), and the most common mistake to avoid
  (putting one leg on the OBD plug and the other on the MCP2515 — which would
  open the bus in series).
- `docs/install.md` — added three new troubleshooting table rows: (1) the
  `000 [0]` flood-vs-silent symptom matrix pointing at the missing terminator,
  (2) non-standard IDs at the wrong bitrate (`70F`/`71F` at 250 kbps)
  pointing at bitrate or crystal mismatch, and (3) `BUS-OFF` / `ERROR-PASSIVE`
  state recovery via `ip link set can0 down/up` with `restart-ms`.
- `docs/compatibility.md` — added a Verified Tools section recording Innova
  5210 as the first verified tool (with the terminator-prerequisite caveat),
  and added a board-side prerequisites checklist that future testers should
  run through before declaring a tool "incompatible."

## Upgrade path for existing builds

If you assembled per v0.4.0 and your scan tool fails to link (`candump can0`
shows the `000 [0]` flood when the tool is plugged in):

1. Buy one 120 Ω resistor (any electronics store; Mercado Libre, Amazon, Steren).
2. Wire it between OBD-II pin 6 and pin 14 right at the connector body — see
   `docs/wiring_walkthrough.md` Connection 3.5 for step-by-step instructions.
3. Multimeter check (Pi off, scan tool unplugged): ~60 Ω between pins 6 and 14.
4. Re-run the smoke test.

No software changes required; no Pi reflash; no `apt` updates.
