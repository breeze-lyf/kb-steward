"""Offline dashboard, authentication and queue boundary checks."""
import asyncio
import datetime as dt
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'agents'))
import common
import dashboard as d
import review


class Cookies(unittest.TestCase):
    def test_sign_verify_expiry_tamper(self):
        token = d.sign_cookie(b'key', 'alice', now=100)
        self.assertEqual(d.verify_cookie(b'key', token, now=101)['user'], 'alice')
        self.assertIsNone(d.verify_cookie(b'key', token, now=28900))
        self.assertIsNone(d.verify_cookie(b'wrong', token, now=101))
        self.assertIsNone(d.verify_cookie(b'key', token[:-1] + ('0' if token[-1] != '0' else '1'), now=101))
        for bad in ('', 'a.b.c', 'bad', 'x.y'):
            self.assertIsNone(d.verify_cookie(b'key', bad, now=101))

    def test_links(self):
        self.assertEqual(d.link('/kb/', 'doc', path='a b&c.md'), '/kb/doc?path=a+b%26c.md')
        self.assertEqual(d.link('', 'login'), '/login')
        self.assertEqual(d.link('/nested/kb', ''), '/nested/kb/')

    def test_secret(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            state = Path(tmp)
            key = d.load_secret(state)
            self.assertEqual(key, d.load_secret(state))
            self.assertEqual((state / 'dashboard-secret').stat().st_mode & 0o777, 0o600)


class DashboardFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.state = Path(self.tmp.name) / 'state'
        self.repo.mkdir(); self.state.mkdir()
        self.env = patch.dict(os.environ, {'APPROVERS': 'finance:alice;support:bob', 'REVIEW_DAYS': '365'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.statepatch = patch.object(common, 'STATE', self.state)
        self.statepatch.start(); self.addCleanup(self.statepatch.stop)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.write_doc('old.md', '2020-01-01', '<script>alert("x")</script>')
        self.write_doc('fresh.md', dt.date.today().isoformat(), 'Fresh')
        self.git('add', '.'); self.git('commit', '-qm', 'Seed', '-m', 'Reviewed-by: alice\nDecision: still-valid')
        self.write('answers.json', [dict(id='A-1', question=' Meal allowance? ', when='2026-01-01', cites=[{'path': 'old.md'}], status='corrected'),
                                    dict(id='A-2', question='meal  ALLOWANCE!', when='2026-02-01', cites=[{'path': 'fresh.md'}]),
                                    dict(id='A-3', question='Unknown?', when='2026-03-01', cites=[])])
        self.write('gaps.json', {'rows': [dict(id='G-1', question='<img src=x>', status='open', owner='finance'), dict(id='G-2', status='answered by KB-1')]})
        self.write('proposals.json', {'KB-1': {'id': 'KB-1', 'owners': ['finance'], 'status': 'merged'}, 'KB-2': {'id': 'KB-2', 'owners': ['finance'], 'status': 'open'}})
        self.write('conflicts.json', {'rows': [{'status': 'open'}, {'status': 'closed'}]})
        self.write('decisions.json', {'rows': [{'id': 'KB-1', 'when': '2026-01-01', 'by': 'alice', 'commit': self.git('rev-parse', 'HEAD'), 'title': 'Retro-corrected: 1 answers in 1 threads', 'decision': 'merged'}]})
        self.write('demo-users.json', {'alice': 'top-secret'})
        self.app = d.Dashboard(self.repo, self.state, secret=b'key')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def write_doc(self, path, date, title):
        (self.repo / path).write_text(f'---\ntitle: {title}\nowner: finance\nlast_reviewed: {date}\n---\nIntro <unsafe>\n## Policy\nBody & text\n')

    def test_summary_and_refresh(self):
        before = sorted(p.name for p in self.state.iterdir())
        s = self.app.summary()
        self.assertEqual(s['docs'], 2)
        self.assertEqual(s['sections'], 4)
        self.assertEqual(s['coverage'], 0.5)
        self.assertEqual(s['stale_rate'], 0.5)
        self.assertEqual(s['open_conflicts'], 2)
        self.assertEqual(s['open_gaps'], 1)
        self.assertEqual(s['retro_corrections'], 1)
        self.assertEqual(s['top_questions'][0]['count'], 2)
        self.assertEqual(s['top_questions'][0]['last_asked'], '2026-02-01')
        self.assertEqual(s['top_questions'][0]['cited_docs'], ['fresh.md', 'old.md'])
        self.assertEqual(s['recent_decisions'][0]['trailers']['Reviewed-by'], 'alice')
        self.assertEqual(s['owner_todos'][0]['pending_proposals'], ['KB-2'])
        self.assertEqual(before, sorted(p.name for p in self.state.iterdir()))
        self.write_doc('new.md', dt.date.today().isoformat(), 'New')
        self.git('add', '.'); self.git('commit', '-qm', 'Add doc')
        self.assertEqual(self.app.summary()['docs'], 3)

    def test_action_authorization_and_queue(self):
        for choice in ('valid', 'update', 'notmine'):
            with self.assertRaises(PermissionError):
                self.app.act('bob', 'old.md', choice, 'support')
        self.assertFalse((self.state / 'review_queue.jsonl').exists())
        with self.assertRaises(ValueError):
            self.app.act('alice', '../outside', 'valid')
        with self.assertRaises(ValueError):
            self.app.act('alice', 'old.md', 'notmine', 'finance')
        self.app.act('alice', 'old.md', 'valid')
        queued = self.app.summary()['queue'][0]
        self.assertEqual(queued['status'], 'pending')
        item = review.drain()[0]
        self.assertEqual(item['path'], 'old.md')
        self.assertEqual(item['user'], 'alice')
        self.assertEqual(item['via'], 'dashboard')
        self.assertIsNone(item['review_id'])
        self.assertFalse((self.state / 'reviews.json').exists())
        self.write('reviews.json', {'rows': [dict(id='R-1', path='old.md', owner='finance', status='open')]})
        self.app.act('alice', 'old.md', 'notmine', 'support')
        self.assertEqual(review.drain()[0]['review_id'], 'R-1')

    def test_escaping_and_credentials(self):
        session = {'user': '<alice>', 'csrf': '"<token>'}
        page = self.app.overview(session)
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img src=x>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('/kb/act', page)
        self.assertNotIn('top-secret', page + json.dumps(self.app.summary()))
        detail = self.app.document('old.md', session)
        self.assertIn('&lt;unsafe&gt;', detail)
        self.assertNotIn('<unsafe>', detail)
        self.assertIn('Reviewed-by', detail)
        self.assertTrue(d.authenticate(self.state, 'alice', 'top-secret'))
        self.assertFalse(d.authenticate(self.state, 'alice', 'wrong'))
        self.write('demo-users.json', {'users': {'alice': {'password': 'nested'}}})
        self.assertTrue(d.authenticate(self.state, 'alice', 'nested'))

    def test_curator_creates_row_and_commits_queued_reassignment(self):
        import curator
        async def run():
            with patch.object(curator, 'KB_REPO', self.repo), patch.object(curator.agent, 'reply', AsyncMock(return_value={'event_id': 'thread'})), patch.object(curator, 'issue_card', AsyncMock()), patch.object(curator, 'log_decision', AsyncMock()):
                self.app.act('alice', 'old.md', 'notmine', 'support')
                await curator.queued_review(review.drain()[0])
                self.assertIn('owner: support', (self.repo / 'old.md').read_text())
                self.assertIn('Reassigned-by: alice via dashboard', self.git('log', '-1', '--format=%B'))
                self.assertEqual(self.git('status', '--porcelain'), '')
                with review.state() as state:
                    self.assertEqual(state['rows'][0]['status'], 'reassigned')
                    self.assertEqual(state['rows'][1]['owner'], 'support')
                    self.assertEqual(state['rows'][1]['status'], 'open')
                # A previously authorized queued action must not survive an ownership change.
                self.write('reviews.json', {'session_id': 's', 'rows': [dict(id='R-9', path='old.md', owner='finance', status='open', stage='review', lang='en', thread='t')]})
                head = self.git('rev-parse', 'HEAD')
                await curator.handle_review({'id': 'R-9'}, 'update', 'alice', 'dashboard')
                self.assertEqual(self.git('rev-parse', 'HEAD'), head)
                with review.state() as state:
                    self.assertEqual(state['rows'][0]['status'], 'superseded')
        asyncio.run(run())

    def test_http_base_csrf_and_queue(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), d.Handler)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = http.client.HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(client.close)
        for path in ('/kb/', '/', '/kb/healthz', '/healthz', '/kb/api/summary', '/api/summary'):
            client.request('GET', path)
            response = client.getresponse()
            self.assertEqual(response.status, 200, path)
            response.read()
        token = d.sign_cookie(self.app.secret, 'alice')
        csrf = d.verify_cookie(self.app.secret, token)['csrf']
        for fields, status in [({'path': 'old.md', 'choice': 'valid'}, 403),
                               ({'path': 'old.md', 'choice': 'valid', 'csrf': csrf}, 200)]:
            client.request('POST', '/kb/act', urlencode(fields), {'Cookie': 'kb_session=' + token})
            response = client.getresponse()
            self.assertEqual(response.status, status)
            body = response.read().decode()
            if status == 200:
                self.assertIn('Queued — kb-curator will commit and post to kb-ops', body)
        client.request('GET', '/kb/login')
        response = client.getresponse()
        cookie = response.getheader('Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        self.assertIn('Path=/kb;', cookie)
        response.read()
        login_token = cookie.split(';')[0].split('=', 1)[1]
        client.request('POST', '/login', urlencode({'csrf': d.verify_cookie(self.app.secret, login_token)['csrf'], 'username': 'alice', 'password': 'top-secret'}), {'Cookie': 'kb_session=' + login_token})
        response = client.getresponse()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader('Location'), '/kb/')
        response.read()


if __name__ == '__main__':
    unittest.main()
