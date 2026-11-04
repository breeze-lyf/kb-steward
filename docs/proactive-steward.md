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
