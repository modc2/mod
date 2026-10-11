"""zquorum — anonymous quorums, Cairo-verified.

A group reaches a quorum without the ledger ever learning who voted.
Commit/nullifier scheme (Semaphore-style), with ALL cryptography in the
Cairo package at circuit/ (package `zq`) so the anchor never reimplements
a hash:

    commitment  C = H(tag_c, secret)            registered once per member
    nullifier   N = H(H(tag_n, secret), topic)  revealed when voting
    root        R = fold H over the sorted commitment set

A vote stores only (nullifier, choice): unlinkable to any commitment
without the secret, and a repeated nullifier is a rejected double vote.
Secrets are verified by executing the circuit and are never persisted.

Trust model v0.1: the anchor is a trusted tallier — it sees a secret
transiently while verifying, then discards it; the persisted ledger and
every other member learn nothing about who voted. v0.2 path: STARK-prove
the same circuit (the statement is already prover-shaped) so votes verify
without the tallier seeing secrets at all.

Toolchain: scarb 2.8.4 in ~/.local/bin (shared with orbit/cairo).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CIRCUIT = os.path.join(HERE, 'circuit')
DATA = os.path.join(HERE, 'data', 'quorums.json')
BIN = os.path.expanduser('~/.local/bin')

# the STARK field prime; every value the protocol touches is a felt252
PRIME = 2 ** 251 + 17 * 2 ** 192 + 1

OP_COMMIT, OP_NULLIFIER, OP_ROOT, OP_VOTE = 1, 2, 3, 4


def _felt(v):
    """Normalise a secret/commitment into a felt252 int. Accepts ints,
    decimal strings, 0x-hex, or an arbitrary passphrase (sha256 % PRIME —
    deterministic, so a passphrase works from any client)."""
    if isinstance(v, bool):
        raise ValueError('not a felt')
    if isinstance(v, int):
        return v % PRIME
    s = str(v).strip()
    if re.fullmatch(r'\d+', s):
        return int(s) % PRIME
    if re.fullmatch(r'0x[0-9a-fA-F]+', s):
        return int(s, 16) % PRIME
    return int.from_bytes(hashlib.sha256(s.encode()).digest(), 'big') % PRIME


def _choice(v):
    s = str(v).strip().lower()
    if s in ('1', 'yes', 'y', 'true', 'aye', 'approve'):
        return 1
    if s in ('0', 'no', 'n', 'false', 'nay', 'reject'):
        return 0
    raise ValueError(f'choice must be yes/no, got {v!r}')


class Mod:
    description = (
        'Anonymous quorums: members register Pedersen commitments, votes '
        'reveal only a nullifier — who voted stays hidden, double votes are '
        'impossible. All cryptography runs as a Cairo program (circuit/).')
    path = HERE
    config = json.load(open(os.path.join(HERE, 'config.json')))
    port = config['port']
    base = config['base_path']

    # ---- module protocol ----------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'zquorum',
            'description': self.description,
            'path': self.path,
            'port': self.port,
            'protocol': self.config['protocol'],
            'quorums': sorted(self._load().keys()),
            'fns': self.config['fns'],
            'endpoints': self.config['endpoints'],
        }

    def health(self):
        return {'ok': True, 'module': 'zquorum',
                'scarb': self._tool('scarb') is not None,
                'circuit_built': os.path.isdir(os.path.join(CIRCUIT, 'target')),
                'quorums': len(self._load())}

    def readme(self):
        p = os.path.join(self.path, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    def toolchain(self):
        exe = self._tool('scarb')
        v = None
        if exe:
            out = subprocess.run([exe, '--version'], capture_output=True,
                                 text=True, timeout=15)
            v = out.stdout.strip().splitlines()[0] if out.stdout else None
        return {'scarb': {'path': exe, 'version': v}, 'circuit': CIRCUIT}

    # ---- the Cairo circuit --------------------------------------------------

    def _tool(self, name):
        local = os.path.join(BIN, name)
        return local if os.path.exists(local) else None

    def _cairo(self, args, timeout=180):
        """Run one circuit op via `scarb cairo-run` and return the felt
        triple it prints. The circuit is the single source of truth for
        every hash in the protocol."""
        exe = self._tool('scarb')
        if not exe:
            raise RuntimeError('scarb not found in ~/.local/bin')
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        out = subprocess.run(
            [exe, 'cairo-run', '--available-gas', '800000000',
             json.dumps([[int(a) for a in args]])],
            cwd=CIRCUIT, capture_output=True, text=True, timeout=timeout,
            env=env)
        m = re.search(r'returning \[([^\]]*)\]', out.stdout)
        if not m:
            raise RuntimeError('circuit run failed: '
                               + (out.stderr or out.stdout).strip()[-1500:])
        return [int(x) for x in m.group(1).split(',')]

    def commit(self, secret):
        """Derive a member commitment from a secret. Run this CLIENT-SIDE
        (python3 mod.py commit secret=...) — only the commitment should ever
        travel; the secret stays with the member."""
        c = self._cairo([OP_COMMIT, _felt(secret)])[0]
        return {'commitment': str(c),
                'note': 'share the commitment, never the secret'}

    def build(self, timeout=600):
        return self._scarb(['build'], timeout)

    def test(self, timeout=900):
        """Run the circuit's cairo-test suite — the protocol's ground truth."""
        return self._scarb(['cairo-test'], timeout)

    def _scarb(self, args, timeout):
        exe = self._tool('scarb')
        if not exe:
            return {'ok': False, 'error': 'scarb not installed'}
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        out = subprocess.run([exe, *args], cwd=CIRCUIT, capture_output=True,
                             text=True, timeout=timeout, env=env)
        return {'ok': out.returncode == 0,
                'log': (out.stdout + out.stderr).strip()[-4000:]}

    # ---- quorum ledger ------------------------------------------------------

    def _load(self):
        try:
            return json.load(open(DATA))
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, q):
        os.makedirs(os.path.dirname(DATA), exist_ok=True)
        tmp = DATA + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(q, f, indent=2)
        os.replace(tmp, DATA)

    def _slug(self, name):
        s = re.sub(r'[^0-9a-zA-Z]+', '-', str(name)).strip('-').lower()
        if not s:
            raise ValueError('empty quorum name')
        return s

    def create(self, name, threshold, desc=None):
        """Open a quorum: it passes when `threshold` anonymous YES votes
        land. Members join by commitment before the first vote freezes
        the set."""
        slug = self._slug(name)
        ledger = self._load()
        if slug in ledger:
            return {'error': f'quorum {slug!r} already exists'}
        threshold = int(threshold)
        if threshold < 1:
            return {'error': 'threshold must be >= 1'}
        # the topic felt separates nullifiers across quorums; it is a
        # public label, so any deterministic derivation works
        topic = int.from_bytes(
            hashlib.sha256(f'zquorum:{slug}'.encode()).digest(), 'big') % PRIME
        ledger[slug] = {
            'name': slug, 'desc': desc or '', 'topic': str(topic),
            'threshold': threshold, 'members': [], 'frozen': False,
            'root': None, 'votes': [], 'created': int(time.time()),
        }
        self._save(ledger)
        return {'created': slug, 'threshold': threshold, 'topic': str(topic),
                'next': [f'm zquorum/join name={slug} commitment=<C>',
                         f'm zquorum/vote name={slug} secret=<s> choice=yes']}

    def join(self, name, commitment):
        """Register a member commitment. The set freezes at the first vote
        (same rule as ztensor's ring freeze) so the root is stable."""
        slug = self._slug(name)
        ledger = self._load()
        q = ledger.get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        if q['frozen']:
            return {'error': 'member set is frozen (voting has started)'}
        c = str(_felt(commitment))
        if c in q['members']:
            return {'error': 'commitment already registered'}
        q['members'].append(c)
        self._save(ledger)
        return {'joined': slug, 'members': len(q['members'])}

    def vote(self, name, secret, choice):
        """Cast an anonymous vote. The circuit verifies membership and
        derives the nullifier; only (nullifier, choice) is stored — the
        secret is used transiently and discarded."""
        slug = self._slug(name)
        ledger = self._load()
        q = ledger.get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        if not q['members']:
            return {'error': 'quorum has no members yet'}
        # canonical order: the circuit's root is order-sensitive on purpose
        leaves = sorted(int(c) for c in q['members'])
        r, n, ok = self._cairo(
            [OP_VOTE, _felt(secret), int(q['topic']), *leaves])
        if ok != 1:
            return {'error': 'not a member of this quorum '
                             '(commitment not in the frozen set)'}
        if any(v['nullifier'] == str(n) for v in q['votes']):
            return {'error': 'already voted (nullifier seen)',
                    'nullifier': str(n)}
        if not q['frozen']:
            q['frozen'] = True
            q['root'] = str(r)
        q['votes'].append({'nullifier': str(n), 'choice': _choice(choice),
                           'at': int(time.time())})
        self._save(ledger)
        return {'accepted': True, 'quorum': slug, 'nullifier': str(n),
                'root': str(r), 'tally': self.tally(slug)}

    def tally(self, name):
        """Public books: counts and whether the quorum is reached — but
        never who. That is the point."""
        slug = self._slug(name)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        yes = sum(1 for v in q['votes'] if v['choice'] == 1)
        no = len(q['votes']) - yes
        return {'quorum': slug, 'desc': q.get('desc', ''), 'yes': yes, 'no': no,
                'threshold': q['threshold'], 'reached': yes >= q['threshold'],
                'members': len(q['members']), 'frozen': q['frozen'],
                'root': q['root']}

    def get(self, name):
        slug = self._slug(name)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        return dict(q, tally=self.tally(slug))

    def list(self):
        return {'count': len(self._load()),
                'quorums': [self.tally(k) for k in sorted(self._load())]}

    def quorums(self):
        return self.list()

    def remove(self, name):
        slug = self._slug(name)
        ledger = self._load()
        if slug not in ledger:
            return {'error': f'no such quorum {slug!r}'}
        del ledger[slug]
        self._save(ledger)
        return {'removed': slug}

    # ---- status / self-test -------------------------------------------------

    def status(self, deep=False):
        s = {'module': 'zquorum', 'toolchain': self.toolchain(),
             'quorums': self.list()}
        if deep:
            s['circuit_test'] = self.test()
        return s

    # ---- HTTP server (app + API) -------------------------------------------

    def serve(self, port=None, background=False):
        """Run server.py — the stdlib server that serves the static console
        (app/ -> dist/) next to the JSON API at /zquorum/api/<fn>. The ledger
        logic all lives here in mod.py; server.py only maps URLs onto it."""
        port = int(port or self.port)
        env = dict(os.environ, ZQUORUM_PORT=str(port))
        cmd = [sys.executable, os.path.join(HERE, 'server.py'), str(port)]
        if background:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=HERE, env=env)
            return {'pid': proc.pid, 'port': port,
                    'app': f'http://localhost:{port}{self.base}',
                    'api': f'http://localhost:{port}{self.base}/api/<fn>'}
        subprocess.run(cmd, cwd=HERE, env=env)

    def kill(self, port=None):
        port = int(port or self.port)
        out = subprocess.run(['fuser', '-k', f'{port}/tcp'],
                             capture_output=True, text=True)
        return {'port': port, 'killed': out.returncode == 0}


if __name__ == '__main__':
    m = Mod()
    argv = sys.argv[1:]
    if not argv:
        print(json.dumps(m.status(), indent=2, default=str))
    elif argv[0] == 'serve':
        m.serve(port=argv[1] if len(argv) > 1 else None)
    else:
        fn, kw = argv[0], {}
        for a in argv[1:]:
            k, _, v = a.partition('=')
            kw[k] = v
        print(json.dumps(getattr(m, fn)(**kw), indent=2, default=str))
