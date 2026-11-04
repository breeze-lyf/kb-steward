"""Publishing adapters. See docs/doc-sources.md for the adapter contract."""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from common import KB_REPO, STATE
from kb_core import git

IDENTITY = ('-c', 'user.name=kb-curator', '-c', 'user.email=kb-curator@example.invalid')


class DocSource:
    name = 'abstract'

    def publish(self, prop, message, trailers) -> dict:
        raise NotImplementedError('See docs/doc-sources.md')

    def poll(self) -> list:
        return []

    def push_main(self, message):
        raise NotImplementedError('See docs/doc-sources.md')


class LocalGitSource(DocSource):
    name = 'local'

    def __init__(self, root=KB_REPO):
        self.root = Path(root)

    def publish(self, prop, message, trailers):
        git(self.root, *IDENTITY, 'merge', '--no-ff', '-q', prop['branch'], '-m', message, '-m', trailers)
        return dict(kind='merged', commit=git(self.root, 'rev-parse', 'HEAD'))

    def push_main(self, message):
        git(self.root, *IDENTITY, 'commit', '--allow-empty', '-q', '-a', '-m', message)


class GitHubPRSource(LocalGitSource):
    name = 'github'

    def __init__(self, repo, token, base='main', api='https://api.github.com', root=KB_REPO,
                 proposals=None, transport=None):
        super().__init__(root)
        self.repo, self.base, self.token = repo, base, token
        self.proposals = Path(proposals) if proposals else STATE / 'proposals.json'
        self.header = 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode()
        self.client = httpx.Client(base_url=api, timeout=30, transport=transport, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'Authorization': 'Bearer ' + token})

    def authgit(self, *args, root=None):
        # Never surface subprocess arguments or stderr: either may contain credentials.
        try:
            r = subprocess.run(['git', '-c', 'http.extraheader=' + self.header, '-C', str(root or self.root),
                                *args], capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise RuntimeError('GitHub git command failed')
            return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('GitHub git command failed') from None

    def request(self, method, path, **kwargs):
        try:
            r = self.client.request(method, '/repos/' + self.repo + path, **kwargs)
            r.raise_for_status()
            return r.json()
        except Exception:
            raise RuntimeError('GitHub API request failed') from None

    def clone(self):
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self.authgit('clone', '--branch', self.base, 'https://github.com/' + self.repo + '.git', str(self.root), root=self.root.parent)
        if self.base != 'main':
            git(self.root, 'branch', '-m', 'main')
        git(self.root, 'config', 'user.name', 'kb-curator')
        git(self.root, 'config', 'user.email', 'kb-curator@example.invalid')

    def publish(self, prop, message, trailers):
        head = 'kb/' + prop['id']
        existing = self.request('GET', '/pulls', params={'head': self.repo.split('/')[0] + ':' + head, 'state': 'open'})
        if existing:
            return dict(kind='pr', url=existing[0]['html_url'], number=existing[0]['number'])
        self.authgit('fetch', 'origin', self.base)
        # A detached temporary worktree leaves both main and the draft intact.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'publish'
            git(self.root, 'worktree', 'add', '--detach', str(work), 'origin/' + self.base)
            try:
                git(work, 'merge', '--squash', prop['branch'])
                git(work, *IDENTITY, 'commit', '-q', '-m', message, '-m', trailers)
                self.authgit('push', '--force-with-lease', 'origin', 'HEAD:refs/heads/' + head, root=work)
            finally:
                git(self.root, 'worktree', 'remove', '--force', str(work))
        n, m = prop.get('retro_counts', (0, 0))
        body = '\n\n'.join([prop.get('summary', ''), 'Files: ' + ', '.join(prop['files']),
            prop.get('impact_en', '**Impact**'), '```\n' + trailers + '\n```',
            'Approved in Switch room kb-ops by @' + prop['approved_by'],
            f'{n} past answers in {m} threads will be corrected after merge.',
            'Opened by kb-curator. Merge this PR to publish; KB Steward syncs within a minute.'])
        pr = self.request('POST', '/pulls', json=dict(title=prop['id'] + ': ' + prop['title'], head=head, base=self.base, body=body))
        return dict(kind='pr', url=pr['html_url'], number=pr['number'])

    def poll(self):
        events = []
        props = json.loads(self.proposals.read_text()) if self.proposals.exists() else {}
        for pid, p in props.items():
            if p.get('status') != 'pr_open':
                continue
            pr = self.request('GET', '/pulls/' + str(p['pr_number']))
            common = dict(pid=pid, number=pr['number'], url=pr['html_url'])
            if pr.get('merged'):
                events.append(dict(common, type='pr_merged', merged_by=(pr.get('merged_by') or {}).get('login', 'unknown'), sha=pr['merge_commit_sha']))
            elif pr['state'] == 'closed':
                events.append(dict(common, type='pr_closed'))
        self.authgit('fetch', 'origin', self.base)
        old, new = git(self.root, 'rev-parse', 'main'), git(self.root, 'rev-parse', 'origin/' + self.base)
        ancestor = subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor', old, new], capture_output=True).returncode == 0
        if old != new and ancestor:
            git(self.root, 'merge', '--ff-only', 'origin/' + self.base)
            events.append(dict(type='upstream', old=old, new=new, files=git(self.root, 'diff', '--name-only', old, new).splitlines()))
        return events

    def push_main(self, message):
        super().push_main(message)
        try:
            self.authgit('push', 'origin', 'main:refs/heads/' + self.base)
        except RuntimeError:
            self.authgit('fetch', 'origin', self.base)
            git(self.root, *IDENTITY, 'rebase', 'origin/' + self.base)
            self.authgit('push', 'origin', 'main:refs/heads/' + self.base)


class NotionSource(DocSource):
    name = 'notion'
    def poll(self):
        raise NotImplementedError('See docs/doc-sources.md')


class ConfluenceSource(NotionSource):
    name = 'confluence'


def make_source(root=KB_REPO):
    if os.environ.get('KB_SOURCE', 'local') == 'github':
        try:
            token = Path(os.environ.get('GITHUB_TOKEN_FILE', '/run/secrets/github_token')).read_text().strip()
        except OSError:
            token = ''
        token = token or os.environ.get('GITHUB_TOKEN', '').strip()
        repo = os.environ.get('GITHUB_REPO', '').strip()
        if token and repo:
            return GitHubPRSource(repo, token, os.environ.get('GITHUB_BASE', 'main'), root=root)
        logging.getLogger(__name__).warning('GitHub source requires GITHUB_REPO and a token; falling back to local')
    return LocalGitSource(root)
