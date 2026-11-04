#!/usr/bin/env python3
"""Stale sweep: list KB docs overdue for review, grouped by owner with @approver reminders.

  python scripts/stale-report.py                       # print (uses $KB_REPO, default /data/kb)
  python scripts/stale-report.py --repo kb-seed --lang zh --days 365
  python scripts/stale-report.py --json
  python scripts/stale-report.py --post                # kb-curator posts it to the room via Switch

Inside the stack:  docker compose exec kb-curator python /app/scripts/stale-report.py --post --lang zh
In chat, the same report:  "@kb-curator stale report"  /  "@kb-curator 复核报告".
--post goes through the Switch agent protocol (POST /agents/{id}/message) as kb-curator; it never calls the chat
platform directly. Schedule it with cron for a weekly reminder.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

import kb_core  # noqa: E402
from common import approvers  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("KB_REPO", "/data/kb"))
    ap.add_argument("--days", type=int, default=kb_core.STALE_DAYS)
    ap.add_argument("--lang", choices=["en", "zh"], default="en", help="language of the report text")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--post", action="store_true", help="post to the kb-ops room as kb-curator")
    a = ap.parse_args()
    idx = kb_core.KBIndex(a.repo)
    if a.json:
        print(json.dumps(kb_core.stale_docs(idx, a.days), ensure_ascii=False, indent=2))
        return
    text = kb_core.stale_report(idx, approvers(), a.lang, a.days)
    print(text)
    if a.post:
        asyncio.run(post(text))


async def post(text: str) -> None:
    from common import SWITCH_URL, room_id
    from switch_proto import SwitchAgent
    ag = SwitchAgent("kb-curator", "KB Curator",
                     "Drafts knowledge-base fixes as git branches and merges them after an owner approves.", SWITCH_URL)
    await ag.ensure_registered(os.environ["AGENT_REGISTRATION_TOKEN"])
    await ag.post(room_id(), text)
    print("posted to room via Switch", file=sys.stderr)


if __name__ == "__main__":
    main()
