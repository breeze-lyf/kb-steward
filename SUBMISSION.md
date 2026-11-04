# Submission form content (Breeze submits personally — the rules forbid automated submission)

**Project name:** KB Steward — a self-healing knowledge base in your team chat

**Track:** Track 01 — Best Business Workflow

**Tagline (one line):** People correct the wiki in chat; Switch-connected agents turn those corrections into owner-approved git changes across every affected doc.

**Repository:** https://github.com/breeze-lyf/kb-steward

**Demo video:** https://youtu.be/8xKwF6o_T2U

**Team:** solo — breeze

**Short description:**
KB Steward puts two agents into a Switch room with the team. `@kb-answerer` answers policy questions
only from a git-versioned knowledge base, with citations, owners and review dates, and flags stale or
conflicting docs instead of guessing. `@kb-curator` listens to the whole room: when a person corrects an
answer, it runs a tool-using agent loop over the repo, finds every document carrying the wrong fact,
commits the fix on its own branch, previews the new answer from that branch and posts the diff. Only the
doc's owner can approve; approval merges with Approved-by/Requested-by trailers, rebuilds the index, logs
the decision in a Switch room document, and the answerer re-answers the original question in-thread.

**Problem it solves:** Internal docs drift. Corrections happen in chat and are lost, so the same wrong
answer repeats. Existing chat bots answer from stale docs and never fix them. KB Steward makes the
correction the update, while keeping humans as the authority.

**How Switch is used:**
- Two agents registered through Switch's agent API with their own identities; one shared room created with `create_room` and room instructions.
- WebSocket event stream with `filter=all`: the answerer acts only when `addressed`, the curator overhears corrections without being mentioned.
- Threaded replies with `post_message(thread_id)`; `read_context` to read the thread a correction belongs to.
- Agent-to-agent hand-offs with `send_targeted_message` (answerer → curator on conflicts/gaps; curator → answerer to re-check after a merge).
- A Switch room document, "KB decision log", as the shared audit record.
- Typing indicators while the model works; gateway addressing policy so every room member can call the agents.
- Channel: Switch standalone stack with bundled Mattermost; the same agents work unchanged in Slack/Teams/Discord/Telegram via Switch bridges.

**Tech stack:** Switch 0.31.0 (core, gateway, Mattermost bridge), Python 3.12 (httpx, websockets), git worktrees, BM25 retrieval, OpenAI-compatible LLM (DeepSeek deepseek-flash), Docker Compose.

**What's next:** push merged changes as PRs to a real docs repo (GitHub/Confluence), scheduled stale-doc sweeps that ask owners to re-confirm, Slack/Teams rollout.

**Blog post (optional):** _<link if written>_
