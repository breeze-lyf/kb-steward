# KB Steward — a self-healing knowledge base that lives in your team chat

> Flip the Switch hackathon · Track 01 (Best Business Workflow) · built on [Switch](https://github.com/sandbox-quantum/switch)

Every team has a wiki that is quietly wrong. Someone asks in chat, a bot (or a colleague) answers from
the stale page, a senior person types *"that's outdated, it's $65 now"* — and the correction dies in the
scroll-back. The doc never gets fixed, so the same wrong answer comes back next week.

**KB Steward closes that loop inside the chat, with people in charge:**

1. **`@kb-answerer`** answers questions *only* from the git-versioned KB, with citations, the doc owner and
   review date. It flags **stale** docs (⚠️ not reviewed in a year) and **conflicting** docs (two pages that
   disagree) and hands those to the curator instead of guessing.
2. **`@kb-curator`** listens to the whole room. When a human *corrects* an answer — no mention needed —
   it runs an agent loop over the repo (`search_kb` / `grep` / `read_file` / `write_file`), finds **every**
   doc that carries the wrong fact (e.g. the policy *and* the FAQ that copied it), commits the fix on its
   own branch, **previews the new answer from that branch**, and posts the diff as proposal `KB-n`.
3. **Only the doc's owner can approve.** A non-owner's `approve KB-1` is refused with a reason. The owner's
   approval merges the branch with `Approved-by:` / `Requested-by:` git trailers, rebuilds the index,
   appends the decision to the Switch room document **"KB decision log"**, and hands the original question
   back to the answerer, which re-answers correctly — in the same thread.

The AI does the real work (impact analysis, multi-file edits, verification); people keep the authority
(owner-gated approval, a full git audit trail).

> All company data in this repo is **example data** for a fictional "Northwind Example Co.".

## Demo

📺 Video: _link added after recording_ — shot list in [DEMO.md](DEMO.md).

```
newhire        @kb-answerer what's the meal allowance when travelling?
kb-answerer    $50/day … [finance/expense-policy.md · owner finance-lead · reviewed 2024-03-12 ⚠️ stale]
finance-lead   that's outdated — it's $65/day since July, receipts over $25
kb-curator     📝 KB-1 · 2 files changed (expense-policy.md, travel-faq.md) · preview answer: "$65/day…"
               @finance-lead reply `approve KB-1` or `reject KB-1`
support-lead   approve KB-1
kb-curator     ⛔ only the owner of finance docs (finance-lead) can approve KB-1
finance-lead   approve KB-1
kb-curator     ✅ merged a1b2c3d (Approved-by: finance-lead) · index rebuilt · logged in "KB decision log"
kb-answerer    (re-check) $65/day, receipts over $25 … [reviewed today]
```

## How Switch is used (the core of the project)

Switch is what turns two Python processes into **teammates in a channel**. Nothing here talks to
Mattermost/Slack directly — every read and write goes through Switch's agent protocol, so the same
agents work unchanged in Slack, Teams, Discord, Telegram or Mattermost.

| What | Switch feature | Where in code |
| --- | --- | --- |
| Two agents, each with its own identity and avatar | `POST /agents` with the registration token → `{id, api_key}` (persisted, re-used on restart) | `switch_proto.py` `SwitchAgent.register` |
| One shared room for people + agents | op `create_room` (public channel, members, **room instructions**) | `bootstrap.py` |
| Live events (every message, not just mentions) | WebSocket `/agents/{id}/connection/ws?filter=all`, ping/pong with cursor, gap/evicted handling, `connect_to_room` claim | `switch_proto.py` `run` |
| Answerer only reacts when addressed; curator overhears corrections | the event's `addressed` flag + `filter=all` | `answerer.py`, `curator.py` |
| Threaded replies | op `post_message(body, thread_id)` | `SwitchAgent.reply` |
| Reading the conversation a correction belongs to | op `read_context` | `curator.thread_text` |
| Agent-to-agent hand-off (conflict → curator, merged → re-check) | op `send_targeted_message(target_names=[...])` | `answerer.py`, `curator.decide` |
| Shared, durable decision record | ops `create_room_document` / `update_room_document` → "KB decision log" | `curator.log_decision` |
| "is typing…" while the model thinks | `POST /agents/{id}/typing` | `SwitchAgent.typing` |
| Let any room member mention the agents | gateway admin `PUT /gateway/agents/{id}/addressing-policy` | `bootstrap.open_addressing` |
| Human identity for the approval gate | `sender_name` on each event = chat username | `common.sender_handle` |

The channel is the bundled **Mattermost** of Switch's standalone stack (fully self-hosted, so anyone can
reproduce it with one command). To move it to Slack/Discord/Telegram/Teams, connect that bridge in the
Switch gateway and invite the two agents to the channel — no agent code changes.

## Architecture

```
             Mattermost / Slack / Discord / Telegram channel  "kb-ops"
                 ▲   people: newhire, finance-lead, support-lead
                 │
        ┌────────┴─────────  Switch (core + gateway)  ──────────────┐
        │ rooms · addressing · threads · targeted msgs · room docs   │
        └──────▲─────────────────────────────▲──────────────────────┘
          ws + ops                       ws + ops
        ┌──────┴───────┐  hand-off   ┌───────┴────────┐
        │ kb-answerer  │◀──────────▶│  kb-curator     │
        │ BM25 + LLM   │  re-check   │ LLM tool loop   │
        └──────┬───────┘             └───────┬────────┘
               │ reads HEAD                  │ worktree kb/KB-n → merge --no-ff
               └──────────►  git repo /data/kb  ◄────────┘   (optional push to a remote)
```

* `agents/kb_core.py` — section-level BM25 index over markdown, front-matter (`owner`, `last_reviewed`),
  answer prompt returning JSON `{answer, used, confidence, conflict}`; index rebuilds whenever `HEAD` moves.
* `agents/curator.py` — correction classifier, git worktree per proposal, tool-calling edit loop (only
  existing `.md` files can be written), preview answer from the branch, owner gate, merge, decision log.
* LLM: any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). Developed on Zhipu `glm-4.6`.

## Run it yourself

Requirements: Docker with compose, and an API key for an OpenAI-compatible model.

```bash
git clone https://github.com/breeze-lyf/kb-steward && cd kb-steward
scripts/up-switch.sh switch-stack            # Switch 0.31.0 + gateway + bundled Mattermost
scripts/make-agents-env.sh switch-stack/.env # writes agents.env (chmod 600), no secrets printed
$EDITOR agents.env                           # set LLM_API_KEY (and LLM_BASE_URL/LLM_MODEL if not GLM)
docker compose up -d --build                 # bootstrap → seeds KB, demo users, agents, room
```

Open Mattermost (`http://127.0.0.1:8065`, the `MATTERMOST_HOST_PORT` in `switch-stack/.env`), log in as
`user` (password: `MATTERMOST_USER_PASSWORD` in that file) or as a demo persona (passwords in the
`kbdata` volume at `/data/state/demo-users.json`), and open channel **kb-ops**.

Rehearse without a browser:

```bash
docker compose exec kb-answerer python mm_say.py newhire "@kb-answerer what's the meal allowance when travelling?"
docker compose exec kb-answerer python mm_say.py --dump 10
```

Inspect what the curator did: `docker compose exec kb-curator git -C /data/kb log --format='%h %s%n%(trailers)'`.

Configuration (`agents.env`): `APPROVERS=finance:finance-lead;support:support-lead;…` maps each doc
`owner` to the chat users who may approve; `KB_STALE_DAYS` (default 365); `KB_PUSH_REMOTE` to push
merged changes to a real remote (e.g. a GitHub docs repo).

Behind the Great Firewall? `.env` build args `PYTHON_IMAGE`, `PIP_INDEX_URL`, `APT_MIRROR` let you use mirrors.

## Safety by design

* Agents can only edit existing markdown inside the KB worktree; nothing reaches `main` without an owner's approval.
* Every change is a normal git merge commit with who asked and who approved — revertable with `git revert`.
* Secrets live only in `agents.env` / the Switch `.env` (both git-ignored, chmod 600) and are never logged.
* If the model provider fails, the agent says so in the thread and changes nothing.

## License

MIT — see [LICENSE](LICENSE). Switch is © its authors under its own license.
