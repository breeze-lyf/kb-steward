# KB Steward v3 — English storyboard (under 3:00)

Prepare separate `kb-ops` windows as **newhire**, **finance-lead** and **support-lead**,
plus the [dashboard](https://fde.yufeng.fun/kb/). From the repository root run
`scripts/demo-start.sh en` (supported choices: `en|zh|both`). This closes review cards,
prepares the demo repository in GitHub mode, resets KB and room state, and waits for both agents.
Use demo data only. Speed up model waits in the edit; show actual results, not promised metrics.

| Time | Action | Show / narration |
| --- | --- | --- |
| 0:00–0:15 | newhire: `@kb-answerer what is the meal allowance on a business trip?` | $50/day, citations, owner and stale review date. "Answers show their sources and freshness." |
| 0:15–0:30 | newhire: `@kb-answerer is there a fitness subsidy, and how do I claim it?` | `G-1` in **KB knowledge gaps**. "Unknown becomes a gap, not a guess." |
| 0:30–1:05 | finance-lead replies in the gap thread: `Yes: up to $50 a month for gym membership, claim it in the expense tool with the receipt.` | KB-1 adds a document or section, with Impact and answer preview. "The owner fills the hole in chat." |
| 1:05–1:15 | finance-lead: `approve KB-1` | Merge, gap closure, @newhire and cited re-check. "Nothing ships without owner approval." |
| 1:15–1:50 | finance-lead, new thread: `@kb-curator the meal allowance is outdated: since July it is $65 a day, receipts needed above $25` | KB-2 changes policy and FAQ; Impact: **1 past answer in 1 thread will be corrected**. |
| 1:50–2:05 | finance-lead: `approve KB-2` | Return to the first thread: "@newhire correction: earlier I told you $50; KB-2 corrected it to $65." |
| 2:05–2:30 | `@kb-curator stale report`; finance-lead clicks **Still valid** on a support document | Still valid / Needs update / Not mine card; unauthorized click refused and card re-issued. |
| 2:30–2:40 | support-lead clicks **Still valid** on the replacement card | Review commit with `Reviewed-by`; only the owner's approvers count. Typed `still valid` is the fallback. |
| 2:40–2:58 | Reload dashboard; show decision history | Actual coverage, stale rate, conflicts, gaps, top questions, owner to-dos, retro-correction count and git trailers. Review actions require login and owner authorization. |

For a GitHub-mode recording (`KB_SOURCE=github`), also open the
[demo docs repo](https://github.com/breeze-lyf/kb-steward-demo-docs).
Owner approval opens a PR and posts its link to chat: show Impact and trailers, then merge on GitHub.
Polling (no webhook) detects the merge before index refresh, gap closure and retro corrections.
Shorten waits or merge KB-1 off-camera to stay under three minutes; distinguish this from the live local-merge demo.

PR mode was verified end-to-end against real GitHub from a development box (PR #1 in the demo docs repo: https://github.com/breeze-lyf/kb-steward-demo-docs/pull/1); the live instance runs local-merge mode.

Keep the thread visible; use example personas and hide credentials. If a draft stalls, cut on
"Drafting" and resume at the actual result. See [README](README.md) for setup and implementation details.
