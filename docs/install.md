# Installation runbook (TeamViewer day)

End-to-end paste-and-go install for the day the hardware arrives.
Each command is meant to be run verbatim. Estimated time: 30 minutes
on a clean Pi 4 + Windows 10/11 laptop.

> If anything below differs from `docs/wiring.md`, the wiring doc wins —
> physical assembly first, then this runbook.

---

## Pre-flight check (do once before TeamViewer call)

On the laptop, confirm Python and git are present:

```powershell
python --version       # need 3.11 or newer
git --version
```

If Python is missing or older than 3.11, install from <https://python.org>
(make sure "Add Python to PATH" is checked).

Confirm the OBDLink SX is detected once plugged in:

```powershell
mode COM3              # adjust COMn to whatever Device Manager shows
```

Confirm the parts are physically assembled per [docs/wiring.md](wiring.md)
and the OBD-II pigtail is wired correctly. Take a photo and send it
before the TeamViewer call so issues can be flagged in advance.

---

## 1. Pi: flash + first boot (5 min)

On the laptop:

```powershell
# Use Raspberry Pi Imager. Choose:
#   Device:    Raspberry Pi 4
#   OS:        Raspberry Pi OS Lite (64-bit) — Bookworm or newer
#   Storage:   the 32GB SD card from the Pi kit
# Click the gear icon BEFORE writing:
#   Set hostname:   uacj-sim
#   Enable SSH (use password auth)
#   Username:       pi
#   Password:       <pick a strong one, share via TeamViewer chat only>
#   Wi-Fi:          your classroom SSID + password
#   Locale:         America/Mexico_City, US keyboard
```

Boot the Pi with the SD card. Wait ~60 seconds. From the laptop:

```powershell
ssh pi@uacj-sim.local
# accept the host key
```

If `uacj-sim.local` doesn't resolve, find the Pi's IP from your router
admin page or run `arp -a | findstr "b8-27"` (Pi's MAC prefix).

---

## 2. Pi: clone repo + run setup script (10 min)

On the Pi (over SSH):

```bash
sudo apt-get update
sudo apt-get install -y git
sudo mkdir -p /opt
sudo git clone https://github.com/<azamat-handle>/uacj-obd-sim.git /opt/uacj-obd-sim
sudo chown -R pi:pi /opt/uacj-obd-sim
cd /opt/uacj-obd-sim
sudo bash scripts/setup_pi.sh
sudo reboot
```

The setup script:
- Installs Python 3, can-utils, build-essential
- Adds the MCP2515 + UART overlays to `/boot/firmware/config.txt`
- Configures `can0` at 500 kbps via `systemd-networkd`
- Creates `.venv` and installs the package
- Registers `uacj-obd-sim.service` to auto-start on boot

Reboot is required for the SPI overlay and UART changes to take effect.

After the Pi comes back:

```bash
ssh pi@uacj-sim.local

# Verify CAN interface is up
ip -details link show can0
# Expect: state UP, can <ECHO,LOOPBACK,...>, bitrate 500000

# Verify K-Line UART exists
ls -l /dev/serial0
# Expect: symlink to ttyAMA0 or ttyS0

# Verify the simulator service is running
systemctl status uacj-obd-sim
# Expect: active (running)

# Hit the health endpoint
curl http://localhost:8765/api/sim/health
# Expect: {"ok":true,"vin":null,"stored_dtcs":[]}
```

If `can0` is missing, double-check MCP2515 wiring (CS/INT/MOSI/MISO/SCK)
and that the 8 MHz oscillator on your board matches the overlay value.
If the L9637D is wired but the simulator can't open `/dev/serial0`,
make sure `enable_uart=1` is in `/boot/firmware/config.txt` and the
serial console was removed from `cmdline.txt` (the script does both).

---

## 3. Laptop: install acquisition app (10 min)

On the laptop:

```powershell
git clone https://github.com/<azamat-handle>/uacj-obd-sim.git "C:\uacj"
cd C:\uacj
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
```

Plug in the OBDLink SX, note the COM port (Device Manager → Ports).
Smoke test against the mock adapter first (no car needed):

```powershell
uacj-obd --data data capture --adapter mock --duration 5
uacj-obd --data data sessions
```

Now connect to a real vehicle (key on, engine off is fine):

```powershell
uacj-obd --data data capture --adapter elm327 --port COM3 --duration 30
```

If protocol auto-detect fails, force one:

```powershell
# COM3 = your OBDLink SX, --baud 38400 if the SX is configured for it
uacj-obd --data data capture --adapter elm327 --port COM3 --baud 38400 --duration 30
```

Start the dashboard:

```powershell
uacj-obd --data data serve --host 0.0.0.0 --port 8000
# open http://localhost:8000 in a browser
```

---

## 4. Connect laptop ↔ Pi (3 min)

In the dashboard, go to **Settings** (or set `UACJ_SIM_BASE_URL` in
the environment) and point the simulator URL at the Pi:

```
http://uacj-sim.local:8765
```

Click "Test connection". Expect a green check + the Pi's reported VIN
(empty until a scenario is loaded).

---

## 5. Push a scenario, smoke-test the scan tool (5 min)

In the dashboard:

1. **Sessions** → pick the real-vehicle capture from step 3
2. **Scenarios → New from preset** → "P0420 catalyst" (or any preset)
3. Click **Push to simulator**

Plug a student scan tool (Autel AL319 / Innova 3100 / generic ELM327
phone app) into the OBD-II port on the assembled board. Power the
board (12 V via OBD pin 16 + GND on pin 4/5).

The scan tool should:
- Connect on whichever protocol (CAN or K-Line) it negotiates
- Read the VIN from the scenario
- Report DTCs P0420 (and any others in the preset)
- Show live values matching the scenario

Watch the dashboard's **Classroom view** — every request the scan tool
sends shows up in the live request log.

---

## 6. Handoff checklist

- [ ] Repo URL transferred to Cristopher's GitHub account
- [ ] Pi auto-starts simulator after reboot (verify with `systemctl status`)
- [ ] Laptop dashboard reaches the Pi (curl + browser both work)
- [ ] At least one preset scenario reads correctly on a student scan tool
- [ ] `docs/wiring.md`, `docs/instructor.md`, `docs/compatibility.md` reviewed with Cristopher
- [ ] Test capture against a real UACJ vehicle saved to `data/sessions/`
- [ ] First-30-day adjustment window communicated

---

## Troubleshooting quick reference

| Symptom | Fix |
|---|---|
| `can0` missing after reboot | Re-check `dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25` matches the board's actual oscillator (8 MHz vs 16 MHz) and INT pin. |
| `systemctl status uacj-obd-sim` shows "Active: failed" with import error | `cd /opt/uacj-obd-sim && .venv/bin/pip install -e ".[dev]"` then `sudo systemctl restart uacj-obd-sim`. |
| `curl http://uacj-sim.local:8765` times out from laptop | Check Pi's IP (`hostname -I`), use the IP directly. mDNS sometimes flakes on classroom networks. |
| OBDLink SX connects but `protocol_id()` returns `?` | Vehicle's ECU isn't responding — turn the key fully on (engine doesn't need to start). |
| Scan tool says "no communication" | Confirm 12 V on OBD pin 16 with a multimeter; confirm GND continuity to pins 4 & 5. |
| K-Line responses arrive but scan tool times out | Slow scan tools may need 5-baud init — already handled in `simulator/kline.py::slow_init_step`, but verify the L9637D's RX/TX aren't swapped. |
