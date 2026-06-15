# Wiring Walkthrough — Stage 1 (Main board)

> **For Cristopher / first-time builders.** This is a step-by-step,
> plain-language walkthrough that pairs with the technical reference in
> [wiring.md](wiring.md). If you've never wired electronics before,
> follow this document instead — it has no schematics to read.

---

## Before you start

1. **Power off the Pi:** SSH in and run `sudo shutdown -h now`, then unplug the USB-C cable. The Pi must be fully off — don't wire a live board.
2. **Lay your parts in order on a clean surface:**
    - Raspberry Pi 4
    - MCP2515 CAN module (the blue board with pin headers)
    - L9637D chip (the small black 8-leg chip)
    - OBD-II 16-pin female pigtail (the black coiled cable with the trapezoid plug)
    - Pack of dupont jumper wires (the rainbow ones)
    - 470 Ω resistor (yellow-purple-brown bands) — for the K-Line side
    - **120 Ω resistor (brown-red-brown bands) — for the CAN bus terminator at the OBD-II end**
    - LM2596 buck converter (the small blue rectangle with screw terminals)

---

## Understanding the Pi's GPIO header

The Pi has a row of 40 metal pins along one edge — that's the GPIO header. Pins are numbered like this when you look at the Pi with the USB ports facing down and the GPIO at the top:

```
       Pi 4 (USB ports at bottom, GPIO header at top)

   pin 1 ┤●  ●├ pin 2     ← pin 1 is the corner closest to the SD card slot
   pin 3 ┤●  ●├ pin 4
   pin 5 ┤●  ●├ pin 6
   pin 7 ┤●  ●├ pin 8
   pin 9 ┤●  ●├ pin 10
   pin 11┤●  ●├ pin 12
   pin 13┤●  ●├ pin 14
   pin 15┤●  ●├ pin 16
   pin 17┤●  ●├ pin 18
   pin 19┤●  ●├ pin 20
   pin 21┤●  ●├ pin 22
   pin 23┤●  ●├ pin 24
   pin 25┤●  ●├ pin 26
   ...                        (continues to pin 40)
```

Odd-numbered pins are on the left column, even-numbered on the right.
**Pin 1 is the corner of the header closest to the SD card slot.** If
you're unsure, look for the tiny square-shaped solder pad — that
marks pin 1.

---

## Connection 1: MCP2515 → Pi (7 wires)

The MCP2515 is a small blue board with about 8 labeled pins (VCC, GND,
CS, SO, SI, SCK, INT — sometimes also has CLKOUT). You'll need 7
female-to-female dupont jumpers.

Plug each wire one at a time, in this order:

| Wire # | From MCP2515 pin labeled | To Pi pin number | What it does |
|---|---|---|---|
| 1 | **VCC** | Pi **pin 2** | +5V power |
| 2 | **GND** | Pi **pin 25** | Ground |
| 3 | **CS**  | Pi **pin 24** | Chip select |
| 4 | **SO**  | Pi **pin 21** | Data out from CAN chip |
| 5 | **SI**  | Pi **pin 19** | Data in to CAN chip |
| 6 | **SCK** | Pi **pin 23** | Clock |
| 7 | **INT** | Pi **pin 22** | Interrupt signal |

**Tip:** use different jumper wire colors per row so you can spot a mistake at a glance. For example:
- VCC = red
- GND = black
- everything else = mixed colors

The MCP2515 also has two screw terminals labeled `H` and `L` (or
`CANH` / `CANL`) on the opposite side from the pin headers. **Leave
those alone for now** — they connect to the OBD-II port later
(Connection 3).

**📸 Take a photo of the wires running between the MCP2515 and the Pi, send it to me. I'll confirm before you go on.**

---

## Connection 2: L9637D K-Line transceiver

The L9637D is the small black 8-pin chip in the bag. It needs to sit
on a tiny breadboard (or in a solderless 8-pin socket) so we can
attach wires to each of its 8 legs.

### How to identify the L9637D's pin 1

Look at the top of the chip. There's a small **dot** or a **half-moon
notch** at one end. **The pin closest to that mark is pin 1.** From
there:

```
    L9637D top view (looking down at the chip)

        ┌─────┐
   1 ┤●  • ┤ 8      ← dot/notch marks the pin-1 end
   2 ┤    ┤ 7
   3 ┤    ┤ 6
   4 ┤    ┤ 5
        └─────┘
```

### Wire the L9637D — 8 connections plus the resistor

| Wire # | From L9637D pin | To | What it does |
|---|---|---|---|
| 1 | **Pin 1** (RX) | Pi **pin 10** (RXD0) | K-Line receive |
| 2 | **Pin 2** (GND) | Pi **pin 9** (GND) | Ground |
| 3 | **Pin 3** (ENA) | Pi **pin 1** (3.3V) | Chip enable |
| 4 | **Pin 4** (TX) | Pi **pin 8** (TXD0) | K-Line transmit |
| 5 | **Pin 5** (GND) | Same GND as wire 2 | Signal ground |
| 6 | **Pin 6** (K-Line) | (Will go to OBD-II pin 7 in Connection 3 — leave loose for now) | K-Line bus signal |
| 7 | **Pin 7** (Vbat) | One end of the 470 Ω resistor (yellow-purple-brown) | Battery sense, through resistor |
| 8 | **Pin 8** (Vcc) | Pi **pin 4** (5V) | 5V power for chip logic |

### The 470 Ω resistor

The other end of the 470 Ω resistor connects to **OBD-II pin 16 (+12V)**
later, in Connection 3. For now, just attach one end to L9637D pin 7
and leave the other end loose.

**📸 Take a photo showing all the wires going into and out of the L9637D, plus the resistor, and send it to me. I'll confirm before you go on.**

---

## Connection 3: OBD-II female pigtail

The OBD-II pigtail is the black coiled cable with a trapezoid-shaped
plug on one end and loose wires on the other. The loose wires are
usually color-coded; if not, count them carefully.

The plug has 16 numbered pins. Looking at the plug **with the wide
flat top facing up**:

```
   ┌──────────────────────────────────────────┐
   │  1  2  3  4  5  6  7  8                  │   ← top row
   │  9 10 11 12 13 14 15 16                  │   ← bottom row
   └──────────────────────────────────────────┘
```

We only use 6 of the 16 pins. Leave the other 10 wires disconnected
(tape them off or trim them):

| OBD-II pin | Color (typical) | Connect to |
|---|---|---|
| **Pin 4** | Black | Pi pin 6 (GND) and L9637D pin 2 |
| **Pin 5** | Black/striped | L9637D pin 5 |
| **Pin 6** | Green (CAN-H) | MCP2515 screw terminal **H** (or **CANH**) |
| **Pin 7** | White or yellow (K-Line) | L9637D pin 6 (the loose K-Line wire from Connection 2) |
| **Pin 14** | Brown or yellow (CAN-L) | MCP2515 screw terminal **L** (or **CANL**) |
| **Pin 16** | Red (+12V) | Two places: (a) **input** of the buck converter, (b) the free end of the 470 Ω resistor from Connection 2 |

**⚠ Critical: don't mix up pin 4 and pin 16.** Pin 4 is ground (0 V),
pin 16 is +12 V. Crossing them will damage the buck converter and
possibly the Pi.

If you have a multimeter, **before** powering on, set it to continuity
and verify:
- Pin 4 has continuity to Pi GND
- Pin 16 is **not** shorted to any ground

### Buck converter (12 V → 5 V)

The LM2596 buck converter has two pairs of screw terminals. Look
closely — they're labeled **IN+, IN-** on one side and **OUT+, OUT-**
on the other.

- **IN+:** OBD-II pin 16 (+12 V)
- **IN-:** OBD-II pin 4 (ground)
- **OUT+:** Pi pin 4 (5V) — note: pin 2 is already taken by MCP2515 VCC, but pin 4 is also 5V, so use that. They share the same 5V rail internally.
- **OUT-:** Pi pin 6 (GND) — can share with other ground wires.

> **The buck converter has a small screw on the blue trimmer.** Don't
> turn it yet — we'll set output voltage to 5 V together before
> connecting the Pi.

**📸 Take a photo showing the OBD-II pigtail wired to everything, and a separate close-up of the buck converter's screw terminals, and send both.**

---

## Connection 3.5: CAN bus terminator (the 120 Ω resistor at the OBD plug)

> **Don't skip this step, even if you don't think you need it.** Most consumer scan tools — Innova 5210, Autel AL319, generic ELM327 clones — do **not** carry their own 120 Ω terminator inside. They assume the car's wiring provides it. Without this resistor, the bus is under-terminated and you will spend hours chasing phantom error frames (we know — we did, on the May 2026 install). 2 minutes now saves 4 hours later.

The CAN bus needs **two** 120 Ω terminators, one at each end, like the two drains in a garden hose. The MCP2515 module already has one built in. The 120 Ω resistor you have here adds the **second** one at the OBD-II connector end.

**Where it goes:** the resistor sits like a single ladder rung between the **CAN-H** wire (going to OBD pin 6) and the **CAN-L** wire (going to OBD pin 14), placed **close to the OBD-II plug** (within ~2-3 cm of the connector body, not at the MCP2515 end of the cable).

```
   OBD-II plug end                                         MCP2515 module end
   pin 6 (CAN-H) ●═══════════════════════════════════════════●  CANH terminal
                  ┃                                              ┃
                  ┃ ← 120 Ω resistor bridges                     ┃
                  ┃   the two wires HERE                         ┃
                  ┃   (near the OBD plug)                        ┃
                  ┃                                              ┃
   pin 14 (CAN-L) ●═══════════════════════════════════════════●  CANL terminal
```

**Installation — no-solder method** (works fine for the first build; can be soldered later for permanence):

1. About 2 cm from the back of the OBD-II plug, find the CAN-H wire (the one going to pin 6 — check your pigtail's color code; on cheap pigtails it is often red or green) and the CAN-L wire (going to pin 14 — often yellow or brown).
2. Strip ~5 mm of plastic insulation off each wire to expose the bare copper.
3. Wrap one leg of the 120 Ω resistor tightly around the bare copper of the CAN-H wire.
4. Wrap the other leg tightly around the bare copper of the CAN-L wire.
5. Squeeze each connection with pliers if you can — the tighter the better.
6. Wrap each connection in electrical tape so the bare copper cannot touch anything else (especially the other resistor leg or any nearby wire).
7. **Critical:** do NOT cut the CAN-H or CAN-L wires. They continue uninterrupted to the MCP2515 screw terminals. The resistor only ADDS a bridge between them at the OBD end.

> **Common mistake to avoid:** do not put one leg of the resistor at the OBD plug and the other leg at the MCP2515 module. That would put the resistor "in series" with the bus and the chip would never see a signal. Both legs of the resistor must be at the SAME location (the OBD end), connecting CAN-H to CAN-L there.

**Verify with a multimeter (Pi powered off, no scan tool plugged in):** measure resistance between OBD-II pin 6 and pin 14. Expected: **~60 Ω** (two 120 Ω resistors in parallel — one on the MCP2515 module, one you just added). ~120 Ω means only one terminator is wired (you may have skipped this step or the MCP2515's onboard terminator is off); ~40 Ω or less means a short or wrong resistor value.

**📸 Take a close-up photo BEFORE you tape it up, showing the bare copper connections clearly, and send it for confirmation.**

---

## Don't power up yet

Once all three connections are wired:

1. **Do not connect 12V to OBD-II pin 16 yet.** I need to OK the wiring first.
2. Don't plug the Pi back into USB-C either — once 12V is on OBD pin 16, the buck converter feeds the Pi's 5V. Powering both at the same time can damage the board.
3. After I confirm the wiring is correct from your photos, I'll walk you through adjusting the buck converter to 5V output and powering up.

---

## If you get confused

- **Send a photo** of whatever step is confusing — I'll mark up the photo and send it back.
- **Don't force anything.** Dupont wires push on lightly; the OBD-II pigtail screws need a small phillips; the buck converter trimmer turns with the screwdriver tip.
- **Take your time.** This is the part where rushing causes hours of debugging.

When you finish each of the three connections, send the photo and
wait for me to confirm before moving to the next. We're not in a
hurry.

---

## What comes next (after Stage 1 is verified working)

- **Stage 2 — Pre-CAN add-ons.** Optional, for 2004–2007 GM (Silverado/Tahoe) and 2004 Ford (F-150/Mustang) vehicles. Adds two small modules to the same board — about 20 extra minutes. See [wiring.md](wiring.md) "Pre-CAN add-ons" section when you're ready.
- **Smoke test.** Push a scenario from the laptop dashboard, plug your scan tool into the OBD-II port, verify it reads the VIN and DTCs from the scenario.
- **Repo transfer.** I'll move the GitHub repo to your account so the code lives under UACJ's name long-term.
