"""Offline tests (no LLM, no Switch): python -m unittest discover -s tests"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["KB_REPO"] = str(TMP / "kb")
os.environ["KBS_STATE_DIR"] = str(TMP / "state")
os.environ["STATE_DIR"] = str(TMP / "state")
os.environ["APPROVERS"] = "finance:finance-lead;support:support-lead"
os.environ.setdefault("LLM_API_KEY", "test")
sys.path.insert(0, str(ROOT / "agents"))

import kb_core  # noqa: E402
import curator  # noqa: E402


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb = TMP / "kb"
        shutil.copytree(ROOT / "kb-seed", kb)
        sh("git", "init", "-q", "-b", "main", cwd=kb)
        sh("git", "add", "-A", cwd=kb)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "seed", cwd=kb)
        cls.kb = kb
        cls.idx = kb_core.KBIndex(kb)

    def test_lang(self):
        self.assertEqual(kb_core.lang_of("出差餐补每天多少？"), "zh")
        self.assertEqual(kb_core.lang_of("what's the meal allowance when travelling?"), "en")
        self.assertEqual(kb_core.lang_of("@kb-answerer 退款期限是多久"), "zh")

    def test_zh_search_hits_zh_docs(self):
        hits = self.idx.search("出差餐补每天多少钱", lang="zh")
        self.assertTrue(hits)
        self.assertTrue(hits[0][1].path.startswith("zh/finance/"))
        self.assertIn("¥100", hits[0][1].text)

    def test_en_search_stays_english(self):
        hits = self.idx.search("what's the meal allowance when travelling?", lang="en")
        self.assertTrue(all(not s.path.startswith("zh/") for _, s in hits))
        self.assertEqual(hits[0][1].path, "finance/expense-policy.md")

    def test_zh_conflict_docs_both_found(self):
        paths = {s.path for _, s in self.idx.search("客户申请退款的期限是多久", lang="zh")}
        self.assertIn("zh/support/refund-policy.md", paths)
        self.assertIn("zh/support/support-macros.md", paths)

    def test_hints(self):
        for msg in ["这个过时了，现在是每天 ¥120", "不对，应该是 30 天", "从 7 天改成 30 天", "其实是¥150", "已经更新为 ¥120 了"]:
            self.assertTrue(curator.ZH_HINT.search(msg), msg)
        self.assertTrue(curator.HINT.search("That's outdated: since July it's $65/day"))
        self.assertFalse(curator.ZH_HINT.search("出差有餐补吗"))

    def test_approve_regex(self):
        m = curator.APPROVE.search("批准 KB-3")
        self.assertEqual((curator.ZH_VERB[m.group(2)], m.group(3)), ("approve", "3"))
        m = curator.APPROVE.search("approve KB-1")
        self.assertEqual((m.group(1), m.group(3)), ("approve", "1"))
        m = curator.APPROVE.search("驳回 KB-2 数字不对")
        self.assertEqual((curator.ZH_VERB[m.group(2)], m.group(4)), ("reject", "数字不对"))

    def test_impact(self):
        kb = self.kb
        sh("git", "checkout", "-qb", "kb/T", cwd=kb)
        for f, old, new in [("zh/finance/travel-expense-policy.md", "**每天 ¥100**", "**每天 ¥120**"),
                            ("zh/finance/travel-expense-policy.md", "超过 **¥200**", "超过 **¥300**"),
                            ("zh/finance/travel-faq.md", "出差期间每天餐补 ¥100，超过 ¥200", "从 11 月起每天餐补 ¥120，超过 ¥300")]:
            p = kb / f
            p.write_text(p.read_text().replace(old, new).replace("last_reviewed: 2024-04-08", "last_reviewed: 2026-11-04"))
        sh("git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qam", "t", cwd=kb)
        imp = curator.impact(kb, "main", "kb/T", "zh")
        sh("git", "checkout", "-q", "main", cwd=kb)
        self.assertEqual(set(imp["files"]), {"zh/finance/travel-expense-policy.md", "zh/finance/travel-faq.md"})
        self.assertIn("差旅餐补", imp["files"]["zh/finance/travel-expense-policy.md"])
        self.assertIn("出差有餐补吗？", imp["files"]["zh/finance/travel-faq.md"])
        self.assertEqual(imp["changes"], ["¥100 → ¥120", "¥200 → ¥300"])
        self.assertIn("数值类", imp["risk"])
        self.assertTrue(any("影响分析" in l for l in curator.fmt_impact(imp, "zh")))

    def test_validate_new_doc(self):
        en = "---\ntitle: Parking\nowner: finance\nlast_reviewed: 2026-11-04\n---\n## Allowance\n$20 per day.\n"
        zh = en.replace("---\n##", "lang: zh\n---\n##")
        self.assertIsNone(curator.validate_new_doc("finance/parking.md", en, {"finance"}, "en"))
        self.assertIsNone(curator.validate_new_doc("zh/finance/parking.md", zh, {"finance"}, "zh"))
        for path, content, lang in [
            ("../x.md", en, "en"), ("/finance/x.md", en, "en"),
            ("finance/x.txt", en, "en"), ("finance/README.md", en, "en"),
            ("finance/x.md", en.replace("owner: finance\n", ""), "en"),
            ("finance/x.md", en.replace("owner: finance", "owner: unknown"), "en"),
            ("finance/x.md", en.replace("2026-11-04", "2026-02-30"), "en"),
            ("zh/finance/x.md", zh, "en"), ("finance/nested/x.md", en, "en")]:
            with self.subTest(path=path, content=content):
                self.assertIsNotNone(curator.validate_new_doc(path, content, {"finance"}, lang))

    def test_pick_owner(self):
        owners = {"finance": {"alice"}, "support": {"bob"}}
        self.assertEqual(curator.pick_owner("support", "alice", owners), "support")
        self.assertEqual(curator.pick_owner("unknown", "bob", owners), "support")
        self.assertEqual(curator.pick_owner(None, "eve", owners), "finance")

    def test_open_gap_for_thread(self):
        row = {"id": "G-2", "thread": "t", "status": "open"}
        gaps = {"rows": [{"thread": "t", "status": "answered by KB-1"}, row]}
        self.assertIs(curator.open_gap_for_thread(gaps, "t"), row)
        self.assertIsNone(curator.open_gap_for_thread(gaps, "other"))
        self.assertIsNone(curator.open_gap_for_thread(gaps, None))

    def test_workspace_create(self):
        curator.WORK.mkdir(parents=True, exist_ok=True)
        ws = curator.Workspace("CREATE", "en", owners={"finance"})
        content = "---\ntitle: Parking\nowner: finance\nlast_reviewed: 2026-11-04\n---\n## Allowance\n$20 per day.\n## Claims\nKeep receipts.\n"
        try:
            args = {"path": "finance/parking.md", "content": content}
            self.assertIn("refused:", ws.call("create_file", args))
            ws.mode = "gap"
            self.assertEqual(ws.call("create_file", args), "ok")
            self.assertIn("already exists", ws.call("create_file", args))
            self.assertIn("owner cannot change", ws.call("write_file", {
                **args, "content": content.replace("owner: finance", "owner: support")}))
            self.assertIn("refused:", ws.call("write_file", {**args, "path": "finance/absent.md"}))
            sh("git", "add", "-A", cwd=ws.dir)
            sh("git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "new doc", cwd=ws.dir)
            imp = curator.impact(ws.dir, "main", ws.branch)
            self.assertEqual(imp["files"][args["path"]], ["Allowance", "Claims"])
            self.assertEqual(imp["new_files"], [args["path"]])
            self.assertIn("(new)", "\n".join(curator.fmt_impact(imp, "en")))
        finally:
            kb_core.git(self.kb, "worktree", "remove", "--force", str(ws.dir))
            kb_core.git(self.kb, "branch", "-D", ws.branch)

    def test_format_answer_zh(self):
        hits = self.idx.search("出差餐补", lang="zh")
        out = {"answer": "每天 ¥100 [1]。", "used": [1], "confidence": "high", "sources": [s for _, s in hits], "lang": "zh"}
        txt = kb_core.format_answer(self.idx, out)
        self.assertIn("负责人", txt)
        self.assertIn("置信度: 高", txt)
        self.assertIn("天未复核", txt)

    def test_stale_report(self):
        rep = kb_core.stale_report(self.idx, {"finance": {"finance-lead"}}, "zh")
        self.assertIn("@finance-lead", rep)
        self.assertIn("zh/finance/travel-expense-policy.md", rep)
        rows = kb_core.stale_docs(self.idx)
        self.assertNotIn("support/support-macros.md", [r["path"] for r in rows])


if __name__ == "__main__":
    unittest.main()
