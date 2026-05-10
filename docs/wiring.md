# Wiring Guide — UACJ OBD-II Training Simulator Board

A 10–15 minute assembly. No soldering required if you use the dupont jumper kits in the BOM. Take your time on the OBD-II connector pinout — that is the only part where a wrong connection has consequences (you would short 12V to ground if pin 16 lands on pin 4).

---

## Bill of materials

| # | Part | Purpose | Notes |
|---|------|---------|-------|
| 1 | Raspberry Pi 4 (4GB) | Main board | Pi 3B+ also works |
| 2 | OBDLink SX USB / STN2120 adapter | Acquisition (laptop side) | Plug into laptop USB |
| 3 | MCP2515 CAN module (8 MHz crystal, TJA1050 transceiver) | CAN responder | Common AliExpress / Mercado Libre part |
| 4 | L9637D K-Line transceiver IC + 470Ω pull-up | K-Line responder | DIP-8 package |
| 5 | OBD-II 16-pin female connector with breakout | Student-facing port | Look for "OBD-II female to dupont" pre-wired |
| 6 | 12V → 5V buck converter (2A) | Power the Pi from the OBD-II port pin 16 | LM2596 module is fine |
| 7 | Project enclosure | Mounts everything | Any 100×80×40mm box |

---

## Pi 4 GPIO header — what we use

```
       3V3 ┤ 1  2 ├ 5V
       SDA ┤ 3  4 ├ 5V
       SCL ┤ 5  6 ├ GND
   GPIO 4  ┤ 7  8 ├ TXD0   ← K-Line TX (to L9637 pin 4)
       GND ┤ 9 10 ├ RXD0   ← K-Line RX (from L9637 pin 1)
   GPIO17  ┤11 12 ├ GPIO18
   GPIO27  ┤13 14 ├ GND
   GPIO22  ┤15 16 ├ GPIO23
       3V3 ┤17 18 ├ GPIO24
   MOSI    ┤19 20 ├ GND     ← MCP2515 SI
       MISO┤21 22 ├ GPIO25  ← MCP2515 INT
   SCLK    ┤23 24 ├ CE0     ← MCP2515 SCK / CS
       GND ┤25 26 ├ CE1
```

---

## Connection 1 — MCP2515 CAN module → Pi

| MCP2515 pin | Pi pin | GPIO |
|---|---|---|
| VCC | 2 (5V) | — |
| GND | 25 (GND) | — |
| CS  | 24 (CE0) | GPIO 8 |
| SO  | 21 (MISO) | GPIO 9 |
| SI  | 19 (MOSI) | GPIO 10 |
| SCK | 23 (SCLK) | GPIO 11 |
| INT | 22 | GPIO 25 |

Then the **MCP2515 module's CAN side** has two screw terminals labeled `CANH` and `CANL`:

| MCP2515 CAN side | OBD-II connector pin |
|---|---|
| CANH | pin 6 |
| CANL | pin 14 |

A 120Ω termination resistor between CANH and CANL is already on most MCP2515 modules — confirm with a multimeter (R between H and L should read ~60Ω if both ends are terminated, ~120Ω with just one). The student's scan tool will provide the second terminator.

---

## Connection 2 — L9637D K-Line transceiver → Pi

The L9637 sits between the Pi's UART (TXD0/RXD0) and the OBD-II K-Line pin.

```
                    +---+--- 12V (OBD pin 16, fused 1A)
                    |   |
         +----------+   |
         |              |
       470Ω           Vbat (L9637 pin 7)
         |              |
   +-----+--------------+
   |     |              |
   |     |   +--------+ |
   |     +---| Vcc    | |   L9637D pinout (DIP-8):
   |         |  L9637 | |       1: RxD  → Pi RXD0 (pin 10)
   |         | pin 8  | |       2: GND
   |         +--------+ |       3: ENA  → 3V3
   |                    |       4: TxD  ← Pi TXD0 (pin 8)
   +--------------------+       5: GND
   |                            6: K-Line ↔ OBD pin 7
   |                            7: Vbat (12V via 470Ω from OBD pin 16)
   |                            8: Vcc → 5V from Pi pin 2
   |
   |
  OBD pin 7 (K-Line)
```

Wiring summary:

| L9637 pin | Connect to |
|---|---|
| 1 (RxD) | Pi GPIO 10 (RXD0) |
| 2 (GND) | OBD pin 4 (chassis ground) and Pi GND |
| 3 (ENA) | Pi 3V3 (pin 1) |
| 4 (TxD) | Pi GPIO 8 (TXD0) |
| 5 (GND) | OBD pin 5 (signal ground) |
| 6 (K-Line) | OBD pin 7 |
| 7 (Vbat) | 12V from OBD pin 16, through a 470Ω resistor |
| 8 (Vcc) | Pi 5V (pin 2) |

---

## Connection 3 — OBD-II female connector pinout

This is the port the student plugs their scan tool into. Wire only these 5 pins:

| OBD-II pin | Signal | Goes to |
|---|---|---|
| 4 | Chassis ground | Pi GND + L9637 pin 2 |
| 5 | Signal ground | L9637 pin 5 |
| 6 | CAN-H | MCP2515 CANH terminal |
| 7 | K-Line | L9637 pin 6 |
| 14 | CAN-L | MCP2515 CANL terminal |
| 16 | +12V | Buck converter input → Pi 5V (and 470Ω resistor → L9637 Vbat) |

Pins 1, 2, 3, 8, 9, 10, 11, 12, 13, 15 are left unconnected — they belong to other protocols (J1850, manufacturer-specific, ignition sense) we are not implementing in v1.

---

## Power

Two options:

1. **From the OBD-II port (12V on pin 16, fused).** Easier for classroom demos because the simulator powers up the moment the student connects their scan tool's lead. Use a buck converter rated for 2A continuous; connect output to Pi 5V/GND.
2. **External 5V supply to Pi USB-C.** More reliable if you also run a Wi-Fi push from the laptop while a scan tool is connected.

Whichever option, **do not connect both at once** — backfeeding the Pi is what kills these boards.

---

## Verifying the build before plugging in a scan tool

After running `scripts/setup_pi.sh` and rebooting, on the Pi:

```bash
# CAN bus is up
ip -details link show can0     # state UP, bitrate 500000

# Generate a fake CAN request and see the simulator respond
candump can0 &                  # in another terminal
cansend can0 7DF#02010C00000000000000   # mode 01 PID 0C (RPM)
# Expect a response on 0x7E8 with 0x41 0x0C ...
```

For K-Line, with the L9637 connected to a USB-to-K-Line tester (or just looped back):

```bash
# Send a KWP fast-init request and see the response
echo -ne "\xC2\x33\xF1\x01\x0C\xA9" > /dev/serial0
cat /dev/serial0 | xxd          # response frame should appear
```

Once both bench tests pass, plug in any OBD-II scan tool. The first thing it will read is the VIN — if that comes back as the VIN you pushed from the laptop, the wiring is correct.

---

# Pre-CAN add-ons (optional)

These are the GM J1850 VPW (2004–2007 Silverado/Tahoe/Sierra) and Ford
J1850 PWM (2004 F-150/Mustang) modules. They are **optional** — if you
only need CAN + K-Line coverage (~95% of 2004+ vehicles), skip this
entire section. The simulator software has the J1850 framing built in
already; these instructions add the electrical layer.

The two add-ons are independent. Build only the ones you need.

## Add-on A — GM J1850 VPW (single-wire, 10.4 kbps)

**Extra parts** (~$10 from Mercado Libre):
- 1× LM358N op-amp (DIP-8)
- 2× 2N7000 N-channel MOSFETs
- 1× 10 kΩ resistor (R1)
- 1× 1 kΩ resistor (R2)
- 1× 100 Ω resistor (R3)
- 1× 0.1 µF ceramic capacitor (C1)
- 1× 10 nF ceramic capacitor (C2)

J1850 VPW is a single-wire bus that swings between ~0V and ~7V at
10.4 kbps. The DIY transceiver below converts Pi UART logic levels
(0–3.3V) to/from the bus voltage.

```
                              +12V (OBD pin 16)
                                  │
                                  ├─── R1 (10kΩ pull-up)
                                  │
   Pi GPIO 14 (TXD1, alt UART) ──┤  Q1 (2N7000) drain
                                  │  source ── GND
                                  │
   ┌─── J1850 BUS ────────────────┴─── OBD pin 2
   │
   │
   ├── R3 (100Ω) ── LM358 pin 3 (+input)
   │
   ├── C1 (0.1µF) ── GND      (debounce)
   │
   LM358 pin 2 (-input) ── voltage divider midpoint (~3.3V from R1/R2)
   LM358 pin 1 (output) ── Pi GPIO 15 (RXD1)
   LM358 pin 8 (Vcc) ── Pi 5V
   LM358 pin 4 (GND) ── GND
```

**Wiring summary table:**

| Component | Pin/Lead | Connect to |
|---|---|---|
| LM358N pin 1 (OUT A) | output | Pi GPIO 15 (RXD1, header pin 10 — second UART, see below) |
| LM358N pin 2 (-IN A) | reference | midpoint of R1+R2 voltage divider (~3.3V) |
| LM358N pin 3 (+IN A) | sense | OBD-II pin 2 via R3 (100Ω) |
| LM358N pin 4 | GND | OBD-II pin 4 + Pi GND |
| LM358N pin 8 | Vcc | Pi 5V (header pin 2) |
| 2N7000 Q1 gate | input | Pi GPIO 14 (TXD1) |
| 2N7000 Q1 drain | output | OBD-II pin 2 (the J1850 bus line) |
| 2N7000 Q1 source | GND | Pi GND |
| R1 (10 kΩ) | between | +12V and OBD-II pin 2 (active pull-up to nominal high) |
| C2 (10 nF) | between | LM358 pin 3 and GND (high-frequency filtering) |

**Important — second UART on the Pi:** The Pi 4's primary UART
(GPIO 14/15) is shared with the L9637D K-Line driver in the main build.
You have two options:

- **Option 1:** Add `dtoverlay=uart3` to `/boot/firmware/config.txt`
  (the script does not do this by default). UART3 maps to GPIO 4 (TX)
  and GPIO 5 (RX). Use those pins for the J1850 transceiver and leave
  the K-Line on UART0.
- **Option 2:** Multiplex — only one of K-Line or J1850 will be active
  on a given scenario, so you can swap the wiring physically. Simpler
  but requires a jumper move per scenario change.

The simulator code reads which UART to use from a config file, so you
can switch in software too.

**Bench-test the GM add-on:**

```bash
# After rebooting with the uart3 overlay enabled:
ls -l /dev/serial2     # symlink to /dev/ttyAMA1 = UART3 on the Pi 4

# Send a J1850 mode 01 PID 0C request and capture the reply
.venv/bin/python -c "
from uacj_obd.simulator.j1850 import encode_request, decode
import serial, time
ser = serial.Serial('/dev/serial2', 10400, timeout=1)
ser.write(encode_request(b'\\x01\\x0C'))
time.sleep(0.2)
print(decode(ser.read(8)).data.hex())
"
# Expect: '410cXXYY' where XXYY is RPM*4
```

## Add-on B — Ford J1850 PWM (twin-wire, 41.6 kbps)

**Extra parts** (~$15 from DigiKey México):
- 1× AM26LS31CN differential line driver (DIP-16) — drives the bus
- 1× AM26LS32ACN differential line receiver (DIP-16) — reads the bus
- 1× 120 Ω resistor (R4) — bus termination
- 1× 0.1 µF bypass capacitor (C3, C4) — one per IC

Ford SCP is a twin-wire differential bus (BUS+ on OBD pin 2, BUS- on
OBD pin 10) at 41.6 kbps. The AM26LS31/32 pair is the standard
RS-422 driver/receiver and works for J1850 PWM.

```
   Pi GPIO 14 (TXD1) ── AM26LS31 input A1 (pin 2)
                        AM26LS31 enable A (pin 1) ── Pi 3V3
                        AM26LS31 output Y1 (pin 3) ── OBD pin 2 (BUS+)
                        AM26LS31 output Z1 (pin 4) ── OBD pin 10 (BUS-)
                        AM26LS31 Vcc (pin 16) ── Pi 5V
                        AM26LS31 GND (pin 8)  ── Pi GND

   OBD pin 2 (BUS+)   ── AM26LS32 input A+ (pin 2)
   OBD pin 10 (BUS-)  ── AM26LS32 input A- (pin 3)
                        AM26LS32 enable A (pin 4) ── Pi 3V3 (active high)
                        AM26LS32 output Y1 (pin 1) ── Pi GPIO 15 (RXD1)
                        AM26LS32 Vcc (pin 16) ── Pi 5V
                        AM26LS32 GND (pin 8)  ── Pi GND

   R4 (120Ω) between OBD pin 2 and OBD pin 10 — bus termination
   C3, C4 (0.1µF each) between Vcc and GND of each IC
```

**Wiring summary table:**

| Net | From | To |
|---|---|---|
| TXD | Pi GPIO 14 | AM26LS31 pin 2 (input A1) |
| BUS+ | AM26LS31 pin 3 | OBD-II pin 2 |
| BUS- | AM26LS31 pin 4 | OBD-II pin 10 |
| BUS+ sense | OBD-II pin 2 | AM26LS32 pin 2 |
| BUS- sense | OBD-II pin 10 | AM26LS32 pin 3 |
| RXD | AM26LS32 pin 1 | Pi GPIO 15 |
| Term R | between OBD pins 2 ↔ 10 | 120 Ω resistor |
| Vcc, GND | both ICs pins 16, 8 | Pi 5V / GND |
| Enables | AM26LS31 pin 1, AM26LS32 pin 4 | Pi 3V3 (header pin 1 or 17) |

**Bench-test the Ford add-on:**

```bash
# Same UART setup as the GM add-on, but at 41.6 kbps for PWM
.venv/bin/python -c "
from uacj_obd.simulator.j1850 import encode_request, decode
import serial, time
ser = serial.Serial('/dev/serial2', 41600, timeout=1)
ser.write(encode_request(b'\\x09\\x02'))   # VIN read
time.sleep(0.3)
chunks = ser.read(64)
print(chunks.hex())
"
# Expect: multiple frames each beginning with the J1850 priority byte
# 0x48 (response), then bus addresses, then VIN bytes.
```

## Choosing which transceiver answers a given scenario

The simulator runs one J1850 transceiver at a time. Pick the active
one in the scenario payload:

```json
{
  "vehicle": { "make": "Chevrolet", "year": 2006 },
  "j1850_variant": "vpw"
}
```

Valid values are `"vpw"` (GM), `"pwm"` (Ford), or absent (CAN/K-Line
only). The Pi-side `j1850_runtime.py` selects which UART/baud to open
based on this field; the framing code is identical for both.

## Common pre-CAN gotchas

- **No 120 Ω terminator on PWM** — Ford's bus is sensitive to
  reflections; without R4 the scan tool may see garbled frames at the
  start of each message.
- **5V vs 7V on VPW** — some VPW vehicles run at ~7V, some at ~5V.
  The simulator drives 5V (Pi-friendly) which is within the J1850
  tolerance for any compliant scan tool.
- **MOSFET orientation** — 2N7000 in TO-92 package: flat side has G,
  D, S left-to-right. Reversing G and D will fry the FET.
- **K-Line and J1850 share the UART overlay** — only one of the two
  protocols can be active per scenario unless you enable a second
  UART (uart3) per the note above.
