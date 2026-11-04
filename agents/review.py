"""Synchronous review state, intent parsing and authenticated-front-end queue."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import uuid
from pathlib import Path

import common
import kb_core
from ledger import locked_state


def state(write=False):
    return locked_state(common.STATE / "reviews.json", {"session_id": str(uuid.uuid4()), "rows": []}, write)


def due_docs(index, days=kb_core.STALE_DAYS):
    return kb_core.stale_docs(index, days)


def select_due(docs, rows, cap=5):
    busy = {r["path"] for r in rows if r["status"] in ("open", "update")}
    return sorted((d for d in docs if d["path"] not in busy),
                  key=lambda d: -(d["age"] if d["age"] is not None else 10**6))[:max(0, cap)]


def owner_allowed(row, user, owners):
    return user.lower() in {u.lower() for u in owners.get(row["owner"], ())}


def _set_field(text, key, value):
    m = re.match(r"\A---\r?\n(.*?)^---[ \t]*\r?$", text, re.M | re.S)
    if not m:
        raise ValueError("document has no front matter")
    front = m.group(1)
    pattern = rf"^{key}:[^\r\n]*"
    front = re.sub(pattern, lambda _: f"{key}: {value}", front, flags=re.M) if re.search(pattern, front, re.M) else front + f"{key}: {value}\n"
    return text[:m.start(1)] + front + text[m.end(1):]


def bump_last_reviewed(text, today):
    return _set_field(text, "last_reviewed", today)


def set_owner(text, new):
    return _set_field(text, "owner", new)


def typed_intent(text, stage="review", owners=()):
    text = text.strip().strip(".!。！").lower()
    if stage == "reassign":
        return text if text in owners else None
    for choice, phrases in {"valid": ("still valid", "looks good", "仍然有效", "没问题"),
                            "update": ("needs update", "outdated", "需要更新"),
                            "notmine": ("not mine", "wrong owner", "不归我", "不是我的")}.items():
        if text in phrases:
            return choice
    return None


def enqueue(review_id, choice, user, via="dashboard", new_owner=None, ts=None, path=None):
    queue = common.STATE / "review_queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(queue) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with queue.open("a") as out:
            out.write(json.dumps(dict(review_id=review_id, choice=choice, user=user, via=via,
                                      new_owner=new_owner, path=path, ts=ts or dt.datetime.now().isoformat())) + "\n")
            out.flush(); os.fsync(out.fileno())


def drain():
    path = common.STATE / "review_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        processing = Path(str(path) + ".processing")
        lines = processing.read_text().splitlines() if processing.exists() else []
        if path.exists():
            os.replace(path, processing)
            lines += processing.read_text().splitlines()
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        processing.unlink(missing_ok=True)
        return rows


def clean_question(text):
    """First line of an LLM reply, unquoted; empty unless it reads like a question."""
    q = (text or "").strip().splitlines()[0].strip().strip('"\'“”`') if (text or "").strip() else ""
    return q if q.endswith(("?", "？")) and len(q) < 200 else ""
