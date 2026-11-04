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
TOKEN = re.compile(r"[a-z0-9$€¥%]+|[\u4e00-\u9fff]+")
CJK = re.compile(r"[\u4e00-\u9fff]")
STOP = set("the a an of to and or in on for is are be with what how do does i my we our it this that when at by as from can".split())
ZH_STOP = set("的了是吗呢吧啊在和与或我你他她们这那有就也都要请问么什怎")


def git(repo: Path | str, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def toks(s: str) -> list[str]:
    """Latin words as-is; Chinese runs as character bigrams (plus single chars for 1-char runs)."""
    out: list[str] = []
    for t in TOKEN.findall(s.lower()):
        if CJK.match(t):
            run = "".join(c for c in t if c not in ZH_STOP) if len(t) > 2 else t
            out += [run[i:i + 2] for i in range(len(run) - 1)] or ([run] if run else [])
        elif t not in STOP:
            out.append(t)
    return out


def lang_of(text: str) -> str:
    """'zh' if a meaningful share of the letters are CJK, else 'en'."""
    text = text or ""
    cjk = len(CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk and cjk / max(1, cjk + latin / 4) >= 0.3 else "en"


def doc_lang(meta: dict, path: str) -> str:
    return meta.get("lang") or ("zh" if path.startswith("zh/") else "en")


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

    def search(self, q: str, k: int = 6, lang: str | None = None) -> list[tuple[float, Section]]:
        """BM25. With `lang`, prefer sections in that language (falls back to all if none match)."""
        self.refresh()
        if lang:
            hits = self._search(q, k, lang)
            return hits or self._search(q, k, None)
        return self._search(q, k, None)

    def _search(self, q: str, k: int, lang: str | None) -> list[tuple[float, Section]]:
        qt = toks(q)
        scored = []
        for s, d, ln in zip(self.sections, self._docs, self._len):
            if lang and doc_lang(s.meta, s.path) != lang:
                continue
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
Write the "answer" (and conflict summary) in {language}, the language the employee asked in.
Return JSON:
{{"answer": str,                       # plain answer for the asker, cite like [1]
 "used": [int],                       # source numbers you relied on
 "confidence": "high"|"medium"|"low", # low if sources don't really answer it
 "conflict": null | {{"summary": str, "sources": [int]}}  # set when two sources disagree on a fact
}}
If sources disagree, do NOT pick one: say they conflict and give both values."""

LANG_NAME = {"zh": "Simplified Chinese (简体中文)", "en": "English"}
L = {
    "en": {"none": "I couldn't find anything in the knowledge base about that.", "owner": "owner",
           "reviewed": "reviewed", "stale": "⚠️ last reviewed {n} days ago", "conf": "confidence"},
    "zh": {"none": "知识库里没有找到相关内容。", "owner": "负责人",
           "reviewed": "复核于", "stale": "⚠️ 已 {n} 天未复核", "conf": "置信度"},
}
CONF_ZH = {"high": "高", "medium": "中", "low": "低"}


async def answer(index: KBIndex, question: str, history: str = "", lang: str | None = None) -> dict:
    lang = lang or lang_of(question)
    hits = index.search(question, k=6, lang=lang)
    if not hits:
        return {"answer": L[lang]["none"], "used": [], "confidence": "low", "conflict": None,
                "sources": [], "lang": lang}
    srcs = [s for _, s in hits]
    ctx = "\n\n".join(
        f"[{i+1}] {s.cite} (owner: {s.meta.get('owner','?')}, last reviewed {s.meta.get('last_reviewed','?')})\n{s.text}"
        for i, s in enumerate(srcs))
    user = (f"Recent room context:\n{history}\n\n" if history else "") + f"Sources:\n{ctx}\n\nQuestion: {question}"
    sys_prompt = ANSWER_SYS.format(language=LANG_NAME[lang])
    out = await llm.chat_json([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}])
    out["sources"] = srcs
    out["lang"] = lang
    return out


def format_answer(index: KBIndex, out: dict) -> str:
    lang = out.get("lang", "en")
    t = L.get(lang, L["en"])
    srcs: list[Section] = out.get("sources", [])
    used = [i for i in out.get("used", []) if isinstance(i, int) and 1 <= i <= len(srcs)] or ([1] if srcs else [])
    used = list(dict.fromkeys(used))
    remap = {old: new for new, old in enumerate(used, 1)}
    text = re.sub(r"\[(\d+)\]", lambda m: f"[{remap[int(m.group(1))]}]" if int(m.group(1)) in remap else "",
                  out.get("answer", "")).strip()
    lines = [text]
    if used:
        lines.append("")
        for i in used:
            s = srcs[i - 1]
            age = index.staleness(s.path)
            flag = " " + t["stale"].format(n=age) if age is not None and age > STALE_DAYS else ""
            lines.append(f"> [{remap[i]}] `{s.cite}` · {t['owner']} **{s.meta.get('owner','?')}** · {t['reviewed']} {s.meta.get('last_reviewed','?')}{flag}")
    conf = out.get("confidence", "?")
    if lang == "zh":
        conf = CONF_ZH.get(conf, conf)
    lines.append(f"\n_kb@{index.head} · {t['conf']}: {conf}_")
    return "\n".join(lines)


def stale_docs(index: KBIndex, days: int = STALE_DAYS) -> list[dict]:
    """Docs whose last_reviewed is older than `days` (or missing), most overdue first."""
    index.refresh()
    rows = []
    for path, meta in index.files.items():
        age = index.staleness(path)
        if age is None or age > days:
            rows.append({"path": path, "title": meta.get("title", path), "owner": meta.get("owner", "?"),
                         "last_reviewed": meta.get("last_reviewed", "?"), "age": age, "lang": doc_lang(meta, path)})
    rows.sort(key=lambda r: -(r["age"] if r["age"] is not None else 10**6))
    return rows


def stale_report(index: KBIndex, approvers: dict[str, set[str]], lang: str = "en", days: int = STALE_DAYS) -> str:
    rows = stale_docs(index, days)
    zh = lang == "zh"
    if not rows:
        return f"✅ 没有超过 {days} 天未复核的文档。" if zh else f"✅ No document is overdue for review (>{days} days)."
    head = (f"🧹 **复核提醒**：{len(rows)} 篇文档超过 {days} 天未复核（kb@{index.head}）" if zh else
            f"🧹 **Stale sweep**: {len(rows)} doc(s) not reviewed in over {days} days (kb@{index.head})")
    lines = [head, ""]
    by_owner: dict[str, list[dict]] = {}
    for r in rows:
        by_owner.setdefault(r["owner"], []).append(r)
    for owner, items in sorted(by_owner.items()):
        who = " ".join("@" + u for u in sorted(approvers.get(owner, set()))) or f"({owner})"
        lines.append(f"**{owner}** → {who}")
        for r in items:
            age = "?" if r["age"] is None else r["age"]
            lines.append(f"- `{r['path']}` {r['title']} · " + (f"复核于 {r['last_reviewed']}（{age} 天）" if zh else
                                                            f"reviewed {r['last_reviewed']} ({age} days)"))
    lines.append("")
    lines.append("请负责人确认内容仍然准确；有变化直接在本频道说明，kb-curator 会起草修订并走审批。" if zh else
                 "Owners: please confirm these are still accurate. If something changed, say so here and kb-curator will draft a reviewed fix.")
    return "\n".join(lines)
