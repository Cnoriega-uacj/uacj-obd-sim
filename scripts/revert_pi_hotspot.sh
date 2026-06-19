#!/usr/bin/env bash
#
# UACJ OBD-II Simulator — Revert Pi-as-WiFi-Access-Point setup (v0.5.1)
#
# Restores the pre-AP-mode WiFi configuration the Pi had before
# `setup_pi_hotspot.sh` ran. After this script the Pi returns to
# client-mode WiFi (joining whatever network wpa_supplicant.conf
# specifies).
#
# Run as root on the Pi:   sudo bash scripts/revert_pi_hotspot.sh
# Idempotent — safe to re-run even if AP mode was never enabled.

set -euo pipefail

BACKUP_DIR="/etc/uacj-ap-backup"
DHCPCD="/etc/dhcpcd.conf"

log() { printf "\033[1;34m[revert]\033[0m %s\n" "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)" >&2; exit 1; }

# --- stop and disable AP services ----------------------------------------

log "Stopping hostapd and dnsmasq"
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true

# --- restore original config files --------------------------------------

if [[ -d "${BACKUP_DIR}" ]]; then
    for f in dhcpcd.conf dnsmasq.conf hostapd.conf default-hostapd; do
        case "$f" in
            dhcpcd.conf)        target="/etc/dhcpcd.conf" ;;
            dnsmasq.conf)       target="/etc/dnsmasq.conf" ;;
            hostapd.conf)       target="/etc/hostapd/hostapd.conf" ;;
            default-hostapd)    target="/etc/default/hostapd" ;;
        esac
        backup="${BACKUP_DIR}/${f%.*}.orig"
        if [[ -f "$backup" ]]; then
            log "Restoring ${target}"
            cp "$backup" "$target"
        fi
    done
fi

# Belt-and-braces — strip any UACJ-AP block even if the backup was missing.
if [[ -f "${DHCPCD}" ]]; then
    sed -i '/^# >>> UACJ-AP block/,/^# <<< UACJ-AP block/d' "${DHCPCD}"
fi

log "Restarting dhcpcd"
systemctl restart dhcpcd

log "Done. The Pi is back to client-mode WiFi."
log "Verify by running:  iwgetid -r   (should show your usual SSID)"
