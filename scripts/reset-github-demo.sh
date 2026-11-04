#!/usr/bin/env bash
# Operator-only reset of a dedicated demo repository (needs `gh`). History is never rewritten:
# open kb/* PRs are closed, their branches deleted, and main gets a normal commit restoring the demo-start tree.
set -euo pipefail
repo=${1:?Usage: reset-github-demo.sh owner/name-demo-docs}
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+-demo-docs$ ]] || { echo 'Refusing: repository must end with -demo-docs'; exit 1; }
gh api "repos/$repo/git/ref/tags/demo-start" >/dev/null 2>&1 || {
  echo 'Required tag demo-start is missing. Tag the intended seed commit and push that tag first.'; exit 1;
}
sha=$(gh api "repos/$repo/commits/demo-start" --jq .sha)
gh pr list --repo "$repo" --state open --limit 1000 --json number,headRefName --jq '.[] | select(.headRefName | startswith("kb/")) | [.number,.headRefName] | @tsv' |
while IFS=$'\t' read -r number branch; do
  gh pr close "$number" --repo "$repo"
  gh api --method DELETE "repos/$repo/git/refs/heads/$branch"
done
tree=$(gh api "repos/$repo/git/commits/$sha" --jq .tree.sha)
head=$(gh api "repos/$repo/git/ref/heads/main" --jq .object.sha)
if [ "$(gh api "repos/$repo/git/commits/$head" --jq .tree.sha)" = "$tree" ]; then echo "$repo main already matches demo-start."; exit 0; fi
new=$(gh api --method POST "repos/$repo/git/commits" -f message="Reset demo content to demo-start" -f tree="$tree" -f "parents[]=$head" --jq .sha)
gh api --method PATCH "repos/$repo/git/refs/heads/main" -f sha="$new" >/dev/null
echo "Restored $repo main to the demo-start content as $new (fast-forward, no history rewrite)."
