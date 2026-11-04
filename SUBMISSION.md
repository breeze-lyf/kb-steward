# Submission form content (Breeze submits personally — the rules forbid automated submission)

**Project name:** KB Steward — Knowledge Governance in Team Chat

**Track:** Track 01 — Best Business Workflow

**Tagline (one line):** AI does the impact analysis and multi-file revision; people keep approval and audit — corrections typed in chat become owner-approved git changes across every affected doc, in English or Chinese.

**Repository:** https://github.com/breeze-lyf/kb-steward

**Demo video:** https://youtu.be/HDlKPY9HajU (v2, 2:13 — English loop, Chinese loop, impact analysis, knowledge gaps, review report; v1: https://youtu.be/8xKwF6o_T2U)

**Team:** solo — breeze

**Short description:**
KB Steward puts two agents into a Switch room with the team. `@kb-answerer` answers policy questions
only from a git-versioned knowledge base, with citations, owners and review dates, and flags stale or
conflicting docs instead of guessing. `@kb-curator` listens to the whole room: when a person corrects an
answer, it runs a tool-using agent loop over the repo, finds every document carrying the wrong fact,
commits the fix on its own branch, previews the new answer from that branch and posts the diff. Only the
doc's owner can approve; approval merges with Approved-by/Requested-by trailers, rebuilds the index, logs
the decision in a Switch room document, and the answerer re-answers the original question in-thread.

Governance controls: stale flags and a stale sweep that @-mentions owners (`@kb-curator stale report`,
`scripts/stale-report.py`), conflict hand-off, an **Impact** block on every proposal (files, sections, numeric
risk such as `$50 → $65`), owner-gated approval, `Approved-by`/`Requested-by`/`Impact` git trailers, and two room
documents: "KB decision log" and "KB knowledge gaps" (questions the KB couldn't answer). It is bilingual: a
Chinese question gets a Chinese answer with the same citations/owner/review flags, Chinese corrections
(“这个过时了，现在是每天 ¥120”) draft proposals, and `批准 KB-n` / `驳回 KB-n` work as approvals.

**Problem it solves:** Internal docs drift. Corrections happen in chat and are lost, so the same wrong
answer repeats. Existing chat bots answer from stale docs and never fix them. KB Steward makes the
correction the update, while keeping humans as the authority.

**How Switch is used:**
- Two agents registered through Switch's agent API with their own identities; one shared room created with `create_room` and room instructions.
- WebSocket event stream with `filter=all`: the answerer acts only when `addressed`, the curator overhears corrections without being mentioned.
- Threaded replies with `post_message(thread_id)`; `read_context` to read the thread a correction belongs to.
- Agent-to-agent hand-offs with `send_targeted_message` (answerer → curator on conflicts/gaps; curator → answerer to re-check after a merge).
- Switch room documents as shared governance records: "KB decision log" (audit) and "KB knowledge gaps".
- `POST /agents/{id}/message` for the scheduled stale sweep (`scripts/stale-report.py --post`).
- Typing indicators while the model works; gateway addressing policy so every room member can call the agents.
- Channel: Switch standalone stack with bundled Mattermost; the same agents work unchanged in Slack/Teams/Discord/Telegram via Switch bridges.

**Tech stack:** Switch 0.31.0 (core, gateway, Mattermost bridge), Python 3.12 (httpx, websockets), git worktrees, BM25 retrieval, OpenAI-compatible LLM (v1 video run: DeepSeek; v2 video and live instance: GPT via an OpenAI-compatible relay), CJK-bigram BM25 for Chinese, Docker Compose.

**What's next:** push merged changes as PRs to a real docs repo (GitHub/Confluence/enterprise wiki), owner re-confirmation buttons on the stale sweep, Slack/Teams/DingTalk-style enterprise chat rollout.

**Blog post (optional):** https://breeze-lyf.hashnode.dev/kb-steward-teaching-two-agents-to-fix-the-wiki-from-team-chat-on-switch

**Chinese blog post (中文博文):** _TODO — link to be filled after publishing (draft: docs/POST.zh-CN.md)_

**README:** English [README.md](README.md) · 中文 [README.zh-CN.md](README.zh-CN.md)
