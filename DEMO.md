# Demo video — 2:45 shot list

Setup before recording: three browser profiles (or one normal + two private windows) logged into
Mattermost as **newhire**, **finance-lead**, **support-lead**, all on channel `kb-ops`. A terminal with
`git log` ready. Reset to a clean KB first (see bottom). Record at 1080p; speed up model waits ×2 in edit.

| # | Time | Screen | Action / narration (voice-over) |
|---|------|--------|------|
| 1 | 0:00–0:15 | Title card → kb-ops channel | "Every team's wiki is quietly wrong. The fix gets typed in chat and then lost. KB Steward makes the chat fix the wiki — with the doc owner in charge. Two agents, one Switch room." |
| 2 | 0:15–0:40 | **newhire** window | Type `@kb-answerer what's the meal allowance when travelling?` → answer with citation, owner, review date and **⚠️ stale** flag. "It only answers from the KB, and it tells you when the source is old." |
| 3 | 0:40–1:10 | **finance-lead** window | Reply in the thread: `that's outdated — since July it's $65/day, and receipts are needed over $25`. Don't mention anyone. Curator posts **KB-1**: 2 files (policy + FAQ), diff, preview answer. "Nobody tagged the curator. It heard a correction, searched the repo, found the FAQ that copied the old number too, and drafted both edits on a branch — then tested the new answer." |
| 4 | 1:10–1:30 | **support-lead** window | `approve KB-1` → ⛔ refused: only finance owner can approve. "Approval is owner-gated." |
| 5 | 1:30–1:55 | **finance-lead** window | `approve KB-1` → ✅ merged, index rebuilt, decision logged; answerer re-answers $65 in the same thread. |
| 6 | 1:55–2:10 | Terminal + Switch room document | `git log -1` showing `Approved-by: finance-lead` trailer and diff; open "KB decision log". "Every change is a git commit with who asked and who approved." |
| 7 | 2:10–2:35 | **newhire** window | `@kb-answerer how long do customers have to request a refund?` → answerer detects 30 vs 14 days **conflict**, hands off; curator asks support-lead. support-lead replies `30 days is right, the macro is wrong` → KB-2 → `approve KB-2`. (Speed up.) |
| 8 | 2:35–2:45 | Architecture slide from README | "Built on Switch: rooms, addressing, threads, agent-to-agent targeted messages and room documents. Works the same in Slack, Teams, Discord or Telegram. github.com/breeze-lyf/kb-steward" |

Tips: keep the thread view open on the right so the whole loop is visible in one frame. Use example
personas only. Don't show `agents.env` or `.env`.

Reset between takes (on the host running the stack):
```bash
scripts/reset-demo.sh          # re-seed KB (KB_LANG from agents.env, default both), clear proposals, gaps, channel
scripts/reset-demo.sh zh       # Chinese-only KB for the Chinese take
```
Full rebuild (also recreates the room): `docker compose down && docker volume rm <project>_kbdata && docker compose up -d`.

## Optional add-on shots (governance features added after the first video)

| # | Screen | Action / narration |
|---|--------|------|
| A | KB-1 proposal | Zoom on the **Impact** block: files, sections touched, `Risk: numeric policy value change ($50 → $65)`. "Before anyone approves, they see the blast radius." |
| B | any window | `@kb-answerer how do I apply for a parking subsidy?` → low confidence → curator logs it to room document **KB knowledge gaps**. |
| C | finance-lead | `@kb-curator stale report` → overdue docs grouped by owner with @mentions. Mention `scripts/stale-report.py --post` for a weekly cron. |
| D | Terminal | `git log -1` now also shows an `Impact:` trailer. |

## 中文版分镜（约 2:00，`scripts/reset-demo.sh zh` 后录制）

| # | 画面 | 操作 / 旁白 |
|---|------|------|
| 1 | 标题 → kb-ops 频道 | "企业知识库最大的问题不是没写，而是写了没人改。KB Steward 把更正变成受控的知识变更：AI 做影响分析和多文件修订，人负责审批和审计。" |
| 2 | **newhire** | `@kb-answerer 出差餐补每天多少钱？` → 中文回答 ¥100，附出处、负责人、复核日期和 ⚠️ 过期标记。 |
| 3 | **finance-lead** | 在帖子里回复 `这个过时了，从 11 月起餐补已经是每天 ¥120，超过 ¥300 才需要开发票。`（不 @ 任何人）→ KB-1：两个文件、影响分析（¥100 → ¥120，¥200 → ¥300）、diff、新回答预览。 |
| 4 | **support-lead** | `批准 KB-1` → ⛔ 无权审批。"只有文档负责人能批。" |
| 5 | **finance-lead** | `批准 KB-1` → ✅ 合并、重建索引、记入决策日志；answerer 在同帖重新回答 ¥120。 |
| 6 | **newhire** → **support-lead** | `@kb-answerer 客户申请退款的期限是多久？` → 冲突（30 天 vs 7 天）移交 → `30 天是对的，话术写错了。` → KB-2 → `批准 KB-2`。 |
| 7 | 任意窗口 | `@kb-answerer 公司的停车补贴怎么申请？` → 记入"KB knowledge gaps"；`@kb-curator 复核报告` → 按负责人列出超期文档。 |
| 8 | 终端 | `git log -1` 展示 `Approved-by` / `Requested-by` / `Impact` trailer。"每一次知识变更都有人提、有人批、有记录、可回滚。" |
