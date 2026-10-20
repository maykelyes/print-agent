#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu 24.04 droplet: Docker, firewall, swap, repo checkout.
# Usage (as root): bash setup_server.sh
set -euo pipefail

REPO_URL="https://github.com/maykelyes/print-agent.git"
BRANCH="open-webui"
APP_DIR="/opt/print-agent"
DATA_DIR="/srv/openwebui/data"

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git sqlite3 ufw
systemctl enable --now docker

# Firewall: SSH + web only. Open WebUI itself is not published; Caddy proxies to it.
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# 2GB swap as a safety net for Whisper transcription peaks
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q && git -C "$APP_DIR" checkout -q "$BRANCH" && git -C "$APP_DIR" pull -q
else
  git clone -q -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

mkdir -p "$DATA_DIR"
echo "Server ready. Next: create $APP_DIR/openwebui/deploy/.env and run 'docker compose up -d --build' there."
