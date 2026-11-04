#!/usr/bin/env bash
# Bring up a Switch standalone stack (core + gateway + bundled Mattermost) next to KB Steward.
# usage: scripts/up-switch.sh [dir]   (default ./switch-stack)
# Uses Switch's own published compose file and secret generator; nothing is printed.
set -euo pipefail
DIR="${1:-switch-stack}"; VER="${SWITCH_VERSION:-0.31.0}"
mkdir -p "$DIR"; cd "$DIR"
RAW=https://raw.githubusercontent.com/sandbox-quantum/switch/main
[ -f docker-compose.yml ] || curl -fsSL "$RAW/deploy/local/standalone-docker-compose.yml" -o docker-compose.yml
if [ ! -f .env ]; then
  curl -fsSL "$RAW/.env.example" -o .env
  curl -fsSL "$RAW/deploy/local/generate-env-secrets.sh" -o gen.sh && bash gen.sh .env kb && rm gen.sh
  sed -i "s|^SWITCH_VERSION=.*|SWITCH_VERSION=$VER|; s|^COMPOSE_PROJECT_NAME=.*|COMPOSE_PROJECT_NAME=kbswitch|; s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=collab,gateway|" .env
  chmod 600 .env
fi
docker compose up -d
echo "Switch is starting. Mattermost: http://127.0.0.1:\${MATTERMOST_HOST_PORT:-8065}  Gateway: http://127.0.0.1:\${GATEWAY_HOST_PORT:-3000}"
