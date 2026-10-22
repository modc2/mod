"""advise — someone else's agent proposes a change; the owner decides.

Two halves, and the seam between them is the point:

    READ    any caller may walk a module's published tree, read its files
            with credentials redacted, and grep it — enough to have a
            specific opinion about code they do not own.
    WRITE   what they can do with that opinion is file a recommendation
            addressed to that module's owner. It runs nothing. The owner
            approves or rejects, and an approval relays it into build's idea
            queue as a suggestion the owner plays as an edit job under their
            own account.

So an agent can scan a peer's module and recommend a change, and the peer's
owner still writes every line that lands — which is what makes the door safe
to leave open.

    m advise                                   # null call → info()
    m advise/modules                           # what can be scanned
    m advise/brief module=boxd                 # one call: everything an agent needs
    m advise/file module=boxd path=mod.py      # read it
    m advise/grep module=boxd query=fetch      # find it
    m advise/recommend module=boxd title="cache the poster wall" \
        summary="..." change="..." anchors='[{"path":"letterboxd.py","line":88}]'
    m advise/inbox                             # what is waiting on me
    m advise/approve id=rc_1a2b3c4d            # → a suggestion in build
    m advise/reject id=rc_1a2b3c4d note="..."
    m advise/serve                             # console + API + MCP on :50990

This is the anchor: the orbit loader imports it by path and instantiates
``Mod``. The HTTP server and the MCP server dispatch into this class rather
than reimplementing it, so the three surfaces cannot drift.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything importing it after us.
if HERE not in sys.path:
    sys.path.append(HERE)

import identity                                              # noqa: E402
import recs as store                                         # noqa: E402
import scan                                                  # noqa: E402


class Mod:
    description = """
    advise — a read-only scan surface over every public module in the fleet,
    plus a queue of recommendations addressed to the address in each module's
    config.json. Anyone (any agent) may read a module and file a proposed
    change with anchors, reasoning and an optional patch; only the owner can
    approve one, and approving relays it into build's idea queue to be played
    as an edit job by the owner's own agent. Nothing an outsider files can
    write to a tree.
    """

    def __init__(self, port=None, local=True, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50990))
        self.base = cfg.get('base_path', '/advise')
        # CLI and stdio callers are the host; HTTP callers are not, and the
        # server constructs this class with local=False to say so.
        self.local = bool(local)

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _caller(self, token=None, ip=None, ua=None, local=None):
        return identity.caller(token=token, ip=ip, ua=ua,
                               local=self.local if local is None else bool(local))

    def forward(self, *args, **kwargs):
        """Protocol entry point — a bare `m advise` lands here."""
        return self.info()

    def info(self):
        """What this module is, and the shape of its queue right now."""
        cfg = self.config()
        rows = store.all_recs()
        return {
            'module': 'advise',
            'version': cfg.get('version'),
            'what': cfg.get('description'),
            'scannable_modules': scan.modules(limit=1000)['count'],
            'recommendations': {
                'total': len(rows),
                'pending': sum(1 for r in rows if r['status'] == 'pending'),
                'approved': sum(1 for r in rows if r['status'] == 'approved'),
                'rejected': sum(1 for r in rows if r['status'] == 'rejected'),
            },
            'deployment_owner': scan.deployment_owner(),
            'urls': cfg.get('urls'),
            'read_is_open': True,
            'approval_required': True,
        }

    def health(self):
        return {'ok': True, 'store': store.STORE,
                'build_api': store.BUILD_API, 'port': self.port}

    def readme(self):
        try:
            with open(os.path.join(HERE, 'README.md')) as f:
                return f.read()
        except FileNotFoundError:
            return self.description

    # ── read: scanning someone else's module ─────────────────────

    def modules(self, q=None, limit=400):
        """Every module an outsider may scan, newest tree order. Private
        modules are absent, not refused."""
        return scan.modules(q=q, limit=limit)

    def tree(self, module=None, path='', depth=3, limit=600):
        """A module's files, with dependency and build directories pruned."""
        return scan.tree(self._need(module, 'module'), path=path,
                         depth=depth, limit=limit)

    def file(self, module=None, path=None, start=1, lines=600):
        """One published file, redacted and bounded."""
        return scan.read(self._need(module, 'module'),
                         self._need(path, 'path'), start=start, lines=lines)

    def grep(self, module=None, query=None, glob=None, limit=80):
        """Literal search with line numbers — what an anchor is built from."""
        return scan.grep(self._need(module, 'module'),
                         self._need(query, 'query'), glob=glob, limit=limit)

    def brief(self, module=None, focus=None, depth=3):
        """One call: config, README, tree, languages, TODOs, open
        recommendations, and who has to approve one. Start here."""
        return scan.brief(self._need(module, 'module'), focus=focus, depth=depth)

    # ── write: recommending a change ─────────────────────────────

    def recommend(self, module=None, title=None, summary='', rationale='',
                  change='', anchors=None, patch='', kind='feature',
                  severity='medium', confidence=None, effort='', evidence=None,
                  agent='', token=None, ip=None, ua=None, local=None):
        """File a recommendation against a module you do not own.

        Open to any caller, signed or not. It changes nothing by itself — it
        waits for the owner. Give anchors: `[{"path":"src/mod.py","line":88,
        "note":"the loop"}]`; a recommendation with real line numbers is the
        one that gets approved.
        """
        caller = self._caller(token, ip, ua, local)
        rec = store.create(self._need(module, 'module'),
                           self._need(title, 'title'), caller,
                           summary=summary, rationale=rationale, change=change,
                           anchors=anchors, patch=patch, kind=kind,
                           severity=severity, confidence=confidence,
                           effort=effort, evidence=evidence, agent=agent)
        return {'filed': rec['id'], 'status': rec['status'],
                'module': rec['module'], 'awaiting': rec['owner'],
                'as': rec['author'],
                'next': f"the owner decides — m advise/rec id={rec['id']}"}

    def recs(self, module=None, status=None, author=None, limit=100):
        """The queue. Public: a recommendation is filed in the open."""
        return store.listing(module=module, status=status,
                             author=author, limit=limit)

    def rec(self, id=None):
        """One recommendation, whole — anchors, patch, thread, decision."""
        return store.get(self._need(id, 'id'))

    def inbox(self, status='pending', token=None, ip=None, ua=None, local=None):
        """What is waiting on my signature, across every module I own."""
        caller = self._caller(token, ip, ua, local)
        return store.inbox(caller['address'], status=status or None)

    def outbox(self, author=None, token=None, ip=None, ua=None, local=None):
        """What I have recommended to other people, and what came of it."""
        caller = self._caller(token, ip, ua, local)
        return store.outbox(author or caller['handle'])

    def approve(self, id=None, note='', relay=True, token=None, ip=None,
                ua=None, local=None):
        """Accept a recommendation — owner only.

        Approval is the only thing that moves code, and it moves it by hand:
        the recommendation is relayed into build's idea queue as a suggestion
        you then play as an edit job, written by your agent, on your account.
        """
        caller = self._caller(token, ip, ua, local)
        rec = store.decide(self._need(id, 'id'), caller, 'approved',
                           note=note, token=token, relay=relay)
        return {'id': rec['id'], 'status': rec['status'],
                'module': rec['module'], 'relay': rec.get('relay'),
                'next': 'play it from the idea queue in build when you are ready'}

    def reject(self, id=None, note='', token=None, ip=None, ua=None, local=None):
        """Decline a recommendation — owner only. A note is a kindness."""
        caller = self._caller(token, ip, ua, local)
        rec = store.decide(self._need(id, 'id'), caller, 'rejected', note=note)
        return {'id': rec['id'], 'status': rec['status'], 'note': rec.get('note')}

    def autoscan(self, module=None, preview=False, token=None, ip=None, ua=None, local=None):
        """Use Claude to scan a module and return a targeted recommendation.

        When preview=True (the default from the UI), returns the raw AI-generated
        dict for the caller to review and edit before filing — no signed caller
        required. When preview=False, files the recommendation immediately and
        requires a signed caller.
        """
        caller = self._caller(token, ip, ua, local)
        if not preview and not caller['signed']:
            raise PermissionError(
                'autoscan requires a signed caller — connect your wallet first'
            )
        try:
            import anthropic as _anthropic
        except ImportError:
            return {'error': 'autoscan requires ANTHROPIC_API_KEY'}
        if not os.environ.get('ANTHROPIC_API_KEY'):
            return {'error': 'autoscan requires ANTHROPIC_API_KEY'}

        module = self._need(module, 'module')
        ctx = json.dumps(self.brief(module), indent=2)[:14000]

        system = (
            'You are a senior software engineer reviewing a module\'s code. '
            'Analyse the brief and identify ONE specific, non-trivial improvement '
            'grounded in the actual code — not a guess, not a platitude. '
            'Return ONLY a JSON object with exactly these fields: '
            '{"title":"<120 chars>","summary":"1-2 sentences describing problem and fix",'
            '"rationale":"what you saw in the code that tells you this needs fixing",'
            '"change":"concrete description of what to change, specific enough for an agent",'
            '"anchors":[{"path":"relative/path/to/file","line":42,"note":"what is on this line"}],'
            '"kind":"bug|security|performance|ux|docs|test|cleanup|feature",'
            '"severity":"low|medium|high|critical","confidence":0.85} '
            'Anchors must reference real files and real line numbers visible in the module tree. '
            'No prose, no markdown, no code fences — valid JSON only.'
        )
        try:
            client = _anthropic.Anthropic()
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=1024,
                system=system,
                messages=[{'role': 'user',
                           'content': f'Module brief for `{module}`:\n\n{ctx}'}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith('```'):
                raw = '\n'.join(raw.split('\n')[1:]).rstrip('`').strip()
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return {'error': f'model returned non-JSON: {e}'}
        except Exception as e:
            return {'error': f'autoscan failed: {type(e).__name__}: {e}'}

        if preview:
            return {
                'title': data.get('title', ''),
                'summary': data.get('summary', ''),
                'rationale': data.get('rationale', ''),
                'change': data.get('change', ''),
                'anchors': data.get('anchors') or [],
                'kind': data.get('kind', 'feature'),
                'severity': data.get('severity', 'medium'),
                'confidence': data.get('confidence'),
            }

        rec = store.create(
            module,
            data.get('title') or 'Auto-scan suggestion',
            caller,
            summary=data.get('summary', ''),
            rationale=data.get('rationale', ''),
            change=data.get('change', ''),
            anchors=data.get('anchors'),
            kind=data.get('kind', 'feature'),
            severity=data.get('severity', 'medium'),
            confidence=data.get('confidence'),
            agent='advise-autoscan',
        )
        return {'filed': rec['id'], 'status': rec['status']}

    def autotriage(self, token=None, ip=None, ua=None, local=None):
        """Use Claude Haiku to vote approve/reject on each pending inbox rec.

        Owner-signed only. Caps at 8 recs per call. Returns votes without
        acting on them — the owner still decides.
        """
        caller = self._caller(token, ip, ua, local)
        if not caller['signed']:
            raise PermissionError(
                'autotriage requires a signed caller — connect your wallet first'
            )
        try:
            import anthropic as _anthropic
        except ImportError:
            return {'error': 'autotriage requires ANTHROPIC_API_KEY'}
        if not os.environ.get('ANTHROPIC_API_KEY'):
            return {'error': 'autotriage requires ANTHROPIC_API_KEY'}

        pending = store.inbox(caller['address'], status='pending')
        rows = pending.get('recommendations', [])[:8]
        if not rows:
            return {'votes': {}, 'count': 0}

        brief_cache = {}
        for row in rows:
            mod = row['module']
            if mod not in brief_cache:
                try:
                    b = scan.brief(mod)
                    brief_cache[mod] = json.dumps({
                        'description': (b.get('config') or {}).get('description', ''),
                        'todos': len(b.get('stats', {}).get('todos') or []),
                    })[:1200]
                except Exception:
                    brief_cache[mod] = '{}'

        system = (
            'You are a senior software engineer reviewing a code-change recommendation. '
            'Read the recommendation and decide APPROVE or REJECT. '
            'Return valid JSON only: '
            '{"vote":"approve"|"reject","note":"one sentence","confidence":0.85}'
        )
        client = _anthropic.Anthropic()
        votes = {}
        for row in rows:
            rec_id = row['id']
            try:
                full_rec = store.get(rec_id)
                text = store.render(full_rec)[:6000]
                ctx = brief_cache.get(full_rec['module'], '{}')
                msg = client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=256,
                    system=system,
                    messages=[{'role': 'user',
                               'content': f'Module context:\n{ctx}\n\nRecommendation:\n{text}'}],
                )
                raw = msg.content[0].text.strip()
                if raw.startswith('```'):
                    raw = '\n'.join(raw.split('\n')[1:]).rstrip('`').strip()
                data = json.loads(raw)
                votes[rec_id] = {
                    'vote': data.get('vote', 'reject'),
                    'note': str(data.get('note', ''))[:300],
                    'confidence': float(data.get('confidence', 0.5)),
                }
            except json.JSONDecodeError as e:
                votes[rec_id] = {'vote': 'reject',
                                 'note': f'model returned non-JSON: {e}', 'confidence': 0.0}
            except Exception as e:
                votes[rec_id] = {'vote': 'reject',
                                 'note': f'error: {type(e).__name__}: {e}', 'confidence': 0.0}

        return {'votes': votes, 'count': len(votes)}

    def withdraw(self, id=None, token=None, ip=None, ua=None, local=None):
        """Take back your own recommendation while it is still undecided."""
        caller = self._caller(token, ip, ua, local)
        rec = store.withdraw(self._need(id, 'id'), caller)
        return {'id': rec['id'], 'status': rec['status']}

    def comment(self, id=None, body=None, token=None, ip=None, ua=None, local=None):
        """Say something on the thread. Open, like the filing."""
        caller = self._caller(token, ip, ua, local)
        rec = store.comment(self._need(id, 'id'), self._need(body, 'body'), caller)
        return {'id': rec['id'], 'comments': len(rec['comments'])}

    def update(self, id=None, title=None, summary=None, rationale=None, change=None,
               anchors=None, patch=None, kind=None, severity=None, module=None,
               token=None, ip=None, ua=None, local=None):
        """Refine a pending recommendation in place — author only."""
        caller = self._caller(token, ip, ua, local)
        rec = store.update(self._need(id, 'id'), caller,
                           title=title, summary=summary, rationale=rationale,
                           change=change, anchors=anchors, patch=patch,
                           kind=kind, severity=severity)
        return {'id': rec['id'], 'status': rec['status'], 'updated_at': rec.get('updated_at')}

    def relay(self, id=None, token=None, ip=None, ua=None, local=None):
        """Re-send an approved recommendation to build's queue — for when the
        console was asleep at approval time."""
        caller = self._caller(token, ip, ua, local)
        rec = store.get(self._need(id, 'id'))
        if not store._is_owner(rec, caller):
            raise PermissionError('only the module owner can relay')
        if rec['status'] != 'approved':
            raise ValueError(f"{rec['id']} is {rec['status']} — approve it first")
        rec['relay'] = store.relay_to_build(rec, token=token)
        store._write(rec)
        return rec['relay']

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Console, REST API and MCP endpoint on one port."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/',
                'mcp': f'http://localhost:{port}/mcp'}

    def mcp(self):
        """The MCP tool list this module publishes to other agents."""
        import mcp as server
        return {'tools': server.tool_list()}

    def test(self):
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def kill(self, port=None):
        """Stop whatever holds the port. Targets the port, never a name — this
        box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _need(value, name):
        if value in (None, ''):
            raise ValueError(f'{name}= is required')
        return value
