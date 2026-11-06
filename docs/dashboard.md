# Knowledge-health dashboard

Start `docker compose up -d kb-dashboard` after configuring `agents.env`. The
service uses the agent image, shared `kbdata` volume and environment file. It
binds the host's loopback port 8870. Open `http://127.0.0.1:8870/kb/`, or proxy
`/kb/` to it. Both preserved and stripped proxy prefixes work; generated links
and form actions always include `DASH_BASE` (default `/kb`, empty for root).
`DASH_PORT` controls the container listener; update the port mapping if changed.
Use HTTPS at the reverse proxy for remote access.

The Python standard-library server serves an English overview, `/doc?path=…`,
`/api/summary`, `/login`, `/act`, and `/healthz`. It rebuilds the in-memory KB
section index when main's full Git HEAD changes. Git operations only inspect
HEAD, history and commit messages. Documents show escaped front matter and
plain text; no document HTML executes. Styles are embedded. There is no sample
case: a step appears only when a state file or a commit that names the proposal
records it.

The overview leads with the strongest recorded closed loop, not with the
figures. A case is a proposal id or a decision id. Ledger rows join it only by
`corrected_by`, a `retro` answer id, or the same stored thread and question.
Gap rows join it only by `gap_id` or a status that names the proposal. The five
stages are question, correction proposal, owner approval, document update, and
trace-back to the original thread. Each stage is **Recorded**, **Pending**, or
**Not recorded**. `approved_by`, `decisions.json` field `by`, and an
`Approved-by` trailer are approval evidence. A `Reviewed-by` trailer is not.
The before/after panel uses `proposals.json` field `diff`, or the patch of a
commit whose message names the proposal. A decision may cite some other
commit; that hash is shown, and its patch is not. Answer bodies, proposal
worktree paths, and credential values are not rendered.

If no proposal or decision exists, the page says no closed-loop case is
recorded and leaves all five stages empty. `DASH_RECORD_LABEL`, when set, is
shown as a label and is not history. Footer links are the published demo
videos and the English and Chinese blog posts.

Publication follows the effective mode. `KB_SOURCE=github` with a repository
slug and a configured token is GitHub pull-request mode: open `pr_open` records
are listed, and the document is described as not yet updated. With no token,
or with `KB_SOURCE` unset, the effective mode is local merge. The page says
documents are updated in the local git repository and does not present an empty
pull-request table. The next step is the next recorded action: approve an open
proposal, wait for a recorded pull request to merge, or start from a new
question. Figures for coverage, stale rate, conflicts, gaps, and reviews stay
on the page, below the case. Review actions still require a signed-in approver.

Data is read fresh from `KB_REPO` and `KBS_STATE_DIR`:

- `answers.json`: question frequency, citation paths and corrected marks.
  Questions group by Unicode-normalized, case-folded words, ignoring punctuation
  and repeated whitespace. Coverage counts ledger rows with citations divided by
  all ledger rows plus open gaps. Repeated questions count as separate asks.
- `gaps.json`: open questions and any assigned owner. Unassigned gaps stay visible
  in the gaps table.
- `proposals.json`: open proposals without a logged decision. Open conflicts
  counts these plus open/pending rows in optional `conflicts.json` (an array or
  `{ "rows": […] }`).
- `decisions.json`: newest-first decisions with approvers, commit hashes and
  parsed final-paragraph Git trailers. Retro-corrections uses the greater of
  corrected ledger marks and the total `Retro-corrected: N` decision counts,
  avoiding double-counting the same corrections.
- `reviews.json`, `review_queue.jsonl` and its `.processing` file: review state,
  queued choices and outcomes. Queue requests show pending while the row remains
  open; after consumption, the review outcomes table retains the resulting state.
- Document front matter: owner, title and last review date. `REVIEW_DAYS` defaults
  to `KB_STALE_DAYS` (365); missing/invalid dates count as stale.

Overview, document detail and summary are public to anyone who can reach this
service. Passwords, signing keys and credential files are never rendered or
included in the API. Action forms require login, a signed eight-hour session,
CSRF validation and membership in `common.approvers()` for the current document
owner. Credentials are checked read-only against `demo-users.json`: bootstrap's
actual `{ "username": "password" }` format and the nested
`{ "users": { "username": { "password": "…" } } }` format are supported.
As with `mm_say.py`, configured Mattermost admin/user environment credentials
are fallback identities. This is demo authentication, not Mattermost SSO.

Session cookies are HMAC-SHA256 signed, HttpOnly, SameSite=Lax and scoped to the
external base path. Set a strong `DASH_SECRET` in the environment, or the server
creates `STATE/dashboard-secret` once with mode 0600. Keep that key stable across
restarts. Credential/signing-key reads do not use state helpers that create lock
files. Apart from this key initialization, the dashboard's only writes are
`review.enqueue()` queue appends (and the helper's queue lock).

Still valid, Needs update and Not mine enqueue the authenticated identity and
`via=dashboard`. Not mine expands an owner selector. If no open review exists,
curator creates the row and kb-ops thread when it consumes the path-based queue
request. Curator re-checks current document ownership and approver membership;
it alone modifies and commits the KB. Needs update opens the existing kb-ops
follow-up flow instead of committing immediately. A queued message means accepted
for processing, not approved or committed; inspect review outcomes and kb-ops.

Run offline checks with `python -m unittest discover -s tests -v`.
