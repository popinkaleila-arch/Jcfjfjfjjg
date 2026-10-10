#!/usr/bin/env bash
set -euo pipefail

APP_SRC="${1:-/opt/channel-reposter/app}"
APP_ROOT="/opt/channel-reposter"
DATA_DIR="${APP_ROOT}/data"
SERVICE_SRC="${APP_SRC}/deploy/channel-reposter.service"

if [[ ! -f "${APP_SRC}/main.py" ]]; then
  echo "нет ${APP_SRC}/main.py" >&2
  exit 1
fi

id channel-reposter >/dev/null 2>&1 || useradd --system --home "${APP_ROOT}" --shell /usr/sbin/nologin channel-reposter
mkdir -p "${DATA_DIR}"
chown -R channel-reposter:channel-reposter "${APP_ROOT}"
chmod 750 "${APP_ROOT}"
chmod 700 "${DATA_DIR}"
if [[ -f "${APP_SRC}/.env" ]]; then
  chmod 600 "${APP_SRC}/.env"
fi

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip >/dev/null

sudo -u channel-reposter python3 -m venv "${APP_SRC}/.venv"
sudo -u channel-reposter "${APP_SRC}/.venv/bin/pip" install -U pip wheel
sudo -u channel-reposter "${APP_SRC}/.venv/bin/pip" install -r "${APP_SRC}/requirements.txt"

install -m 644 "${SERVICE_SRC}" /etc/systemd/system/channel-reposter.service
systemctl daemon-reload
systemctl enable --now channel-reposter.service
systemctl --no-pager --full status channel-reposter.service | head -20
