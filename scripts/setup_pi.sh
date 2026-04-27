#!/usr/bin/env bash
#
# UACJ OBD-II Simulator — Raspberry Pi setup
#
# Provisions a fresh Raspberry Pi OS Lite installation:
#   - System packages (Python, can-utils, build essentials)
#   - SPI + UART overlays for MCP2515 (CAN) and the L9637 (K-Line)
#   - SocketCAN can0 brought up at 500 kbps
#   - Python virtual environment with the simulator package
#   - systemd unit so the simulator survives reboots
#
# Run as root on the Pi:   sudo bash scripts/setup_pi.sh
# Idempotent — safe to re-run.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/uacj-obd-sim}"
SERVICE_USER="${SERVICE_USER:-pi}"
CAN_CHANNEL="${CAN_CHANNEL:-can0}"
CAN_BITRATE="${CAN_BITRATE:-500000}"
KLINE_PORT="${KLINE_PORT:-/dev/serial0}"
HTTP_PORT="${HTTP_PORT:-8765}"

log() { printf "\033[1;34m[setup]\033[0m %s\n" "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)" >&2; exit 1; }
[[ -d "${REPO_DIR}" ]] || { echo "${REPO_DIR} not found — clone the repo there first" >&2; exit 1; }

log "Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    can-utils \
    git \
    build-essential

CONFIG_FILE=/boot/firmware/config.txt
[[ -f "${CONFIG_FILE}" ]] || CONFIG_FILE=/boot/config.txt

ensure_line() {
    local line="$1"
    grep -qxF "${line}" "${CONFIG_FILE}" || echo "${line}" >> "${CONFIG_FILE}"
}

log "Configuring boot overlays for MCP2515 + UART"
ensure_line "dtparam=spi=on"
ensure_line "dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25"
ensure_line "dtoverlay=spi-bcm2835-overlay"
ensure_line "enable_uart=1"

# /boot/firmware/cmdline.txt — remove serial console so the UART is free
CMDLINE=/boot/firmware/cmdline.txt
[[ -f "${CMDLINE}" ]] || CMDLINE=/boot/cmdline.txt
sed -i 's/console=serial0,[0-9]\+ //g' "${CMDLINE}" || true

log "Bringing up ${CAN_CHANNEL} (will reapply on boot via networkd)"
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/80-can.network <<EOF
[Match]
Name=${CAN_CHANNEL}

[CAN]
BitRate=${CAN_BITRATE}
EOF
systemctl enable systemd-networkd >/dev/null 2>&1 || true

log "Creating Python virtualenv"
sudo -u "${SERVICE_USER}" python3 -m venv "${REPO_DIR}/.venv"
sudo -u "${SERVICE_USER}" "${REPO_DIR}/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "${SERVICE_USER}" "${REPO_DIR}/.venv/bin/pip" install --quiet -e "${REPO_DIR}[dev]"
sudo -u "${SERVICE_USER}" "${REPO_DIR}/.venv/bin/pip" install --quiet python-can pyserial

log "Installing systemd service"
cat > /etc/systemd/system/uacj-obd-sim.service <<EOF
[Unit]
Description=UACJ OBD-II Training Simulator
After=network-online.target systemd-networkd.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/.venv/bin/uacj-obd simulator \\
    --channel ${CAN_CHANNEL} \\
    --kline-port ${KLINE_PORT} \\
    --http-host 0.0.0.0 \\
    --http-port ${HTTP_PORT}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable uacj-obd-sim.service

log "Done."
log "Reboot to apply boot overlays:   sudo reboot"
log "After reboot, check status with:  systemctl status uacj-obd-sim"
log "And confirm CAN is up:            ip -details link show ${CAN_CHANNEL}"
