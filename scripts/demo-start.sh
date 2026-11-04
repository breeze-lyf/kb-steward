#!/usr/bin/env bash
# One command to the "ready to record" state: close open review cards, (GitHub mode) close demo PRs and
# restore the demo repo, re-seed the KB and wipe the room, then wait until both agents are back online.
#   scripts/demo-start.sh [en|zh|both]
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T kb-curator python demo_prep.py cards || true
docker compose exec -T kb-curator python demo_prep.py github
scripts/reset-demo.sh "$@"
for i in $(seq 1 30); do
  if docker compose logs --since 60s kb-curator 2>&1 | grep -q "claimed room" &&
     docker compose logs --since 60s kb-answerer 2>&1 | grep -q "claimed room"; then
    echo "ready: room clean, agents online, KB at $(docker compose exec -T kb-curator git -C /data/kb rev-parse --short HEAD)"; exit 0
  fi
  sleep 2
done
echo "warning: agents did not report online within 60s; check docker compose logs" >&2; exit 1
