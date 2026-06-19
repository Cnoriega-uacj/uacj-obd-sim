# Pi as standalone WiFi access point (v0.5.1)

When you want the simulator kit to run completely self-contained — no
classroom WiFi, no router, no internet — the Pi can broadcast its own
WiFi network. The laptop connects to that network and pushes scenarios
exactly like it does over school WiFi.

This makes the kit portable: take it anywhere, plug in 12 V power,
boot, you're done.

---

## Default settings

| Setting | Default | Override env var |
|---|---|---|
| SSID (network name) | `UACJ-SIM` | `UACJ_AP_SSID` |
| Password (WPA2) | `uacj1234` | `UACJ_AP_PASS` |
| Country code | `MX` | `UACJ_AP_COUNTRY` |
| WiFi channel (2.4 GHz) | `6` | `UACJ_AP_CHANNEL` |
| Pi IP | `192.168.50.1` | `UACJ_AP_IP` |
| DHCP pool | `192.168.50.10` – `192.168.50.50` | `UACJ_AP_DHCP_START` / `UACJ_AP_DHCP_END` |
| WiFi interface | `wlan0` | `UACJ_AP_IFACE` |

The password MUST be 8–63 characters (WPA2 requirement). Change it for
production deployments — `uacj1234` is fine for a single shared
classroom kit but won't survive any real threat model.

---

## Setup (one-time, on the Pi)

> **Heads-up:** enabling AP mode disconnects the Pi from any
> pre-existing client WiFi connection. If you're currently SSH'd in
> over WiFi, the connection drops when this script finishes. You'll
> need to either:
>
> - Connect a **monitor + keyboard** to the Pi, or
> - Plug in an **Ethernet cable** for management, or
> - Just be ready to **rejoin the new `UACJ-SIM` WiFi from your laptop**
>   and reconnect over `ssh pi@192.168.50.1`.

SSH into the Pi (over whichever path you have), then:

```bash
cd /opt/uacj-obd-sim
sudo bash scripts/setup_pi_hotspot.sh
```

The script installs `hostapd` and `dnsmasq`, writes their configs from
the defaults above, sets the Pi's static IP on `wlan0`, and starts
both services. Total time: ~30 seconds plus apt-get download.

Custom settings via env vars (one-line override):

```bash
sudo UACJ_AP_SSID="MyClassroom" UACJ_AP_PASS="changeme123" \
    bash scripts/setup_pi_hotspot.sh
```

---

## Connecting your laptop after AP setup

1. Open WiFi on your laptop → join the `UACJ-SIM` network
   (password `uacj1234` unless you changed it).
2. The Pi assigns your laptop an IP in `192.168.50.10`–`192.168.50.50`
   via DHCP — no manual config needed.
3. Open the dashboard as before:
   ```
   http://localhost:8000
   ```
4. In the dashboard's scenario push popup, change the simulator URL to:
   ```
   http://192.168.50.1:8765
   ```
   (The Pi's static IP on its own AP. `uacj-sim.local` mDNS works too
   when both sides are on this WiFi.)
5. Push the scenario, scan with the Innova. Same flow as classroom
   WiFi.

---

## Reverting to client-mode WiFi

If you need the Pi to rejoin your home / school WiFi again (e.g. for
firmware updates, `git pull`):

```bash
sudo bash scripts/revert_pi_hotspot.sh
```

This:
- Stops and disables `hostapd` + `dnsmasq`
- Restores the pre-AP configs from `/etc/uacj-ap-backup/`
- Restarts `dhcpcd` so client-mode WiFi (via `wpa_supplicant`) takes
  over again

Verify with `iwgetid -r` — should show your usual SSID, not
`UACJ-SIM`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `UACJ-SIM` doesn't appear in laptop WiFi list | hostapd failed to start. SSH in via Ethernet and check `systemctl status hostapd`. Usually a country-code or channel issue — most countries require `country_code` to be set before any 2.4 GHz traffic is allowed. |
| Laptop joins but no IP | dnsmasq isn't running. `sudo systemctl restart dnsmasq`. Check `journalctl -u dnsmasq` for binding errors (usually port 53 conflict with `systemd-resolved` on Ubuntu-like distros). |
| Laptop has IP but can't reach `http://192.168.50.1:8765` | Simulator service not bound to `0.0.0.0`. Check `systemctl status uacj-obd-sim` and confirm the ExecStart includes `--http-host 0.0.0.0`. |
| Pi still tries to connect to old WiFi | `wpa_supplicant` is fighting hostapd. The script writes `nohook wpa_supplicant` to dhcpcd.conf — verify with `grep wpa_supplicant /etc/dhcpcd.conf`. |
| Want to use a different IP range to avoid clashing with another network | Override `UACJ_AP_IP` / `UACJ_AP_DHCP_START` / `UACJ_AP_DHCP_END`, then re-run the setup script. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Laptop (Windows)                                               │
│                                                                 │
│   wpa_supplicant   ──── joins WiFi: SSID UACJ-SIM ──┐           │
│                                                     ▼           │
│   Browser           http://localhost:8000           │           │
│       │                                             │           │
│       │  push scenario                              │           │
│       ▼                                             │           │
│   Dashboard         http://192.168.50.1:8765 ───────┤           │
│                                                     │           │
└─────────────────────────────────────────────────────┼───────────┘
                                                     │
                                                     │ 802.11 g/n
                                                     │ 2.4 GHz channel 6
                                                     │ WPA2
                                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Raspberry Pi 4 — running setup_pi_hotspot.sh                   │
│                                                                 │
│   hostapd          ── broadcasts SSID UACJ-SIM (WPA2)           │
│   dnsmasq          ── DHCP pool 192.168.50.10-50                │
│   dhcpcd           ── static 192.168.50.1 on wlan0              │
│                                                                 │
│   uacj-obd-sim.service  ── listens on 0.0.0.0:8765              │
│                            (the simulator API)                  │
│                                                                 │
│   CAN  ──► MCP2515 ──► OBD-II connector ──► student scan tool   │
│   K-Line  ──► L9637D ──► OBD-II pin 7                           │
└─────────────────────────────────────────────────────────────────┘
```

Internet is **not** required at any point in this diagram.
