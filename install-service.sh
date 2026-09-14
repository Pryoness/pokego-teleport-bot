#!/bin/bash
# PokeGo Teleport Bot — systemd Service Installer
# Sets up auto-start, auto-restart on crash, and log rotation.
#
# Usage:
#   ./install-service.sh           # Install & enable (starts now + on boot)
#   ./install-service.sh uninstall # Remove the service

set -e

SERVICE_NAME="pokebot"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOGROTATE_FILE="/etc/logrotate.d/${SERVICE_NAME}"

# Detect the current user
CURRENT_USER="$(whoami)"
# Allow root on VPS where root is the only user
if [ "$CURRENT_USER" = "root" ] && [ -n "$SUDO_USER" ]; then
    CURRENT_USER="$SUDO_USER"
fi

# If running as root with no sudo user, set service user to root
if [ "$CURRENT_USER" = "root" ]; then
    echo "WARNING: Running as root. Service will run as root."
fi

USER_HOME="$(getent passwd "$CURRENT_USER" | cut -d: -f6)"
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: Virtual environment not found at ${VENV_PYTHON}"
    echo "Run ./setup.sh first to create it."
    exit 1
fi

# --- Uninstall mode ---
if [ "$1" = "uninstall" ]; then
    echo "Stopping and removing ${SERVICE_NAME} service..."
    sudo systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
    sudo systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo rm -f "$LOGROTATE_FILE"
    sudo systemctl daemon-reload
    echo "Service removed. The bot files in ${SCRIPT_DIR} are untouched."
    exit 0
fi

# --- Install mode ---

echo "============================================"
echo "  PokeGo Teleport Bot — Service Installer"
echo "============================================"
echo ""
echo "  User:       ${CURRENT_USER}"
echo "  Install dir: ${SCRIPT_DIR}"
echo "  Python:      ${VENV_PYTHON}"
echo ""

# Check that main.py exists
if [ ! -f "${SCRIPT_DIR}/main.py" ]; then
    echo "ERROR: main.py not found in ${SCRIPT_DIR}"
    echo "Make sure you're running this from the bot's directory."
    exit 1
fi

# Check that config.json has the required fields
CONFIG="${SCRIPT_DIR}/config.json"
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: config.json not found. Copy config.example.json to config.json and fill in your details."
    exit 1
fi

# Write the systemd service file
echo "Writing service file to ${SERVICE_FILE}..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=PokeGo Teleport Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} ${SCRIPT_DIR}/main.py
Restart=always
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5
TimeoutStopSec=30
KillSignal=SIGINT

# Environment
Environment=PYTHONUNBUFFERED=1
Environment=HOME=${USER_HOME}

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

echo "Service file written."

# Write logrotate config for terminal.log
echo "Setting up log rotation for terminal.log..."
sudo tee "$LOGROTATE_FILE" > /dev/null <<EOF
${SCRIPT_DIR}/terminal.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
    size 50M
}
EOF

# Reload systemd, enable and start
echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Enabling service (start on boot)..."
sudo systemctl enable "${SERVICE_NAME}.service"

echo "Starting service..."
sudo systemctl start "${SERVICE_NAME}.service"

# Wait a moment and check status
sleep 3

echo ""
echo "============================================"
echo "  Service installed and started!"
echo "============================================"
echo ""
echo "  Dashboard:  http://YOUR_VPS_IP:8765"
echo ""
echo "  Commands:"
echo "    View logs:       journalctl -u ${SERVICE_NAME} -f"
echo "    View bot logs:   tail -f ${SCRIPT_DIR}/terminal.log"
echo "    Status:          systemctl status ${SERVICE_NAME}"
echo "    Restart:         sudo systemctl restart ${SERVICE_NAME}"
echo "    Stop:            sudo systemctl stop ${SERVICE_NAME}"
echo "    Disable boot:    sudo systemctl disable ${SERVICE_NAME}"
echo "    Uninstall:       ./${0##*/} uninstall"
echo ""
echo "  NOTE: Open port 8765 in your firewall or use SSH tunnel:"
echo "    ssh -L 8765:127.0.0.1:8765 ${CURRENT_USER}@YOUR_VPS_IP"
