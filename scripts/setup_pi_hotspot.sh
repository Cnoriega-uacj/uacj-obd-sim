#!/usr/bin/env bash
#
# UACJ OBD-II Simulator — Pi-as-WiFi-Access-Point setup (v0.5.1)
#
# Reconfigures the Raspberry Pi to broadcast its own WiFi network so the
# laptop dashboard can talk to the simulator without any external router
# or internet. After this script runs:
#
#   - SSID:      UACJ-SIM (configurable below)
#   - Password:  uacj1234 (configurable below — minimum 8 characters for WPA2)
#   - Pi IP:     192.168.50.1
#   - DHCP:      192.168.50.10 — 192.168.50.50
#   - Dashboard: http://192.168.50.1:8765 from any device on the Pi's WiFi
#
# Trade-off: enabling AP mode disconnects the Pi from any pre-existing
# client WiFi connection. If you need both, plug the Pi into Ethernet
# for management. To revert, run `scripts/revert_pi_hotspot.sh`.
#
# Run as root on the Pi:   sudo bash scripts/setup_pi_hotspot.sh
# Idempotent — safe to re-run.

set -euo pipefail

# --- configuration ---------------------------------------------------------

SSID="${UACJ_AP_SSID:-UACJ-SIM}"
PASSPHRASE="${UACJ_AP_PASS:-uacj1234}"
COUNTRY="${UACJ_AP_COUNTRY:-MX}"
CHANNEL="${UACJ_AP_CHANNEL:-6}"
PI_IP="${UACJ_AP_IP:-192.168.50.1}"
DHCP_START="${UACJ_AP_DHCP_START:-192.168.50.10}"
DHCP_END="${UACJ_AP_DHCP_END:-192.168.50.50}"
WLAN_IFACE="${UACJ_AP_IFACE:-wlan0}"

log() { printf "\033[1;34m[hotspot]\033[0m %s\n" "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)" >&2; exit 1; }

if [[ ${#PASSPHRASE} -lt 8 ]]; then
    echo "WPA2 passphrase must be at least 8 characters" >&2
    exit 1
fi

# --- packages --------------------------------------------------------------

log "Installing hostapd and dnsmasq"
apt-get update -qq
apt-get install -y -qq hostapd dnsmasq

# Stop services while we configure them; they will be started at the end.
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# --- backup previous config so revert is possible --------------------------

BACKUP_DIR="/etc/uacj-ap-backup"
mkdir -p "${BACKUP_DIR}"
for f in /etc/dhcpcd.conf /etc/dnsmasq.conf /etc/hostapd/hostapd.conf \
         /etc/default/hostapd; do
    if [[ -f "$f" && ! -f "${BACKUP_DIR}/$(basename "$f").orig" ]]; then
        cp "$f" "${BACKUP_DIR}/$(basename "$f").orig"
    fi
done

# --- static IP on wlan0 via dhcpcd -----------------------------------------

log "Configuring static IP ${PI_IP} on ${WLAN_IFACE}"
DHCPCD="/etc/dhcpcd.conf"
# Remove any previous block (between our markers) so re-running is idempotent.
sed -i '/^# >>> UACJ-AP block/,/^# <<< UACJ-AP block/d' "${DHCPCD}"
cat >> "${DHCPCD}" <<EOF
# >>> UACJ-AP block
interface ${WLAN_IFACE}
    static ip_address=${PI_IP}/24
    nohook wpa_supplicant
# <<< UACJ-AP block
EOF

# --- dnsmasq: DHCP for AP clients -----------------------------------------

log "Configuring dnsmasq DHCP pool ${DHCP_START}-${DHCP_END}"
cat > /etc/dnsmasq.conf <<EOF
# UACJ OBD-II Simulator — DHCP for laptop / scan-tool clients
interface=${WLAN_IFACE}
bind-interfaces
domain-needed
bogus-priv
dhcp-range=${DHCP_START},${DHCP_END},255.255.255.0,12h
# Pi itself is the gateway / DNS — even with no internet, this keeps
# clients from spamming retries against unreachable upstream DNS.
dhcp-option=3,${PI_IP}
dhcp-option=6,${PI_IP}
EOF

# --- hostapd: AP for wlan0 -------------------------------------------------

log "Configuring hostapd: SSID=${SSID} channel=${CHANNEL}"
mkdir -p /etc/hostapd
cat > /etc/hostapd/hostapd.conf <<EOF
# UACJ OBD-II Simulator — WiFi access point
interface=${WLAN_IFACE}
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=${CHANNEL}
country_code=${COUNTRY}
ieee80211d=1
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
wpa_passphrase=${PASSPHRASE}
EOF

echo "DAEMON_CONF=\"/etc/hostapd/hostapd.conf\"" > /etc/default/hostapd

# --- enable + start services ----------------------------------------------

log "Unmasking and enabling hostapd"
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

log "Restarting networking"
systemctl restart dhcpcd
sleep 1
systemctl start hostapd
systemctl start dnsmasq

# --- summary --------------------------------------------------------------

log "Done. The Pi is now broadcasting WiFi."
log ""
log "  SSID:     ${SSID}"
log "  Password: ${PASSPHRASE}"
log "  Pi IP:    ${PI_IP}"
log "  Dashboard endpoint from any client on this WiFi:"
log "      http://${PI_IP}:8765"
log ""
log "If you are currently SSH'd via the Pi's old WiFi, the connection has"
log "dropped. Reconnect by joining the ${SSID} network from your laptop."
log "To revert to client-mode WiFi:  sudo bash scripts/revert_pi_hotspot.sh"
