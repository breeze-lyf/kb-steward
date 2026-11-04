#!/usr/bin/env bash
# Reset the demo: re-seed the KB from kb-seed/, clear proposals, decision-log rows, knowledge gaps and the
# channel (keeps the Switch room and agents). Example data only.
#   scripts/reset-demo.sh            # language from agents.env KB_LANG (default: both)
#   scripts/reset-demo.sh zh         # en | zh | both
set -euo pipefail
cd "$(dirname "$0")/.."
LANG_ARG=()
[ -n "${1:-}" ] && LANG_ARG=(-e "KB_LANG=$1")
docker compose stop kb-curator kb-answerer
docker compose run --rm --no-deps "${LANG_ARG[@]}" kb-curator sh -c '
  set -e
  if [ -d /data/kb/.git ]; then
    cd /data/kb && git worktree prune
    for w in /data/state/worktrees/*; do [ -d "$w" ] && git worktree remove --force "$w" || true; done
  fi
  rm -rf /data/state/worktrees
  if [ "${KB_SOURCE:-local}" != github ]; then
    find /data/kb -mindepth 1 -delete 2>/dev/null || true
  fi
  cd /app/agents && python - <<PY
import json, pathlib
import bootstrap
bootstrap.seed_kb()
from docsource import make_source
from kb_core import git
source = make_source()
if source.name == "github":
    source.authgit("fetch", "origin", source.base)
    git(source.root, "reset", "--hard", "origin/" + source.base)
    git(source.root, "clean", "-fd")
s = pathlib.Path("/data/state")
for p in s.glob("review_queue.jsonl*"):
    p.unlink(missing_ok=True)
for name in ("proposals.json", "answers.json", "answers.json.lock", "reviews.json", "reviews.json.lock"):
    (s / name).unlink(missing_ok=True)
for name in ("decisions.json", "gaps.json"):
    d = s / name
    if d.exists():
        j = json.loads(d.read_text()); j["rows"] = []; d.write_text(json.dumps(j))
PY
  python mm_say.py --wipe'
docker compose up -d kb-answerer kb-curator
