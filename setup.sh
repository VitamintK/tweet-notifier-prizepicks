#!/bin/bash
# Run this on your VPS to install and start the monitor.
# Assumes Ubuntu/Debian and that you're logged in as root.

set -e

echo "==> Installing Python venv"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-pip

python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> Installing systemd service"
sudo cp prizepicks-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable prizepicks-monitor
sudo systemctl start prizepicks-monitor

echo ""
echo "==> Done! Check status with:"
echo "    sudo systemctl status prizepicks-monitor"
echo "    journalctl -u prizepicks-monitor -f"
