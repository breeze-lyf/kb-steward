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
docker compose down && docker volume rm kb-steward_kbdata   # volume name = <project>_kbdata
docker compose up -d                                         # re-seeds KB + room
```
