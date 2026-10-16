"""reviewer — code review for the fleet.

Point it at a module, a path, a pasted file or a diff and get a scored
review: mod-protocol conformance, the house rules this box has learned the
hard way, and plain code hygiene, every finding anchored to file:line with
a fix. The static engine is stdlib and offline; the deep engine hands the
same target to the claudecode agent for a written review.

    m reviewer                                  # null call → info()
    m reviewer/targets                          # what this box can review
    m reviewer/review target=jokes              # static review of a module
    m reviewer/review path=/root/mod/mod/orbit/jokes/serve.py
    m reviewer/review target=jokes engine=agent # + claudecode written review
    m reviewer/list                             # past reviews
    m reviewer/get id=<id>                      # one, with findings + verdicts
    m reviewer/verdict id=<id> index=3 verdict=fixed
    m reviewer/rules                            # every rule the static pass runs
    m reviewer/serve                            # console + API + MCP on :51430
    m reviewer/test                             # offline tests
    m reviewer/kill                             # stop it

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Everything the module exposes to the CLI, the gateway
and other modules is a public method on that class.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)

import rules  # noqa: E402

# Where modules live. The orbit tree is this module's parent; core sits
# beside it. A `src/` folder is never a module of its own.
ORBIT = os.path.dirname(HERE)
ROOTS = [ORBIT, os.path.join(os.path.dirname(ORBIT), 'core')]


def find_targets(roots=None):
    """Every module directory this box can review, by name."""
    out = {}
    for root in roots or ROOTS:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if name.startswith('.') or name == 'src' or not os.path.isdir(d):
                continue
            cfg_p = os.path.join(d, 'config.json')
            has_anchor = any(os.path.isfile(os.path.join(d, a)) for a in ('mod.py', 'src/mod.py'))
            if not os.path.isfile(cfg_p) and not has_anchor:
                continue
            cfg = {}
            try:
                with open(cfg_p) as f:
                    cfg = json.load(f)
            except Exception:  # noqa: BLE001 — a broken config is itself a finding
                pass
            if not isinstance(cfg, dict):
                cfg = {}
            desc = (cfg.get('description') or '').strip().split('\n')[0]
            out.setdefault(name, {
                'name': name, 'path': d, 'root': os.path.basename(root),
                'version': cfg.get('version'), 'port': cfg.get('port'),
                'description': desc[:160],
                'readme': os.path.isfile(os.path.join(d, 'README.md')),
                'tests': any(os.path.isdir(os.path.join(d, t)) for t in ('tests', 'test', 'src/tests')),
            })
    return out


class Mod:
    description = """
    reviewer — code review for the fleet. Review a module, a path, pasted
    code or a diff: mod-protocol conformance, house rules, code hygiene,
    each finding at file:line with a fix and a 0-100 score. Static engine
    is offline and stdlib; the agent engine adds a written review from
    claudecode. Reviews and verdicts live in one local SQLite file.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51430))
        self.base = cfg.get('base_path', '/reviewer')
        self._path = path
        self._store = None

    @property
    def store(self):
        if self._store is None:
            import store
            self._store = store.Store(self._path)
        return self._store

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def info(self):
        """What this module is, with review stats and every route it serves."""
        cfg = self.config()
        return {'name': 'reviewer', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'mcp': f'http://localhost:{self.port}/mcp',
                'rules': len(rules.rules()), 'stats': self.store.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self, probe_agent=True):
        """Liveness: the store, the rule count, and whether the agent answers."""
        import agent
        return {'ok': True, 'port': self.port, 'rules': len(rules.rules()),
                'agent': agent.available() if probe_agent else None,
                **self.store.stats()}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── reviewing ────────────────────────────────────────────────

    def targets(self, q=''):
        """Every module this box can review, with a search filter."""
        q = (q or '').lower()
        items = [t for t in find_targets().values()
                 if not q or q in t['name'] or q in t['description'].lower()]
        return {'targets': items, 'count': len(items)}

    def in_fleet(self, p):
        """True when p sits inside a fleet module tree (orbit/ or core/).
        Remote callers are confined to these; the box's own files are not
        for review by strangers."""
        if not p:
            return False
        p = os.path.realpath(p)
        for root in ROOTS:
            r = os.path.realpath(root)
            if p == r or p.startswith(r + os.sep):
                return True
        return False

    def resolve(self, target):
        """A module name or a path → an absolute path, or None."""
        if not target:
            return None
        t = os.path.expanduser(str(target))
        if os.path.exists(t):
            return os.path.abspath(t)
        hit = find_targets().get(t)
        return hit['path'] if hit else None

    def review(self, target=None, path=None, code=None, filename='snippet.py', diff=None,
               engine='static', focus='', model=None, timeout=600, save=True):
        """Run a review. One of target (module name), path, code, or diff.
        engine=static (offline) or agent (static + claudecode written review)."""
        import agent as agent_engine
        engine = (engine or 'static').lower()
        if engine not in ('static', 'agent'):
            raise ValueError('engine must be static|agent')
        label, findings, meta, summary = None, [], {}, None
        if diff:
            findings, meta = rules.review_diff(str(diff))
            label = 'diff'
        elif code is not None:
            findings, meta = rules.review_code(str(code), filename or 'snippet.py')
            label = f'code:{filename or "snippet.py"}'
        else:
            p = self.resolve(target or path)
            if not p:
                raise KeyError(f'no such module or path: {target or path}')
            if os.path.isdir(p):
                findings, meta = rules.review_dir(p)
                label = meta.get('name') or os.path.basename(p)
            else:
                findings, meta = rules.review_file(p)
                label = p
            meta['path'] = p
        if engine == 'agent':
            p = meta.get('path')
            if not p or not os.path.isdir(p):
                raise ValueError('the agent engine reviews a module or directory; give target or path')
            r = agent_engine.review(p, findings, focus=focus, model=model, timeout=timeout)
            meta['agent'] = r.get('meta', {})
            if r.get('ok'):
                summary = r.get('text') or ''
                meta['agent_score'] = agent_engine.parse_score(summary)
            else:
                summary = None
                meta['agent_error'] = r.get('error')
        sc = rules.score(findings)
        out = {'target': label, 'kind': meta.get('kind'), 'engine': engine, **sc,
               'findings': findings, 'summary': summary, 'meta': meta}
        if save:
            saved = self.store.save(label, meta.get('kind') or 'dir', engine, findings, summary, meta)
            out = saved
        return out

    def get(self, id):
        """One review, with findings and verdicts."""
        r = self.store.get(str(id))
        if not r:
            raise KeyError(f'no review {id}')
        return r

    def list(self, target='', engine='', limit=50, offset=0):
        """Past reviews, newest first."""
        return {'reviews': self.store.list(target, engine, limit, offset)}

    def rules(self):
        """Every rule the static engine runs."""
        return {'rules': rules.rules()}

    def verdict(self, id, index, verdict='fixed', note=''):
        """Mark a finding open | fixed | ignored; the score follows."""
        return self.store.verdict(str(id), int(index), verdict, note)

    def delete(self, id):
        """Delete a review from this box."""
        return self.store.delete(str(id))

    # ── surfaces ─────────────────────────────────────────────────

    def mcp(self, stdio=True):
        """Run the MCP server over stdio (the HTTP one is part of serve)."""
        import mcp_server
        return mcp_server.main() if stdio else {'mcp': f'http://localhost:{self.port}/mcp'}

    def serve(self, port=None, background=False):
        """Run the console, the API and the MCP endpoint on one port."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/',
                'api': f'http://localhost:{port}/',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever is holding the port. Targets the port, never a name:
        a name match on this box would take other services down with it."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests (offline, in a throwaway store)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
