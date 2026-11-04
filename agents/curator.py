"""kb-curator: a coding agent that maintains the knowledge base as a git repo.

It listens to the whole room. When a person corrects an answer (nobody has to
mention it), or kb-answerer hands off a conflict or a gap, it:
  1. works in its own git worktree with tools (search, read, write files),
  2. finds every document the correction touches, not just the one cited,
  3. commits a branch, previews the new answer against that branch,
  4. posts the diff and asks the document's owner to approve in the room,
  5. on `approve KB-n` from an authorised owner: merges, rebuilds the index,
     logs the decision to a Switch room document, and asks kb-answerer to
     re-answer the original question.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil
from pathlib import Path

import kb_core
import llm
from common import (KB_REPO, STATE, SWITCH_URL, approvers, is_agent, room_id,
                    sender_handle, strip_mentions)
from kb_core import git
from switch_proto import SwitchAgent

log = logging.getLogger("kb-curator")
agent = SwitchAgent("kb-curator", "KB Curator",
                    "Drafts knowledge-base fixes as git branches and merges them after an owner approves.",
                    SWITCH_URL)
WORK = STATE / "worktrees"
PROPS = STATE / "proposals.json"
DECISIONS = STATE / "decisions.json"
APPROVE = re.compile(r"\b(approve|reject)\s+KB-(\d+)\b\s*(.*)", re.I | re.S)
HINT = re.compile(r"\b(wrong|outdated|out of date|incorrect|actually|changed|no longer|not anymore|now (it'?s|is)|"
                  r"since|update|should be|it'?s \$?\d|correct (one|value) is|is \$?\d)", re.I)
lock = asyncio.Lock()
pending: dict[str, dict] = {}  # thread_id -> context of an open question to an owner


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p: Path, v) -> None:
    p.write_text(json.dumps(v, indent=2))


# ---------------------------------------------------------------- room helpers
async def thread_text(thread: str | None, limit: int = 60) -> str:
    """Pull the thread from Switch's room history (shared context, not our memory)."""
    try:
        ctx = await agent.op("read_context", limit=limit)
    except Exception as e:  # noqa: BLE001
        log.warning("read_context failed: %s", e)
        return ""
    rows = []
    for th in ctx.get("threads", []):
        entries = [th["root"], *th.get("replies", [])]
        if thread and not any(e.get("id") == thread for e in entries):
            continue
        rows += [f"{e.get('sender_name')}: {e.get('body')}" for e in entries]
    return "\n".join(rows[-25:])


async def log_decision(entry: dict) -> None:
    """Keep an auditable decision log as a Switch room document every agent can read."""
    d = load(DECISIONS, {"doc_id": None, "rows": []})
    d["rows"].append(entry)
    lines = ["# KB decision log (kb-ops)", "",
             "| When | Change | Decision | By | Commit |", "|---|---|---|---|---|"]
    lines += [f"| {r['when']} | {r['id']}: {r['title']} | {r['decision']} | {r['by']} | {r.get('commit','')} |"
              for r in d["rows"]]
    content = "\n".join(lines)
    try:
        if d["doc_id"]:
            await agent.op("update_room_document", document_id=d["doc_id"], content=content)
        else:
            res = await agent.op("create_room_document", name="KB decision log",
                                 description="Every knowledge-base change proposed in this room, and who approved it.",
                                 instructions="Read before answering questions about why a policy changed.",
                                 content=content)
            d["doc_id"] = (res or {}).get("id") or (res or {}).get("document_id")
    except Exception as e:  # noqa: BLE001
        log.warning("decision log update failed: %s", e)
    save(DECISIONS, d)


# ---------------------------------------------------------------- the coding loop
TOOLS = [
    {"type": "function", "function": {"name": "search_kb", "description": "BM25 search over KB sections. Search for the OLD value too, to find every copy of a fact.",
                                      "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "grep", "description": "Case-insensitive literal search across all KB files; returns path:line matches.",
                                      "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a KB file (path relative to repo root).",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Overwrite a KB file with full new content. Keep front matter; set last_reviewed to today.",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "done", "description": "Finish. Give a short imperative title and a 1-2 sentence summary.",
                                      "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}}, "required": ["title", "summary"]}}},
]

CURATOR_SYS = """You are kb-curator, a careful coding agent that maintains a company knowledge base: a git repo of Markdown files with YAML front matter (title, owner, last_reviewed).
A person in the team chat stated a correction or resolved a conflict. Your job:
- Find EVERY file that states the affected fact (use grep for the old value and search_kb with different phrasings). Facts are often duplicated in FAQs and macros.
- Edit minimally: change only what the correction covers. Do not invent anything the person did not say.
- Set last_reviewed to {today} in each file you change. Keep the rest of the front matter.
- Then call done. Today is {today}."""


class Workspace:
    def __init__(self, pid: str):
        self.dir = WORK / pid
        self.branch = f"kb/{pid}"
        if self.dir.exists():
            git(KB_REPO, "worktree", "remove", "--force", str(self.dir), check=False)
            shutil.rmtree(self.dir, ignore_errors=True)
        git(KB_REPO, "branch", "-D", self.branch, check=False)
        git(KB_REPO, "worktree", "add", "-q", "-b", self.branch, str(self.dir), "main")
        self.index = kb_core.KBIndex(self.dir)
        self.written: set[str] = set()

    def safe(self, path: str) -> Path:
        p = (self.dir / path).resolve()
        if not str(p).startswith(str(self.dir.resolve())) or not p.suffix == ".md":
            raise ValueError("path outside KB")
        return p

    def call(self, name: str, a: dict) -> str:
        if name == "search_kb":
            self.index.build()
            return "\n".join(f"{s.cite} (owner {s.meta.get('owner')}): {s.text[:200]}" for _, s in self.index.search(a["query"])) or "no hits"
        if name == "grep":
            out = []
            for p in self.dir.rglob("*.md"):
                if ".git" in p.parts:
                    continue
                for i, line in enumerate(p.read_text().splitlines(), 1):
                    if a["text"].lower() in line.lower():
                        out.append(f"{p.relative_to(self.dir)}:{i}: {line.strip()}")
            return "\n".join(out[:40]) or "no matches"
        if name == "read_file":
            return self.safe(a["path"]).read_text()
        if name == "write_file":
            p = self.safe(a["path"])
            if not p.exists():
                return "refused: only existing files can be edited in this demo"
            p.write_text(a["content"].rstrip() + "\n")
            self.written.add(a["path"])
            return "ok"
        return "unknown tool"


async def draft_fix(pid: str, instruction: str, context: str) -> dict | None:
    ws = Workspace(pid)
    today = dt.date.today().isoformat()
    msgs = [{"role": "system", "content": CURATOR_SYS.format(today=today)},
            {"role": "user", "content": f"Chat thread:\n{context}\n\nCorrection to apply:\n{instruction}"}]
    result = None
    for step in range(12):
        m = await llm.chat(msgs, tools=TOOLS, max_tokens=4096)
        msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
        calls = m.get("tool_calls") or []
        if not calls:
            msgs.append({"role": "user", "content": "Use the tools. Call done when finished."})
            continue
        for c in calls:
            fn = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            log.info("%s step %d: %s %s", pid, step, fn, {k: (v[:60] if isinstance(v, str) else v) for k, v in args.items()})
            if fn == "done":
                result = args
                out = "finished"
            else:
                try:
                    out = ws.call(fn, args)
                except Exception as e:  # noqa: BLE001
                    out = f"error: {e}"
            msgs.append({"role": "tool", "tool_call_id": c.get("id", fn), "content": out[:6000]})
        if result:
            break
    if not ws.written:
        git(KB_REPO, "worktree", "remove", "--force", str(ws.dir), check=False)
        git(KB_REPO, "branch", "-D", ws.branch, check=False)
        return None
    result = result or {"title": "Update knowledge base", "summary": instruction[:200]}
    git(ws.dir, "add", "-A")
    git(ws.dir, "-c", "user.name=kb-curator", "-c", "user.email=kb-curator@example.invalid",
        "commit", "-q", "-m", f"{pid}: {result['title']}", "-m", result.get("summary", ""))
    diff = git(KB_REPO, "diff", "main", ws.branch, "--", ".")
    stat = git(KB_REPO, "diff", "--shortstat", "main", ws.branch)
    owners = sorted({kb_core.parse_front_matter((ws.dir / f).read_text())[0].get("owner", "?") for f in ws.written})
    return {"id": pid, "branch": ws.branch, "dir": str(ws.dir), "files": sorted(ws.written), "owners": owners,
            "title": result["title"], "summary": result.get("summary", ""), "diff": diff, "stat": stat,
            "status": "open"}


def fmt_diff(diff: str, limit: int = 3500) -> str:
    keep = [l for l in diff.splitlines() if not l.startswith(("index ", "new file mode"))]
    s = "\n".join(keep)
    return s if len(s) <= limit else s[:limit] + "\n… (truncated)"


# ---------------------------------------------------------------- flows
async def propose(thread: str | None, instruction: str, question: str, requested_by: str) -> None:
    async with lock:
        props = load(PROPS, {})
        pid = f"KB-{len(props) + 1}"
        await agent.reply(f"On it: drafting **{pid}** from @{requested_by}'s correction. Checking every doc that states this fact…",
                          thread_id=thread)
        context = await thread_text(thread)
        prop = await draft_fix(pid, instruction, context)
        if not prop:
            await agent.reply("I couldn't find anything in the KB that this changes, so no edit was made.", thread_id=thread)
            return
        prop.update(thread=thread, question=question, requested_by=requested_by,
                    created=dt.datetime.now().isoformat(timespec="minutes"))
        preview = ""
        if question:
            try:
                out = await kb_core.answer(kb_core.KBIndex(prop["dir"]), question)
                preview = re.sub(r"\s*\[\d+\]", "", out.get("answer", ""))
            except Exception as e:  # noqa: BLE001
                log.warning("preview failed: %s", e)
        props[pid] = prop
        save(PROPS, props)
    appr = approvers()
    who = sorted({u for o in prop["owners"] for u in appr.get(o, set())})
    tag = " ".join(f"@{u}" for u in who) or "an owner"
    body = [f"📝 **{pid} · {prop['title']}** — `{prop['branch']}` ({prop['stat'].strip()})",
            prop["summary"], "",
            "Files: " + ", ".join(f"`{f}`" for f in prop["files"]) + f" · owner: **{', '.join(prop['owners'])}**",
            "```diff", fmt_diff(prop["diff"]), "```"]
    if preview:
        body += [f"🔎 **Preview** — on this branch, kb-answerer would now say: _{preview}_"]
    body += ["", f"{tag} please review. Reply `approve {pid}` to merge, or `reject {pid} <reason>`."]
    await agent.reply("\n".join(body), thread_id=thread)


async def decide(p: dict, verb: str, pid: str, reason: str, thread: str | None) -> None:
    user = sender_handle(p)
    async with lock:
        props = load(PROPS, {})
        prop = props.get(pid)
        if not prop or prop["status"] != "open":
            await agent.reply(f"{pid} isn't an open proposal.", thread_id=thread)
            return
        appr = approvers()
        allowed = set().union(*[appr.get(o, set()) for o in prop["owners"]]) if prop["owners"] else set()
        if user not in allowed:
            await agent.reply(
                f"⛔ @{user} can't {verb} {pid}: it changes **{', '.join(prop['owners'])}** documents, "
                f"which only {', '.join('@'+u for u in sorted(allowed)) or 'their owners'} can sign off. Nothing was merged.",
                thread_id=thread)
            return
        now = dt.datetime.now().isoformat(timespec="minutes")
        if verb.lower() == "reject":
            git(KB_REPO, "worktree", "remove", "--force", prop["dir"], check=False)
            git(KB_REPO, "branch", "-D", prop["branch"], check=False)
            prop["status"] = "rejected"
            save(PROPS, props)
            await agent.reply(f"🗑️ {pid} rejected by @{user}. Branch deleted; the KB is unchanged.", thread_id=thread)
            await log_decision({"when": now, "id": pid, "title": prop["title"], "decision": f"rejected: {reason[:80]}", "by": user})
            return
        git(KB_REPO, "-c", "user.name=kb-curator", "-c", "user.email=kb-curator@example.invalid",
            "merge", "--no-ff", "-q", prop["branch"], "-m", f"Merge {pid}: {prop['title']}",
            "-m", f"Approved-by: {user} in Switch room kb-ops\nRequested-by: {prop['requested_by']}")
        commit = git(KB_REPO, "rev-parse", "--short", "HEAD")
        git(KB_REPO, "worktree", "remove", "--force", prop["dir"], check=False)
        git(KB_REPO, "branch", "-d", prop["branch"], check=False)
        idx = kb_core.KBIndex(KB_REPO)
        idx.refresh()
        prop.update(status="merged", commit=commit, approved_by=user)
        save(PROPS, props)
    await agent.reply(f"✅ {pid} approved by @{user} and merged as `{commit}`. Index rebuilt: "
                      f"{len(idx.sections)} sections from {len(idx.files)} docs. Logged to the room's *KB decision log*.",
                      thread_id=thread)
    await log_decision({"when": now, "id": pid, "title": prop["title"], "decision": "approved + merged", "by": user, "commit": commit})
    push = os.environ.get("KB_PUSH_REMOTE")
    if push:
        git(KB_REPO, "push", "-q", push, "main", check=False)
    if prop.get("question"):
        await agent.op("send_targeted_message", target_names=["kb-answerer"], thread_id=thread,
                       body=f"re-check: {prop['question']}")


async def classify(body: str, context: str) -> dict:
    return await llm.chat_json([
        {"role": "system", "content":
         "You watch a team chat where an AI answers questions from a knowledge base. Decide if the LAST message "
         "is a person stating that some KB fact is wrong/outdated or stating the correct value (a correction), "
         "or answering a question about which of two conflicting facts is right. Return JSON "
         '{"is_correction": bool, "instruction": str (the correction as one precise sentence, with the new values), '
         '"question": str (the original employee question this relates to, or "")}'},
        {"role": "user", "content": f"Thread so far:\n{context}\n\nLAST message: {body}"}], max_tokens=400)


async def on_message(data: dict, p: dict) -> None:
    body = p.get("body") or ""
    if (p.get("sender_name") or "").lower() in ("kb-curator", "kb curator"):
        return
    thread = p.get("thread_id") or p.get("message_id")
    log.info("event from %s (addressed=%s, agent=%s): %s", p.get("sender_name"), p.get("addressed"), is_agent(p), body[:120])

    if is_agent(p):
        if p.get("addressed") and "hand-off" in body:
            kind = "CONFLICT" if "CONFLICT" in body else "GAP"
            q = body.split("question:", 1)[-1].strip()
            files = re.search(r"files: ([^·]+)", body)
            owners = set()
            idx = kb_core.KBIndex(KB_REPO)
            idx.refresh()
            for f in (files.group(1).split(",") if files else []):
                owners.add(idx.files.get(f.strip(), {}).get("owner", ""))
            who = sorted({u for o in owners for u in approvers().get(o, set())})
            tag = " ".join("@" + u for u in who) or "owners"
            pending[thread] = {"question": q}
            if kind == "CONFLICT":
                await agent.reply(f"🧭 Two documents disagree, so I won't let the answer drift. {tag}: which value is correct? "
                                  f"Reply in this thread and I'll draft the fix for every affected doc.", thread_id=thread)
            else:
                await agent.reply(f"🕳️ Knowledge gap logged for: _{q}_. Anyone who knows: reply in this thread and I'll draft a doc update.",
                                  thread_id=thread)
        return

    m = APPROVE.search(body)
    if m:
        await decide(p, m.group(1).lower(), f"KB-{m.group(2)}", m.group(3), thread)
        return

    if not (p.get("addressed") or HINT.search(body) or thread in pending):
        return
    context = await thread_text(thread)
    verdict = await classify(strip_mentions(body), context)
    log.info("classify: %s", verdict)
    if not verdict.get("is_correction"):
        if p.get("addressed"):
            await agent.reply("I maintain the KB. Tell me what's wrong or what changed and I'll draft a reviewed fix.", thread_id=thread)
        return
    q = verdict.get("question") or pending.get(thread, {}).get("question", "")
    pending.pop(thread, None)
    await propose(thread, verdict.get("instruction") or body, q, sender_handle(p))


async def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    git(KB_REPO, "worktree", "prune", check=False)
    await agent.ensure_registered(os.environ["AGENT_REGISTRATION_TOKEN"])
    await agent.run([room_id()], on_message)


if __name__ == "__main__":
    asyncio.run(main())
