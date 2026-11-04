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
