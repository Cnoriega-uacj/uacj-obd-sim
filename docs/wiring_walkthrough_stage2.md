# Wiring Walkthrough — Stage 2 (Pre-CAN add-ons)

> **For Cristopher / first-time builders.** This pairs with the
> technical reference in [wiring.md](wiring.md) ("Pre-CAN add-ons"
> section). It's a step-by-step, plain-language walkthrough for adding
> J1850 protocol support to the board you already built in
> [Stage 1](wiring_walkthrough.md).
>
> **You only need Stage 2 if your training program covers 2004–2007
> GM (Silverado/Tahoe/Sierra) or 2004 Ford (F-150/Mustang) vehicles.**
> For everything 2008+ and most 2003+ — CAN and K-Line from Stage 1
> handle the diagnostics. Skip this entire doc if you don't.

---

## Before you start

1. **Stage 1 must be working.** Push a CAN scenario from the dashboard
   and verify your scan tool reads the VIN. If Stage 1 isn't verified
   yet, finish that first — Stage 2 sits on top of it and you don't
   want to debug both layers at once.
2. **Power off the Pi:** SSH in and run `sudo shutdown -h now`, then
   unplug the USB-C cable.
3. **Decide which add-on you need:**
   - **Add-on A — GM J1850 VPW** (single-wire bus, 10.4 kbps).
     2004–2007 Chevrolet Silverado / Tahoe / GMC Sierra and similar.
   - **Add-on B — Ford J1850 PWM** (twin-wire bus, 41.6 kbps).
     2004 F-150 / Mustang and similar.
   The two are **independent** — build only the ones you need. You can
   build both side-by-side if you teach mixed makes.
4. **Lay parts for the add-on you're building on a clean surface:**

   **Add-on A (GM VPW) parts (~$10 from Mercado Libre):**
   - 1× LM358N op-amp (DIP-8, looks like a small black chip with 8 legs)
   - 2× 2N7000 N-channel MOSFETs (small TO-92 transistors, like 3-legged drops)
   - 1× 10 kΩ resistor (brown-black-orange bands) — R1
   - 1× 1 kΩ resistor (brown-black-red bands) — R2
   - 1× 100 Ω resistor (brown-black-brown bands) — R3
   - 1× 0.1 µF ceramic capacitor (often labeled "104") — C1
   - 1× 10 nF ceramic capacitor (often labeled "103") — C2
   - 1× small breadboard or DIP-8 socket for the LM358

   **Add-on B (Ford PWM) parts (~$15 from DigiKey México):**
   - 1× AM26LS31CN differential line driver (DIP-16)
   - 1× AM26LS32ACN differential line receiver (DIP-16)
   - 1× 120 Ω resistor (brown-red-brown bands) — R4
   - 2× 0.1 µF ceramic capacitors — C3, C4
   - 1× small breadboard or 2× DIP-16 sockets

---

## Pi GPIO pins you'll use for Stage 2

Stage 1 occupies the **primary UART (GPIO 14/15, header pins 8/10)** —
that's wired to the L9637D K-Line driver and we are NOT touching
those wires. Stage 2 uses the Pi's **second UART (UART3)** so K-Line
and J1850 can coexist.

UART3 maps to:
- **GPIO 4** (TX) — header **pin 7**
- **GPIO 5** (RX) — header **pin 29**

You also need the 3.3V rail (pin 1 or 17), 5V (pin 4), and GND (any
even-numbered pin from 6 onward).

**Important — enable UART3 in software first:**

```bash
# On the Pi, before powering off:
sudo nano /boot/firmware/config.txt
```

Add at the bottom (or anywhere after the existing `[all]` block):

```
dtoverlay=uart3
```

Save (Ctrl-O, Enter, Ctrl-X). Then `sudo reboot`. After it comes back,
verify the device appeared:

```bash
ls -l /dev/serial1
# Expect a symlink to ttyAMA1 (the Pi 4 UART3)
```

If `/dev/serial1` does not exist, the overlay didn't take — check
the spelling in config.txt. **Don't continue Stage 2 until this
shows up.**

Now you can shut the Pi down (`sudo shutdown -h now`) and unplug it.

---

# Add-on A — GM J1850 VPW

Skip to [Add-on B](#add-on-b--ford-j1850-pwm) if you're building only
the Ford module.

## Connection A1: LM358 op-amp on a breadboard (10 wires)

The LM358 has 8 legs. Lay it across the breadboard's center notch so
4 legs are on each side.

**Pin 1 of the LM358** is marked with a tiny dimple or a printed dot
on the chip's top face. Hold the chip with the dimple at the
upper-left; the pins then number counter-clockwise:

```
   1 ┤●     ●├ 8        ← pin 1 has the dimple
   2 ┤      ●├ 7
   3 ┤      ●├ 6
   4 ┤●     ●├ 5
```

| Wire # | From | To | What it does |
|---|---|---|---|
| 1 | LM358 **pin 8** (Vcc) | Pi **pin 4** | +5V power |
| 2 | LM358 **pin 4** (GND) | Pi **pin 6** | Ground |
| 3 | LM358 **pin 3** (+input) | OBD-II **pin 2** via R3 (100 Ω) | Bus sense |
| 4 | LM358 **pin 2** (-input) | Midpoint of R1 (10 kΩ) and R2 (1 kΩ) | Threshold reference (~3.3 V) |
| 5 | LM358 **pin 1** (output) | Pi **pin 29** (GPIO 5, RX of UART3) | Decoded bus → Pi |

**R1 and R2 form a voltage divider** between Pi 3.3V (header pin 1)
and GND. Wire R1 from 3.3V to a row on the breadboard, then R2 from
that same row to GND. Then wire the LM358's pin 2 to that middle row.

**C1 (0.1 µF)** wires between LM358 pin 3 and GND — debounce.
**C2 (10 nF)** wires between LM358 pin 3 and the same GND — high-
frequency filter. Mount both as physically close to the LM358 as you
can.

**📸 Take a photo of the LM358 with its 10 connections, send it to me.
I'll check before you go on.**

---

## Connection A2: 2N7000 MOSFET driver (4 wires)

Now the MOSFET that pulls the J1850 bus low. The 2N7000 has 3 legs:
flat side facing you, legs left-to-right are **G** (Gate), **D**
(Drain), **S** (Source).

**Reversing G and D will destroy the MOSFET.** Verify with the
datasheet image before powering up. The flat side typically has
"2N7000" printed on it.

| Wire # | From | To | What it does |
|---|---|---|---|
| 6 | 2N7000 **Gate (G)** | Pi **pin 7** (GPIO 4, TX of UART3) | Pi drives the bus low |
| 7 | 2N7000 **Drain (D)** | OBD-II **pin 2** | The J1850 bus wire |
| 8 | 2N7000 **Source (S)** | Pi **pin 6** (GND) | Ground reference |

**R1 (10 kΩ) is a pull-up.** Wire it between **+12 V at OBD pin 16**
and the OBD bus (pin 2). When the MOSFET is OFF, R1 pulls the bus to
the nominal "high" voltage. When the MOSFET is ON, it shorts the bus
to GND (the "low" state).

Reminder — the OBD-II pin 16 connection from Stage 1 already brings
+12 V near the board. If your OBD pigtail wasn't already broken out
for pin 16, peel back the heat shrink and tap it now.

**📸 Take a photo of the MOSFET with its 4 connections + the R1
pull-up to OBD pin 16, send it to me.**

---

## Connection A3: OBD-II pin 2 (bus tap)

The whole J1850 VPW protocol lives on **OBD pin 2** only — single
wire. You wire the bus from:
- the MOSFET drain (sends data),
- the LM358 pin 3 sense input via R3 (reads data),
- the R1 pull-up to +12 V (keeps the bus nominally high when idle).

All three meet at one breadboard row. From that row, run **one wire
to OBD-II pin 2**.

If you're building both add-ons at once, **don't share OBD pin 2
with the Ford add-on** — Ford uses pin 2 differently. The simulator
picks which protocol to drive per scenario, but the electrical mixing
would put both transceivers on the bus simultaneously. Build one,
test it, unplug it, then build the other if you need both.

**📸 Photo of OBD-II pin 2 with the three converging wires.**

---

## Don't power up yet

Before plugging the Pi USB-C back in, double-check:

- [ ] **MOSFET orientation:** flat side facing you, G-D-S
      left-to-right, Gate goes to Pi pin 7.
- [ ] **R1 is between +12 V (OBD pin 16) and the bus**, not between
      the bus and GND. Wrong direction = no pull-up = no bus.
- [ ] **LM358 pin 1 (the dimple) is at the same corner you wired
      "pin 1" to.** Reversing the chip will not blow it up but it
      won't work either.
- [ ] **No bare wires touching each other** between the +12 V
      pull-up rail and the Pi 5 V / 3.3 V rails. A short there will
      brick the Pi.

When you're sure, plug the Pi back in.

---

## Bench-test the GM add-on (no scan tool yet)

SSH into the Pi and load the GM test scenario:

```bash
cd /opt/uacj-obd-sim
sudo systemctl restart uacj-obd-sim

# Push a small VPW test scenario from a separate terminal or laptop:
curl -X POST http://uacj-sim.local:8765/api/sim/load \
  -H 'Content-Type: application/json' \
  -d '{
    "vehicle": {
      "vin": "1GCEC14X14Z123456",
      "make": "Chevrolet",
      "year": 2004
    },
    "j1850_variant": "vpw",
    "live_baseline": {"010C": 800, "010D": 50}
  }'

# Read back the simulator state to confirm:
curl http://uacj-sim.local:8765/api/sim/state
```

Then with a J1850 VPW scan tool plugged into the OBD-II port (any
Innova 31xx/32xx series, or a USB OBD2 tool that supports VPW),
read VIN. Expected: VIN reads `1GCEC14X14Z123456`, RPM shows
~800, speed ~50.

**If the scan tool says "no link" or "unable to read":**
1. Check the +12 V at OBD pin 16 with a multimeter — should be
   ~12.0 V relative to OBD pin 5.
2. Probe the bus with an oscilloscope (if available). Idle bus
   should sit at ~7 V (pulled up by R1). Active data should swing
   between 0 V and ~7 V at 10.4 kbps.
3. Re-verify the MOSFET orientation. The most common build error
   is G/D swapped.
4. Check that `/dev/serial1` exists (from the UART3 setup above).
5. `sudo systemctl status uacj-obd-sim` — look for J1850 startup
   errors in the log.

---

# Add-on B — Ford J1850 PWM

Skip to [Wrap-up](#wrap-up) if you only need the GM add-on.

## Connection B1: AM26LS31 driver (8 wires)

The AM26LS31 has 16 legs in a long thin package. Pin 1 is again
marked with a dimple or printed dot — hold the chip with the dimple
upper-left, pins count counter-clockwise.

```
   1 ┤●          ●├ 16     ← pin 1 has the dimple
   2 ┤           ●├ 15
   3 ┤           ●├ 14
   4 ┤           ●├ 13
   5 ┤           ●├ 12
   6 ┤           ●├ 11
   7 ┤           ●├ 10
   8 ┤●          ●├  9
```

| Wire # | From | To | What it does |
|---|---|---|---|
| 1 | AM26LS31 **pin 16** (Vcc) | Pi **pin 4** | +5V |
| 2 | AM26LS31 **pin 8** (GND) | Pi **pin 6** | Ground |
| 3 | AM26LS31 **pin 1** (Enable A) | Pi **pin 1** (3.3V) | Drives the bus continuously |
| 4 | AM26LS31 **pin 2** (Input A1) | Pi **pin 7** (GPIO 4 = TX of UART3) | Pi sends bus data |
| 5 | AM26LS31 **pin 3** (Output Y1) | OBD-II **pin 2** | BUS+ wire |
| 6 | AM26LS31 **pin 4** (Output Z1) | OBD-II **pin 10** | BUS- wire |

**C3 (0.1 µF) decoupling cap:** wire between AM26LS31 pin 16 and
pin 8 (Vcc and GND). Mount it as close to the chip as physically
possible.

**📸 Photo of the AM26LS31 with all 6 logic wires + C3.**

---

## Connection B2: AM26LS32 receiver (6 wires)

Same package, same pin-1 location.

| Wire # | From | To | What it does |
|---|---|---|---|
| 7 | AM26LS32 **pin 16** (Vcc) | Pi **pin 4** | +5V (you can share the same +5V rail as the AM26LS31) |
| 8 | AM26LS32 **pin 8** (GND) | Pi **pin 6** | Ground |
| 9 | AM26LS32 **pin 4** (Enable A, active-high) | Pi **pin 1** (3.3V) | Always-listening |
| 10 | AM26LS32 **pin 2** (+A) | OBD-II **pin 2** | BUS+ sense |
| 11 | AM26LS32 **pin 3** (-A) | OBD-II **pin 10** | BUS- sense |
| 12 | AM26LS32 **pin 1** (Output Y1) | Pi **pin 29** (GPIO 5 = RX of UART3) | Decoded bus → Pi |

**C4 (0.1 µF):** same as C3, between this chip's pin 16 and pin 8.

**📸 Photo of the AM26LS32 with all 6 wires + C4.**

---

## Connection B3: 120 Ω bus terminator (R4)

Ford's twin-wire bus needs a termination resistor between BUS+ and
BUS-. Without it, signal reflections garble the frames.

Wire **R4 (120 Ω, brown-red-brown bands)** directly between OBD-II
pin 2 and OBD-II pin 10.

**📸 Photo of R4 spanning the two pins.**

---

## Bench-test the Ford add-on

```bash
curl -X POST http://uacj-sim.local:8765/api/sim/load \
  -H 'Content-Type: application/json' \
  -d '{
    "vehicle": {
      "vin": "1FAFP404X4F123456",
      "make": "Ford",
      "year": 2004
    },
    "j1850_variant": "pwm",
    "live_baseline": {"010C": 800, "010D": 50}
  }'
```

Plug a Ford-compatible scan tool (Innova 31xx/32xx supports PWM, or
older OBDLink LX). Read VIN — expected `1FAFP404X4F123456`.

**If "no link":**
1. **R4 is critical.** Verify ~120 Ω resistance between OBD pins 2
   and 10 with the scan tool unplugged.
2. **Enable pins floating.** AM26LS31 pin 1 and AM26LS32 pin 4 must
   both be tied to 3.3 V. A loose dupont jumper here gives "no
   link" with no other symptoms.
3. **Verify the second UART** with `cat /dev/serial1` — should
   block waiting for input, not error out.

---

## Wrap-up

### Verify in one scenario push

The simulator picks which J1850 variant to use from the scenario
payload's `j1850_variant` field. Valid values:
- `"vpw"` — GM, drives the single-wire 10.4 kbps bus
- `"pwm"` — Ford, drives the twin-wire 41.6 kbps bus
- absent — CAN + K-Line only (Stage 1 behaviour)

A scenario for a Stage-2-only vehicle should include the variant
field so the Pi picks the right protocol:

```json
{
  "vehicle": { "make": "Chevrolet", "year": 2006 },
  "j1850_variant": "vpw",
  "live_baseline": { ... }
}
```

The dashboard's scenario editor doesn't expose `j1850_variant` yet
— you'll need to edit it via `curl` or via the JSON file directly
until that lands in a future release.

### What you've built

After Stage 2 the same Pi simulator answers:
- 2008+ vehicles via CAN (95% of the curriculum) ← from Stage 1
- 2003+ K-Line vehicles ← from Stage 1
- 2004–2007 GM via J1850 VPW ← Add-on A
- 2004 Ford via J1850 PWM ← Add-on B

The board is bigger but the same scenario-push workflow drives all
four protocols. Students see one OBD-II port, and the simulator
picks the right protocol based on the scenario the instructor
loaded.

### Photo to send

Before considering Stage 2 complete, please send me ONE photo of
the finished board with both add-ons (or just the one you built)
labeled with masking tape:
- "VPW" on the LM358 + MOSFET cluster
- "PWM" on the AM26LS31 + AM26LS32 pair
- The OBD-II pigtail with pins 2, 10 visible

Plus the output of:

```bash
ls -l /dev/serial*
curl http://uacj-sim.local:8765/api/sim/health
```

That confirms both UARTs are alive and the simulator service is up.

---

## Common Stage-2 gotchas

- **Pull-up R1 missing on VPW.** No pull-up → bus floats → bus is
  always "low" → no scan tool will see anything.
- **MOSFET G/D swapped on VPW.** Bus is always "low" because the
  MOSFET is always conducting. Replace the FET — it's likely fried.
- **Bus reflections on PWM without R4.** Scan tool reports
  intermittent "no link" or garbled VIN. The fix is always to add
  R4. Don't try to debug the firmware — it's the resistor.
- **Both AM26LS31 enable AND AM26LS32 enable floating.** Without
  the 3.3 V tie, the driver doesn't drive and the receiver doesn't
  receive. Scan tool reports "no link" with both transceiver power
  LEDs lit normally.
- **Sharing TX between K-Line and J1850.** If you only have the
  primary UART enabled, the L9637D and the J1850 driver both pull
  the same Pi pin. Symptom: K-Line stops working too. Fix: enable
  `dtoverlay=uart3` as described at the top.
- **Bench-test scan tool doesn't support the protocol you're
  testing.** A CAN-only Innova won't see VPW or PWM no matter how
  perfect the wiring is. Check the scan tool's spec sheet —
  "J1850 VPW" or "J1850 PWM" must be listed.

---

## If you get stuck

Send a photo of the wiring around the failing connection and the
output of the `curl /api/sim/health` and `journalctl -u uacj-obd-sim
--since "5 min ago"` commands. We'll diagnose from there. Don't
unwire anything before sending the photo — the wiring is the
evidence.

Take your time. Stage 2 is optional, so there's no rush. Most
classroom curriculums work fine with just Stage 1.
