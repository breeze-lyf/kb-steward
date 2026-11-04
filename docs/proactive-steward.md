# Proactive stewardship

## Learning from human answers (gap → new doc)

When kb-answerer hands off an unanswered question, kb-curator records an open
`G-n` gap with the original question, asker, language, and Switch thread ID.
After approval commands and stale-report requests are handled, human replies in
that thread (except the original asker's) go through `classify_gap_answer`.
Non-answers fall through to the existing correction flow. Owner selection uses
a recognized classifier owner, then the sender's approver membership, then the
first configured owner.

A qualifying answer enters the existing proposal/coding loop in gap mode. The
model searches for a relevant document of that owner and prefers adding a new
`##` section; otherwise it creates a document. Only the stated facts are used,
in the question's language, with today's review date. The proposal includes the
usual impact analysis, all sections of new documents, and new-file markers.
Approval permissions still come from the front matter of written files.

Safety is enforced by Workspace tools, not just prompts. Correction mode keeps
its original tool list and refuses `create_file` even if called directly.
`write_file` requires an existing file in language scope and preserves its owner
in both modes. In gap mode, `create_file` requires a relative path matching
`^(zh/)?[a-z0-9-]+/[a-z0-9][a-z0-9-]*\.md$`, a configured owner directory,
and a resolved destination inside the worktree. Traversal, `.git`, README files,
other extensions, deep nesting, and existing destinations are refused. Front
matter must have a non-empty title, a known owner matching the directory, and a
valid ISO review date. English scope excludes `zh/`; Chinese scope requires it
and `lang: zh`. The pure `validate_new_doc` checks content/path rules; Workspace
checks destination existence and resolution against its worktree.

State lives under `KBS_STATE_DIR`: `gaps.json` stores the room-document ID and
rows `{id, when, question, asked_by, lang, thread, status}`; `proposals.json`
stores branches, changed files, impact, approval state, and gap proposals' `kind`,
`gap_id`, and `gap_asker`; `decisions.json` stores approval history. Drafts live
in `worktrees/KB-n`. On approval and successful merge, the matching gap ID becomes
`answered by KB-n`, the room's knowledge-gap document is synchronized, and the
original thread receives a closure notice followed by kb-answerer's cited
re-check. Rejection leaves the gap open. Correction proposals retain their
question-based `close_gap` behavior. `scripts/reset-demo.sh` clears gap rows
alongside proposal and decision state.

## Answer ledger and retroactive corrections

`/data/state/answers.json` (under `KBS_STATE_DIR`) is a JSON array shared by
answerer and curator. Each row contains `{id, thread, asker, question, lang,
kb_commit, answer, cites, when, status}`. IDs are increasing `A-n` values;
`answer` holds at most 500 characters. Cites contain `{path, section, intro}`;
`intro` preserves whether the section was the document title/path when recorded.
Corrected rows also contain `corrected_by`, the proposal ID. Empty citations,
agent re-checks, conflicts, and low-confidence answers are not recorded. All
reads and writes use `answers.json.lock` with `fcntl.flock`; writes flush a
temporary file and atomically replace the ledger. Ledger failures are logged
without interrupting human answers. Tests can override `ledger.FILE`.

Matching requires the same document path and section. A citation to a document's
intro (its title, or path when no title exists) matches any change to that path.
Only the latest answer per `(thread, asker, path)` is eligible; corrected entries
are excluded, and each proposal sends at most one correction per thread.
Drafts persist matching IDs in `retro` and display counts even when zero.
Approval recomputes matches to include answers recorded while review was open.

Corrections mention the original asker in their original language and thread,
using Switch replies. If an impact `old → new` pair's old value occurs in the
stored answer, the message explicitly corrects that value (joining multiple
pairs with semicolons). Otherwise curator answers the stored question against
the merged KB, removes numeric citation markers, and posts the updated answer.
If answering fails or produces a conflict/low confidence, it asks the person to
re-ask. In the proposal thread, the same asker's retro post is skipped when the
normal re-check already answers their question; that ledger row is still marked
corrected. Successfully posted corrections are marked corrected, summarized in
the proposal thread, and counted in the decision-log title. The merge message
includes `Retro-corrections: N answers in M threads` alongside the existing
approval, requester, and impact trailers. Resetting the demo deletes both the
ledger and its lock file.

## Review sweep and one-click confirm

`@kb-curator stale report` retains the report and also opens review threads.
Automatic sweeps run every `REVIEW_SWEEP_HOURS` (24 by default; 0 disables),
starting after `REVIEW_SWEEP_DELAY_S` (default: one full interval, so a freshly reset room stays clean). Each sweep opens at most
`REVIEW_MAX_CARDS` (5), most overdue first, skipping active reviews. Document
language controls card and message language; English is the default.

The Switch client starts a UUID session once with
`POST /agent-sessions/{session_id}/started` and `start_source: automation`
(start errors are ignored), then creates a questions approval at
`POST /agent-sessions/{session_id}/approvals`, using Bearer API-key auth.
The root reply's `event_id` is the card's `thread_id`. The request uses
`turn_id: review`, empty top-level options, a single non-custom, single-select
question, and null expiry. Mattermost renders option-order action IDs
`switch1`, `switch2`, `switch3`, with typed fallback `A<n> <option#>`.

Every three seconds curator polls `GET /agent-sessions/approvals/outcomes`
and acknowledges outcomes through the approval's `/delivered` endpoint.
`answeredBy` looks like
`@switch-mattermost-<36-character UUID>-username:localhost`; the client strips
that wrapper and lowercases the username. Anyone allowed to address the agent
may press a button, so curator independently checks the current owner's
`APPROVERS` membership on every decision. Unauthorized presses change no KB
content and receive a fresh card. Chat decisions close the outstanding card
through `/close`; old request IDs cannot resolve a reissued review.
Expired reviews become superseded and are eligible for another issue.

Owners may choose Still valid, Needs update, or Not mine, or use their English
or Chinese thread equivalents. Global `review R-3 valid|update|notmine` works
as well. Not mine opens a second card listing other owner keys; only the
current owner's approvers can select the new owner. Reassignment changes only
front matter and immediately issues a review to the new owner's approvers.
Needs update routes the subsequent thread reply through the existing correction
classifier and proposal approval flow with the document path in context;
`don't know` / `不知道` records a knowledge gap.

`reviews.json` under `KBS_STATE_DIR` stores a persisted session UUID and review
rows (ID, path, owner, previous review date/age, thread, request ID, status,
stage, timestamp and decision user). Locked atomic replacement uses the same
helper as the answer ledger. `review.enqueue(review_id, choice, user, via,
new_owner=None)` appends to `review_queue.jsonl` under a file lock; polling
renames it to `.processing` and drains it. Front ends must authenticate users;
curator still checks owner membership. Reset clears both state files and queue
suffix files.

Still-valid commits change only `last_reviewed`, without explicitly rebuilding
an index, and use `Review R-n: <path> still valid`, with trailers:

```text
Reviewed-by: <user> via <via> in Switch room kb-ops
Decision: still-valid
Previous-review: <date>
```

Reassignments use `Reassigned-by: <user> via <via>` and
`Owner-change: <old> -> <new>`. Both are authored by kb-curator and recorded
with their commit in the decision log. All agent communication uses Switch.

`agents/mm_press.py <persona> <post-id-prefix> <action-id>` presses a card as a
demo persona, for example `finance-lead abc123 switch1`. Like `mm_say.py`, it
is a demo-only human-side Mattermost driver and is never called by agents.
