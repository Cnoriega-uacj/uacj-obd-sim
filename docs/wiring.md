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
