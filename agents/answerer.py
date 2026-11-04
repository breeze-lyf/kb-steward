"""kb-answerer: a self-built RAG agent that answers in the room with citations.

When the KB can't answer, or two documents disagree, it does not guess: it
hands the thread to kb-curator with a mention, which is a Switch hand-off.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

import kb_core
import ledger
from common import KB_REPO, SWITCH_URL, is_agent, room_id, sender_handle, strip_mentions
from switch_proto import SwitchAgent

log = logging.getLogger("kb-answerer")
agent = SwitchAgent("kb-answerer", "KB Answerer",
                    "Answers questions from the team knowledge base, with citations.", SWITCH_URL)
index = kb_core.KBIndex(KB_REPO)


async def on_message(data: dict, p: dict) -> None:
    if not p.get("addressed"):
        return
    if (p.get("sender_name") or "").lower() in ("kb-answerer", "kb answerer"):
        return
    q = strip_mentions(p.get("body", ""))
    if q.lower().startswith("re-check:"):
        q = q.split(":", 1)[1].strip()
    if not q:
        return
    thread = p.get("thread_id") or p.get("message_id")
    rid = data.get("room_id")
    log.info("question from %s: %s", p.get("sender_name"), q)
    await agent.typing(rid, True)
    changed = index.refresh()
    if changed:
        log.info("index rebuilt at %s (%d sections)", index.head, len(index.sections))
    out = await kb_core.answer(index, q)
    await agent.reply(kb_core.format_answer(index, out), thread_id=thread)
    await agent.typing(rid, False)
    if is_agent(p):
        return  # re-checks requested by the curator end here
    if not out.get("conflict") and out.get("confidence") != "low":
        try:
            ledger.record({"thread": thread, "asker": sender_handle(p), "question": q,
                           "lang": out.get("lang", "en"), "kb_commit": index.head,
                           "answer": out.get("answer", ""),
                           "cites": [{"path": s.path, "section": s.heading} for s in kb_core.used_sources(out)],
                           "when": dt.datetime.now().isoformat()})
        except Exception:
            log.exception("answer ledger write failed")
    srcs = out.get("sources", [])
    conflict = out.get("conflict")
    if conflict and isinstance(conflict, dict):
        cites = [srcs[i - 1].path for i in conflict.get("sources", []) if isinstance(i, int) and 0 < i <= len(srcs)]
        await agent.op("send_targeted_message", target_names=["kb-curator"], thread_id=thread,
                       body=f"hand-off · CONFLICT · {conflict.get('summary','')} · files: {', '.join(cites)} · question: {q}")
    elif out.get("confidence") == "low":
        await agent.op("send_targeted_message", target_names=["kb-curator"], thread_id=thread,
                       body=f"hand-off · GAP · the KB has no good answer · asked by: {p.get('sender_name') or '?'} · question: {q}")


async def main() -> None:
    await agent.ensure_registered(os.environ["AGENT_REGISTRATION_TOKEN"])
    index.refresh()
    log.info("index ready at %s (%d sections)", index.head, len(index.sections))
    await agent.run([room_id()], on_message)


if __name__ == "__main__":
    asyncio.run(main())
