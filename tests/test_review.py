"""Offline review contracts and shared decision backend."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import common
import curator
import review
from switch_proto import SwitchAgent, parse_answered_by
import httpx


class Review(unittest.TestCase):
    def test_answered_by(self):
        for value in ("@Alice:localhost", "Alice", "@switch-mattermost-12345678-1234-1234-1234-123456789abc-Alice:localhost"):
            self.assertEqual(parse_answered_by(value), "alice")

    def test_front_matter(self):
        raw = '---\ntitle: "Travel"\nowner: finance\nlast_reviewed: 2020-01-01\nlang: zh\n---\nowner: body\n'
        self.assertEqual(review.bump_last_reviewed(raw, "2026-11-04"), raw.replace("2020-01-01", "2026-11-04"))
        missing = raw.replace("last_reviewed: 2020-01-01\n", "")
        self.assertEqual(review.bump_last_reviewed(missing, "2026-11-04"), missing.replace("lang: zh\n", "lang: zh\nlast_reviewed: 2026-11-04\n"))
        self.assertEqual(review.set_owner(raw, "support"), raw.replace("owner: finance", "owner: support"))
        with self.assertRaises(ValueError):
            review.set_owner("owner: body", "support")

    def test_intent(self):
        for choice, phrases in {"valid": ["Still valid", "looks good!", "仍然有效", "没问题"],
                                "update": ["needs update", "outdated", "需要更新"],
                                "notmine": ["not mine", "wrong owner", "不归我", "不是我的"]}.items():
            for phrase in phrases:
                self.assertEqual(review.typed_intent(phrase), choice)
        self.assertIsNone(review.typed_intent("not still valid"))
        self.assertEqual(review.typed_intent("support", "reassign", {"support"}), "support")
        self.assertIsNone(review.typed_intent("unknown", "reassign", {"support"}))

    def test_selection(self):
        docs = [dict(path=p, age=a) for p, a in [("a", 10), ("b", 90), ("c", None), ("d", 20)]]
        self.assertEqual([d["path"] for d in review.select_due(docs, [{"path": "b", "status": "open"}], 2)], ["c", "d"])
        self.assertEqual(review.select_due(docs, [], 0), [])
        self.assertEqual(len(review.select_due(docs, [{"path": "a", "status": "valid"}])), 4)

    def test_owner(self):
        self.assertTrue(review.owner_allowed({"owner": "finance"}, "ALICE", {"finance": {"alice"}}))
        self.assertFalse(review.owner_allowed({"owner": "finance"}, "bob", {"support": {"bob"}}))

    def test_queue(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(common, "STATE", Path(tmp)):
            review.enqueue("R-1", "valid", "alice")
            review.enqueue("R-2", "reassign", "bob", "web", "finance")
            rows = review.drain()
            self.assertEqual([r["review_id"] for r in rows], ["R-1", "R-2"])
            self.assertEqual(rows[1]["new_owner"], "finance")
            self.assertEqual(review.drain(), [])
            with review.state(True) as st:
                st["rows"].append({"id": "R-1"})
            with review.state() as st:
                self.assertEqual(st["rows"], [{"id": "R-1"}])

    def test_card_contract(self):
        async def run():
            requests = []
            def respond(req):
                requests.append(req)
                return httpx.Response(200, json=[])
            ag = curator.agent
            original = ag.http
            ag._started_sessions.clear()
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                ag.http = client
                try:
                    sid = "12345678-1234-1234-1234-123456789abc"
                    for rid in ("R-1-1", "R-1-2"):
                        await ag.open_card(sid, rid, "Title", "Detail", "review", "Prompt", [("valid", "Still valid")], "room", "sw_thread")
                    await ag.card_outcomes()
                    await ag.card_delivered(sid, "R-1-1")
                    await ag.card_close(sid, "R-1-1")
                finally:
                    ag.http = original
            self.assertEqual(len([r for r in requests if r.url.path.endswith('/started')]), 1)
            data = json.loads(requests[1].content)
            self.assertEqual(data["thread_id"], "sw_thread")
            self.assertEqual(data["questions"][0]["options"], [{"id": "valid", "label": "Still valid", "description": None}])
            self.assertFalse(data["questions"][0]["allow_custom_answer"])
        asyncio.run(run())

    def test_denial_and_stale_request(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp, patch.object(common, "STATE", Path(tmp)):
                row = dict(id="R-1", path="a.md", owner="finance", status="open", stage="review", thread="t", request_id="R-1-2", lang="en")
                with review.state(True) as st:
                    st["rows"].append(row)
                with patch.object(curator.agent, "reply", AsyncMock()) as reply, patch.object(curator.agent, "card_close", AsyncMock()), patch.object(curator, "issue_card", AsyncMock()) as issue:
                    await curator.handle_review({**row, "request_id": "R-1-1"}, "valid", "alice")
                    reply.assert_not_called()
                    await curator.handle_review(row, "valid", "intruder")
                    self.assertIn("isn't an owner", reply.call_args.args[0])
                    issue.assert_awaited_once()
                with review.state() as st:
                    self.assertEqual(st["rows"][0]["status"], "open")
        asyncio.run(run())

    def test_git_decisions(self):
        import subprocess
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                kb = root / 'kb'
                kb.mkdir()
                def git(*args):
                    return subprocess.check_output(['git', '-C', str(kb), *args], text=True).strip()
                git('init', '-q', '-b', 'main')
                raw = '---\ntitle: Travel\nowner: finance\nlast_reviewed: 2020-01-01\n---\n## Meals\n$50\n'
                (kb / 'travel.md').write_text(raw)
                git('add', '.')
                git('-c', 'user.name=test', '-c', 'user.email=test@x', 'commit', '-qm', 'seed')
                row = dict(id='R-1', path='travel.md', owner='finance', status='open', stage='review', thread='t', request_id='R-1-1', lang='en')
                with patch.object(common, 'STATE', root / 'state'), patch.object(curator, 'KB_REPO', kb), patch.object(curator, 'approvers', return_value={'finance': {'alice'}, 'support': {'bob'}}), patch.object(curator.agent, 'reply', AsyncMock()), patch.object(curator.agent, 'card_close', AsyncMock()) as close, patch.object(curator, 'log_decision', AsyncMock()) as decision, patch.object(curator, 'sweep', AsyncMock()) as sweep:
                    with review.state(True) as st:
                        st['rows'].append(row)
                    await curator.handle_review(row, 'valid', 'alice', 'chat')
                    self.assertIn('Reviewed-by: alice via chat in Switch room kb-ops', git('log', '-1', '--format=%B'))
                    self.assertEqual(git('log', '-1', '--format=%an'), 'kb-curator')
                    self.assertEqual((kb / 'travel.md').read_text().split('---', 2)[2], raw.split('---', 2)[2])
                    self.assertEqual(git('status', '--porcelain'), '')
                    close.assert_awaited_once()
                    decision.assert_awaited_once()
                    row.update(id='R-2', stage='reassign', request_id='R-2-1')
                    with review.state(True) as st:
                        st['rows'].append(row)
                    await curator.handle_review(row, 'support', 'alice', 'card')
                    self.assertIn('owner: support', (kb / 'travel.md').read_text())
                    self.assertIn('Owner-change: finance -> support', git('log', '-1', '--format=%B'))
                    sweep.assert_awaited_once_with('travel.md')
                    with review.state() as st:
                        self.assertEqual([r['status'] for r in st['rows']], ['valid', 'reassigned'])
        asyncio.run(run())
