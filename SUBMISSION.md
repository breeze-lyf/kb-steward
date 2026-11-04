# Submission form content (Breeze submits personally — the rules forbid automated submission)

**Project name:** KB Steward — Knowledge Governance in Team Chat

**Track:** Track 01 — Best Business Workflow

**Tagline (one line):** Close knowledge gaps, correct past answers, and review stale docs from team chat — with owner approval and a git audit trail.

**Repository:** https://github.com/breeze-lyf/kb-steward

**Demo video (v3, under 3:00):** VIDEO_URL_V3 — placeholder, link added after upload.

**Previous versions:** [v2](https://youtu.be/HDlKPY9HajU) · [v1](https://youtu.be/8xKwF6o_T2U).

**Team:** solo — breeze

**Short description:**
KB Steward puts two agents into a Switch room. `@kb-answerer` answers from a git-versioned KB with
citations, owners and review dates, flagging stale or conflicting sources instead of guessing.
`@kb-curator` overhears corrections, finds affected documents, drafts changes on a branch, and shows
Impact, diff and a preview answer. Only the documents' configured owner approvers can publish changes.

In v3, unanswered questions become `G-n` entries in the **KB knowledge gaps** room document. An owner's
thread reply becomes a proposal for a new document or section; publication closes the gap, @-mentions
the original asker and triggers a cited re-check. An answers ledger tracks cited answers and sections
(excluding uncited, low-confidence/conflicting answers and agent re-checks). After a proposal merges,
past askers affected by changed sections get an @-correction in their original thread:
"@newhire correction: earlier I told you $50; KB-2 corrected it to $65."
Impact previews **N past answers in M threads will be corrected**; duplicate thread corrections are suppressed.

`@kb-curator stale report` and periodic sweeps (`REVIEW_SWEEP_HOURS`) open review threads for overdue
documents, subject to a per-sweep cap. Switch cards offer **Still valid / Needs update / Not mine**.
Non-owner clicks are refused and cards re-issued. Still valid commits `last_reviewed` with `Reviewed-by`
trailers; Not mine reassigns ownership through a second card with an `Owner-change` trailer; Needs update
feeds the proposal flow. Typed replies are a fallback.

**Dashboard:** https://fde.yufeng.fun/kb/ — coverage, stale rate, conflicts, gaps, top questions,
per-owner to-dos, recent decisions with git trailers and retro-correction count. One-click review actions
require login and the same owner check; kb-curator executes changes as the single git writer.

**GitHub open-PR mode:** `KB_SOURCE=github` opens a docs-repo PR after owner approval, with Impact and
trailers, and posts the link to chat. Polling detects merge (no webhook); only then are the index rebuilt
and retro corrections sent. Demo docs repo: https://github.com/breeze-lyf/kb-steward-demo-docs .

PR mode was verified end-to-end against real GitHub from a development box (PR #1 in the demo docs repo: https://github.com/breeze-lyf/kb-steward-demo-docs/pull/1); the live instance runs local-merge mode.

Notion/Confluence are adapter interfaces only, not implemented integrations.
One-command demo reset: `scripts/demo-start.sh [en|zh|both]`.

**Problem it solves:** Internal docs drift, corrections disappear in chat, and people keep receiving old
answers. KB Steward turns those corrections into approved updates and follows up with affected people.

**How Switch is used:**

- Two registered agent identities in one room, with WebSocket `filter=all`; answerer reacts when addressed, curator overhears corrections.
- Threaded replies, `read_context`, and `send_targeted_message` for conflict/gap hand-offs and post-merge re-checks.
- Shared **KB decision log** and **KB knowledge gaps** room documents.
- Approval cards through `POST /agent-sessions/{id}/approvals`, outcome polling, delivery acknowledgement and card closure; curator checks owner authorization independently.
- Typing indicators and gateway addressing policy. Demo runs on Switch's bundled Mattermost; enterprise bridges are the rollout path.

**Tech stack:** Switch 0.31.0, Python 3.12, git worktrees, section-level BM25 retrieval,
OpenAI-compatible LLM, Docker Compose; local merge and GitHub PR publishing adapters.

**What's next:** hosted GitHub mode on the live instance, implemented Notion/Confluence adapters,
and enterprise chat rollout.

**Blog post (optional):** https://breeze-lyf.hashnode.dev/kb-steward-teaching-two-agents-to-fix-the-wiki-from-team-chat-on-switch

**README:** [README.md](README.md)
