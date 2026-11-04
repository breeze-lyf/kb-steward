"""Shared, locked answer history; no model or network calls."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import common

FILE = None  # Tests may override this; otherwise resolve STATE at call time.


@contextmanager
def locked_state(path, default, write=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        rows = json.loads(path.read_text()) if path.exists() else default
        yield rows
        if write:
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
                    name = tmp.name
                    json.dump(rows, tmp, ensure_ascii=False, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(name, path)
            finally:
                if name and os.path.exists(name):
                    os.unlink(name)


def _state(write=False):
    return locked_state(Path(FILE) if FILE is not None else common.STATE / "answers.json", [], write)


def _intro(cite):
    if cite["section"] == cite["path"]:
        return True
    p = common.KB_REPO / cite["path"]
    if p.exists():
        raw = p.read_text()
        if raw.startswith("---\n"):
            for line in raw.split("\n---", 1)[0].splitlines():
                if line.startswith("title:"):
                    return cite["section"] == line.split(":", 1)[1].strip().strip('\"\'')
    return False


def record(entry) -> str | None:
    if not entry.get("cites"):
        return None
    with _state(True) as rows:
        aid = f"A-{max([int(r['id'].split('-')[1]) for r in rows] or [0]) + 1}"
        row = {**entry, "id": aid, "answer": entry.get("answer", "")[:500], "status": "active"}
        row["cites"] = [{**c, "intro": _intro(c)} for c in entry["cites"]]
        rows.append(row)
    return aid


def affected(changed: dict) -> list[dict]:
    with _state() as rows:
        # Latest answer wins even if its cited section no longer matches the change.
        latest = {}
        for row in rows:
            for c in row["cites"]:
                latest[row["thread"], row["asker"], c["path"]] = row["id"]
        hits = {}
        for row in reversed(rows):
            if row.get("status") == "corrected":
                continue
            for c in row["cites"]:
                if (latest[row["thread"], row["asker"], c["path"]] == row["id"]
                        and c["path"] in changed
                        and (c["section"] in changed[c["path"]] or c.get("intro") or _intro(c))):
                    hits.setdefault(row["thread"], row)
        return list(reversed(list(hits.values())))


def asker_for(thread, question):
    with _state() as rows:
        return next((r["asker"] for r in reversed(rows)
                     if r["thread"] == thread and r["question"] == question), None)


def summarize(entries) -> tuple[int, int]:
    return len(entries), len({e["thread"] for e in entries})


def mark_corrected(ids, pid):
    ids = set(ids)
    with _state(True) as rows:
        for row in rows:
            if row["id"] in ids:
                row.update(status="corrected", corrected_by=pid)
