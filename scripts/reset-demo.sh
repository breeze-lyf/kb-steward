#!/usr/bin/env bash
# Reset the demo to a clean KB (keeps the Switch room and agents). Example data only.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose stop kb-curator kb-answerer
docker compose run --rm --no-deps kb-curator sh -c '
  cd /data/kb && root=$(git rev-list --max-parents=0 HEAD | tail -1) &&
  git worktree prune && for w in /data/state/worktrees/*; do [ -d "$w" ] && git worktree remove --force "$w" || true; done &&
  git reset -q --hard "$root" && git for-each-ref --format="%(refname:short)" refs/heads/kb/ | xargs -r git branch -D -q &&
  python - <<PY
import json, pathlib
s = pathlib.Path("/data/state")
(s / "proposals.json").unlink(missing_ok=True)
d = s / "decisions.json"
if d.exists():
    j = json.loads(d.read_text()); j["rows"] = []; d.write_text(json.dumps(j))
PY
  cd /app/agents && python mm_say.py --wipe'
docker compose up -d kb-answerer kb-curator
