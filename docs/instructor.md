# Instructor Quick-Start

A 30-minute guide for getting from "freshly set up" to "students plugging scan tools into the simulator and diagnosing your scenarios."

---

## Step 1 — Install the laptop side

```bash
git clone <repo-url> uacj-obd-sim
cd uacj-obd-sim
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uacj-obd serve            # dashboard at http://127.0.0.1:8000
```

If you have an OBDLink SX or any ELM327 plugged into your laptop's USB, you can capture vehicles immediately.

## Step 2 — Set up the simulator board (one time)

The simulator is a Raspberry Pi running this same package, but in `simulator` mode. Once `scripts/setup_pi.sh` has been run on the Pi (see [docs/wiring.md](wiring.md) for the assembly), the Pi auto-starts the simulator service on boot.

Confirm it's running:

```bash
# from the laptop, on the same Wi-Fi network as the Pi:
curl http://uacj-sim.local:8765/api/sim/health
# → {"ok": true, "vin": null, "stored_dtcs": []}
```

Idle (no scenario loaded) → the Pi will respond to scan tools, but with empty data. That's expected.

## Step 3 — Capture a real vehicle

1. Plug your OBDLink SX into the laptop USB.
2. Plug it into a vehicle's OBD-II port. Turn the ignition to ON (engine running for live data).
3. In the dashboard, click **Start**.
4. The system auto-detects the protocol, reads VIN, DTCs, monitors, freeze frame, and starts streaming live data.
5. Click **Stop** after 30–60 seconds.
6. The session is now in the **Past Sessions** list — labeled by VIN, make, model, year.

That's a full vehicle snapshot saved on disk. You can capture as many vehicles as you have available — each gets its own folder.

## Step 4 — Build a teaching scenario

Open the **Scenarios** page in the dashboard.

1. Pick a saved session in the dropdown.
2. Give your scenario a label like `"P0420 Catalyst — Civic 2015"`.
3. Click **Create scenario**.
4. In the editor:
   - Add or remove DTCs (any code: P0420, P0301, U0100, etc.). Set status: stored / pending / permanent.
   - Toggle monitor support and ready bits.
   - Set live overrides — e.g. force RPM (`010C`) to 2200, or coolant temp (`0105`) to 105°C to simulate overheating.
5. Click **Save**.

You now have a scenario. Repeat to build a library of training cases.

## Step 5 — Push to the simulator board

1. Click **Push to board** on the scenario.
2. Enter the simulator's URL (default `http://uacj-sim.local:8765`).
3. The Pi swaps to the new scenario.
4. Hand a student a scan tool.
5. They plug into the simulator's OBD-II port and read codes / live data exactly as if it were the original vehicle, with the modifications you applied.

## Step 6 — Verify before the class

A 60-second pre-flight any time you push a new scenario:

```bash
# laptop:
curl http://uacj-sim.local:8765/api/sim/state | jq
# Confirm VIN, stored_dtcs, and live_pids match what you set
```

Then plug your own scan tool into the simulator and confirm the codes show up before students do.

## Common scenarios to start with

| Scenario | DTCs | Live overrides | Teaches |
|---|---|---|---|
| Catalyst inefficiency | P0420 stored | normal RPM/temps | Reading bank-1 cat code, freeze-frame inspection |
| Lean condition | P0171 stored | LTFT (`0107`) = +18% | Trim diagnosis, MAF/vacuum-leak path |
| Cylinder 1 misfire | P0301 stored, P0300 pending | RPM rough | Multi-code interpretation, ignition vs fuel |
| EVAP leak | P0455 stored | normal | Reading manufacturer-specific monitor states |
| Drive-cycle incomplete | (none) | monitors B=0xFF (all incomplete) | Why cars fail emissions even with no codes |

## Troubleshooting

- **Scan tool reads "no communication"** — the wrong protocol is being attempted. The simulator answers CAN (ISO 15765) and K-Line (KWP2000); it does not answer J1850 VPW/PWM. Make sure the student's scan tool is set to "auto" or specifically to one of the supported protocols.
- **Simulator HTTP returns 502** — the Pi can't reach the laptop, or the URL is wrong. `ping uacj-sim.local` from the laptop. Fall back to using the Pi's IP directly: `http://192.168.x.x:8765`.
- **Scenario push 200s but scan tool still shows old data** — the scan tool is caching. Have the student turn the key off, count to 5, key on, and re-read.
- **DTC clears on its own** — the student's scan tool sent mode 0x04 (clear DTCs). The simulator honors it. Push the scenario again to restore.

## Beyond v1

- J1850 VPW/PWM (pre-CAN GM/Ford 2004–2007) — currently scoped out; planned for a v2 hardware revision.
- Multiple ECU emulation (ABS, BCM, transmission) — currently single-ECU. The architecture supports multiple ECU instances; UI work needed.
- Custom mode 0x22 manufacturer PID responses — instructors can drop YAML files into the PID registry; encoding for those PIDs in the simulator is on the day-5 list.
