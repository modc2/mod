"""The write half: recommendations, and the approval that is the whole point.

A recommendation is one outsider's proposed change to a module they do not
own. Anyone may file one — filing is cheap on purpose, because the agent that
just read the code is usually the one holding the detail. What filing buys is
a place in a queue addressed to the address in that module's ``config.json``,
and nothing else: a recommendation never touches a tree, never starts a job,
and never expires into an action.

The owner decides. ``reject`` closes it with a note. ``approve`` does one
thing beyond changing a status — it relays the recommendation into build's
idea queue as a suggestion, where the owner can play it as an edit job under
their own account, with their own agent writing the code. So the outsider
contributes the reasoning and the anchors; the owner contributes the decision
and the hands. That asymmetry is the design, not a limitation of it.

Records are one JSON file per recommendation under ``~/.mod/advise/recs/``,
which is also the audit trail: who proposed what, who decided, when, and the
id of the suggestion the approval produced.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

import scan

HOME = os.path.expanduser('~')
STORE = os.environ.get('ADVISE_STORE') or os.path.join(HOME, '.mod', 'advise', 'recs')

KINDS = ('bug', 'security', 'performance', 'ux', 'docs', 'test', 'cleanup', 'feature')
SEVERITIES = ('low', 'medium', 'high', 'critical')
STATUSES = ('pending', 'approved', 'rejected', 'withdrawn')

LIMITS = {'title': 200, 'summary': 2000, 'rationale': 8000, 'change': 8000,
          'patch': 40_000, 'anchors': 24, 'evidence': 12, 'comment': 4000}
# Filing is open, so the queue is bounded per author rather than per caller.
MAX_PENDING_PER_MODULE = 5
MAX_PENDING_ANON = 2
MAX_PENDING_TOTAL = 30

BUILD_API = os.environ.get('ADVISE_BUILD_API') or 'http://127.0.0.1:8890'
RELAY_BODY_MAX = 10_000                              # build's own cap


class RecError(ValueError):
    """The caller asked for something the queue will not do."""


class Denied(PermissionError):
    """Right request, wrong signer."""


# ── store ────────────────────────────────────────────────────────────

def _path(rec_id):
    if not re.fullmatch(r'rc_[0-9a-f]{8}', str(rec_id or '')):
        raise RecError(f'not a recommendation id: {rec_id!r}')
    return os.path.join(STORE, f'{rec_id}.json')


def _write(rec):
    os.makedirs(STORE, exist_ok=True)
    rec['updated_at'] = int(time.time())
    tmp = _path(rec['id']) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, _path(rec['id']))
    return rec


def get(rec_id):
    try:
        with open(_path(rec_id)) as f:
            return json.load(f)
    except FileNotFoundError:
        raise RecError(f'no recommendation {rec_id}')


def all_recs():
    try:
        names = sorted(os.listdir(STORE))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            with open(os.path.join(STORE, n)) as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda r: r.get('created_at', 0), reverse=True)
    return out


def for_module(module, status=None):
    return [r for r in all_recs()
            if r['module'].lower() == str(module).lower()
            and (status is None or r['status'] == status)]


# ── filing ───────────────────────────────────────────────────────────

def _text(value, field, required=False):
    text = str(value or '').strip()
    if required and not text:
        raise RecError(f'{field} is required')
    cap = LIMITS.get(field)
    if cap and len(text) > cap:
        raise RecError(f'{field} too long ({len(text)} > {cap} chars)')
    return text


def _anchors(raw):
    """Where in the code this is about. Checked, not trusted: a path that is
    not in the module is the difference between a report and a guess."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [{'path': p.strip()} for p in raw.split(',') if p.strip()]
    if isinstance(raw, dict):
        raw = [raw]
    if len(raw) > LIMITS['anchors']:
        raise RecError(f'at most {LIMITS["anchors"]} anchors')
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {'path': item}
        path = str((item or {}).get('path') or '').strip().lstrip('/')
        if not path:
            continue
        out.append({'path': path,
                    'line': int(item.get('line') or 0) or None,
                    'note': str(item.get('note') or '')[:400] or None})
    return out


def _verify_anchors(module, anchors):
    """Mark each anchor with whether that file is really there — the owner
    reads it as a quality signal, and a stale anchor is worth seeing."""
    for a in anchors:
        try:
            full = scan._safe_join(scan.module_dir(module), a['path'])
            a['exists'] = os.path.isfile(full)
        except (scan.ScanError, OSError):
            a['exists'] = False
    return anchors


def _quota(module, author, signed):
    pending = [r for r in all_recs()
               if r['author'] == author and r['status'] == 'pending']
    if len(pending) >= MAX_PENDING_TOTAL:
        raise RecError(f'{author} already has {len(pending)} pending recommendations '
                       '— wait for decisions before filing more')
    here = [r for r in pending if r['module'].lower() == module.lower()]
    cap = MAX_PENDING_PER_MODULE if signed else MAX_PENDING_ANON
    if len(here) >= cap:
        raise RecError(f'{author} already has {len(here)} pending on {module} '
                       f'(limit {cap}{"" if signed else " for unsigned callers"})')


def create(module, title, caller, summary='', rationale='', change='', anchors=None,
           patch='', kind='', severity='', confidence=None, effort='', evidence=None,
           agent=''):
    """File one recommendation. Open to any caller; binds nothing."""
    module = str(module or '').strip()
    scan.module_dir(module)                          # 404s a private module too
    author = (caller or {}).get('handle') or 'anon:unknown'
    signed = bool((caller or {}).get('signed'))
    _quota(module, author, signed)

    kind = (kind or 'feature').strip().lower()
    if kind not in KINDS:
        raise RecError(f'kind must be one of {", ".join(KINDS)}')
    severity = (severity or 'medium').strip().lower()
    if severity not in SEVERITIES:
        raise RecError(f'severity must be one of {", ".join(SEVERITIES)}')
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            raise RecError('confidence is a number between 0 and 1')

    if isinstance(evidence, str):
        evidence = [evidence]
    rec = {
        'id': 'rc_' + uuid.uuid4().hex[:8],
        'module': module,
        'owner': scan.owner_of(module),
        'author': author,
        'author_signed': signed,
        'agent': _text(agent, 'title')[:80],         # which agent wrote it, if any
        'title': _text(title, 'title', required=True),
        'kind': kind,
        'severity': severity,
        'summary': _text(summary, 'summary'),
        'rationale': _text(rationale, 'rationale'),
        'change': _text(change, 'change'),
        'anchors': _verify_anchors(module, _anchors(anchors)),
        'patch': _text(patch, 'patch'),
        'confidence': confidence,
        'effort': _text(effort, 'title')[:80],
        'evidence': [str(e)[:500] for e in (evidence or [])][:LIMITS['evidence']],
        'status': 'pending',
        'comments': [],
        'relay': None,
        'decided_by': None,
        'decided_at': None,
        'note': None,
        'created_at': int(time.time()),
        'updated_at': int(time.time()),
    }
    return _write(rec)


# ── rendering ────────────────────────────────────────────────────────

def render(rec, include_patch=True):
    """The recommendation as the owner should read it — and as it is relayed.

    Attribution is in the text because the relayed suggestion is authored by
    whoever approved it: the owner should never lose track of whose idea it
    was, or that it came in from outside.
    """
    who = rec['author'] + ('' if rec['author_signed'] else ' (no account)')
    if rec.get('agent'):
        who += f" via {rec['agent']}"
    lines = [f"Recommended by {who} — advise {rec['id']}",
             f"{rec['kind']} · severity {rec['severity']}"
             + (f" · confidence {rec['confidence']:.0%}" if rec.get('confidence') is not None else '')
             + (f" · effort {rec['effort']}" if rec.get('effort') else ''),
             '']
    if rec.get('summary'):
        lines += [rec['summary'], '']
    if rec.get('rationale'):
        lines += ['WHY', rec['rationale'], '']
    if rec.get('change'):
        lines += ['PROPOSED CHANGE', rec['change'], '']
    if rec.get('anchors'):
        lines.append('WHERE')
        for a in rec['anchors']:
            where = a['path'] + (f":{a['line']}" if a.get('line') else '')
            miss = '' if a.get('exists', True) else '  [not found at file time]'
            lines.append(f"  - {where}{miss}" + (f" — {a['note']}" if a.get('note') else ''))
        lines.append('')
    if rec.get('evidence'):
        lines.append('EVIDENCE')
        lines += [f'  - {e}' for e in rec['evidence']]
        lines.append('')
    if include_patch and rec.get('patch'):
        lines += ['PATCH (proposed, not applied)', '```diff', rec['patch'], '```', '']
    return '\n'.join(lines).strip()


def _relay_body(rec):
    """Same text, trimmed to what build's queue accepts, patch first to go."""
    body = render(rec)
    if len(body) <= RELAY_BODY_MAX:
        return body
    body = render(rec, include_patch=False)
    tail = f"\n\n(patch omitted — read it at /advise/api/rec?id={rec['id']})"
    if len(body) + len(tail) <= RELAY_BODY_MAX:
        return body + tail
    return body[:RELAY_BODY_MAX - len(tail) - 1] + tail


# ── the decision ─────────────────────────────────────────────────────

def _is_owner(rec, caller):
    address = str((caller or {}).get('address') or '').lower()
    if not address:
        return False
    owner = str(rec.get('owner') or scan.owner_of(rec['module']) or '').lower()
    # The deployment owner can always decide: a module with no owner field of
    # its own is still theirs, and they own the box the queue runs on.
    return address == owner or address == scan.deployment_owner()


def decide(rec_id, caller, decision, note='', token=None, relay=True):
    """Approve or reject. Owner-signed, always — this is the gate."""
    rec = get(rec_id)
    if decision not in ('approved', 'rejected'):
        raise RecError('decision is approve or reject')
    if not _is_owner(rec, caller):
        raise Denied(f"only {rec.get('owner') or 'the module owner'} can decide on "
                     f"a recommendation about {rec['module']}")
    if rec['status'] not in ('pending', 'rejected'):
        raise RecError(f"{rec_id} is {rec['status']} — nothing to decide")
    rec['status'] = decision
    rec['decided_by'] = (caller or {}).get('address')
    rec['decided_at'] = int(time.time())
    rec['note'] = str(note or '')[:LIMITS['comment']] or None
    _write(rec)
    if decision == 'approved' and relay:
        try:
            rec['relay'] = relay_to_build(rec, token=token)
        except Exception as e:                       # the decision still stands
            rec['relay'] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        _write(rec)
    return rec


def relay_to_build(rec, token=None):
    """Hand an approved recommendation to build's idea queue.

    Authored by the approver when they passed their token through, so the
    suggestion lands in the owner's own queue ready to play; by this host
    otherwise. Either way the text says whose recommendation it was.
    """
    payload = json.dumps({'title': rec['title'][:200],
                          'body': _relay_body(rec)}).encode()
    url = f"{BUILD_API}/modules/{rec['module']}/suggestions"
    # build reads a mod-protocol token from the `token:` header (its Bearer
    # slot is for its own console sessions), so that is where the approver's
    # signature goes — it is what makes the suggestion theirs.
    token = token or identity_token()
    refused = []
    out, signed = _post(url, payload, token, refused), bool(token)
    if out is None and token:
        # Their token was not good enough for build. Filing a suggestion is
        # open to unsigned callers there, so the idea still gets through — it
        # just arrives anonymously, and the text says whose it was anyway.
        out, signed = _post(url, payload, None, refused), False
    if out is None:
        raise RecError(f'build would not take the relay: {" / ".join(refused)}')
    sid = (out.get('suggestion') or out).get('id') if isinstance(out, dict) else None
    return {'ok': bool(sid), 'suggestion_id': sid, 'module': rec['module'],
            'signed': signed, 'url': f"/build/?idea={sid}" if sid else None,
            'at': int(time.time()), 'response': out if not sid else None}


def _post(url, payload, token, refused=None):
    """POST once. None means build said no to this attempt — the reason is
    appended to `refused`, because "it did not go through" is not an answer
    an owner can act on. A raised error means build is not there at all."""
    req = urllib.request.Request(url, data=payload, method='POST',
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('token', token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            if refused is not None:
                refused.append(f'{e.code} {"signed" if token else "unsigned"}: '
                               f'{e.read().decode()[:200]}')
            return None
        raise RecError(f'build refused the relay ({e.code}): '
                       f'{e.read().decode()[:300]}')
    except urllib.error.URLError as e:
        raise RecError(f'build is not answering on {BUILD_API}: {e.reason}')


def identity_token():
    """This host's own token — the fallback signer when an approval arrives
    without one. A STRING payload, because build reads it as one."""
    import identity
    return identity.token_for('advise')


def withdraw(rec_id, caller):
    """The author taking it back, while it is still undecided."""
    rec = get(rec_id)
    handle = (caller or {}).get('handle')
    if rec['author'] != handle and not _is_owner(rec, caller):
        raise Denied('only the author can withdraw a recommendation')
    if rec['status'] != 'pending':
        raise RecError(f"{rec_id} is already {rec['status']}")
    rec['status'] = 'withdrawn'
    return _write(rec)


def comment(rec_id, body, caller):
    """The discussion. Open to anyone, like the filing — an owner asking one
    question is usually cheaper than a reject."""
    rec = get(rec_id)
    text = _text(body, 'comment', required=True)
    rec['comments'].append({'author': (caller or {}).get('handle') or 'anon:unknown',
                            'body': text, 'timestamp': int(time.time())})
    return _write(rec)


# ── views ────────────────────────────────────────────────────────────

def _brief_row(rec):
    return {k: rec.get(k) for k in
            ('id', 'module', 'title', 'kind', 'severity', 'status', 'author',
             'author_signed', 'agent', 'confidence', 'owner', 'created_at',
             'decided_at', 'decided_by')} | {
        'summary': (rec.get('summary') or '')[:280],
        'anchors': len(rec.get('anchors') or []),
        'has_patch': bool(rec.get('patch')),
        'comments': len(rec.get('comments') or []),
        'relayed': bool((rec.get('relay') or {}).get('ok')),
    }


def listing(module=None, status=None, author=None, limit=100):
    rows = all_recs()
    if module:
        rows = [r for r in rows if r['module'].lower() == str(module).lower()]
    if status:
        rows = [r for r in rows if r['status'] == status]
    if author:
        rows = [r for r in rows if r['author'].lower() == str(author).lower()]
    rows = rows[:max(1, min(int(limit or 100), 500))]
    return {'count': len(rows), 'recommendations': [_brief_row(r) for r in rows]}


def inbox(address, status='pending'):
    """Everything waiting on one owner's signature, across every module."""
    address = str(address or '').lower()
    if not address:
        raise Denied('inbox needs a signed caller')
    mine, modules = [], {}
    for rec in all_recs():
        owner = modules.setdefault(rec['module'], str(rec.get('owner') or '').lower())
        if owner != address and address != scan.deployment_owner():
            continue
        if status and rec['status'] != status:
            continue
        mine.append(_brief_row(rec))
    order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    mine.sort(key=lambda r: (order.get(r['severity'], 9), -r['created_at']))
    return {'owner': address, 'status': status or 'all',
            'count': len(mine), 'recommendations': mine}


def outbox(author):
    """What one author has filed, and what came of it."""
    rows = [_brief_row(r) for r in all_recs()
            if r['author'].lower() == str(author or '').lower()]
    return {'author': author, 'count': len(rows), 'recommendations': rows,
            'accepted': sum(1 for r in rows if r['status'] == 'approved'),
            'rejected': sum(1 for r in rows if r['status'] == 'rejected')}
