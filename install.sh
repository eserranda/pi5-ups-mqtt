#!/usr/bin/env bash
# Pasang script dan service X1202 UPS -> MQTT.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Jalankan dengan sudo: sudo ./install.sh" >&2
  exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -m 755 "$DIR/x1202_mqtt.py" /usr/local/bin/x1202_mqtt.py
install -m 644 "$DIR/x1202-ups.service" /etc/systemd/system/x1202-ups.service
systemctl daemon-reload

if [[ ! -f /etc/x1202-mqtt.env ]]; then
  install -m 600 "$DIR/x1202-mqtt.env.example" /etc/x1202-mqtt.env
  echo "File /etc/x1202-mqtt.env dibuat dengan nilai contoh."
  echo "1. Edit username dan password MQTT: sudo nano /etc/x1202-mqtt.env"
  echo "2. Jalankan: sudo systemctl enable --now x1202-ups.service"
else
  systemctl enable --now x1202-ups.service
  systemctl restart x1202-ups.service
  echo "Service dipasang dan dijalankan. Cek log: sudo journalctl -u x1202-ups.service -f"
fi
