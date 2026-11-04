"""Knowledge base: a git repo of Markdown files, a BM25 section index, and grounded answering."""
from __future__ import annotations

import datetime as dt
import math
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import llm

STALE_DAYS = int(os.environ.get("KB_STALE_DAYS", "365"))
TOKEN = re.compile(r"[a-z0-9$€%]+")
STOP = set("the a an of to and or in on for is are be with what how do does i my we our it this that when at by as from can".split())


def git(repo: Path | str, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def toks(s: str) -> list[str]:
    return [t for t in TOKEN.findall(s.lower()) if t not in STOP]


@dataclass
class Section:
    path: str
    heading: str
    text: str
    meta: dict = field(default_factory=dict)

    @property
    def cite(self) -> str:
        return f"{self.path} § {self.heading}"


def parse_front_matter(raw: str) -> tuple[dict, str]:
    meta: dict = {}
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for line in raw[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            raw = raw[end + 4:]
    return meta, raw


class KBIndex:
    """BM25 over Markdown sections. Rebuilt whenever the repo HEAD moves."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.head = ""
        self.sections: list[Section] = []
        self.files: dict[str, dict] = {}

    def refresh(self) -> bool:
        head = git(self.root, "rev-parse", "--short", "HEAD", check=False) or "worktree"
        if head == self.head and self.sections:
            return False
        self.build()
        self.head = head
        return True

    def build(self) -> None:
        self.sections, self.files = [], {}
        for p in sorted(self.root.rglob("*.md")):
            if ".git" in p.parts or p.name.lower() == "readme.md":
                continue
            rel = str(p.relative_to(self.root))
            meta, body = parse_front_matter(p.read_text())
            self.files[rel] = meta
            title = meta.get("title", rel)
            parts = re.split(r"^## +(.+)$", body, flags=re.M)
            intro = parts[0].strip()
            if intro:
                self.sections.append(Section(rel, title, intro, meta))
            for i in range(1, len(parts), 2):
                self.sections.append(Section(rel, parts[i].strip(), parts[i + 1].strip(), meta))
        self._docs = [Counter(toks(s.heading + " " + s.text + " " + s.meta.get("title", ""))) for s in self.sections]
        self._len = [sum(d.values()) for d in self._docs]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1
        df = Counter(t for d in self._docs for t in d)
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, q: str, k: int = 6) -> list[tuple[float, Section]]:
        self.refresh()
        qt = toks(q)
        scored = []
        for s, d, ln in zip(self.sections, self._docs, self._len):
            sc = 0.0
            for t in qt:
                if t in d:
                    f = d[t]
                    sc += self._idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * ln / self._avg))
            if sc > 0:
                scored.append((sc, s))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def staleness(self, path: str) -> int | None:
        d = self.files.get(path, {}).get("last_reviewed")
        try:
            return (dt.date.today() - dt.date.fromisoformat(d)).days
        except (TypeError, ValueError):
            return None


ANSWER_SYS = """You are kb-answerer, the knowledge-base agent in a team chat room.
Answer ONLY from the numbered sources. Never invent policy. Be brief (max 4 sentences).
Return JSON:
{"answer": str,                       # plain answer for the asker, cite like [1]
 "used": [int],                       # source numbers you relied on
 "confidence": "high"|"medium"|"low", # low if sources don't really answer it
 "conflict": null | {"summary": str, "sources": [int]}  # set when two sources disagree on a fact
}
If sources disagree, do NOT pick one: say they conflict and give both values."""


async def answer(index: KBIndex, question: str, history: str = "") -> dict:
    hits = index.search(question, k=6)
    if not hits:
        return {"answer": "I couldn't find anything in the knowledge base about that.",
                "used": [], "confidence": "low", "conflict": None, "sources": []}
    srcs = [s for _, s in hits]
    ctx = "\n\n".join(
        f"[{i+1}] {s.cite} (owner: {s.meta.get('owner','?')}, last reviewed {s.meta.get('last_reviewed','?')})\n{s.text}"
        for i, s in enumerate(srcs))
    user = (f"Recent room context:\n{history}\n\n" if history else "") + f"Sources:\n{ctx}\n\nQuestion: {question}"
    out = await llm.chat_json([{"role": "system", "content": ANSWER_SYS}, {"role": "user", "content": user}])
    out["sources"] = srcs
    return out


def format_answer(index: KBIndex, out: dict) -> str:
    srcs: list[Section] = out.get("sources", [])
    used = [i for i in out.get("used", []) if isinstance(i, int) and 1 <= i <= len(srcs)] or ([1] if srcs else [])
    lines = [out.get("answer", "").strip()]
    if used:
        lines.append("")
        for i in used:
            s = srcs[i - 1]
            age = index.staleness(s.path)
            flag = f" ⚠️ last reviewed {age} days ago" if age is not None and age > STALE_DAYS else ""
            lines.append(f"> [{i}] `{s.cite}` · owner **{s.meta.get('owner','?')}** · reviewed {s.meta.get('last_reviewed','?')}{flag}")
    lines.append(f"\n_kb@{index.head} · confidence: {out.get('confidence','?')}_")
    return "\n".join(lines)
