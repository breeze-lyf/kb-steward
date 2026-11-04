"""Read-only knowledge health views; authenticated actions go to curator's queue."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import unicodedata
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import common
import kb_core
import review


def esc(value):
    return html.escape(str(value), quote=True)


def read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def rows(value):
    return value if isinstance(value, list) else value.get('rows', [])


def base_path(value):
    return '/' + value.strip('/') if value.strip('/') else ''


def link(base, route='', **query):
    return base_path(base) + '/' + route.lstrip('/') + ('?' + urlencode(query) if query else '')


def sign_cookie(secret, user, now=None):
    payload = base64.urlsafe_b64encode(json.dumps({'user': user, 'exp': int(time.time() if now is None else now) + 28800,
                                                  'csrf': secrets.token_hex(24)}, separators=(',', ':')).encode()).decode()
    return payload + '.' + hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def verify_cookie(secret, token, now=None):
    try:
        payload, sig = token.split('.')
        if not hmac.compare_digest(sig, hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data['exp'] <= (time.time() if now is None else now) or not isinstance(data['user'], str) or not data['csrf']:
            return None
        return data
    except (ValueError, KeyError, TypeError):
        return None


def load_secret(state):
    if os.environ.get('DASH_SECRET'):
        return os.environ['DASH_SECRET'].encode()
    state.mkdir(parents=True, exist_ok=True)
    path = state / 'dashboard-secret'
    # Exclusive creation plus lock prevents readers observing a partially written key.
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+') as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        os.fchmod(out.fileno(), 0o600)
        key = out.read()
        if not key:
            key = secrets.token_hex(32)
            out.write(key)
            out.flush()
            os.fsync(out.fileno())
        return key.encode()


def authenticate(state, user, password):
    creds = read(state / 'demo-users.json', {})
    value = creds.get('users', creds).get(user)
    expected = value.get('password') if isinstance(value, dict) else value
    # mm_say's environment-backed demo identities use the same read-only credentials.
    if expected is None:
        for suffix in ('ADMIN_USER', 'USER'):
            if user == os.environ.get('MATTERMOST_' + suffix):
                expected = os.environ.get('MATTERMOST_' + ('ADMIN_PASSWORD' if suffix == 'ADMIN_USER' else 'USER_PASSWORD'))
    return isinstance(expected, str) and bool(expected) and hmac.compare_digest(expected.encode(), password.encode())


def trailers(message):
    # Git trailers are the final paragraph, allowing indented continuation lines.
    paragraph = message.strip().split('\n\n')[-1]
    out = {}
    key = None
    for line in paragraph.splitlines():
        m = re.match(r'^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$', line)
        if m:
            key = m[1]
            out[key] = (out[key] + '\n' if key in out else '') + m[2]
        elif key and line.startswith((' ', '\t')):
            out[key] += '\n' + line.strip()
        else:
            return {}
    return out


class Dashboard:
    def __init__(self, repo=None, state=None, base=None, secret=None):
        self.repo = Path(repo if repo is not None else common.KB_REPO)
        self.state = Path(state if state is not None else common.STATE)
        self.base = base_path(os.environ.get('DASH_BASE', '/kb') if base is None else base)
        self.secret = load_secret(self.state) if secret is None else secret
        self.index = kb_core.KBIndex(self.repo)
        self.head = None
        self.lock = threading.RLock()
        self.days = int(os.environ.get('REVIEW_DAYS', str(kb_core.STALE_DAYS)))

    def refresh(self):
        head = kb_core.git(self.repo, 'rev-parse', 'main', check=False) or kb_core.git(self.repo, 'rev-parse', 'HEAD', check=False)
        if head != self.head:
            self.index.build()
            self.head = head
            self.index.head = head

    def url(self, route='', **query):
        return link(self.base, route, **query)

    def history(self, path):
        hashes = kb_core.git(self.repo, 'log', '-5', '--format=%H', '--', path, check=False).splitlines()
        return [dict(commit=h[:10], message=kb_core.git(self.repo, 'show', '-s', '--format=%B', h, check=False),
                     trailers=trailers(kb_core.git(self.repo, 'show', '-s', '--format=%B', h, check=False))) for h in hashes]

    def summary(self):
        with self.lock:
            self.refresh()
            return self._summary()

    def _summary(self):
        answers = rows(read(self.state / 'answers.json', []))
        gaps = [g for g in rows(read(self.state / 'gaps.json', {})) if g.get('status', 'open') == 'open']
        reviews = rows(read(self.state / 'reviews.json', {}))
        decisions = sorted(rows(read(self.state / 'decisions.json', {})), key=lambda r: r.get('when', ''), reverse=True)
        props = read(self.state / 'proposals.json', {})
        props = list(props.values()) if isinstance(props, dict) and 'rows' not in props else rows(props)
        decided = {d.get('id') for d in decisions}
        pending = [p for p in props if p.get('status', 'open') == 'open' and p.get('id') not in decided]
        conflicts = rows(read(self.state / 'conflicts.json', {}))
        conflicts = [c for c in conflicts if c.get('status', 'open') in ('open', 'pending')]
        stale = []
        for path, meta in self.index.files.items():
            age = self.index.staleness(path)
            if age is None or age > self.days:
                r = next((r for r in reversed(reviews) if r.get('path') == path), {})
                stale.append(dict(path=path, title=meta.get('title', path), owner=meta.get('owner', '?'),
                                  last_reviewed=meta.get('last_reviewed', '?'), age=age,
                                  review_id=r.get('id'), status=r.get('status', 'Not opened'), stage=r.get('stage', 'review')))
        stale.sort(key=lambda r: -(r['age'] if r['age'] is not None else 10**6))
        groups = {}
        for a in answers:
            q = ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', a.get('question', '')).casefold()))
            g = groups.setdefault(q, dict(question=a.get('question', ''), normalized=q, count=0, last_asked='', cited_docs=[]))
            g['count'] += 1
            g['last_asked'] = max(g['last_asked'], a.get('when', ''))
            g['cited_docs'] = sorted(set(g['cited_docs']) | {c['path'] for c in a.get('cites', []) if c.get('path')})
        owners = common.approvers()
        todos = []
        for owner in sorted(set(owners) | {m.get('owner', '?') for m in self.index.files.values()} | {g.get('owner', '?') for g in gaps}):
            todos.append(dict(owner=owner, approvers=sorted(owners.get(owner, [])),
                              overdue_reviews=[r['path'] for r in stale if r['owner'] == owner],
                              open_reviews=[r.get('id') for r in reviews if r.get('owner') == owner and r.get('status') == 'open'],
                              open_gaps=[g.get('id') for g in gaps if g.get('owner') == owner],
                              pending_proposals=[p.get('id') for p in pending if owner in p.get('owners', [])]))
        for d in decisions:
            commit = d.get('commit', '')
            d['trailers'] = trailers(kb_core.git(self.repo, 'show', '-s', '--format=%B', commit, check=False)) if re.fullmatch(r'[0-9a-fA-F]{4,40}', commit) else {}
            d['kind'] = d.get('kind', d.get('decision', ''))
            d['approver'] = d.get('by', '')
            d['commit'] = commit[:10]
        corrected = sum(a.get('status') == 'corrected' for a in answers)
        logged = sum(int(m[1]) for d in decisions if (m := re.search(r'Retro-corrected:\s*(\d+)', d.get('title', ''))))
        numerator = sum(bool(a.get('cites')) for a in answers)
        total = len(answers) + len(gaps)
        queue = []
        for name in ('review_queue.jsonl.processing', 'review_queue.jsonl'):
            try:
                lines = (self.state / name).read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                r = next((r for r in reversed(reviews) if r.get('id') == item.get('review_id') or
                          (item.get('path') and r.get('path') == item['path'])), {})
                queue.append(dict(path=item.get('path') or r.get('path'), user=item.get('user'),
                                  choice=item.get('choice'), status=r.get('status') if r.get('status') not in (None, 'open') else 'pending'))
        return dict(docs=len(self.index.files), sections=len(self.index.sections), coverage=numerator / total if total else 0,
                    coverage_answered=numerator, coverage_total=total, stale_rate=len(stale) / len(self.index.files) if self.index.files else 0,
                    open_conflicts=len(pending) + len(conflicts), open_gaps=len(gaps), retro_corrections=max(corrected, logged),
                    open_reviews=sum(r.get('status') == 'open' for r in reviews), review_days=self.days,
                    top_questions=sorted(groups.values(), key=lambda g: (-g['count'], g['question'])), owner_todos=todos,
                    recent_decisions=decisions, gaps=gaps, stale_docs=stale, queue=queue,
                    review_rows=[{k: r.get(k) for k in ('id', 'path', 'status', 'by', 'stage')} for r in reviews])

    def act(self, user, path, choice, new_owner=None):
        with self.lock:
            self.refresh()
            meta = self.index.files.get(path)
            if not meta:
                raise ValueError('Document not found.')
            owners = common.approvers()
            if not review.owner_allowed(meta, user, owners):
                raise PermissionError('You are not an approver for this document’s owner.')
            if choice not in ('valid', 'update', 'notmine'):
                raise ValueError('Unknown review choice.')
            if choice == 'notmine' and (new_owner not in owners or new_owner == meta.get('owner')):
                raise ValueError('Select another configured owner.')
            current = next((r for r in reversed(rows(read(self.state / 'reviews.json', {})))
                            if r.get('path') == path and r.get('status') in ('open', 'update')), None)
            if current and current['status'] == 'update':
                raise ValueError('This document already needs update; continue in its kb-ops review thread.')
            # The queue helper resolves common.STATE, shared by all services.
            review.enqueue(current['id'] if current else None, choice, user, new_owner=new_owner, path=path)

    def page(self, title, body, session=None):
        identity = esc(session['user']) if session else f'<a href="{esc(self.url("login"))}">Sign in</a>'
        return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · KB Steward</title><style>
:root{{font-family:system-ui,sans-serif;color:#263444;background:#f5f6f8;--accent:#3267c8}}*{{box-sizing:border-box}}body{{margin:0}}header{{background:white;border-bottom:1px solid #dfe4eb;padding:20px max(5vw,20px);display:flex;justify-content:space-between}}main{{max-width:1280px;margin:auto;padding:32px 20px}}a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}h1{{font-size:30px;margin:0 0 8px}}h2{{font-size:19px}}p,.muted{{color:#637185}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px}}.card,section{{background:white;border:1px solid #dfe4eb;border-radius:12px;padding:20px;margin-bottom:18px}}.value{{display:block;font-size:30px;font-weight:650;color:#263444}}.label{{font-size:13px;color:#637185}}.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{text-align:left;padding:12px 10px;border-bottom:1px solid #edf0f4;vertical-align:top}}th{{font-size:12px;color:#637185}}button,input,select{{font:inherit;border:1px solid #cbd3df;border-radius:6px;padding:8px;background:white;max-width:100%}}button{{cursor:pointer;color:var(--accent)}}button:hover{{background:#edf3ff}}form.actions{{display:flex;gap:6px;flex-wrap:wrap;min-width:280px}}.notice{{padding:15px;border-radius:8px;background:#eaf1ff;color:#244e99;margin-bottom:20px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.65 ui-monospace,monospace}}svg{{vertical-align:middle;margin-right:8px}}label{{display:block;margin:15px 0}}small{{display:block;color:#637185;margin-top:8px}}@media(max-width:600px){{main{{padding:24px 12px}}.card,section{{padding:14px}}h1{{font-size:25px}}}}
</style><header><a href="{esc(self.url())}"><strong>KB Steward</strong> / Knowledge health</a><span>{identity}</span></header><main><h1>{esc(title)}</h1>{body}</main></html>'''

    def table(self, headers, data):
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + (''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>' for row in data) or f'<tr><td colspan="{len(headers)}">Nothing here yet.</td></tr>') + '</tbody></table></div>'

    def doclink(self, path):
        return f'<a href="{esc(self.url("doc", path=path))}">{esc(path)}</a>'

    def overview(self, session, flash=''):
        s = self.summary()
        body = '<p>A live view of your knowledge base and the work keeping it trustworthy.</p>'
        if flash:
            body += f'<div class="notice">{esc(flash)}</div>'
        body += '<div class="grid">'
        for label, value in [('Docs', s['docs']), ('Sections', s['sections']), ('Coverage', f"{s['coverage']:.0%}"),
                             ('Stale rate', f"{s['stale_rate']:.0%}"), ('Open conflicts', s['open_conflicts']),
                             ('Open gaps', s['open_gaps']), ('Retro-corrections sent', s['retro_corrections']), ('Open reviews', s['open_reviews'])]:
            chart = ''
            if label in ('Coverage', 'Stale rate'):
                ratio = s['coverage' if label == 'Coverage' else 'stale_rate']
                chart = f'<svg width="32" height="32" viewBox="0 0 36 36" role="img" aria-label="{esc(label)} {value}"><circle cx="18" cy="18" r="14" fill="none" stroke="#e7ecf3" stroke-width="5"/><circle cx="18" cy="18" r="14" fill="none" stroke="#3267c8" stroke-width="5" pathLength="100" stroke-dasharray="{ratio * 100} 100" transform="rotate(-90 18 18)"/></svg>'
            body += f'<div class="card"><span class="label">{label}</span><span class="value">{chart}{value}</span></div>'
        body += f'</div><p class="muted">Coverage = answered questions with sources / (all questions in answers ledger + open gaps) = {s["coverage_answered"]}/{s["coverage_total"]}. Stale rate = docs past {s["review_days"]} days (including missing review dates) / docs. Open conflicts includes undecided proposals.</p>'
        def section(title, headers, data):
            return f'<section><h2>{title}</h2>{self.table(headers, data)}</section>'
        maximum = max([g['count'] for g in s['top_questions']] or [1])
        body += section('Top questions', ['Question', 'Times asked', 'Last asked', 'Cited docs'],
                        [[esc(g['question']), f'<svg width="70" height="8" aria-hidden="true"><rect width="{70*g["count"]/maximum}" height="8" rx="4" fill="#3267c8"/></svg>{g["count"]}', esc(g['last_asked']), '<br>'.join(self.doclink(p) for p in g['cited_docs'])] for g in s['top_questions']])
        body += section('Owner to-dos', ['Owner / approvers', 'Overdue reviews', 'Open review cards', 'Assigned gaps', 'Awaiting approval'],
                        [[esc(o['owner']) + '<small>' + esc(', '.join(o['approvers']) or 'No approvers configured') + '</small>', '<br>'.join(self.doclink(p) for p in o['overdue_reviews']), esc(', '.join(o['open_reviews'])), esc(', '.join(o['open_gaps'])), esc(', '.join(o['pending_proposals']))] for o in s['owner_todos']])
        body += section('Recent decisions', ['When / ID', 'Kind', 'Approver', 'Commit / trailers'],
                        [[esc(d.get('when', '')) + '<br>' + esc(d.get('id', '')), esc(d['kind']), esc(d['approver']), esc(d['commit']) + '<pre>' + esc('\n'.join(f'{k}: {v}' for k, v in d['trailers'].items())) + '</pre>'] for d in s['recent_decisions']])
        body += section('Knowledge gaps', ['ID', 'Question', 'Asked by', 'Owner', 'When'],
                        [[esc(g.get(k, '—')) for k in ('id', 'question', 'asked_by', 'owner', 'when')] for g in s['gaps']])
        data = []
        for r in s['stale_docs']:
            actions = f'<a href="{esc(self.url("login"))}">Sign in to review</a>'
            if session:
                options = ''.join(f'<option value="{esc(o)}">{esc(o)}</option>' for o in sorted(common.approvers()) if o != r['owner'])
                actions = f'''<form class="actions" method="post" action="{esc(self.url('act'))}"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><input type="hidden" name="path" value="{esc(r['path'])}"><button name="choice" value="valid">Still valid</button><button name="choice" value="update">Needs update</button><details><summary>Not mine</summary><select name="new_owner" aria-label="Other owner"><option value="">Select other owner</option>{options}</select><button name="choice" value="notmine">Not mine</button></details></form>'''
            data.append([self.doclink(r['path']), esc(r['title']), esc(r['owner']), esc(r['last_reviewed']), esc(r['age'] if r['age'] is not None else 'Unknown'), esc(r['status']), actions])
        body += section('Stale docs', ['Path', 'Title', 'Owner', 'Last reviewed', 'Age (days)', 'Review status', 'Actions'], data)
        body += section('Review queue', ['Document', 'User', 'Choice', 'Status'], [[esc(q.get(k, '')) for k in ('path', 'user', 'choice', 'status')] for q in s['queue']])
        body += section('Review outcomes', ['ID', 'Document', 'Status', 'By'], [[esc(r.get(k) or '—') for k in ('id', 'path', 'status', 'by')] for r in s['review_rows']])
        return self.page('Knowledge health', body, session)

    def document(self, path, session):
        with self.lock:
            self.refresh()
            target = (self.repo / path).resolve()
            if path not in self.index.files or not target.is_relative_to(self.repo.resolve()):
                raise ValueError('Document not found.')
            meta, body = kb_core.parse_front_matter(target.read_text())
            content = '<section><h2>Front matter</h2>' + self.table(['Field', 'Value'], [[esc(k), esc(v)] for k, v in meta.items()]) + '</section>'
            content += '<section><h2>Document text</h2><pre>' + esc(body) + '</pre></section><section><h2>Last five commits</h2>'
            for h in self.history(path):
                content += '<article><strong>' + esc(h['commit']) + '</strong><pre>' + esc(h['message']) + '</pre><small>Trailers: ' + esc(json.dumps(h['trailers'], ensure_ascii=False)) + '</small></article>'
            return self.page(meta.get('title', path), content + '</section>', session)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # URLs and submitted credentials never enter access logs.

    def send(self, status, body, content_type='text/html; charset=utf-8', cookie=None, location=None):
        data = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        path = urlsplit(self.path).path
        base = self.server.app.base
        return (path[len(base):] or '/') if base and (path == base or path.startswith(base + '/')) else path

    def session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
            return verify_cookie(self.server.app.secret, jar['kb_session'].value) if 'kb_session' in jar else None
        except cookies.CookieError:
            return None

    def do_GET(self):
        app, path, session = self.server.app, self.route(), self.session()
        try:
            if path == '/healthz':
                self.send(200, 'ok', 'text/plain; charset=utf-8')
            elif path == '/api/summary':
                self.send(200, json.dumps(app.summary(), ensure_ascii=False), 'application/json; charset=utf-8')
            elif path == '/':
                self.send(200, app.overview(session))
            elif path == '/doc':
                self.send(200, app.document(parse_qs(urlsplit(self.path).query).get('path', [''])[0], session))
            elif path == '/login':
                token = sign_cookie(app.secret, '')
                csrf = verify_cookie(app.secret, token)['csrf']
                self.send(200, app.page('Sign in', f'<section><p>Use your demo persona credentials. Only the document owner’s approvers can queue reviews.</p><form method="post" action="{esc(app.url("login"))}"><input type="hidden" name="csrf" value="{csrf}"><label>Username <input name="username" autocomplete="username" required></label><label>Password <input type="password" name="password" autocomplete="current-password" required></label><button>Sign in</button></form></section>'), cookie=self.cookie(token))
            else:
                self.send(404, app.page('Not found', '<p>Page not found.</p>', session))
        except ValueError as e:
            self.send(404, app.page('Not found', f'<p>{esc(e)}</p>', session))

    def cookie(self, token):
        return f'kb_session={token}; HttpOnly; SameSite=Lax; Path={quote(self.server.app.base or "/", safe="/")}; Max-Age=28800'

    def do_POST(self):
        app, session = self.server.app, self.session()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError('Invalid form size.')
            fields = parse_qs(self.rfile.read(length).decode())
            get = lambda k: fields.get(k, [''])[0]
            if not session or not hmac.compare_digest(session['csrf'], get('csrf')):
                raise PermissionError('Invalid CSRF token. Sign in and try again.')
            if self.route() == '/login':
                user = get('username').strip().lower()
                if not authenticate(app.state, user, get('password')):
                    raise PermissionError('Invalid username or password.')
                self.send(303, '', cookie=self.cookie(sign_cookie(app.secret, user)), location=app.url())
            elif self.route() == '/act':
                if not session['user']:
                    raise PermissionError('Sign in before reviewing a document.')
                app.act(session['user'], get('path'), get('choice'), get('new_owner') or None)
                self.send(200, app.overview(session, 'Queued — kb-curator will commit and post to kb-ops'))
            else:
                self.send(404, app.page('Not found', '<p>Page not found.</p>', session))
        except (PermissionError, ValueError) as e:
            self.send(403 if isinstance(e, PermissionError) else 400, app.page('Action refused', f'<div class="notice">{esc(e)}</div>', session))


def main():
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('DASH_PORT', '8870'))), Handler)
    server.app = Dashboard()
    server.serve_forever()


if __name__ == '__main__':
    main()
