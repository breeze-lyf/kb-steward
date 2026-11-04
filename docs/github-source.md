# GitHub document source

Set `KB_SOURCE=github`, `GITHUB_REPO=owner/name`, `GITHUB_BASE=main` (default), and `GITHUB_POLL_S=20` (default). KB_REPO must be a clone of that repository; bootstrap clones it when absent and sets the local kb-curator Git identity. The checked-out local integration branch is named main, including when GITHUB_BASE differs.

Provide `GITHUB_TOKEN_FILE=/run/secrets/github_token` (default) as a read-only secret mount in all agent containers that access GitHub. Set the host file mode to **0600**. For the supplied Compose stack, export `GITHUB_TOKEN_HOST_FILE=/absolute/path/to/secret` in the operator shell (or Compose .env); the shared service configuration mounts it read-only at the default container path. Alternatively use GITHUB_TOKEN. A fine-grained token should select only this repository, with Contents read/write, Pull requests read/write and Metadata read. Missing repo or token logs a warning and falls back to local mode. No token is stored in Git configuration or remote URLs; authentication uses a per-command HTTP header.

```text
correction → proposal worktree → owner approval in kb-ops
  → squash commit + push kb/KB-n → open GitHub PR
  → human merges on GitHub → polling fetch + fast-forward main
  → index refresh → corrections to past answers + close gaps
```

There is no webhook. Polling checks tracked open PRs and fetches the configured base. Direct upstream doc edits rebuild the index and produce a short room note, without retro corrections. The answerer checks HEAD on every question. Still-valid and ownership reviews commit directly and push, retrying once with fetch/rebase if needed.

Limits: one curator writer; local main must be clean and fast-forwardable. Merge/squash conflicts and branch protection errors require operator resolution. API/network failures retry on the next poll. GitHub approvals and merge permissions remain repository policy. PR bodies include the English Impact block, audit trailers and planned correction counts; corrections use the current ledger after merge.

`scripts/reset-demo.sh` only resets the local clone to origin's configured base in GitHub mode; it never changes GitHub. Open demo PRs remain open. On an operator machine with authenticated `gh`, use `scripts/reset-github-demo.sh owner/name-demo-docs` to close open kb/* PRs, delete their branches and force-reset main to the required `demo-start` tag. This destructive helper refuses other repository names; create and push that tag at the intended seed commit beforehand.
