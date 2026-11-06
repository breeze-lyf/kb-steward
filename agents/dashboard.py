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


# Public video and blog URLs already published with this project.
VIDEOS = (
    ('Demo video', 'https://youtu.be/BU5vN0Acqbc'),
    ('Earlier video', 'https://youtu.be/HDlKPY9HajU'),
    ('First video', 'https://youtu.be/8xKwF6o_T2U'),
)
BLOGS = (
    ('Blog', 'https://breeze-lyf.hashnode.dev/kb-steward-teaching-two-agents-to-fix-the-wiki-from-team-chat-on-switch'),
    ('中文博客', 'https://breeze-lyf.hashnode.dev/kb-steward-agent-switch'),
)
STAGE_LABELS = (
    ('question', 'Question'),
    ('proposal', 'Correction proposal'),
    ('approval', 'Owner approval'),
    ('update', 'Document update'),
    ('trace', 'Traced to the original thread'),
)
STATE_LABEL = {'recorded': 'Recorded', 'pending': 'Pending', 'missing': 'Not recorded'}
PAGE_STYLE = '''
:root { --paper:#f6f1e7; --ink:#1c1915; --muted:#5f584e; --line:#e4d9c8; --accent:#c2410c; }
* { box-sizing:border-box; }
html, body { margin:0; background:var(--paper); color:var(--ink); }
body { font:17px/1.55 "Liberation Serif", Georgia, "Iowan Old Style", Palatino, "Palatino Linotype", serif; }
header, main, footer { width:min(1180px, 100%); margin:0 auto; }
header, footer { display:flex; justify-content:space-between; gap:16px 24px; align-items:baseline; padding:22px 28px; }
header { border-bottom:1px solid var(--line); }
footer { flex-wrap:wrap; justify-content:flex-start; border-top:1px solid var(--line); padding-bottom:48px; }
main { padding:36px 28px 12px; }
a { color:var(--accent); text-decoration-thickness:1px; text-underline-offset:3px; }
a.brand { color:var(--ink); font-weight:650; letter-spacing:-0.03em; text-decoration:none; font-size:18px; }
h1, h2, h3 { font-weight:560; letter-spacing:-0.03em; line-height:1.08; }
h1 { font-size:clamp(36px, 5vw, 60px); margin:0 0 14px; }
h2 { font-size:clamp(24px, 3vw, 32px); margin:0 0 12px; }
h3 { font-size:20px; margin:8px 0; }
p { margin:0 0 12px; }
.kicker, .state, .meta, .who, footer, .stats, table, button, input, select, small, .legend, .banner, .notice {
  font-family:Inter, "Liberation Sans", system-ui, sans-serif;
}
.kicker, .state { margin:0 0 8px; color:var(--accent); font-size:13px; letter-spacing:0.12em; text-transform:uppercase; }
.state-pending { color:var(--ink); }
.state-missing { color:var(--muted); }
.meta, small, .legend { color:var(--muted); font-size:14px; line-height:1.45; }
.lede { font-size:20px; line-height:1.4; max-width:42rem; }
.statusline { font-family:Inter, "Liberation Sans", system-ui, sans-serif; font-size:15px; letter-spacing:0.04em; text-transform:uppercase; }
.hero { display:grid; grid-template-columns:minmax(0, 1.15fr) minmax(0, 0.85fr); gap:40px; align-items:start; padding-bottom:8px; }
.diff-col { min-width:0; }
.banner, .notice { border-left:3px solid var(--accent); padding:10px 14px; margin:0 0 22px; font-size:14px; }
.stages { list-style:none; padding:28px 0 0; margin:0; display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:22px; }
.stages li { min-width:0; }
.num { color:var(--accent); font-variant-numeric:tabular-nums; }
.block { border-top:1px solid var(--line); padding:28px 0; margin-top:8px; }
.stats dl { display:flex; flex-wrap:wrap; gap:14px 28px; margin:0 0 10px; }
.stats dt { color:var(--muted); font-size:12px; letter-spacing:0.04em; text-transform:uppercase; }
.stats dd { margin:2px 0 0; font-family:"Liberation Serif", Georgia, serif; font-size:18px; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:0 0 12px; font:13px/1.45 "Liberation Mono", ui-monospace, monospace; }
pre.before { color:var(--muted); }
pre.after { border-left:2px solid var(--accent); padding-left:12px; }
.scroll, table, td, th, a, p, h1, li { max-width:100%; overflow-wrap:anywhere; }
table { width:100%; border-collapse:collapse; font-size:14px; }
td, th { text-align:left; vertical-align:top; padding:10px 10px 10px 0; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:12px; letter-spacing:0.04em; text-transform:uppercase; font-weight:600; }
button, input, select { font:inherit; color:var(--ink); background:transparent; border:1px solid var(--ink); padding:8px; max-width:100%; }
button { cursor:pointer; }
form.actions, footer { display:flex; gap:8px; flex-wrap:wrap; }
ul { padding-left:1.1rem; }
label { display:block; margin:12px 0; }
@media (max-width:960px) {
  .hero, .stages { grid-template-columns:1fr 1fr; }
}
@media (max-width:720px) {
  header, footer, main { padding-left:16px; padding-right:16px; }
  header { flex-direction:column; align-items:flex-start; }
  .hero, .stages { grid-template-columns:1fr; }
  h1 { font-size:34px; }
  .lede { font-size:18px; }
  table, thead, tbody, tr, th, td { display:block; width:100%; }
  thead { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
  td { border-bottom:0; padding:4px 0; }
  tr { border-bottom:1px solid var(--line); padding:10px 0; }
  td::before { content:attr(data-label); display:block; color:var(--muted); font-size:12px; letter-spacing:0.06em; text-transform:uppercase; }
}
'''


def clip(value, limit=280):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def safe_http(url):
    if not isinstance(url, str):
        return ''
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme in ('http', 'https') and parts.netloc and not parts.username and not parts.password:
        return cleaned
    return ''


def load_proposals(raw):
    if isinstance(raw, dict) and 'rows' not in raw:
        items = list(raw.values())
    else:
        items = rows(raw)
    return [item for item in items if isinstance(item, dict)]


def source_mode():
    """Effective publisher. Never reads credential values into the result."""
    selected = (os.environ.get('KB_SOURCE') or 'local').strip() or 'local'
    repo = (os.environ.get('GITHUB_REPO') or '').strip()
    configured = bool((os.environ.get('GITHUB_TOKEN') or '').strip())
    if not configured:
        path = os.environ.get('GITHUB_TOKEN_FILE') or '/run/secrets/github_token'
        try:
            configured = Path(path).is_file() and Path(path).stat().st_size > 0
        except OSError:
            configured = False
    repo_ok = bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo))
    if selected == 'github' and repo_ok and configured:
        return {'selected': 'github', 'effective': 'github', 'repo': repo, 'fallback': False}
    return {'selected': selected, 'effective': 'local', 'repo': repo if repo_ok else '', 'fallback': selected == 'github'}


def publication_view(mode, proposals):
    prs = []
    for prop in proposals:
        if prop.get('status') != 'pr_open':
            continue
        href = safe_http(prop.get('pr_url'))
        number = prop.get('pr_number')
        number_ok = isinstance(number, int) or (isinstance(number, str) and number.isdigit())
        label = ('PR #' + str(number)) if number_ok and href else (str(prop.get('id') or 'Pull request') + ('' if href else ' (link omitted)'))
        prs.append({'id': str(prop.get('id') or ''), 'label': label, 'href': href, 'status': 'pr_open'})
    opens = [prop for prop in proposals if prop.get('status', 'open') == 'open']
    if mode['effective'] == 'github':
        where = mode['repo'] or 'the configured repository'
        headline = 'GitHub pull requests'
        note = 'Owner approval opens a pull request on ' + where + '. The knowledge base updates only after that pull request merges.'
        merged = any(prop.get('status') == 'merged' for prop in proposals)
        if prs:
            nxt = 'Wait for the recorded pull request to merge. A correction is traced back to the original thread after the merge, not when the pull request opens.'
        elif opens:
            nxt = 'An owner approves or rejects the open proposal in the room. Approval opens a pull request. It does not merge the document by itself.'
        elif merged:
            nxt = 'No open pull request is recorded. The latest recorded case is already merged. A new question in the room starts the next loop.'
        else:
            nxt = 'No open pull request is recorded. The next step is a room question that becomes a proposal, then an owner approval.'
    else:
        headline = 'Local repository'
        if mode['fallback']:
            note = 'KB_SOURCE is github, but this process has no usable GitHub credential, so the effective mode is local merge. Documents are updated in the local git repository. This page is not publishing a GitHub pull request.'
        else:
            note = 'Documents are updated in the local git repository when an owner approves. This page does not show a GitHub pull request, because the current mode does not publish one.'
        if prs:
            note += ' A stored record has status pr_open. That link is a stored field, not a pull request this process is publishing now.'
        merged = any(prop.get('status') == 'merged' for prop in proposals)
        if opens:
            nxt = 'An owner approves or rejects the open proposal in the room. Approval merges into the local repository. It does not open a GitHub pull request.'
        elif merged:
            nxt = 'The latest recorded update is already merged in the local repository. A new question in the room starts the next loop. That approval does not open a GitHub pull request.'
        else:
            nxt = 'Nothing is waiting to publish. A new question in the room can become a proposal; after owner approval the document is merged locally.'
    return {'headline': headline, 'note': note, 'next_step': nxt, 'effective': mode['effective'],
            'repo': mode['repo'], 'fallback': mode['fallback'], 'prs': prs}


def parse_diff(diff, limit=8):
    """Concise removed/added lines from a unified diff. Binary patches are skipped."""
    if not isinstance(diff, str) or not diff.strip() or 'GIT binary patch' in diff:
        return []
    files, current = [], None

    def start(path):
        nonlocal current
        current = {'path': path, 'removed': [], 'added': []}
        files.append(current)

    for line in diff.splitlines():
        if line.startswith('diff --git '):
            match = re.search(r' b/(.+)$', line)
            start(match.group(1) if match else '')
        elif line.startswith('+++ b/'):
            if current is None:
                start(line[6:])
            else:
                current['path'] = line[6:]
        elif current is None or line.startswith(('---', 'index ', '@@', 'new file', 'deleted file', 'similarity ',
                                                  'rename ', 'old mode', 'new mode', '\\', 'diff ')):
            continue
        elif line.startswith('+'):
            current['added'].append(line[1:])
        elif line.startswith('-'):
            current['removed'].append(line[1:])
    parsed = []
    for item in files:
        if not item['path'] and not item['removed'] and not item['added']:
            continue
        removed, added = item['removed'], item['added']
        parsed.append({'path': item['path'], 'removed': removed[:limit], 'added': added[:limit],
                       'truncated': len(removed) > limit or len(added) > limit})
        if len(parsed) == 3:
            break
    return parsed


def norm_question(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', str(value or '')).casefold()))


def commit_message(repo, sha):
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{4,40}', sha):
        return ''
    return kb_core.git(repo, 'show', '-s', '--format=%B', sha, check=False)


def commit_patch(repo, sha):
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{4,40}', sha):
        return ''
    return kb_core.git(repo, 'show', '--format=', '--unified=1', sha, check=False)


def retro_count(title):
    match = re.search(r'Retro-corrected:\s*(\d+)', title or '')
    return int(match.group(1)) if match else None


def linked_answers(prop, answers):
    pid = str((prop or {}).get('id') or '')
    retro = {str(item) for item in (prop or {}).get('retro') or [] if isinstance(item, (str, int))}
    thread = (prop or {}).get('thread')
    question = norm_question((prop or {}).get('question'))
    linked = []
    for row in answers:
        if not isinstance(row, dict):
            continue
        same_thread = thread and row.get('thread') == thread and question and norm_question(row.get('question')) == question
        if row.get('corrected_by') == pid or (row.get('id') and str(row.get('id')) in retro) or same_thread:
            linked.append(row)
    return linked


def linked_gap(prop, gaps):
    if not prop:
        return None
    gid = prop.get('gap_id')
    pid = str(prop.get('id') or '')
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        status = str(gap.get('status') or '')
        if gid and gap.get('id') == gid:
            return gap
        if pid and pid in status and ('answered' in status or 'fixed' in status):
            return gap
    return None


def stage(key, state, detail, source='', when=''):
    return {'key': key, 'label': dict(STAGE_LABELS)[key], 'state': state if state in STATE_LABEL else 'missing',
            'detail': clip(detail, 420), 'source': source, 'when': str(when or '')}


def assemble_cases(repo, proposals, decisions, answers, gaps):
    """Build cases from explicit ids and stored links. Unlinked rows stay unlinked."""
    by_id = {}
    order = []
    for prop in proposals:
        pid = str(prop.get('id') or '')
        if pid and pid not in by_id:
            by_id[pid] = {'proposal': prop, 'decisions': []}
            order.append(pid)
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        pid = str(decision.get('id') or '')
        if not pid:
            continue
        slot = by_id.setdefault(pid, {'proposal': None, 'decisions': []})
        if pid not in order:
            order.append(pid)
        slot['decisions'].append(decision)
    cases = []
    for pid in order:
        slot = by_id[pid]
        prop = slot['proposal']
        chosen = sorted(slot['decisions'], key=lambda item: str(item.get('when') or ''))
        decision = chosen[-1] if chosen else None
        cases.append(assemble_case(repo, pid, prop, decision, answers, gaps))
    cases.sort(key=lambda item: (sum(part['state'] == 'recorded' for part in item['stages']), item['score'], item['when'], item['id']), reverse=True)
    return cases


def assemble_case(repo, pid, prop, decision, answers, gaps):
    prop = prop or {}
    status = str(prop.get('status') or '').strip()
    if not status and decision:
        status = str(decision.get('kind') or decision.get('decision') or 'decision recorded')
    sha = ''
    for candidate in (prop.get('commit'), (decision or {}).get('commit_full'), (decision or {}).get('commit')):
        if isinstance(candidate, str) and re.fullmatch(r'[0-9a-fA-F]{4,40}', candidate):
            sha = candidate
            break
    message = commit_message(repo, sha) if sha else ''
    named = bool(pid and pid in message)
    links = linked_answers(prop if prop else {'id': pid}, answers) if prop or decision else []
    # A decision-only id still links answers by corrected_by / retro. Proposal may be absent.
    if not prop:
        links = linked_answers({'id': pid}, answers)
    gap = linked_gap(prop, gaps) if prop else None
    sources = []
    if prop:
        sources.append('proposals.json')
    if decision:
        sources.append('decisions.json')
    if links:
        sources.append('answers.json')
    if gap:
        sources.append('gaps.json')

    question_text = str(prop.get('question') or '').strip() if prop else ''
    question_source, question_when = '', ''
    if question_text:
        question_source, question_when = 'proposals.json question', str(prop.get('created') or '')
    elif links and str(links[0].get('question') or '').strip():
        question_text = str(links[0].get('question') or '').strip()
        question_source, question_when = 'answers.json ' + str(links[0].get('id') or ''), str(links[0].get('when') or '')
    elif gap and str(gap.get('question') or '').strip():
        question_text = str(gap.get('question') or '').strip()
        question_source, question_when = 'gaps.json ' + str(gap.get('id') or ''), str(gap.get('when') or '')
    if question_text:
        question = stage('question', 'recorded', 'Question recorded: ' + question_text, question_source, question_when)
    elif gap:
        question = stage('question', 'missing', 'Gap ' + str(gap.get('id') or '') + ' is linked to this proposal, but it stores no question text.',
                         'gaps.json ' + str(gap.get('id') or ''))
    else:
        question = stage('question', 'missing', 'No question text is stored on the proposal, a linked ledger row, or a linked gap.')

    if prop:
        bits = [pid + ' is recorded with status ' + (str(prop.get('status') or 'not stored') + '.')]
        bits.append('Requested by ' + str(prop['requested_by']) + '.' if prop.get('requested_by') else 'requested_by is not stored.')
        files = [item for item in (prop.get('files') or []) if isinstance(item, str)]
        impact = prop.get('impact') if isinstance(prop.get('impact'), dict) else {}
        for path in impact.get('files') or {}:
            if isinstance(path, str) and path not in files:
                files.append(path)
        bits.append('Files recorded: ' + ', '.join(files) + '.' if files else 'No file list is stored on the proposal.')
        if prop.get('summary'):
            bits.append(str(prop.get('summary')))
        proposal_stage = stage('proposal', 'recorded', ' '.join(bits), 'proposals.json', str(prop.get('created') or ''))
    else:
        files = []
        proposal_stage = stage('proposal', 'missing', 'No proposals.json row is stored for ' + pid + '.', '', '')

    approval_bits = []
    if prop and prop.get('approved_by'):
        approval_bits.append('proposals.json approved_by is ' + str(prop.get('approved_by')) + '.')
    if decision and (decision.get('by') or decision.get('approver')):
        who = decision.get('by') or decision.get('approver')
        kind = decision.get('kind') or decision.get('decision') or 'decision'
        approval_bits.append('decisions.json by is ' + str(who) + ' (' + str(kind) + ').')
    trailers_map = (decision or {}).get('trailers') if isinstance((decision or {}).get('trailers'), dict) else {}
    if named and trailers_map.get('Approved-by'):
        approval_bits.append('Commit trailer Approved-by is ' + str(trailers_map.get('Approved-by')) + '.')
    if approval_bits:
        approval = stage('approval', 'recorded', ' '.join(approval_bits), 'decisions.json' if decision else 'proposals.json',
                         str((decision or {}).get('when') or ''))
    elif status == 'open':
        approval = stage('approval', 'pending', 'No approver is recorded. The proposal is still open.')
    elif status == 'pr_open':
        approval = stage('approval', 'missing', 'Status is pr_open, but approved_by is empty and no decision names an approver.')
    else:
        approval = stage('approval', 'missing', 'No approver is recorded.')

    rejected = 'reject' in status.lower() or 'reject' in str((decision or {}).get('decision') or '').lower()
    merged = status == 'merged' or (not rejected and 'merg' in str((decision or {}).get('decision') or '').lower())
    if status == 'pr_open':
        update = stage('update', 'pending', 'A pull request is recorded. The document is not updated until it merges.', 'proposals.json')
    elif rejected:
        update = stage('update', 'recorded', 'No document change was recorded. The decision is a rejection.', 'decisions.json',
                       str((decision or {}).get('when') or ''))
    elif merged and sha:
        caveat = ''
        if message and not named:
            caveat = ' The commit message does not name ' + pid + ', so this page does not treat that patch as this proposal\'s diff.'
        elif not message:
            caveat = ' This repository did not return a commit message that names ' + pid + ', so no patch from that commit is shown.'
        update = stage('update', 'recorded', 'Decision records commit ' + sha[:10] + '.' + caveat,
                       'decisions.json' if decision and decision.get('commit') else 'proposals.json', str((decision or {}).get('when') or ''))
    elif merged:
        update = stage('update', 'missing', 'Status is merged, but no commit hash is recorded.')
    elif status == 'open' or (prop and not status):
        update = stage('update', 'pending', 'The proposal is open, so the document is unchanged.')
    else:
        update = stage('update', 'missing', 'No merge, commit, or pull request is recorded for this case.')

    diff_source, diff_note, diff_files = '', '', []
    stored = prop.get('diff') if isinstance(prop.get('diff'), str) else ''
    if stored.strip():
        diff_files = parse_diff(stored)
        diff_source = 'proposals.json diff'
        diff_note = '' if diff_files else 'The proposal stores a diff, but it has no text lines to show.'
    elif sha and named:
        diff_files = parse_diff(commit_patch(repo, sha))
        diff_source = 'commit ' + sha[:10]
        diff_note = '' if diff_files else 'The commit names ' + pid + ', but it has no text diff to show.'
    elif sha and message and not named:
        diff_note = 'Decision cites commit ' + sha[:10] + ', but that commit message does not name ' + pid + ', so its patch is not shown as this case\'s diff.'
    elif sha:
        diff_note = 'Decision cites commit ' + sha[:10] + ', but this repository did not return a commit message for it, so no patch is shown.'
    else:
        diff_note = 'No diff text is stored on the proposal.'

    corrected = [row for row in links if row.get('status') == 'corrected' or row.get('corrected_by') == pid]
    traced = [row for row in corrected if row.get('thread')]
    shared = [row for row in links if row.get('thread') and row not in traced]
    count = retro_count((decision or {}).get('title'))
    if traced:
        names = ', '.join(str(row.get('id') or '?') for row in traced)
        threads = ', '.join(str(row.get('thread')) for row in traced)
        trace = stage('trace', 'recorded', 'Ledger ' + names + ' records thread ' + threads + '.', 'answers.json')
    elif shared:
        names = ', '.join(str(row.get('id') or '?') for row in shared)
        threads = ', '.join(str(row.get('thread')) for row in shared)
        trace = stage('trace', 'missing', 'Ledger ' + names + ' shares thread ' + threads + ', but the row is not marked corrected, so the trace-back is not recorded.', 'answers.json')
    elif corrected:
        trace = stage('trace', 'missing', 'A linked answer is marked corrected, but it has no thread id, so the correction cannot be traced to the original thread.', 'answers.json')
    elif prop and prop.get('retro') and not links:
        trace = stage('trace', 'missing', 'The proposal lists retro answer ids, but those rows are not in answers.json.', 'proposals.json retro')
    elif count:
        trace = stage('trace', 'missing', 'The decision title says ' + str(count) + ' retro-correction(s), but no ledger row is linked to this proposal.', 'decisions.json title')
    elif prop and prop.get('retro') == [] and merged:
        trace = stage('trace', 'recorded', 'The proposal records an empty retro list, so no past answer was marked for correction.', 'proposals.json retro')
    elif status in ('open', 'pr_open'):
        trace = stage('trace', 'pending', 'Trace-back runs after the document update. None is recorded yet.')
    else:
        trace = stage('trace', 'missing', 'No original thread id is recorded for this case.')

    title = str(prop.get('title') or '').strip() if prop else ''
    when = ''
    for candidate in (str((decision or {}).get('when') or ''), str(prop.get('created') or '') if prop else '', question_when):
        if candidate and candidate > when:
            when = candidate
    times = []
    if prop and prop.get('created'):
        times.append('Proposal created ' + str(prop.get('created')))
    if decision and decision.get('when'):
        times.append('Decision ' + str(decision.get('when')))
    recorded_n = 0
    stages = [question, proposal_stage, approval, update, trace]
    for part in stages:
        if part['state'] == 'recorded':
            recorded_n += 10
        elif part['state'] == 'pending':
            recorded_n += 3
    if diff_files:
        recorded_n += 4
    if status == 'merged':
        recorded_n += 2
    return {'id': pid, 'title': title, 'status': status, 'when': when, 'times': times, 'sources': sources,
            'files': files, 'score': recorded_n, 'diff_source': diff_source, 'diff_note': diff_note,
            'diff_files': diff_files, 'stages': stages}


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
        answers = [row for row in rows(read(self.state / 'answers.json', [])) if isinstance(row, dict)]
        all_gaps = [row for row in rows(read(self.state / 'gaps.json', {})) if isinstance(row, dict)]
        gaps = [dict(g, owner=g.get('owner') or common.default_owner()) for g in all_gaps if g.get('status', 'open') == 'open']
        reviews = rows(read(self.state / 'reviews.json', {}))
        decisions = sorted(rows(read(self.state / 'decisions.json', {})), key=lambda r: r.get('when', ''), reverse=True)
        props = load_proposals(read(self.state / 'proposals.json', {}))
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
            d['commit_full'] = commit if isinstance(commit, str) and re.fullmatch(r'[0-9a-fA-F]{4,40}', commit) else ''
            d['trailers'] = trailers(kb_core.git(self.repo, 'show', '-s', '--format=%B', commit, check=False)) if d['commit_full'] else {}
            d['kind'] = d.get('kind', d.get('decision', ''))
            d['approver'] = d.get('by', '')
            d['commit'] = commit[:10] if isinstance(commit, str) else ''
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
        mode = source_mode()
        cases = assemble_cases(self.repo, props, decisions, answers, all_gaps)
        return dict(docs=len(self.index.files), sections=len(self.index.sections), coverage=numerator / total if total else 0,
                    coverage_answered=numerator, coverage_total=total, stale_rate=len(stale) / len(self.index.files) if self.index.files else 0,
                    open_conflicts=len(pending) + len(conflicts), open_gaps=len(gaps), retro_corrections=max(corrected, logged),
                    open_reviews=sum(r.get('status') == 'open' for r in reviews), review_days=self.days,
                    top_questions=sorted(groups.values(), key=lambda g: (-g['count'], g['question'])), owner_todos=todos,
                    recent_decisions=decisions, gaps=gaps, stale_docs=stale, queue=queue,
                    review_rows=[{k: r.get(k) for k in ('id', 'path', 'status', 'by', 'stage')} for r in reviews],
                    featured_case=cases[0] if cases else None, cases=cases, publication=publication_view(mode, props))

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

    def page(self, title, body, session=None, heading=True):
        identity = esc(session['user']) if session else f'<a href="{esc(self.url("login"))}">Sign in</a>'
        heading_html = f'<h1>{esc(title)}</h1>' if heading else ''
        links = ''.join(f'<a href="{esc(url)}">{esc(name)}</a>' for name, url in VIDEOS + BLOGS)
        return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · KB Steward</title><style>{PAGE_STYLE}</style>
<header><a class="brand" href="{esc(self.url())}">KB Steward</a><span class="who">{identity}</span></header>
<main>{heading_html}{body}</main><footer>{links}</footer></html>'''

    def table(self, headers, data):
        head = ''.join(f'<th>{esc(h)}</th>' for h in headers)
        if data:
            body = ''.join('<tr>' + ''.join(f'<td data-label="{esc(h)}">{v}</td>' for h, v in zip(headers, row)) + '</tr>' for row in data)
        else:
            body = f'<tr><td colspan="{len(headers)}">Nothing here yet.</td></tr>'
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    def doclink(self, path):
        return f'<a href="{esc(self.url("doc", path=path))}">{esc(path)}</a>'

    def render_files(self, paths):
        bits = []
        for path in paths:
            if path in self.index.files:
                bits.append(self.doclink(path))
            else:
                bits.append(esc(path) + ' <span class="meta">recorded path, not in this repository</span>')
        return ', '.join(bits)

    def render_diff(self, case):
        files = case.get('diff_files') or []
        parts = ['<div class="diff-col"><p class="kicker">Before / after</p>']
        if not files:
            parts.append(f'<p>{esc(case.get("diff_note") or "No diff is recorded.")}</p></div>')
            return ''.join(parts)
        parts.append(f'<p class="meta">Diff source: {esc(case.get("diff_source") or "")}</p>')
        for item in files:
            path = item.get('path') or ''
            if path:
                parts.append('<p class="file">' + (self.doclink(path) if path in self.index.files else esc(path)) + '</p>')
            removed, added = item.get('removed') or [], item.get('added') or []
            parts.append('<p class="kicker">Before</p>' + (f'<pre class="before">{esc(chr(10).join(removed))}</pre>' if removed else '<p class="meta">No removed lines in the stored diff.</p>'))
            parts.append('<p class="kicker">After</p>' + (f'<pre class="after">{esc(chr(10).join(added))}</pre>' if added else '<p class="meta">No added lines in the stored diff.</p>'))
            if item.get('truncated'):
                parts.append('<p class="meta">Showing the first lines only.</p>')
        if case.get('files'):
            parts.append('<p class="meta">Evidence</p><p>' + self.render_files(case['files']) + '</p>')
        parts.append('</div>')
        return ''.join(parts)

    def render_stages(self, stages, publication):
        items = []
        for index, part in enumerate(stages, 1):
            state = part['state'] if part.get('state') in STATE_LABEL else 'missing'
            detail = part.get('detail') or ''
            if part.get('key') == 'update' and publication.get('effective') == 'local' and 'pull request' not in detail.lower():
                detail += ' Current mode is local merge, not a GitHub pull request.'
            items.append(
                f'<li><span class="num">{index:02d}</span><h3>{esc(part.get("label") or "")}</h3>'
                f'<p class="state state-{state}">{esc(STATE_LABEL[state])}</p><p>{esc(detail)}</p>'
                f'<p class="meta">{esc(" ".join(bit for bit in (part.get("source"), part.get("when")) if bit))}</p></li>')
        return '<ol class="stages">' + ''.join(items) + '</ol>'

    def render_publication(self, publication):
        items = []
        for pr in publication.get('prs') or []:
            if pr.get('href'):
                items.append(f'<li><a href="{esc(pr["href"])}">{esc(pr["label"])}</a> <span class="meta">{esc(pr.get("status") or "")}</span></li>')
            else:
                items.append(f'<li>{esc(pr.get("label") or "Pull request")}</li>')
        repo = ''
        if publication.get('effective') == 'github' and publication.get('repo'):
            repo = f'<p><a href="https://github.com/{esc(publication["repo"])}">github: {esc(publication["repo"])}</a></p>'
        elif publication.get('fallback') and publication.get('repo'):
            repo = f'<p class="meta">Configured repository slug: {esc(publication["repo"])}. It is not the publisher while the effective mode is local.</p>'
        listing = '<ul class="prs">' + ''.join(items) + '</ul>' if items else ''
        return ('<section class="block"><p class="kicker">Publication</p><h2>' + esc(publication.get('headline') or '') + '</h2>'
                + repo + f'<p>{esc(publication.get("note") or "")}</p>' + listing
                + f'<p><strong>Next step. </strong>{esc(publication.get("next_step") or "")}</p></section>')

    def render_stats(self, summary):
        figures = [('Docs', summary['docs']), ('Sections', summary['sections']), ('Coverage', f"{summary['coverage']:.0%}"),
                   ('Stale rate', f"{summary['stale_rate']:.0%}"), ('Open conflicts', summary['open_conflicts']),
                   ('Open gaps', summary['open_gaps']), ('Retro-corrections sent', summary['retro_corrections']),
                   ('Open reviews', summary['open_reviews'])]
        cells = ''.join(f'<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>' for label, value in figures)
        formula = (f'Coverage = answered questions with sources / (all questions in answers ledger + open gaps) = '
                   f'{summary["coverage_answered"]}/{summary["coverage_total"]}. Stale rate = docs past {summary["review_days"]} days '
                   '(including missing review dates) / docs. Open conflicts includes undecided proposals.')
        return f'<section class="block stats"><h2>Figures</h2><dl>{cells}</dl><p class="meta">{esc(formula)}</p></section>'

    def overview(self, session, flash=''):
        s = self.summary()
        publication = s['publication']
        case = s['featured_case']
        label = os.environ.get('DASH_RECORD_LABEL', '').strip()
        body = f'<p class="banner">Labeled source: {esc(label)}. This is not production activity.</p>' if label else ''
        if flash:
            body += f'<div class="notice">{esc(flash)}</div>'
        if case:
            headline = case.get('title') or ('Proposal ' + case['id'] if case.get('id') else 'Recorded case')
            times = ' · '.join(case.get('times') or []) or 'Time not stored'
            body += '<section class="hero"><div class="hero-copy"><p class="kicker">Strongest recorded case</p>'
            body += f'<h1>{esc(headline)}</h1><p class="statusline">{esc(case.get("status") or "status not stored")}</p>'
            body += f'<p class="lede">{esc(case["stages"][0]["detail"])}</p>'
            body += f'<p class="meta">{esc(times)}</p><p class="meta">Sources: {esc(", ".join(case.get("sources") or []))}. Status is the stored field, not an inference.</p>'
            if case.get('files'):
                body += '<p class="meta">Files</p><p>' + self.render_files(case['files']) + '</p>'
            body += '</div>' + self.render_diff(case) + '</section>'
            body += self.render_stages(case['stages'], publication)
        else:
            if s['top_questions']:
                lede = 'The ledger has answers, but no proposal or decision links them into a closed loop. Those answers stay in the list below and are not shown as an approval.'
            elif s['gaps']:
                lede = 'Open gaps are listed below. No proposal or decision is recorded, so there is no closed loop to show.'
            else:
                lede = 'No proposal, decision, answer, or gap is recorded in this workspace.'
            empty_stages = [
                stage('question', 'missing', 'A question would be read from proposals.json, a linked answers.json row, or a linked gaps.json row.'),
                stage('proposal', 'missing', 'A correction proposal would be a row in proposals.json.'),
                stage('approval', 'missing', 'An approver would be decisions.json field by, or proposals.json approved_by. A Reviewed-by trailer is not an approval.'),
                stage('update', 'missing', 'A document update would be a merge commit whose message names the proposal, or a pull request that has merged.'),
                stage('trace', 'missing', 'A trace would be an answers.json row with this proposal in corrected_by and a thread id.'),
            ]
            body += '<section class="hero"><div class="hero-copy"><p class="kicker">Knowledge health</p><h1>No closed-loop case is recorded here</h1>'
            body += f'<p class="lede">{esc(lede)}</p><p class="meta">Checked answers.json, proposals.json, decisions.json, and gaps.json.</p></div>'
            body += '<div class="diff-col"><p class="kicker">Before / after</p><p>No diff is recorded, because there is no case.</p></div></section>'
            body += self.render_stages(empty_stages, publication)
        body += '<p class="legend">Recorded means that field is in the state files, or in a commit message that names the proposal. Pending means the case is open and this step has not happened. Not recorded means the file has no such field. This legend is not a case, and it is not an approval.</p>'
        if len(s['cases']) > 1:
            items = []
            for other in s['cases'][1:]:
                recorded = sum(part['state'] == 'recorded' for part in other['stages'])
                items.append(f'<li>{esc(other.get("id") or "")} · status {esc(other.get("status") or "not stored")} · {esc(other.get("when") or "time not stored")} · {recorded} recorded</li>')
            body += '<section class="block"><h2>Other recorded proposals</h2><ul>' + ''.join(items) + '</ul></section>'
        body += self.render_publication(publication)
        body += self.render_stats(s)

        def section(title, headers, data):
            return f'<section class="block"><h2>{esc(title)}</h2>{self.table(headers, data)}</section>'

        body += section('Top questions', ['Question', 'Times asked', 'Last asked', 'Cited docs'],
                        [[esc(g['question']), str(g['count']), esc(g['last_asked']), '<br>'.join(self.doclink(p) for p in g['cited_docs'])] for g in s['top_questions']])
        body += section('Owner to-dos', ['Owner / approvers', 'Overdue reviews', 'Open review cards', 'Assigned gaps', 'Awaiting approval'],
                        [[esc(o['owner']) + '<small>' + esc(', '.join(o['approvers']) or 'No approvers configured') + '</small>', '<br>'.join(self.doclink(p) for p in o['overdue_reviews']), esc(', '.join(str(item) for item in o['open_reviews'] if item)), esc(', '.join(str(item) for item in o['open_gaps'] if item)), esc(', '.join(str(item) for item in o['pending_proposals'] if item))] for o in s['owner_todos']])
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
        return self.page('Closed loop', body, session, heading=False)

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
