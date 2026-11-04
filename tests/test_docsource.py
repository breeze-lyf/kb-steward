"""Offline publishing contract tests using a bare origin and mock HTTP."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agents'))
from docsource import GitHubPRSource, LocalGitSource, NotionSource, ConfluenceSource, make_source
from kb_core import git


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin, self.kb = self.root / 'origin.git', self.root / 'kb'
        self.kb.mkdir()
        git(self.origin.parent, 'init', '--bare', str(self.origin))
        git(self.kb, 'init', '-b', 'main')
        git(self.kb, 'config', 'user.name', 'test')
        git(self.kb, 'config', 'user.email', 'test@example.invalid')
        (self.kb / 'policy.md').write_text('old\n')
        git(self.kb, 'add', '.')
        git(self.kb, 'commit', '-m', 'seed')
        git(self.kb, 'remote', 'add', 'origin', str(self.origin))
        git(self.kb, 'push', '-u', 'origin', 'main')
        self.props = self.root / 'proposals.json'
        self.calls = []
        self.response = dict(number=1, html_url='https://github.com/o/r/pull/1', state='open')
        self.source = GitHubPRSource('o/r', 'offline-sensitive-value', root=self.kb, proposals=self.props,
                                     transport=httpx.MockTransport(self.api))
        self.addCleanup(self.source.client.close)

    def api(self, request):
        self.calls.append(request)
        return httpx.Response(200, json=[] if request.url.path.endswith('/pulls') and request.method == 'GET' else self.response)

    def test_publish(self):
        git(self.kb, 'checkout', '-b', 'draft')
        (self.kb / 'policy.md').write_text('new\n')
        git(self.kb, 'commit', '-am', 'draft one')
        (self.kb / 'second.md').write_text('second\n')
        git(self.kb, 'add', '.')
        git(self.kb, 'commit', '-m', 'draft two')
        git(self.kb, 'checkout', 'main')
        old = git(self.kb, 'rev-parse', 'HEAD')
        prop = dict(id='KB-1', title='Policy', branch='draft', summary='Summary', files=['policy.md'],
                    impact_en='**Impact**\n- Risk: wording', approved_by='alice', retro_counts=[2, 1])
        trailer = 'Approved-by: alice\nRequested-by: bob\nImpact: wording\nRetro-corrections: 2 answers in 1 threads\nProposal: KB-1'
        result = self.source.publish(prop, 'Merge KB-1: Policy', trailer)
        self.assertEqual(result['kind'], 'pr')
        self.assertEqual(git(self.kb, 'rev-parse', 'HEAD'), old)
        message = git(self.origin, 'show', '-s', '--format=%B', 'kb/KB-1')
        self.assertIn(trailer, message)
        self.assertEqual(git(self.origin, 'rev-list', '--count', 'main..kb/KB-1'), '1')
        body = json.loads(self.calls[-1].content)['body']
        for part in ['**Impact**', trailer, '2 past answers in 1 threads', '@alice']:
            self.assertIn(part, body)
        self.assertNotIn(self.source.token, (self.kb / '.git/config').read_text())
        self.assertNotIn(self.source.token, git(self.kb, 'remote', 'get-url', 'origin'))

    def test_poll(self):
        old = git(self.kb, 'rev-parse', 'HEAD')
        (self.kb / 'policy.md').write_text('upstream\n')
        git(self.kb, 'commit', '-am', 'upstream')
        new = git(self.kb, 'rev-parse', 'HEAD')
        git(self.kb, 'push', 'origin', 'main')
        git(self.kb, 'reset', '--hard', old)
        self.props.write_text(json.dumps({'KB-1': dict(status='pr_open', pr_number=1)}))
        self.response.update(merged=True, merged_by={'login': 'alice'}, merge_commit_sha=new)
        events = self.source.poll()
        self.assertEqual([e['type'] for e in events], ['pr_merged', 'upstream'])
        self.assertEqual(events[-1]['files'], ['policy.md'])
        self.assertEqual(git(self.kb, 'rev-parse', 'HEAD'), new)
        self.response.update(merged=False, state='closed')
        self.assertEqual(self.source.poll()[0]['type'], 'pr_closed')

    def test_safe_errors(self):
        failure = subprocess.CompletedProcess([], 1, '', self.source.token + self.source.header)
        with patch('docsource.subprocess.run', return_value=failure):
            with self.assertRaises(RuntimeError) as caught:
                self.source.authgit('fetch', 'origin')
        self.assertNotIn(self.source.token, str(caught.exception))
        with patch.object(self.source.client, 'request', side_effect=RuntimeError(self.source.token)):
            with self.assertRaises(RuntimeError) as caught:
                self.source.request('GET', '/pulls')
        self.assertNotIn(self.source.token, str(caught.exception))

    def test_factory_stubs(self):
        with patch.dict(os.environ, {'KB_SOURCE': 'github', 'GITHUB_REPO': '', 'GITHUB_TOKEN': '', 'GITHUB_TOKEN_FILE': '/missing'}):
            self.assertIsInstance(make_source(self.kb), LocalGitSource)
            self.assertEqual(make_source(self.kb).name, 'local')
        for cls in (NotionSource, ConfluenceSource):
            for operation in (lambda s: s.publish({}, '', ''), lambda s: s.poll(), lambda s: s.push_main('')):
                with self.assertRaisesRegex(NotImplementedError, 'docs/doc-sources.md'):
                    operation(cls())

    def test_review_push(self):
        (self.kb / 'policy.md').write_text('reviewed\n')
        self.source.push_main('Review R-1\n\nReviewed-by: alice')
        self.assertEqual(git(self.origin, 'rev-parse', 'main'), git(self.kb, 'rev-parse', 'HEAD'))
        self.assertIn('Reviewed-by: alice', git(self.origin, 'show', '-s', '--format=%B', 'main'))

    def test_reuse(self):
        self.source.client.close()
        self.source.client = httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=[self.response])))
        self.assertEqual(self.source.publish({'id': 'KB-1'}, '', '')['number'], 1)
