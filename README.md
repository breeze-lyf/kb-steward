# KB Steward — Knowledge Governance in Team Chat

**English** · [简体中文](README.zh-CN.md)

> Flip the Switch hackathon · Track 01 (Best Business Workflow) · built on [Switch](https://github.com/sandbox-quantum/switch)

**The AI does the impact analysis and the multi-file revision. People keep the approval and the audit trail.**

Every team has a wiki that is quietly wrong. Someone asks in chat, a bot (or a colleague) answers from
the stale page, a senior person types *"that's outdated, it's $65 now"* — and the correction dies in the
scroll-back. The doc never gets fixed, so the same wrong answer comes back next week.

KB Steward turns the team channel into a governed change process for the knowledge base:

| Governance control | What happens in chat |
| --- | --- |
| **Stale detection** | Every answer cites doc, section, **owner** and **review date**; docs not reviewed in `KB_STALE_DAYS` get ⚠️. `@kb-curator stale report` (or `scripts/stale-report.py`) lists overdue docs and @-mentions their owners. |
| **Conflict hand-off** | When two docs disagree, the answerer gives both values instead of picking one and hands off to the curator, which asks the owner which is right. |
| **Impact analysis** | A correction becomes proposal `KB-n` on its own git branch, with an **Impact** block: files changed, sections touched, and a one-line risk (e.g. `numeric policy value change ($50 → $65)`), plus a preview of the new answer. |
| **Owner gate** | Only the doc owner can `approve KB-n` / `reject KB-n` (`批准` / `驳回` also work). Anyone else is refused with a reason; nothing reaches `main`. |
| **Git audit trail** | Approval merges with `Approved-by:` / `Requested-by:` / `Impact:` trailers — revertable with `git revert`. |
| **Room decision log** | Every decision is appended to the Switch room document **"KB decision log"**; unanswered questions go to **"KB knowledge gaps"**. |
| **Bilingual** | Ask in Chinese, get a Chinese answer; ask in English, get English — same citations, owners and flags. Corrections are understood in both languages. |

How the loop works:

1. **`@kb-answerer`** answers questions *only* from the git-versioned KB, with citations, the doc owner and
   review date, in the asker's language. It flags **stale** and **conflicting** docs and hands conflicts and
   knowledge gaps to the curator instead of guessing.
2. **`@kb-curator`** listens to the whole room. When a human *corrects* an answer — no mention needed, English
   or Chinese — it runs an agent loop over the repo (`search_kb` / `grep` / `read_file` / `write_file`), finds
   **every** doc that carries the wrong fact (e.g. the policy *and* the FAQ that copied it), commits the fix on
   its own branch, computes the impact, **previews the new answer from that branch**, and posts proposal `KB-n`.
3. **Only the doc's owner can approve.** The owner's approval merges the branch with git trailers, rebuilds
   the index, appends the decision to the room's **"KB decision log"**, and hands the original question back
   to the answerer, which re-answers correctly — in the same thread.

> All company data in this repo is **example data** for a fictional "Northwind Example Co.".
> `kb-seed/` is the English KB, `kb-seed/zh/` the Chinese one (separate example policies, e.g. ¥100/day vs $50/day).

## Demo

📺 Video: [KB Steward demo (1:58)](https://youtu.be/8xKwF6o_T2U) — shot list in [DEMO.md](DEMO.md).
📝 Blog: [KB Steward: Teaching Two Agents to Fix the Wiki From Team Chat, on Switch](https://breeze-lyf.hashnode.dev/kb-steward-teaching-two-agents-to-fix-the-wiki-from-team-chat-on-switch)

Real transcript from a run on DeepSeek (`kb-ops` channel, example data, lightly trimmed):

```
newhire       @kb-answerer what's the meal allowance when travelling?
kb-answerer   The daily meal allowance when travelling for work is $50 per day [1][2], and alcohol is
              not reimbursable [1]. Keep receipts for anything above $40 [2].
              [1] finance/expense-policy.md § Meal allowance · owner finance · reviewed 2024-03-12 ⚠️ last reviewed 967 days ago
              [2] finance/travel-faq.md § Do I get a per diem? · owner finance · reviewed 2025-01-20 ⚠️ last reviewed 653 days ago
              kb@cfbdc03 · confidence: high
finance-lead  That's outdated: since July it's $65/day, and receipts are needed for anything over $25.
kb-curator    On it: drafting KB-1 from @finance-lead's correction. Checking every doc that states this fact…
kb-curator    📝 KB-1 · Update meal allowance to $65/day and receipt threshold to $25 — kb/KB-1 (2 files changed)
              Files: finance/expense-policy.md, finance/travel-faq.md · owner: finance
              (diff) -daily meal allowance is **$50 per day**  +daily meal allowance is **$65 per day** …
              🔎 Preview — on this branch, kb-answerer would now say: The daily meal allowance … is $65 per day …
              @finance-lead please review. Reply `approve KB-1` to merge, or `reject KB-1 <reason>`.
support-lead  approve KB-1
kb-curator    ⛔ @support-lead can't approve KB-1: it changes finance documents, which only @finance-lead
              can sign off. Nothing was merged.
finance-lead  approve KB-1
kb-curator    ✅ KB-1 approved by @finance-lead and merged as f7f2723. Index rebuilt: 27 sections from 7 docs.
              Logged to the room's KB decision log.
kb-curator    @kb-answerer re-check: what's the meal allowance when travelling?
kb-answerer   The daily meal allowance when travelling for work is $65 per day [1][2]; keep receipts for
              anything above $25 [2]. … reviewed 2026-11-04 · kb@f7f2723 · confidence: high

newhire       @kb-answerer how long do customers have to request a refund?
kb-answerer   The sources disagree on the refund window: the refund policy says 30 days [1], while the
              support macro says 14 days [2]. … confidence: medium
kb-answerer   @kb-curator hand-off · CONFLICT · Refund request window: 30 days per refund-policy.md vs
              14 days per support-macros.md · files: … · question: how long do customers have to request a refund?
kb-curator    🧭 Two documents disagree, so I won't let the answer drift. @support-lead: which value is correct?
support-lead  30 days is right, the macro is wrong.
kb-curator    📝 KB-2 · Fix refund window in support macro to 30 days — 1 file changed …
support-lead  approve KB-2
kb-curator    ✅ KB-2 approved by @support-lead and merged as 99ba48b. …
kb-answerer   Customers have 30 days from purchase to request a full refund [1][2]. … confidence: high
```

Git history after the run:

```
99ba48b Merge KB-2: Fix refund window in support macro to 30 days
Approved-by: support-lead in Switch room kb-ops
Requested-by: support-lead
f7f2723 Merge KB-1: Update meal allowance to $65/day and receipt threshold to $25
Approved-by: finance-lead in Switch room kb-ops
Requested-by: finance-lead
```

Chinese run (same stack, `kb-seed/zh/`, 2026-11-04, lightly trimmed):

```
newhire       @kb-answerer 出差餐补每天多少钱？
kb-answerer   出差餐补每天 ¥100，按出差自然日计算。[1][2]
              [1] zh/finance/travel-faq.md § 出差有餐补吗？ · 负责人 finance · 复核于 2025-02-18 ⚠️ 已 624 天未复核
              [2] zh/finance/travel-expense-policy.md § 差旅餐补 · 负责人 finance · 复核于 2024-04-08 ⚠️ 已 940 天未复核
finance-lead  这个过时了，从 11 月起餐补已经是每天 ¥120，超过 ¥300 才需要开发票。
kb-curator    📝 KB-2 · 更新差旅餐补与发票门槛 — kb/KB-2 (2 files changed)
              影响分析
              - 2 个文件：zh/finance/travel-expense-policy.md, zh/finance/travel-faq.md
              - 涉及章节：§ 差旅餐补; § 发票要求; § 出差有餐补吗？
              - 风险: 数值类制度变更（¥100 → ¥120, ¥200 → ¥300）…；2 篇文档需保持一致
              🔎 预览：从 11 月起，因公出差餐补为每天 ¥120 …
support-lead  批准 KB-2
kb-curator    ⛔ @support-lead 无权批准 KB-2：它修改的是 finance 的文档，只有 @finance-lead 可以审批。未合并任何内容。
finance-lead  批准 KB-2
kb-curator    ✅ KB-2 已由 @finance-lead 批准，合并为 d575676。索引已重建 …
kb-answerer   从 11 月起，出差餐补为每天 ¥120 … 复核于 2026-11-04 · 置信度: 高

newhire       @kb-answerer 客户申请退款的期限是多久？
kb-answerer   资料中的退款申请期限存在冲突：退款政策为 30 天[1]，客服话术写 7 天[2] …
kb-curator    🧭 两份文档说法不一致，我不会随便选一个。@support-lead：哪个才是对的？
support-lead  30 天是对的，话术写错了。
kb-curator    📝 KB-3 · 更正客服退款申请期限 … 风险: 数值类制度变更（7 天 → 30 天）
support-lead  批准 KB-3   →  ✅ merged; kb-answerer: 客户可在购买后 30 天内申请全额退款。

newhire       @kb-answerer 公司的停车补贴怎么申请？
kb-curator    🕳️ 已记录知识缺口：公司的停车补贴怎么申请？（频道文档 KB knowledge gaps，当前 1 条未解决）
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
| Knowledge-gaps view | same ops → room document "KB knowledge gaps" | `curator.log_gap` |
| Scheduled stale sweep posted by the curator | `POST /agents/{id}/message` (no socket needed) | `scripts/stale-report.py --post` |
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
* Language: `kb_core.lang_of` (CJK-ratio heuristic) picks the answer language; Chinese text is indexed as
  character bigrams; docs carry `lang: zh` front matter and retrieval prefers the asker's language. A
  correction only edits docs of the KB it was answered from (the en and zh KBs are maintained separately).
* `agents/curator.py` — correction classifier (English + Chinese hints), git worktree per proposal, impact analysis, tool-calling edit loop (only
  existing `.md` files can be written), preview answer from the branch, owner gate, merge, decision log.
* LLM: any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). Developed on Zhipu `glm-4.6`; the English video run used DeepSeek; the live instance now runs a GPT model through an OpenAI-compatible relay.

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

Rehearse without a browser — English loop:

```bash
say() { docker compose exec kb-answerer python mm_say.py "$@"; }
scripts/reset-demo.sh                 # re-seed KB (KB_LANG, default both), clear proposals + channel
say newhire "@kb-answerer what's the meal allowance when travelling?"
say finance-lead "That's outdated: since July it's \$65/day, and receipts are needed for anything over \$25." --reply-last
say support-lead "approve KB-1" --reply-last     # ⛔ refused: not the owner
say finance-lead "approve KB-1" --reply-last     # ✅ merged → re-check answers $65
say newhire "@kb-answerer how long do customers have to request a refund?"   # conflict hand-off
say support-lead "30 days is right, the macro is wrong." --reply-last
say --dump 10
```

Chinese loop (中文演示):

```bash
say newhire "@kb-answerer 出差餐补每天多少钱？"
say finance-lead "这个过时了，从 11 月起餐补已经是每天 ¥120，超过 ¥300 才需要开发票。" --reply-last
say support-lead "批准 KB-1" --reply-last        # ⛔ 非负责人被拒
say finance-lead "批准 KB-1" --reply-last        # ✅ 合并 → 重新回答 ¥120
say newhire "@kb-answerer 客户申请退款的期限是多久？"   # 冲突移交
say support-lead "30 天是对的，话术写错了。" --reply-last
say newhire "@kb-answerer 公司的停车补贴怎么申请？"     # 知识缺口 → "KB knowledge gaps"
say finance-lead "@kb-curator 复核报告"                 # 超期复核提醒
```

(Proposal numbers keep counting within one reset; run `scripts/reset-demo.sh zh` for a Chinese-only KB.)

Stale sweep from the shell or cron: `docker compose exec kb-curator python /app/scripts/stale-report.py [--lang zh] [--json] [--post]`.
Offline tests: `python -m unittest discover -s tests`.

Inspect what the curator did: `docker compose exec kb-curator git -C /data/kb log --format='%h %s%n%(trailers)'`.

Configuration (`agents.env`): `APPROVERS=finance:finance-lead;support:support-lead;…` maps each doc
`owner` to the chat users who may approve; `KB_STALE_DAYS` (default 365); `KB_LANG=en|zh|both` (default both) chooses which seed KB is loaded and
which room instructions are used; `KB_PUSH_REMOTE` to push
merged changes to a real remote (e.g. a GitHub docs repo).

Behind the Great Firewall? `.env` build args `PYTHON_IMAGE`, `PIP_INDEX_URL`, `APT_MIRROR` let you use mirrors.

## Safety by design

* Agents can only edit existing markdown inside the KB worktree (and only in the KB language being discussed); nothing reaches `main` without an owner's approval.
* Every change is a normal git merge commit with who asked and who approved — revertable with `git revert`.
* Secrets live only in `agents.env` / the Switch `.env` (both git-ignored, chmod 600) and are never logged.
* If the model provider fails, the agent says so in the thread and changes nothing.

## License

MIT — see [LICENSE](LICENSE). Switch is © its authors under its own license.
