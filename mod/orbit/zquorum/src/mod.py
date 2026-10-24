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
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))   # src/ — the code
ROOT = os.path.dirname(HERE)                          # module root — config + data
CIRCUIT = os.path.join(HERE, 'circuit')
DATA = os.path.join(ROOT, 'data', 'quorums.json')
BIN = os.path.expanduser('~/.local/bin')
CONTRACT = os.path.join(HERE, 'contract')
DEPLOYS = os.path.join(ROOT, 'data', 'deployments.json')
DEVNET_DIR = os.path.join(ROOT, 'data', 'devnet')
DEVNET_PORT = int(os.environ.get('ZQUORUM_DEVNET_PORT', 51302))

# Where the ZQuorum contract can live. sncast picks the accounts-file
# section from the chain id; devnet reports SN_SEPOLIA, so its accounts
# sit under alpha-sepolia too (same convention as orbit/ztensor).
NETWORKS = {
    'devnet': {'rpc': f'http://127.0.0.1:{DEVNET_PORT}/rpc',
               'section': 'alpha-sepolia', 'fee': 'eth'},
    'sepolia': {'rpc': os.environ.get(
        'ZQUORUM_RPC_SEPOLIA', 'https://starknet-sepolia-rpc.publicnode.com'),
        'section': 'alpha-sepolia', 'fee': 'strk'},
    'mainnet': {'rpc': os.environ.get(
        'ZQUORUM_RPC_MAINNET', 'https://starknet-rpc.publicnode.com'),
        'section': 'alpha-mainnet', 'fee': 'strk'},
}
DEFAULT_ACCOUNTS = os.path.expanduser(
    '~/.starknet_accounts/starknet_open_zeppelin_accounts.json')

# starknet_keccak entry-point selectors of the contract's views, so reads go
# straight over JSON-RPC (no sncast, no account). Computed with
# orbit/starknet's chain.selector().
SELECTORS = {
    'tally': '0x1096ffe4756b6ccced4fc07961d29e9d57f7de283a8d6224c11c5d8988e3919',
    'exists': '0x1e37c7ed6a06a994c20fbd2f42bf29a9f5d6c8e3fc27051e5e2335817ef219a',
    'member_count': '0x10f57feaee3ad42ea23e767735c03fef5907659c25d304b86f38d3793a83684',
    'member': '0xceb1149cdab84b395151a21d3de6707dd76fff3e7bc4e018925a9986b7f72f',
    'spent': '0x1a88df2976f6e0e24378e2dd0fe64cc4cbcc7e4055ddf10761c8f53ffdd40e6',
    'quorum_count': '0x2321f13ef2ecec97690256f0fe7b2a7d0f783ab76fbeea91519179d235afe0d',
    'quorum_at': '0x1cbb32e3db3c2586e9f82399292fb058bf7d33433caf10065dbf31c4cdf2963',
    'tallier': '0x29cb8b1619cc23f379f313bec2bd3d09058a9b855943effe00202e2a6deb47a',
}

# one writer at a time: the JSON ledger and the tallier's account nonce
_LOCK = threading.RLock()
BOOT = {}  # last boot() result, served at /zquorum/api/boot

# the STARK field prime; every value the protocol touches is a felt252
PRIME = 2 ** 251 + 17 * 2 ** 192 + 1

OP_COMMIT, OP_NULLIFIER, OP_ROOT, OP_VOTE = 1, 2, 3, 4
MAX_MEMBERS = 64  # mirrors contract/ MAX_MEMBERS


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
    path = ROOT
    config = json.load(open(os.path.join(ROOT, 'config.json')))
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
                'circuit_built': self._circuit_fresh(),
                'quorums': len(self._load()),
                'starknet': {
                    'devnet_up': self._devnet_alive(),
                    'deployments': {k: v['address'] for k, v in
                                    self._deploys().items()},
                    'anchored': sum(1 for q in self._load().values()
                                    if q.get('chain'))}}

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
        tools = {t: os.path.exists(os.path.join(BIN, t))
                 for t in ('snforge', 'sncast', 'starknet-devnet')}
        return {'scarb': {'path': exe, 'version': v}, 'circuit': CIRCUIT,
                'contract': CONTRACT, 'starknet_tools': tools}

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
        # --no-build when the compiled program is current: ~0.3s vs ~10s
        fresh = self._circuit_fresh()
        if not fresh:
            with _LOCK:
                if not self._circuit_fresh():
                    b = self.build()
                    if not b['ok']:
                        raise RuntimeError('circuit build failed: ' + b['log'])
        out = subprocess.run(
            [exe, 'cairo-run', '--no-build', '--available-gas', '800000000',
             json.dumps([[int(a) for a in args]])],
            cwd=CIRCUIT, capture_output=True, text=True, timeout=timeout,
            env=env)
        m = re.search(r'returning \[([^\]]*)\]', out.stdout)
        if not m:
            raise RuntimeError('circuit run failed: '
                               + (out.stderr or out.stdout).strip()[-1500:])
        return [int(x) for x in m.group(1).split(',')]

    def _circuit_fresh(self):
        art = os.path.join(CIRCUIT, 'target', 'dev', 'zq.sierra.json')
        if not os.path.exists(art):
            return False
        src = [os.path.join(CIRCUIT, 'Scarb.toml')] + [
            os.path.join(r, f) for r, _, fs in os.walk(os.path.join(CIRCUIT, 'src'))
            for f in fs]
        return os.path.getmtime(art) >= max(os.path.getmtime(f) for f in src)

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

    def _scarb(self, args, timeout, cwd=CIRCUIT):
        exe = self._tool('scarb')
        if not exe:
            return {'ok': False, 'error': 'scarb not installed'}
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        out = subprocess.run([exe, *args], cwd=cwd, capture_output=True,
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

    def create(self, name, threshold, desc=None, network=None):
        """Open a quorum: it passes when `threshold` anonymous YES votes
        land. Members join by commitment before the first vote freezes
        the set. network=devnet|sepolia|mainnet also opens it on the
        ZQuorum Starknet contract (deployed on demand for devnet) and
        mirrors every later join and vote there."""
        slug = self._slug(name)
        threshold = int(threshold)
        if threshold < 1:
            return {'error': 'threshold must be >= 1'}
        with _LOCK:
            ledger = self._load()
            if slug in ledger:
                return {'error': f'quorum {slug!r} already exists'}
            # the topic felt separates nullifiers across quorums; it is a
            # public label, so any deterministic derivation works
            topic = int.from_bytes(hashlib.sha256(
                f'zquorum:{slug}'.encode()).digest(), 'big') % PRIME
            ledger[slug] = {
                'name': slug, 'desc': desc or '', 'topic': str(topic),
                'threshold': threshold, 'members': [], 'frozen': False,
                'root': None, 'votes': [], 'created': int(time.time()),
            }
            self._save(ledger)
        out = {'created': slug, 'threshold': threshold, 'topic': str(topic),
               'next': [f'm zquorum/join name={slug} commitment=<C>',
                        f'm zquorum/vote name={slug} secret=<s> choice=yes']}
        if network:
            out['chain'] = self.anchor(slug, network)
        return out

    def join(self, name, commitment):
        """Register a member commitment. The set freezes at the first vote
        (same rule as ztensor's ring freeze) so the root is stable."""
        slug = self._slug(name)
        with _LOCK:
            ledger = self._load()
            q = ledger.get(slug)
            if not q:
                return {'error': f'no such quorum {slug!r}'}
            if q['frozen']:
                return {'error': 'member set is frozen (voting has started)'}
            c = str(_felt(commitment))
            if c in q['members']:
                return {'error': 'commitment already registered'}
            if len(q['members']) >= MAX_MEMBERS:
                return {'error': f'member set full ({MAX_MEMBERS})'}
            tx = None
            if q.get('chain'):  # chain first: a failed tx records nothing
                try:
                    tx = self._invoke(q['chain']['network'], 'join',
                                      [int(q['topic']), int(c)])
                except Exception as e:  # noqa: BLE001
                    return {'error': f'starknet join failed: {e}'}
                q['chain']['txs'] = q['chain'].get('txs', 0) + 1
                q['chain']['last_tx'] = tx
            q['members'].append(c)
            self._save(ledger)
        out = {'joined': slug, 'members': len(q['members'])}
        if tx:
            out['tx'] = tx
        return out

    def vote(self, name, secret, choice):
        """Cast an anonymous vote. The circuit verifies membership and
        derives the nullifier; only (nullifier, choice) is stored — the
        secret is used transiently and discarded. On an anchored quorum the
        (nullifier, choice) pair is also posted to the Starknet contract,
        which re-enforces the freeze, the one-vote rule and the tally."""
        slug = self._slug(name)
        ch = _choice(choice)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        if not q['members']:
            return {'error': 'quorum has no members yet'}
        # canonical order: the circuit's root is order-sensitive on purpose
        leaves = sorted(int(c) for c in q['members'])
        # the circuit runs outside the lock (it is the slow part); the
        # member set cannot change under us once frozen, and if this vote
        # is the one that freezes it we re-check below
        r, n, ok = self._cairo(
            [OP_VOTE, _felt(secret), int(q['topic']), *leaves])
        if ok != 1:
            return {'error': 'not a member of this quorum '
                             '(commitment not in the frozen set)'}
        with _LOCK:
            ledger = self._load()
            q = ledger.get(slug)
            if not q:
                return {'error': f'no such quorum {slug!r}'}
            if sorted(int(c) for c in q['members']) != leaves:
                return {'error': 'member set changed while voting; retry'}
            if any(v['nullifier'] == str(n) for v in q['votes']):
                return {'error': 'already voted (nullifier seen)',
                        'nullifier': str(n)}
            tx = None
            if q.get('chain'):
                try:
                    tx = self._invoke(q['chain']['network'], 'vote',
                                      [int(q['topic']), n, ch])
                except Exception as e:  # noqa: BLE001
                    return {'error': f'starknet vote failed: {e}'}
                q['chain']['txs'] = q['chain'].get('txs', 0) + 1
                q['chain']['last_tx'] = tx
            if not q['frozen']:
                q['frozen'] = True
                q['root'] = str(r)
            q['votes'].append({'nullifier': str(n), 'choice': ch,
                               'at': int(time.time())})
            self._save(ledger)
        out = {'accepted': True, 'quorum': slug, 'nullifier': str(n),
               'root': str(r), 'tally': self.tally(slug)}
        if tx:
            out['tx'] = tx
        return out

    def tally(self, name):
        """Public books: counts and whether the quorum is reached — but
        never who. That is the point."""
        slug = self._slug(name)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        yes = sum(1 for v in q['votes'] if v['choice'] == 1)
        no = len(q['votes']) - yes
        return {'quorum': slug, 'name': slug, 'desc': q.get('desc', ''),
                'yes': yes, 'no': no,
                'threshold': q['threshold'], 'reached': yes >= q['threshold'],
                'members': len(q['members']), 'frozen': q['frozen'],
                'root': q['root'], 'chain': q.get('chain')}

    def get(self, name):
        slug = self._slug(name)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        return dict(q, tally=self.tally(slug))

    def list(self):
        ledger = self._load()
        return {'count': len(ledger),
                'quorums': [self.tally(k) for k in sorted(ledger)]}

    def quorums(self):
        return self.list()

    def remove(self, name):
        """Drop a quorum from the local ledger. An anchored quorum's
        on-chain books are permanent; only the local mirror goes."""
        slug = self._slug(name)
        with _LOCK:
            ledger = self._load()
            if slug not in ledger:
                return {'error': f'no such quorum {slug!r}'}
            chain = ledger[slug].get('chain')
            del ledger[slug]
            self._save(ledger)
        out = {'removed': slug}
        if chain:
            out['note'] = (f"still on {chain['network']} at "
                           f"{chain['address']} (chain books are permanent)")
        return out

    # ---- Starknet: the ZQuorum contract -------------------------------------
    #
    # contract/ (Scarb package `zquorum`, depends on circuit/ `zq` by path)
    # holds the same books on-chain: commitments, spent nullifiers, tally,
    # and the frozen root computed with zq::root. The anchor is the
    # contract's TALLIER: it checks membership with the circuit (as above)
    # and posts only (nullifier, choice) from its own account, so the tx
    # sender never identifies a voter.

    def _env(self):
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        return env

    def _deploys(self):
        try:
            return json.load(open(DEPLOYS))
        except Exception:  # noqa: BLE001
            return {}

    def _save_json(self, path, obj, private=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     0o600 if private else 0o644)
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)

    def _devnet_alive(self):
        try:
            urllib.request.urlopen(
                f'http://127.0.0.1:{DEVNET_PORT}/is_alive', timeout=1).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def devnet(self, action='status'):
        """start | stop | reset | status for zquorum's local starknet-devnet
        (:51302, seed 0, account 0 = the tallier). State persists across
        restarts in data/devnet/devnet.json."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{DEVNET_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.devnet('stop')
            time.sleep(0.5)
            with _LOCK:
                d = self._deploys()
                d.pop('devnet', None)
                self._save_json(DEPLOYS, d)
                ledger = self._load()
                for q in ledger.values():
                    if (q.get('chain') or {}).get('network') == 'devnet':
                        q.pop('chain')
                self._save(ledger)
            for f in ('devnet.json', 'accounts.json'):
                p = os.path.join(DEVNET_DIR, f)
                if os.path.exists(p):
                    os.remove(p)
            return self.devnet('start')
        if action == 'start' and not self._devnet_alive():
            exe = os.path.join(BIN, 'starknet-devnet')
            if not os.path.exists(exe):
                return {'up': False,
                        'error': 'starknet-devnet not in ~/.local/bin'}
            os.makedirs(DEVNET_DIR, exist_ok=True)
            dump = os.path.join(DEVNET_DIR, 'devnet.json')
            log = open(os.path.join(DEVNET_DIR, 'devnet.log'), 'a')
            subprocess.Popen(
                [exe, '--host', '127.0.0.1', '--port', str(DEVNET_PORT),
                 '--seed', '0', '--accounts', '2', '--dump-on', 'block',
                 '--dump-path', dump],
                stdout=log, stderr=log, cwd=DEVNET_DIR, start_new_session=True)
            for _ in range(300):
                if self._devnet_alive():
                    break
                time.sleep(0.1)
            else:
                return {'up': False, 'error': 'devnet did not come up '
                        '(see data/devnet/devnet.log)'}
        up = self._devnet_alive()
        if up and not os.path.exists(os.path.join(DEVNET_DIR, 'accounts.json')):
            rows = json.loads(urllib.request.urlopen(
                f'http://127.0.0.1:{DEVNET_PORT}/predeployed_accounts',
                timeout=5).read())
            cls = self._rpc('devnet', 'starknet_getClassHashAt',
                            ['latest', rows[0]['address']])
            book = {'tallier': {'address': rows[0]['address'],
                                'class_hash': cls, 'deployed': True,
                                'legacy': False, 'type': 'open_zeppelin',
                                'private_key': rows[0]['private_key'],
                                'public_key': rows[0]['public_key']}}
            self._save_json(os.path.join(DEVNET_DIR, 'accounts.json'),
                            {NETWORKS['devnet']['section']: book},
                            private=True)
        out = {'up': up, 'port': DEVNET_PORT,
               'rpc': NETWORKS['devnet']['rpc']}
        if up:
            out['block'] = self._rpc('devnet', 'starknet_blockNumber', [])
        return out

    def _rpc(self, network, method, params, rpc=None):
        url = rpc or self._deploys().get(network, {}).get('rpc') \
            or NETWORKS[network]['rpc']
        req = urllib.request.Request(
            url, data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                  'params': params}).encode(),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'zquorum/0.3'})
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if 'error' in r:
            err = r['error']
            data = err.get('data') if isinstance(err, dict) else None
            raise RuntimeError(f"{err.get('message', err)}"
                               + (f': {data}' if data else ''))
        return r['result']

    def _ctx(self, network, account=None, accounts_file=None, rpc=None):
        if network not in NETWORKS:
            raise ValueError(f'network must be one of {sorted(NETWORKS)}')
        net = dict(NETWORKS[network], network=network)
        d = self._deploys().get(network, {})
        if rpc or d.get('rpc'):
            net['rpc'] = rpc or d['rpc']
        if network == 'devnet':
            st = self.devnet('start')
            if not st.get('up'):
                raise RuntimeError(st.get('error', 'devnet down'))
            net['accounts_file'] = os.path.join(DEVNET_DIR, 'accounts.json')
            net['account'] = 'tallier'
        else:
            net['accounts_file'] = accounts_file or d.get(
                'accounts_file') or DEFAULT_ACCOUNTS
            net['account'] = account or d.get('account')
            if not net['account']:
                raise ValueError(
                    f'{network}: pass account=<sncast account name> (create '
                    'one with `sncast account create`, fund it with STRK, '
                    'then `sncast account deploy`)')
        return net

    def _sncast(self, net, args):
        cmd = [os.path.join(BIN, 'sncast'), '--accounts-file',
               net['accounts_file'], '--account', net['account'], '--json']
        if net['network'] != 'devnet':
            cmd.append('--wait')
        out = subprocess.run(cmd + args + ['--url', net['rpc']],
                             cwd=CONTRACT, capture_output=True, text=True,
                             timeout=600, env=self._env())
        text = out.stdout + '\n' + out.stderr
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"command"' in line:
                r = json.loads(line)
                if 'error' not in r:
                    return r
        m = re.findall(r"Failure reason: (?:0x[0-9a-f]+ )?\(?'([^']*)'\)?",
                       text)
        raise RuntimeError(m[-1] if m else text.strip()[-800:])

    def _invoke(self, network, fn, calldata):
        net = self._ctx(network)
        addr = self._contract(network)['address']
        r = self._sncast(net, ['invoke', '--contract-address', addr,
                               '--function', fn, '--fee-token', net['fee'],
                               '--calldata', *[hex(int(x)) for x in calldata]])
        return r.get('transaction_hash')

    def _view(self, network, fn, calldata=(), address=None):
        """Read a view over plain JSON-RPC: no sncast, no account."""
        if network == 'devnet' and not self._devnet_alive():
            # e.g. after a host reboot: the chain state is in the dump
            if not self.devnet('start').get('up'):
                raise RuntimeError('devnet is down (m zquorum/devnet action=start)')
        addr = address or self._contract(network)['address']
        res = self._rpc(network, 'starknet_call', [
            {'contract_address': addr, 'entry_point_selector': SELECTORS[fn],
             'calldata': [hex(int(x)) for x in calldata]}, 'latest'])
        return [int(x, 16) for x in res]

    def _contract(self, network):
        d = self._deploys().get(network)
        if not d:
            raise RuntimeError(f'ZQuorum not deployed on {network}; '
                               f'run m zquorum/deploy network={network}')
        return d

    def contract_build(self):
        """scarb build of the Starknet contract (sierra + casm)."""
        return self._scarb(['build'], 900, cwd=CONTRACT)

    def contract_test(self):
        """snforge suite of the Starknet contract."""
        exe = os.path.join(BIN, 'snforge')
        if not os.path.exists(exe):
            return {'ok': False, 'error': 'snforge not installed'}
        out = subprocess.run([exe, 'test'], cwd=CONTRACT, capture_output=True,
                             text=True, timeout=1800, env=self._env())
        log = out.stdout + out.stderr
        m = re.search(r'Tests: (\d+) passed, (\d+) failed', log)
        return {'ok': out.returncode == 0,
                'passed': int(m.group(1)) if m else None,
                'failed': int(m.group(2)) if m else None,
                'log': log.strip()[-3000:]}

    def deploy(self, network='devnet', account=None, accounts_file=None,
               rpc=None, force=False):
        """Declare + deploy the ZQuorum contract with this anchor's account
        as tallier. devnet needs nothing (it is started on demand); for
        sepolia/mainnet pass account= (an sncast account funded with STRK),
        optionally accounts_file= and rpc=."""
        with _LOCK:
            have = self._deploys().get(network)
            if have and not force:
                if network != 'devnet' or self._contract_alive(network):
                    return {'ok': True, 'network': network, 'existing': True,
                            **have}
            net = self._ctx(network, account, accounts_file, rpc)
            b = self.contract_build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log']}
            try:
                dec = self._sncast(net, ['declare', '--contract-name',
                                         'ZQuorum', '--fee-token', net['fee']])
                class_hash = dec['class_hash']
            except RuntimeError as e:
                m = re.search(r'(0x[0-9a-fA-F]+) is already declared', str(e))
                if not m:
                    return {'ok': False, 'stage': 'declare', 'error': str(e)}
                class_hash = m.group(1)
            tallier = self._load_accounts(net).get('address')
            if not tallier:
                return {'ok': False, 'error': f"account {net['account']!r} not "
                        f"in {net['accounts_file']} [{net['section']}]"}
            try:
                dep = self._sncast(net, ['deploy', '--class-hash', class_hash,
                                         '--fee-token', net['fee'],
                                         '--constructor-calldata', tallier])
            except RuntimeError as e:
                return {'ok': False, 'stage': 'deploy', 'error': str(e)}
            rec = {'address': dep['contract_address'],
                   'class_hash': class_hash, 'tx': dep['transaction_hash'],
                   'tallier': tallier, 'rpc': net['rpc'],
                   'at': int(time.time())}
            if network != 'devnet':
                rec.update(account=net['account'],
                           accounts_file=net['accounts_file'])
            d = self._deploys()
            d[network] = rec
            self._save_json(DEPLOYS, d)
            # quorums anchored to a previous deployment are detached
            ledger = self._load()
            for q in ledger.values():
                c = q.get('chain') or {}
                if c.get('network') == network and c.get('address') != rec['address']:
                    q.pop('chain')
            self._save(ledger)
        return {'ok': True, 'network': network, **rec}

    def _load_accounts(self, net):
        try:
            book = json.load(open(net['accounts_file']))
        except Exception:  # noqa: BLE001
            return {}
        return book.get(net['section'], {}).get(net['account'], {})

    def _contract_alive(self, network):
        try:
            d = self._deploys()[network]
            self._rpc(network, 'starknet_getClassHashAt',
                      ['latest', d['address']])
            return True
        except Exception:  # noqa: BLE001
            return False

    def anchor(self, name, network='devnet'):
        """Put an existing quorum on Starknet: create it on the ZQuorum
        contract, replay its members and (nullifier, choice) votes, and
        mirror every later join/vote. Deploys on demand for devnet."""
        slug = self._slug(name)
        with _LOCK:
            q = self._load().get(slug)
            if not q:
                return {'error': f'no such quorum {slug!r}'}
            if q.get('chain'):
                return {'error': f"already anchored on {q['chain']['network']}",
                        'chain': q['chain']}
            if network not in self._deploys() or (
                    network == 'devnet' and not self._contract_alive(network)):
                if network != 'devnet':
                    return {'error': f'ZQuorum not deployed on {network}; '
                            f'run m zquorum/deploy network={network} '
                            'account=<sncast account>'}
                dep = self.deploy('devnet', force=True)
                if not dep.get('ok'):
                    return {'error': 'devnet deploy failed', 'deploy': dep}
            addr = self._contract(network)['address']
            topic = int(q['topic'])
            if self._view(network, 'exists', [topic])[0]:
                return {'error': f'topic already on {network} at {addr}'}
            txs = []
            try:
                txs.append(self._invoke(network, 'create',
                                        [topic, q['threshold']]))
                for c in q['members']:
                    txs.append(self._invoke(network, 'join', [topic, int(c)]))
                for v in q['votes']:
                    txs.append(self._invoke(network, 'vote', [
                        topic, int(v['nullifier']), int(v['choice'])]))
            except Exception as e:  # noqa: BLE001
                return {'error': f'starknet anchor failed after {len(txs)} '
                        f'tx(s): {e}', 'txs': txs}
            ledger = self._load()
            ledger[slug]['chain'] = {'network': network, 'address': addr,
                                     'txs': len(txs), 'last_tx': txs[-1],
                                     'at': int(time.time())}
            self._save(ledger)
        return {'anchored': slug, 'network': network, 'address': addr,
                'txs': txs, 'verify': self.onchain(slug)}

    def onchain(self, name, network=None):
        """Read a quorum's books straight off the ZQuorum contract and
        diff them against the local ledger (yes, no, threshold, members,
        frozen, root). match=false means the mirror drifted."""
        slug = self._slug(name)
        q = self._load().get(slug)
        if not q:
            return {'error': f'no such quorum {slug!r}'}
        network = network or (q.get('chain') or {}).get('network') or 'devnet'
        topic = int(q['topic'])
        try:
            if not self._view(network, 'exists', [topic])[0]:
                return {'quorum': slug, 'network': network, 'onchain': False}
            y, n, t, m, fz, root, reached = self._view(network, 'tally', [topic])
        except Exception as e:  # noqa: BLE001
            return {'quorum': slug, 'network': network, 'error': str(e)}
        chain = {'yes': y, 'no': n, 'threshold': t, 'members': m,
                 'frozen': bool(fz), 'root': str(root) if fz else None,
                 'reached': bool(reached)}
        local = self.tally(slug)
        diff = [k for k in chain if chain[k] != local.get(k)]
        return {'quorum': slug, 'network': network, 'onchain': True,
                'address': self._contract(network)['address'],
                'chain': chain, 'match': not diff, 'diff': diff}

    def chain(self, network='devnet'):
        """Chain status for one network: devnet liveness, the deployment,
        and every quorum topic the contract holds."""
        out = {'network': network, 'deployment': self._deploys().get(network)}
        if network == 'devnet':
            out['devnet'] = {'up': self._devnet_alive(), 'port': DEVNET_PORT}
            if not out['devnet']['up']:
                return out
        if not out['deployment']:
            return out
        try:
            n = self._view(network, 'quorum_count')[0]
            out['quorums_onchain'] = n
            out['block'] = self._rpc(network, 'starknet_blockNumber', [])
        except Exception as e:  # noqa: BLE001
            out['error'] = str(e)
        return out

    def deployments(self):
        return self._deploys()

    # ---- status / self-test -------------------------------------------------

    def status(self, deep=False):
        s = {'module': 'zquorum', 'toolchain': self.toolchain(),
             'quorums': self.list(),
             'starknet': {n: self.chain(n) for n in
                          sorted(set(self._deploys()) | {'devnet'})}}
        if deep:
            s['circuit_test'] = self.test()
        return s

    # ---- auto-run ------------------------------------------------------------

    def boot(self):
        """Bring every moving part up so nothing waits on a first click:
        compile the circuit if stale, start the devnet, make sure ZQuorum is
        deployed on it, and build the console if dist/ is missing. server.py
        runs this in a background thread on start; safe to re-run."""
        steps = {}
        for step, fn in (
                ('circuit', lambda: {'ok': True, 'fresh': True}
                 if self._circuit_fresh() else self.build()),
                ('devnet', lambda: self.devnet('start')),
                ('contract', lambda: self.deploy('devnet')),
                ('console', self._ensure_console)):
            try:
                r = fn()
            except Exception as e:  # noqa: BLE001
                r = {'ok': False, 'error': str(e)}
            steps[step] = {k: r.get(k) for k in
                           ('ok', 'up', 'existing', 'address', 'error', 'fresh')
                           if r.get(k) is not None}
        BOOT.update(steps=steps, at=int(time.time()),
                    ok=all(v.get('ok', v.get('up')) for v in steps.values()))
        return dict(BOOT)

    def _ensure_console(self):
        if os.path.exists(os.path.join(HERE, 'dist', 'index.html')):
            return {'ok': True, 'existing': True}
        out = subprocess.run(['bash', os.path.join(HERE, 'build.sh')],
                             cwd=HERE, capture_output=True, text=True,
                             timeout=900)
        return {'ok': out.returncode == 0,
                'error': None if out.returncode == 0
                else (out.stdout + out.stderr).strip()[-1500:]}

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
