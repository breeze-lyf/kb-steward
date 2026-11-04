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
import ledger
import llm
import review
from common import (KB_REPO, STATE, SWITCH_URL, approvers, is_agent, room_id,
                    sender_handle, strip_mentions)
from kb_core import git
from switch_proto import SwitchAgent, parse_answered_by

log = logging.getLogger("kb-curator")
agent = SwitchAgent("kb-curator", "KB Curator",
                    "Drafts knowledge-base fixes as git branches and merges them after an owner approves.",
                    SWITCH_URL)
WORK = STATE / "worktrees"
PROPS = STATE / "proposals.json"
DECISIONS = STATE / "decisions.json"
GAPS = STATE / "gaps.json"
APPROVE = re.compile(r"(?:\b(approve|reject)|(批准|通过|同意|驳回|拒绝))\s*KB-(\d+)\b\s*(.*)", re.I | re.S)
ZH_VERB = {"批准": "approve", "通过": "approve", "同意": "approve", "驳回": "reject", "拒绝": "reject"}
HINT = re.compile(r"\b(wrong|outdated|out of date|incorrect|actually|changed|no longer|not anymore|now (it'?s|is)|"
                  r"since|update|should be|it'?s \$?\d|correct (one|value) is|is \$?\d)", re.I)
ZH_HINT = re.compile(r"(过时|过期了|不对|错了|有误|改成|改为|应该是|其实是|已经是|不是了|更新为|更正|纠正|调整为|上调|下调|"
                     r"从.{0,12}(改|调|变)|现在是|现在(要|只要|需要)|[¥￥$]\s*\d|\d+\s*(元|块|天))")
STALE_CMD = re.compile(r"\b(stale (report|sweep|docs?)|review report)\b|复核报告|过期(文档|报告)|超期文档", re.I)
T = {
    "en": {
        'impact_retro': '{n} past answer(s) in {m} thread(s) will be corrected',
        'retro_done': '📣 Sent {n} correction(s) to past answers in {m} thread(s).',
        'retro_numeric': '@{asker} correction: earlier I told you {old}; {pid} corrected it to {new}.', 'and': ' and ',
        'retro_updated': '@{asker} correction to my earlier answer: the knowledge base changed ({pid}). Updated answer: {new_answer}',
        'retro_retry': '@{asker} correction to my earlier answer: the knowledge base changed ({pid}). Please re-ask your question for an updated answer.',
        "new": "(new)",
        "gap_drafting": "On it: @{who} answered gap {gid}. Drafting **{pid}** for the {owner} docs…",
        "gap_closed": "@{asker} your question _{q}_ is now in the knowledge base ({pid}, `{files}`), approved by @{user}.",
        "drafting": "On it: drafting **{pid}** from @{who}'s correction. Checking every doc that states this fact…",
        "nochange": "I couldn't find anything in the KB that this changes, so no edit was made.",
        "files": "Files", "owner": "owner",
        "preview": "🔎 **Preview** — on this branch, kb-answerer would now say: _{p}_",
        "review": "{tag} please review. Reply `approve {pid}` to merge, or `reject {pid} <reason>`.",
        "notopen": "{pid} isn't an open proposal.",
        "denied": "⛔ @{user} can't {verb} {pid}: it changes **{owners}** documents, which only {allowed} can sign off. Nothing was merged.",
        "rejected": "🗑️ {pid} rejected by @{user}. Branch deleted; the KB is unchanged.",
        "merged": "✅ {pid} approved by @{user} and merged as `{commit}`. Index rebuilt: {n} sections from {d} docs. Logged to the room's *KB decision log*.",
        "conflict": "🧭 Two documents disagree, so I won't let the answer drift. {tag}: which value is correct? Reply in this thread and I'll draft the fix for every affected doc.",
        "gap": "🕳️ Knowledge gap logged for: _{q}_ (room document *KB knowledge gaps*, {n} open). Anyone who knows: reply in this thread and I'll draft a doc update.",
        "help": "I maintain the KB. Tell me what's wrong or what changed and I'll draft a reviewed fix. Say `stale report` for docs overdue for review.",
        "impact": "**Impact**", "impact_files": "{n} file(s): {files}", "impact_secs": "Sections touched: {secs}",
        "risk": "Risk", "risk_num": "numeric policy value change ({chg}) — affects every answer that quotes it",
        "risk_text": "wording-only change, no numbers changed",
        "risk_new": "new policy content with values ({vals}); no existing value changed", "risk_multi": "; {n} docs must stay in sync",
    },
    "zh": {
        'impact_retro': '将更正 {m} 个帖子中的 {n} 条历史回答',
        'retro_done': '📣 已更正 {m} 个帖子中的 {n} 条历史回答。',
        'retro_numeric': '@{asker} 更正：之前我告诉你 {old}，{pid} 已更正为 {new}。', 'and': '、',
        'retro_updated': '@{asker} 更正之前的回答：知识库已更新（{pid}）。更新后的回答：{new_answer}',
        'retro_retry': '@{asker} 更正之前的回答：知识库已更新（{pid}）。请重新提问以获取最新回答。',
        "new": "（新建）",
        "gap_drafting": "收到：@{who} 回答了缺口 {gid}，正在为 {owner} 文档起草 **{pid}**…",
        "gap_closed": "@{asker} 你的问题 _{q}_ 已收录到知识库（{pid}，`{files}`），由 @{user} 批准。",
        "drafting": "收到：根据 @{who} 的更正起草 **{pid}**，正在排查所有写到这条信息的文档…",
        "nochange": "知识库里没有找到这条更正涉及的内容，未做任何修改。",
        "files": "文件", "owner": "负责人",
        "preview": "🔎 **预览**：在这个分支上，kb-answerer 会这样回答：_{p}_",
        "review": "{tag} 请审核。回复 `approve {pid}`（或“批准 {pid}”）合并，或 `reject {pid} <原因>` 驳回。",
        "notopen": "{pid} 不是待审批的提案。",
        "denied": "⛔ @{user} 无权{verb} {pid}：它修改的是 **{owners}** 的文档，只有 {allowed} 可以审批。未合并任何内容。",
        "rejected": "🗑️ {pid} 已被 @{user} 驳回，分支已删除，知识库保持不变。",
        "merged": "✅ {pid} 已由 @{user} 批准，合并为 `{commit}`。索引已重建：{d} 篇文档、{n} 个章节。已记入频道文档 *KB decision log*。",
        "conflict": "🧭 两份文档说法不一致，我不会随便选一个。{tag}：哪个才是对的？在本帖回复，我会把所有相关文档一起改好。",
        "gap": "🕳️ 已记录知识缺口：_{q}_（频道文档 *KB knowledge gaps*，当前 {n} 条未解决）。知道答案的同事请在本帖回复，我来起草文档更新。",
        "help": "我负责维护知识库。告诉我哪里不对或有什么变化，我会起草修订并请负责人审批。发送“复核报告”查看超期未复核的文档。",
        "impact": "**影响分析**", "impact_files": "{n} 个文件：{files}", "impact_secs": "涉及章节：{secs}",
        "risk": "风险", "risk_num": "数值类制度变更（{chg}），所有引用该数值的回答都会随之变化",
        "risk_text": "仅文字调整，无数值变化",
        "risk_new": "新增制度内容（{vals}），未改动已有数值", "risk_multi": "；{n} 篇文档需保持一致",
    },
}
T['en'].update({'review_due': '🧹 Review due: `{path}` ({title}) · owner **{owner}** · last reviewed {last_reviewed} ({age} days). {tags} please confirm with one click below, or reply `still valid` / `needs update` / `not mine` in this thread.', 'review_denied': "⛔ @{user} isn't an owner of `{path}` ({owner}: {allowed}). Card re-issued.", 'review_valid': '✅ {id}: `{path}` still valid · `{commit}`.', 'review_update': "@{user} thanks — reply in this thread with what changed and I'll draft a proposal for approval (or say `don't know` and I'll log it as a knowledge gap).", 'review_reassign': 'Who should own `{path}`?', 'review_reassigned': '✅ {id}: `{path}` reassigned · `{commit}`.', 'review_prompt': 'Is this document still accurate?', 'review_options': ['Still valid', 'Needs update', 'Not mine']})
T['zh'].update({'review_due': '🧹 复核提醒：`{path}`（{title}）· 负责人 **{owner}** · 上次复核 {last_reviewed}（{age} 天）。{tags} 请点击下方按钮，或在本帖回复 `仍然有效` / `需要更新` / `不归我`。', 'review_denied': '⛔ @{user} 不是 `{path}` 的负责人（{owner}：{allowed}）。已重新发卡。', 'review_valid': '✅ {id}：`{path}` 仍然有效 · `{commit}`。', 'review_update': '@{user} 谢谢，请在本帖说明变化，我会起草提案请负责人审批；也可回复“不知道”，我会记录知识缺口。', 'review_reassign': '`{path}` 应由谁负责？', 'review_reassigned': '✅ {id}：`{path}` 已转交 · `{commit}`。', 'review_prompt': '文档是否仍然准确？', 'review_options': ['仍然有效', '需要更新', '不归我']})
VERB_ZH = {"approve": "批准", "reject": "驳回"}
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


async def log_gap(question: str, asked_by: str, lang: str, thread: str | None) -> int:
    """Knowledge gaps view: every question the KB couldn't answer, as a room document."""
    g = load(GAPS, {"doc_id": None, "rows": []})
    if not open_gap_for_thread(g, thread):
        g["rows"].append({"when": dt.datetime.now().isoformat(timespec="minutes"), "question": question,
                          "asked_by": asked_by, "lang": lang, "status": "open", "thread": thread,
                          "id": f"G-{max([int(r.get('id', 'G-0').split('-')[-1]) for r in g['rows']] or [0]) + 1}"})
    await _sync_gaps(g)
    return sum(r["status"] == "open" for r in g["rows"])


def open_gap_for_thread(gaps: dict, thread) -> dict | None:
    return next((r for r in gaps.get("rows", []) if thread and r.get("thread") == thread
                 and r.get("status") == "open"), None)


def pick_owner(llm_owner, sender, approvers_map):
    if llm_owner in approvers_map:
        return llm_owner
    return next((o for o, users in approvers_map.items() if sender.lower() in users),
                next(iter(approvers_map), ""))


async def close_gap(question: str, pid: str) -> None:
    g = load(GAPS, {"doc_id": None, "rows": []})
    hit = False
    for r in g["rows"]:
        if r["status"] == "open" and question and r["question"] == question:
            r["status"], hit = f"fixed by {pid}", True
    if hit:
        await _sync_gaps(g)


async def _sync_gaps(g: dict) -> None:
    lines = ["# KB knowledge gaps (kb-ops)", "",
             "Questions the knowledge base could not answer. Owners: add or fix a doc, then reply in the thread.", "",
             "| ID | When | Question | Asked by | Thread | Status |", "|---|---|---|---|---|---|"]
    lines += [f"| {r.get('id', '—')} | {r['when']} | {r['question'].replace('|', '/')} | {r['asked_by']} | {r.get('thread', '—')} | {r['status']} |" for r in g["rows"]]
    content = "\n".join(lines)
    try:
        if g["doc_id"]:
            await agent.op("update_room_document", document_id=g["doc_id"], content=content)
        else:
            res = await agent.op("create_room_document", name="KB knowledge gaps",
                                 description="Questions asked in this room that the knowledge base could not answer.",
                                 instructions="Check before writing new docs: these are the questions people actually ask.",
                                 content=content)
            g["doc_id"] = (res or {}).get("id") or (res or {}).get("document_id")
    except Exception as e:  # noqa: BLE001
        log.warning("gaps doc update failed: %s", e)
    save(GAPS, g)


# ---------------------------------------------------------------- impact analysis
# policy-ish numbers only: a currency prefix or a unit suffix ("11 月" or a bare "2" is not a policy value)
NUM = re.compile(r"[$¥￥€]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|元|美元|天|个?工作日|days?|business days|hours?|小时)")


def num_kind(n: str) -> str:
    return n[0] if n[0] in "$¥￥€" else re.sub(r"[\d,.\s]", "", n).rstrip("s")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def section_at(lines: list[str], lineno: int, title: str) -> str:
    for i in range(min(lineno, len(lines)) - 1, -1, -1):
        if lines[i].startswith("## "):
            return lines[i][3:].strip()
    return title


def impact(repo_dir: Path, base: str, branch: str, lang: str = "en") -> dict:
    """Which files and sections a proposal touches, and the numeric values it changes."""
    diff = git(repo_dir, "diff", "-U0", base, branch, "--", ".")
    new_files = git(repo_dir, "diff", "--name-only", "--diff-filter=A", base, branch).splitlines()
    files: dict[str, list[str]] = {}
    changes: list[str] = []
    added: list[str] = []  # brand-new policy values (e.g. a new section from a gap answer)
    cur, new_lines, title = None, [], ""
    minus: list[str] = []
    plus: list[str] = []

    def flush():
        olds = [n.strip() for l in minus for n in NUM.findall(l)]
        news = [n.strip() for l in plus for n in NUM.findall(l)]
        o2 = [n for n in olds if n not in news]
        n2 = [n for n in news if n not in olds]
        for kind in dict.fromkeys(num_kind(n) for n in o2):
            for a, b in zip([n for n in o2 if num_kind(n) == kind], [n for n in n2 if num_kind(n) == kind]):
                c = f"{a} → {b}"
                if c not in changes:
                    changes.append(c)
        if not o2:
            added.extend(n for n in n2 if n not in added)
        minus.clear(); plus.clear()

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            flush()
            cur = line[6:]
            p = repo_dir / cur
            raw = p.read_text() if p.exists() else ""
            title = kb_core.parse_front_matter(raw)[0].get("title", cur)
            new_lines = raw.splitlines()
            files.setdefault(cur, [])
        elif line.startswith(("---", "diff ", "index ")):
            continue
        elif (m := HUNK.match(line)) and cur:
            flush()
            start, count = int(m.group(1)), int(m.group(2) or 1)
            ln = new_lines[start - 1] if 0 < start <= len(new_lines) else ""
            if ln.startswith("last_reviewed"):
                continue
            sec = section_at(new_lines, max(start, 1) + max(count, 1) - 1, title)
            if sec not in files[cur]:
                files[cur].append(sec)
        elif line.startswith("-") and not line.startswith("-last_reviewed"):
            minus.append(line[1:])
        elif line.startswith("+") and not line.startswith("+last_reviewed"):
            plus.append(line[1:])
    flush()
    for f in new_files:
        raw = (repo_dir / f).read_text()
        files[f] = re.findall(r"^## +(.+)$", raw, re.M) or [kb_core.parse_front_matter(raw)[0].get("title", f)]
    t = T[lang]
    risk = (t["risk_num"].format(chg=", ".join(changes)) if changes else
            t["risk_new"].format(vals=", ".join(added)) if added else t["risk_text"])
    if len(files) > 1:
        risk += t["risk_multi"].format(n=len(files))
    return {"files": files, "new_files": new_files, "changes": changes, "risk": risk}


def fmt_impact(imp: dict, lang: str, retro: tuple[int, int] | None = None) -> list[str]:
    t = T[lang]
    secs = [f"`{f}` § {s}" for f, ss in imp["files"].items() for s in ss]
    return [t["impact"],
            "- " + t["impact_files"].format(n=len(imp["files"]), files=", ".join(f"`{f}`" + (" " + t["new"] if f in imp.get("new_files", []) else "") for f in imp["files"])),
            "- " + t["impact_secs"].format(secs="; ".join(secs) or "—"),
            f"- {t['risk']}: {imp['risk']}"] + (["- " + t["impact_retro"].format(n=retro[0], m=retro[1])] if retro is not None else [])


def retro_pairs(entry: dict, changes: list[str]) -> list[tuple[str, str]]:
    return [(old, new) for c in changes if " → " in c
            for old, new in [c.split(" → ", 1)] if old in entry.get("answer", "")]


def retro_message(entry: dict, pid: str, changes: list[str], lang: str, new_answer=None) -> str:
    t = T[lang]
    pairs = retro_pairs(entry, changes)
    if pairs:
        return t["retro_numeric"].format(asker=entry["asker"], pid=pid, old=t["and"].join(o for o, _ in pairs),
                                         new=t["and"].join(n for _, n in pairs))
    return t["retro_updated" if new_answer else "retro_retry"].format(
        asker=entry["asker"], pid=pid, new_answer=new_answer)


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
- Keep each document in its own language (a Chinese doc stays Chinese, an English doc stays English), even if the correction was said in the other language.
- Set last_reviewed to {today} in each file you change. Keep the rest of the front matter.
- Then call done. Today is {today}."""


CREATE_TOOL = {"type": "function", "function": {"name": "create_file", "description": "Create a new Markdown owner document with title, owner and last_reviewed front matter.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}
GAP_SYS = """You maintain a company knowledge base. Learn ONLY the facts stated in the supplied human answer; do not invent details.
Search the existing documents of the selected owner. Add a new ## section ONLY if an existing document's title/scope clearly covers this topic (e.g. parking on a business trip fits a travel & expense policy; a fitness or phone benefit does NOT fit a travel policy). Otherwise create a NEW document with create_file.
Write documents in the question language: {language}. English paths: <owner>/<slug>.md; Chinese paths: zh/<owner>/<slug>.md.
New documents MUST start with front matter containing non-empty title, owner: <owner>, last_reviewed: {today}, and lang: zh for Chinese.
Keep existing owners unchanged. Only edit documents of the selected owner. Set last_reviewed to {today}. Call done when finished."""


def validate_new_doc(rel_path: str, content: str, owners: set[str], lang: str | None) -> str | None:
    if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
        return "path must be relative without traversal"
    if not re.fullmatch(r"(zh/)?[a-z0-9-]+/[a-z0-9][a-z0-9-]*\.md", rel_path):
        return "expected <owner>/<slug>.md or zh/<owner>/<slug>.md; README.md is forbidden"
    parts = rel_path.split("/")
    if parts[-2] not in owners:
        return "unknown owner directory"
    if lang and rel_path.startswith("zh/") != (lang == "zh"):
        return "path outside language scope"
    meta, _ = kb_core.parse_front_matter(content)
    if not content.startswith("---\n") or not meta.get("title"):
        return "front matter requires a title"
    if meta.get("owner") not in owners or meta.get("owner") != parts[-2]:
        return "front matter requires a known matching owner"
    try:
        date = meta.get("last_reviewed", "")
        if dt.date.fromisoformat(date).isoformat() != date:
            return "last_reviewed must be an ISO date"
    except ValueError:
        return "last_reviewed must be an ISO date"
    if lang == "zh" and meta.get("lang") != "zh":
        return "Chinese documents require lang: zh"
    if meta.get("lang", "zh" if rel_path.startswith("zh/") else "en") != ("zh" if rel_path.startswith("zh/") else "en"):
        return "front matter language conflicts with path"
    return None


class Workspace:
    def __init__(self, pid: str, lang: str | None = None, mode: str = "correction", owners: set[str] | None = None):
        self.mode, self.owners = mode, set(approvers()) if owners is None else owners
        self.lang = lang  # en and zh are separate example KBs: a correction only touches docs in its language
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
        if not p.is_relative_to(self.dir.resolve()) or ".git" in Path(path).parts or not p.suffix == ".md":
            raise ValueError("path outside KB")
        return p

    def in_scope(self, rel: str) -> bool:
        if not self.lang:
            return True
        p = self.dir / rel
        meta = kb_core.parse_front_matter(p.read_text())[0] if p.exists() else {}
        return kb_core.doc_lang(meta, rel) == self.lang

    def call(self, name: str, a: dict) -> str:
        if name == "search_kb":
            self.index.build()
            return "\n".join(f"{s.cite} (owner {s.meta.get('owner')}): {s.text[:200]}"
                             for _, s in self.index._search(a["query"], 6, self.lang)) or "no hits"
        if name == "grep":
            out = []
            for p in self.dir.rglob("*.md"):
                if ".git" in p.parts or not self.in_scope(str(p.relative_to(self.dir))):
                    continue
                for i, line in enumerate(p.read_text().splitlines(), 1):
                    if a["text"].lower() in line.lower():
                        out.append(f"{p.relative_to(self.dir)}:{i}: {line.strip()}")
            return "\n".join(out[:40]) or "no matches"
        if name == "read_file":
            return self.safe(a["path"]).read_text()
        if name == "create_file":
            if self.mode != "gap":
                return "refused: create_file is only available in gap mode"
            reason = validate_new_doc(a["path"], a["content"], self.owners, self.lang)
            if reason:
                return "refused: " + reason
            try:
                p = self.safe(a["path"])
            except ValueError:
                return "refused: path outside worktree"
            if p.exists():
                return "refused: file already exists"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a["content"].rstrip() + "\n")
            self.written.add(a["path"])
            return "ok"
        if name == "write_file":
            p = self.safe(a["path"])
            if not p.exists():
                return "refused: only existing files can be edited in this demo"
            if not self.in_scope(a["path"]):
                return f"refused: {a['path']} belongs to the other-language KB, which is maintained separately"
            if kb_core.parse_front_matter(p.read_text())[0].get("owner") != kb_core.parse_front_matter(a["content"])[0].get("owner"):
                return "refused: front matter owner cannot change"
            p.write_text(a["content"].rstrip() + "\n")
            self.written.add(a["path"])
            return "ok"
        return "unknown tool"


async def draft_fix(pid: str, instruction: str, context: str, lang: str = "en", scope: str | None = None, mode: str = "correction") -> dict | None:
    ws = Workspace(pid, scope, mode, set(approvers()))
    today = dt.date.today().isoformat()
    msgs = [{"role": "system", "content": (GAP_SYS if mode == "gap" else CURATOR_SYS).format(today=today, language=kb_core.LANG_NAME[scope or lang])},
            {"role": "user", "content": f"Chat thread:\n{context}\n\nCorrection to apply:\n{instruction}"}]
    result = None
    for step in range(12):
        m = await llm.chat(msgs, tools=TOOLS + ([CREATE_TOOL] if mode == "gap" else []), max_tokens=4096)
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
    imp = impact(ws.dir, "main", ws.branch, lang)
    return {"id": pid, "branch": ws.branch, "dir": str(ws.dir), "files": sorted(ws.written), "owners": owners,
            "title": result["title"], "summary": result.get("summary", ""), "diff": diff, "stat": stat,
            "status": "open", "impact": imp, "lang": lang}


def fmt_diff(diff: str, limit: int = 3500) -> str:
    keep = [l for l in diff.splitlines() if not l.startswith(("index ", "new file mode"))]
    s = "\n".join(keep)
    return s if len(s) <= limit else s[:limit] + "\n… (truncated)"


# ---------------------------------------------------------------- flows
async def propose(thread: str | None, instruction: str, question: str, requested_by: str, lang: str = "en",
                  scope: str | None = None, mode: str = "correction", gap: dict | None = None, owner: str = "") -> None:
    t = T[lang]
    async with lock:
        props = load(PROPS, {})
        pid = f"KB-{len(props) + 1}"
        await agent.reply(t["gap_drafting" if mode == "gap" else "drafting"].format(
            pid=pid, who=requested_by, gid=(gap or {}).get("id"), owner=owner), thread_id=thread)
        context = await thread_text(thread)
        prop = await draft_fix(pid, instruction, context, lang, scope or lang, mode)
        if not prop:
            await agent.reply(t["nochange"], thread_id=thread)
            return
        prop.update(thread=thread, question=question, requested_by=requested_by,
                    created=dt.datetime.now().isoformat(timespec="minutes"))
        if gap:
            prop.update(gap_id=gap["id"], gap_asker=gap["asked_by"], kind="gap")
        retro = ledger.affected(prop["impact"]["files"])
        prop["retro"] = [e["id"] for e in retro]
        prop["recheck_asker"] = (gap or {}).get("asked_by") or ledger.asker_for(thread, question)
        preview = ""
        if question:
            try:
                out = await kb_core.answer(kb_core.KBIndex(prop["dir"]), question, lang=kb_core.lang_of(question))
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
            f"{t['files']}: " + ", ".join(f"`{f}`" + (" " + t["new"] if f in prop["impact"].get("new_files", []) else "") for f in prop["files"]) + f" · {t['owner']}: **{', '.join(prop['owners'])}**",
            "", *fmt_impact(prop["impact"], lang, ledger.summarize(retro)), "",
            "```diff", fmt_diff(prop["diff"]), "```"]
    if preview:
        body += [t["preview"].format(p=preview)]
    body += ["", t["review"].format(tag=tag, pid=pid)]
    await agent.reply("\n".join(body), thread_id=thread)


async def decide(p: dict, verb: str, pid: str, reason: str, thread: str | None) -> None:
    user = sender_handle(p)
    async with lock:
        props = load(PROPS, {})
        prop = props.get(pid)
        lang = (prop or {}).get("lang") or kb_core.lang_of(p.get("body", ""))
        t = T[lang]
        if not prop or prop["status"] != "open":
            await agent.reply(t["notopen"].format(pid=pid), thread_id=thread)
            return
        appr = approvers()
        allowed = set().union(*[appr.get(o, set()) for o in prop["owners"]]) if prop["owners"] else set()
        if user not in allowed:
            await agent.reply(t["denied"].format(
                user=user, verb=VERB_ZH.get(verb, verb) if lang == "zh" else verb, pid=pid, owners=", ".join(prop["owners"]),
                allowed=", ".join("@" + u for u in sorted(allowed)) or "their owners"), thread_id=thread)
            return
        now = dt.datetime.now().isoformat(timespec="minutes")
        if verb.lower() == "reject":
            git(KB_REPO, "worktree", "remove", "--force", prop["dir"], check=False)
            git(KB_REPO, "branch", "-D", prop["branch"], check=False)
            prop["status"] = "rejected"
            save(PROPS, props)
            await agent.reply(t["rejected"].format(pid=pid, user=user), thread_id=thread)
            await log_decision({"when": now, "id": pid, "title": prop["title"], "decision": f"rejected: {reason[:80]}", "by": user})
            return
        retro = ledger.affected(prop["impact"]["files"])
        rn, rm = ledger.summarize(retro)
        git(KB_REPO, "-c", "user.name=kb-curator", "-c", "user.email=kb-curator@example.invalid",
            "merge", "--no-ff", "-q", prop["branch"], "-m", f"Merge {pid}: {prop['title']}",
            "-m", f"Retro-corrections: {rn} answers in {rm} threads",
            "-m", f"Approved-by: {user} in Switch room kb-ops\nRequested-by: {prop['requested_by']}\n"
                  f"Impact: {(prop.get('impact') or {}).get('risk', 'n/a')}")
        retro = ledger.affected(prop["impact"]["files"])
        commit = git(KB_REPO, "rev-parse", "--short", "HEAD")
        git(KB_REPO, "worktree", "remove", "--force", prop["dir"], check=False)
        git(KB_REPO, "branch", "-d", prop["branch"], check=False)
        idx = kb_core.KBIndex(KB_REPO)
        idx.refresh()
        prop.update(status="merged", commit=commit, approved_by=user)
        save(PROPS, props)
    await agent.reply(t["merged"].format(pid=pid, user=user, commit=commit, n=len(idx.sections), d=len(idx.files)),
                      thread_id=thread)
    sent, corrected = [], []
    for entry in retro:
        try:
            if True:  # also in the proposal thread: the asker gets an explicit @-mention correction
                new_answer = None
                if not retro_pairs(entry, prop["impact"]["changes"]):
                    try:
                        out = await kb_core.answer(idx, entry["question"], lang=entry["lang"])
                        if not out.get("conflict") and out.get("confidence") != "low":
                            new_answer = re.sub(r"\s*\[\d+\]", "", out.get("answer", "")).strip()
                    except Exception:
                        log.exception("retro answer failed")
                await agent.reply(retro_message(entry, pid, prop["impact"]["changes"], entry["lang"], new_answer),
                                  thread_id=entry["thread"])
                sent.append(entry)
            corrected.append(entry["id"])
        except Exception:
            log.exception("retro correction failed for %s", entry["id"])
    ledger.mark_corrected(corrected, pid)
    n, m = ledger.summarize(sent)
    if n:
        await agent.reply(t["retro_done"].format(n=n, m=m), thread_id=prop.get("thread") or thread)
    await log_decision({"when": now, "id": pid,
                        "title": prop["title"] + f"; Retro-corrected: {n} answers in {m} threads",
                        "decision": "approved + merged", "by": user, "commit": commit})
    push = os.environ.get("KB_PUSH_REMOTE")
    if push:
        git(KB_REPO, "push", "-q", push, "main", check=False)
    if prop.get("question"):
        if prop.get("kind") == "gap":
            g = load(GAPS, {"doc_id": None, "rows": []})
            for row in g["rows"]:
                if row.get("id") == prop["gap_id"]:
                    row["status"] = f"answered by {pid}"
            await _sync_gaps(g)
            await agent.reply(t["gap_closed"].format(asker=prop["gap_asker"], q=prop["question"],
                pid=pid, files=", ".join(prop["files"]), user=user), thread_id=prop["thread"])
        else:
            await close_gap(prop["question"], pid)
        await agent.op("send_targeted_message", target_names=["kb-answerer"], thread_id=prop.get("thread") or thread,
                       body=f"re-check: {prop['question']}")


async def classify_gap_answer(question: str, body: str, context: str, owners) -> dict:
    return await llm.chat_json([
        {"role": "system", "content": "Decide whether the LAST human message answers the knowledge gap question. "
         "Questions and chit-chat are not answers. Extract ONLY facts stated by that human in one or two precise "
         "sentences, in the same language as their message. Select an owner from the supplied keys and a short slug-able topic. "
         'Return JSON {"answers_gap": bool, "answer": str, "owner": str, "topic": str}.'},
        {"role": "user", "content": f"Owner keys: {list(owners)}\nQuestion: {question}\nThread: {context}\nLAST message: {body}"}], max_tokens=400)


async def classify(body: str, context: str) -> dict:
    return await llm.chat_json([
        {"role": "system", "content":
         "You watch a team chat where an AI answers questions from a knowledge base. Decide if the LAST message "
         "is a person stating that some KB fact is wrong/outdated or stating the correct value (a correction), "
         "or answering a question about which of two conflicting facts is right. Messages may be in English or Chinese "
         "(e.g. 'that's outdated, it's $65 now', '这个过时了，现在是每天 ¥120', '不对，应该是 30 天', '从 7 天改成 30 天'). "
         "Questions, chit-chat and approvals are NOT corrections. Write `instruction` in the same language as the LAST message. Return JSON "
         '{"is_correction": bool, "instruction": str (the correction as one precise sentence, with the new values), '
         '"question": str (the original employee question this relates to, or "")}'},
        {"role": "user", "content": f"Thread so far:\n{context}\n\nLAST message: {body}"}], max_tokens=400)


# ---------------------------------------------------------------- periodic reviews
async def issue_card(row):
    with review.state(True) as st:
        live = next(r for r in st["rows"] if r["id"] == row["id"])
        live["issue"] = live.get("issue", 0) + 1
        live["request_id"] = f"{live['id']}-{live['issue']}"
        live["card_pending"] = True
        row.update(live)
        session = st["session_id"]
    t = T[row.get("lang", "en")]
    options = ([(o, f"{o} ({' '.join('@' + u for u in sorted(users))})")
                for o, users in approvers().items() if o != row["owner"]] if row["stage"] == "reassign" else
               list(zip(("valid", "update", "notmine"), t["review_options"])))
    await agent.open_card(session, row["request_id"], f"{row['id']} · {row['path']}", row["path"], "review",
                          t["review_reassign"].format(**row) if row["stage"] == "reassign" else t["review_prompt"],
                          options, room_id(), row["thread"])
    row["card_pending"] = False
    persist_review(row)


def persist_review(row):
    with review.state(True) as st:
        next(r for r in st["rows"] if r["id"] == row["id"]).update(row)


async def sweep(only=None):
    async with lock:
        idx = kb_core.KBIndex(KB_REPO)
        docs = review.due_docs(idx)
        if only:
            meta = idx.files.get(only)
            docs = [dict(path=only, title=meta.get("title", only), owner=meta.get("owner", "?"),
                         last_reviewed=meta.get("last_reviewed", "?"), age=idx.staleness(only),
                         lang=kb_core.doc_lang(meta, only))] if meta else []
        with review.state() as st:
            docs = review.select_due(docs, st["rows"], int(os.environ.get("REVIEW_MAX_CARDS", "5")))
        for doc in docs:
            try:
                with review.state(True) as st:
                    rid = f"R-{max([int(r['id'][2:]) for r in st['rows']] or [0]) + 1}"
                    row = dict(doc, id=rid, thread=None, request_id=None, status="open", stage="review",
                               when=dt.datetime.now().isoformat(), by=None)
                    st["rows"].append(row)
                tags = " ".join('@' + u for u in sorted(approvers().get(row["owner"], ())))
                res = await agent.reply(T[row["lang"]]["review_due"].format(**row, tags=tags))
                row["thread"] = res["event_id"]
                persist_review(row)
                await issue_card(row)
            except Exception:
                row["status"] = "superseded"
                persist_review(row)
                log.exception("review issue failed")


async def handle_review(row, choice, user, via="switch-card", new_owner=None):
    resweep = None
    expected_request = row.get("request_id")
    async with lock:
        with review.state() as st:
            row = next((dict(r) for r in st["rows"] if r["id"] == row["id"]), None)
            session = st["session_id"]
        if not row or row["status"] != "open" or (expected_request and row.get("request_id") != expected_request):
            return
        user = user.lower()
        t = T[row.get("lang", "en")]
        async def close():
            if row.get("request_id"):
                try:
                    await agent.card_close(session, row["request_id"])
                except Exception:
                    log.exception("review close failed")
        if not review.owner_allowed(row, user, approvers()):
            await agent.reply(t["review_denied"].format(**row, user=user,
                allowed=", ".join('@' + u for u in sorted(approvers().get(row["owner"], ())))), row["thread"])
            await close()
            await issue_card(row)
            return
        path = (KB_REPO / row["path"]).resolve()
        if not path.is_relative_to(KB_REPO.resolve()) or not path.is_file():
            return
        if kb_core.parse_front_matter(path.read_text())[0].get("owner") != row["owner"]:
            row["status"] = "superseded"
            persist_review(row)
            return
        if row["stage"] == "reassign":
            target = new_owner or choice
            if target not in approvers() or target == row["owner"]:
                return
        elif choice not in ("valid", "update", "notmine"):
            return
        if choice == "notmine" and row["stage"] == "review":
            await close()
            row.update(stage="reassign")
            persist_review(row)
            if new_owner in approvers() and new_owner != row["owner"]:
                # Dashboard supplies the second-stage selection in the same request.
                target = new_owner
            else:
                await agent.reply(t["review_reassign"].format(**row), row["thread"])
                await issue_card(row)
                return
        if choice == "update" and row["stage"] == "review":
            row.update(status="update", by=user)
            persist_review(row)
            pending[row["thread"]] = {"question": f"Is {row['path']} still accurate?", "kind": "review", "path": row["path"]}
            await close()
            await agent.reply(t["review_update"].format(user=user), row["thread"])
            return
        path = (KB_REPO / row["path"]).resolve()
        if not path.is_relative_to(KB_REPO.resolve()) or path.suffix != ".md":
            raise ValueError("review outside KB")
        if git(KB_REPO, "branch", "--show-current") != "main" or git(KB_REPO, "status", "--porcelain"):
            raise RuntimeError("review requires clean KB main")
        raw = path.read_text()
        if kb_core.parse_front_matter(raw)[0].get("owner") != row["owner"]:
            row["status"] = "superseded"
            persist_review(row)
            await close()
            return
        if row["stage"] == "reassign":
            changed = review.set_owner(raw, target)
            message = f"Review {row['id']}: {row['path']} reassigned"
            trailers = f"Reassigned-by: {user} via {via}\nOwner-change: {row['owner']} -> {target}"
            status, resweep = "reassigned", row["path"]
        else:
            changed = review.bump_last_reviewed(raw, dt.date.today().isoformat())
            message = f"Review {row['id']}: {row['path']} still valid"
            trailers = f"Reviewed-by: {user} via {via} in Switch room kb-ops\nDecision: still-valid\nPrevious-review: {kb_core.parse_front_matter(raw)[0].get('last_reviewed', '?')}"
            status = "valid"
        path.write_text(changed)
        git(KB_REPO, "-c", "user.name=kb-curator", "-c", "user.email=kb-curator@example.invalid",
            "commit", "--allow-empty", "-q", "--only", "--author=kb-curator <kb-curator@example.invalid>",
            "-m", message, "-m", trailers, "--", row["path"])
        commit = git(KB_REPO, "rev-parse", "--short", "HEAD")
        row.update(status=status, by=user, when=dt.datetime.now().isoformat())
        persist_review(row)
        await close()
        await log_decision(dict(when=row["when"], id=row["id"], title=message, decision=status, by=user, commit=commit))
        await agent.reply(t["review_valid" if status == "valid" else "review_reassigned"].format(**row, commit=commit), row["thread"])
    if resweep:
        await sweep(resweep)


async def queued_review(item):
    if not item.get("review_id") and item.get("path"):
        await sweep(item["path"])
        with review.state() as current:
            live = next((r for r in reversed(current["rows"])
                         if r["path"] == item["path"] and r["status"] == "open"), None)
        if not live:
            return
        item["review_id"] = live["id"]
    await handle_review({"id": item["review_id"]}, item["choice"], item["user"], item["via"], item.get("new_owner"))


async def review_outcomes():
    while True:
        try:
            # Recover review follow-up context after process restarts.
            with review.state() as st:
                for r in st["rows"]:
                    if r["status"] == "update":
                        pending.setdefault(r["thread"], {"kind": "review", "path": r["path"], "question": f"Is {r['path']} still accurate?"})
            for r in st["rows"]:
                if r["status"] == "open" and r.get("card_pending") and r.get("thread"):
                    async with lock:
                        await issue_card(dict(r))
            for outcome in await agent.card_outcomes():
                try:
                    sid, req = outcome.get("sessionId"), outcome.get("requestId")
                    with review.state() as st:
                        row = next((dict(r) for r in st["rows"] if r.get("request_id") == req and st["session_id"] == sid), None)
                    if not row:
                        await agent.card_delivered(sid, req)
                        continue
                    if outcome.get("state") == "answered":
                        answers = outcome.get("answers") or []
                        choices = next((a.get("selected_option_ids", []) for a in answers if a.get("question_id") == "review"), [])
                        if choices:
                            await handle_review(row, choices[0], parse_answered_by(outcome.get("answeredBy", "")), via="switch-card")
                    elif row["status"] == "open":
                        expired = False
                        async with lock:
                            with review.state(True) as current:
                                live = next(r for r in current["rows"] if r["id"] == row["id"])
                                if live["status"] == "open" and live.get("request_id") == req:
                                    live["status"], expired = "superseded", True
                        if expired:
                            await sweep(row["path"])
                    await agent.card_delivered(sid, req)
                except Exception:
                    log.exception("review outcome failed")
        except Exception:
            log.exception("review polling failed")
        try:
            for item in review.drain():
                try:
                    await queued_review(item)
                except Exception:
                    log.exception("review queue item failed")
        except Exception:
            log.exception("review queue failed")
        await asyncio.sleep(3)


async def review_sweeps():
    try:
        hours = float(os.environ.get("REVIEW_SWEEP_HOURS", "24"))
        delay = float(os.environ.get("REVIEW_SWEEP_DELAY_S", str(hours * 3600)))
    except ValueError:
        log.exception("invalid review schedule")
        return
    if hours <= 0:
        return
    await asyncio.sleep(max(0, delay))
    while True:
        try:
            await sweep()
        except Exception:
            log.exception("review sweep failed")
        await asyncio.sleep(hours * 3600)


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
            lang = kb_core.lang_of(q)
            if kind == "CONFLICT":
                await agent.reply(T[lang]["conflict"].format(tag=tag), thread_id=thread)
            else:
                asker = (re.search(r"asked by: ([^·]+)", body) or [None, "?"])[1]
                n = await log_gap(q, asker.strip(), lang, thread)
                await agent.reply(T[lang]["gap"].format(q=q, n=n), thread_id=thread)
        return

    m = APPROVE.search(body)
    if m:
        verb = (m.group(1) or ZH_VERB.get(m.group(2), "approve")).lower()
        await decide(p, verb, f"KB-{m.group(3)}", m.group(4), thread)
        return

    if p.get("addressed") and STALE_CMD.search(body):
        idx = kb_core.KBIndex(KB_REPO)
        await agent.reply(kb_core.stale_report(idx, approvers(), kb_core.lang_of(strip_mentions(body))), thread_id=thread)
        await sweep()
        return

    command = re.search(r"\breview\s+(R-\d+)\s+(valid|update|notmine)\b", body, re.I)
    with review.state() as st:
        row = next((dict(r) for r in st["rows"] if r["status"] == "open" and
                    (r["id"] == command[1].upper() if command else r["thread"] == thread)), None)
    choice = command[2].lower() if command else review.typed_intent(strip_mentions(body), row["stage"], approvers()) if row else None
    if row and choice:
        await handle_review(row, choice, sender_handle(p), via="chat")
        return
    ctx = pending.get(thread, {})
    if ctx.get("kind") == "review" and re.search(r"don[’\']?t know|do not know|不知道|不清楚", body, re.I):
        await log_gap(ctx["question"], sender_handle(p), kb_core.lang_of(body), thread)
        pending.pop(thread, None)
        return

    gap = open_gap_for_thread(load(GAPS, {"rows": []}), thread)
    if gap and sender_handle(p) != gap["asked_by"].strip().lower():
        context = await thread_text(thread)
        appr = approvers()
        verdict = await classify_gap_answer(gap["question"], strip_mentions(body), context, appr)
        if verdict.get("answers_gap") is True:
            owner = pick_owner(verdict.get("owner"), sender_handle(p), appr)
            if owner:
                instruction = f"Owner: {owner}\nTopic: {verdict.get('topic', '')}\nQuestion: {gap['question']}\nHuman answer: {verdict.get('answer', '')}"
                await propose(thread, instruction, gap["question"], sender_handle(p), gap["lang"],
                              gap["lang"], mode="gap", gap=gap, owner=owner)
                return

    if not (p.get("addressed") or HINT.search(body) or ZH_HINT.search(body) or thread in pending):
        return
    context = await thread_text(thread)
    if pending.get(thread, {}).get("kind") == "review":
        context += "\nReview document: " + pending[thread]["path"]
    verdict = await classify(strip_mentions(body), context)
    log.info("classify: %s", verdict)
    if not verdict.get("is_correction"):
        if p.get("addressed"):
            await agent.reply(T[kb_core.lang_of(strip_mentions(body))]["help"], thread_id=thread)
        return
    q = verdict.get("question") or pending.get(thread, {}).get("question", "")
    review_context = pending.pop(thread, {})
    lang = kb_core.lang_of(strip_mentions(body))
    scope = ("zh" if review_context["path"].startswith("zh/") else "en") if review_context.get("kind") == "review" else kb_core.lang_of(q) if q else lang
    await propose(thread, verdict.get("instruction") or body, q, sender_handle(p), lang, scope)


async def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    git(KB_REPO, "worktree", "prune", check=False)
    await agent.ensure_registered(os.environ["AGENT_REGISTRATION_TOKEN"])
    tasks = [asyncio.create_task(review_sweeps()), asyncio.create_task(review_outcomes())]
    try:
        await agent.run([room_id()], on_message)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
