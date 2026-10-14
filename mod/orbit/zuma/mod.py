"""zuma — an anonymous subnet on Starknet.

Bittensor-style miners and validators where neither side knows who the
other is — only that they are in the registered set. Every action is a
scope-linkable ring signature (LSAG over the STARK curve) that the Starknet
contract verifies natively with the EC builtin:

    submit  miner proves "one of the miners" for (subnet, epoch); its key
            image is the epoch TAG: one submission per miner per epoch,
            unlinkable to its key or to its tags in other epochs
    score   validator proves "one of the validators" for (subnet, epoch,
            tag); its key image is a nullifier: one score per validator per
            tag, and its scores on different tags are unlinkable
    claim   after the epoch, the miner re-signs in the epoch scope (same
            image = same tag) and credits its share to a recipient

The anonymity set is the ring, so privacy is only as good as the ring is
large: min_ring is enforced on-chain, rings max out at 64 (verification
cost is linear in ring size), and the tx sender is public — so actions go
through a RELAYER account, never the member's own.

Layout:
    contracts/  Scarb package `zuma` — lsag.cairo (the scheme) + zuma.cairo
                (the Starknet contract) + snforge tests
    prover/     Cairo program over the SAME lsag module (keygen/sign/verify
                via `scarb cairo-run`), so signer and verifier never drift
    data/       keys.json (0600, secrets — never leave this box),
                deployments.json, devnet/ (local chain state)

Toolchain: scarb 2.8.4, snforge/sncast 0.31, starknet-devnet 0.2.4 in
~/.local/bin (shared with orbit/cairo).
"""

import hashlib
import json
import os
import random
import re
import secrets as _secrets
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACTS = os.path.join(HERE, 'contracts')
PROVER = os.path.join(HERE, 'prover')
DATA = os.path.join(HERE, 'data')
KEYS = os.path.join(DATA, 'keys.json')
DEPLOYS = os.path.join(DATA, 'deployments.json')
DEVNET_DIR = os.path.join(DATA, 'devnet')
LAST_SIM = os.path.join(DATA, 'last_sim.json')
CLASSES = os.path.join(DATA, 'classes.json')  # {network: {src, class_hash}}
BIN = os.path.expanduser('~/.local/bin')

PRIME = 2 ** 251 + 17 * 2 ** 192 + 1
ORDER = 0x800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f
ROLES = {'miner': 1, 'validator': 2}
MAX_RING = 64

DEVNET_PORT = int(os.environ.get('ZUMA_DEVNET_PORT', 51332))
NETWORKS = {
    # sncast picks the accounts-file section from the chain id; devnet
    # reports SN_SEPOLIA, so its accounts live under alpha-sepolia too
    'devnet': {'rpc': f'http://127.0.0.1:{DEVNET_PORT}/rpc',
               'section': 'alpha-sepolia', 'fee': 'eth'},
    'sepolia': {'rpc': os.environ.get(
        'ZUMA_RPC_SEPOLIA', 'https://starknet-sepolia-rpc.publicnode.com'),
        'section': 'alpha-sepolia', 'fee': 'strk'},
    'mainnet': {'rpc': os.environ.get(
        'ZUMA_RPC_MAINNET', 'https://starknet-rpc.publicnode.com'),
        'section': 'alpha-mainnet', 'fee': 'strk'},
}
DEFAULT_ACCOUNTS = os.path.expanduser(
    '~/.starknet_accounts/starknet_open_zeppelin_accounts.json')


def _felt(v):
    """ints, decimal, 0x-hex, or any text (sha256 % PRIME) -> felt int."""
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


def _rand():
    return _secrets.randbelow(ORDER - 1) + 1


def _hex(v):
    return hex(int(v))


def _selector(name):
    """starknet_keccak(name): keccak256 truncated to 250 bits."""
    from eth_hash.auto import keccak
    return hex(int.from_bytes(keccak(name.encode()), 'big') & ((1 << 250) - 1))


def _env():
    env = dict(os.environ)
    env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
    return env


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:  # noqa: BLE001
        return default


def _save(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 0o600 if private else 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


class Mod:
    description = ('Anonymous subnets on Starknet: miners and validators '
                   'act through ring signatures over the registered set, so '
                   'neither side learns who the other is.')
    path = HERE
    config = json.load(open(os.path.join(HERE, 'config.json')))

    # ---- module protocol ---------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {'name': 'zuma', 'description': self.description,
                'version': self.config.get('version'),
                'protocol': self.config.get('protocol'),
                'networks': sorted(NETWORKS),
                'deployments': _load(DEPLOYS, {}),
                'keys': self.keys(),
                'fns': self.config.get('fns')}

    def health(self):
        return {'ok': True, 'module': 'zuma',
                'tools': {t: os.path.exists(os.path.join(BIN, t)) for t in
                          ('scarb', 'snforge', 'sncast', 'starknet-devnet')},
                'contract_built': os.path.exists(os.path.join(
                    CONTRACTS, 'target', 'dev',
                    'zuma_Zuma.contract_class.json')),
                'prover_built': os.path.isdir(os.path.join(PROVER, 'target')),
                'devnet_up': self._devnet_alive()}

    def readme(self):
        p = os.path.join(HERE, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ---- build / test --------------------------------------------------------

    def _run(self, cmd, cwd, timeout=900):
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                             timeout=timeout, env=_env())
        log = '\n'.join(l for l in (out.stdout + out.stderr).splitlines()
                        if not l.startswith(('warning:', '   ', ' -->')))
        return {'ok': out.returncode == 0, 'log': log.strip()[-4000:]}

    def build(self):
        """Compile the contract (Sierra+CASM) and the prover."""
        c = self._run([os.path.join(BIN, 'scarb'), 'build'], CONTRACTS)
        p = self._run([os.path.join(BIN, 'scarb'), 'build'], PROVER)
        return {'ok': c['ok'] and p['ok'], 'contracts': c, 'prover': p}

    def test(self):
        """snforge suite: LSAG soundness/linkability + the full epoch flow
        and every rejection path. First run compiles snforge_std (~1 min)."""
        r = self._run([os.path.join(BIN, 'snforge'), 'test'], CONTRACTS)
        m = re.search(r'Tests: (\d+) passed, (\d+) failed', r['log'])
        r['passed'], r['failed'] = (int(m.group(1)), int(m.group(2))) if m \
            else (0, None)
        r['tests'] = re.findall(r'\[(PASS|FAIL)\] \S+::(\w+)', r['log'])
        return r

    # ---- the prover (Cairo, same lsag module the contract runs) ------------

    def _prove(self, args, timeout=120):
        if not os.path.isdir(os.path.join(PROVER, 'target')):
            b = self._run([os.path.join(BIN, 'scarb'), 'build'], PROVER)
            if not b['ok']:
                raise RuntimeError('prover build failed: ' + b['log'][-800:])
        out = subprocess.run(
            [os.path.join(BIN, 'scarb'), 'cairo-run', '--no-build',
             '--available-gas', '4000000000',
             json.dumps([[int(a) for a in args]])],
            cwd=PROVER, capture_output=True, text=True, timeout=timeout,
            env=_env())
        vals = [int(x) for x in re.findall(r'^Z (\d+)$', out.stdout, re.M)]
        if out.returncode != 0 or 'Run panicked' in out.stdout:
            raise RuntimeError('prover: ' + (out.stdout + out.stderr)
                               .strip()[-800:])
        return vals

    # ---- keys (secrets stay in data/keys.json, 0600) -----------------------

    def keygen(self, name, secret=None):
        """Create a member key. Without `secret`, a fresh random one is
        drawn. Only the public key ever leaves this box."""
        keys = _load(KEYS, {})
        if name in keys and secret is None:
            return {'error': f'key {name!r} exists', **self.pubkey(name)}
        x = _felt(secret) % ORDER if secret is not None else _rand()
        if x == 0:
            return {'error': 'secret reduces to zero'}
        px, py, kid = self._prove([1, x])
        keys[name] = {'secret': hex(x), 'x': hex(px), 'y': hex(py),
                      'key_id': hex(kid), 'at': int(time.time())}
        _save(KEYS, keys, private=True)
        return self.pubkey(name)

    def keys(self):
        return [{'name': n, 'x': k['x'], 'y': k['y'], 'key_id': k['key_id']}
                for n, k in sorted(_load(KEYS, {}).items())]

    def pubkey(self, name):
        k = _load(KEYS, {}).get(name)
        if not k:
            raise ValueError(f'no key {name!r} (keygen it first)')
        return {'name': name, 'x': k['x'], 'y': k['y'], 'key_id': k['key_id']}

    def _secret(self, name):
        k = _load(KEYS, {}).get(name)
        if not k:
            raise ValueError(f'no key {name!r}')
        return int(k['secret'], 16)

    def sign(self, name, scope, msg, ring):
        """Low-level: ring-sign `msg` in `scope` as key `name`. `ring` is a
        list of {x,y} (yours included, any order — the prover sorts)."""
        pts = [(int(str(p['x']), 0), int(str(p['y']), 0)) for p in ring]
        n = len(pts)
        args = [2, _felt(scope), _felt(msg), self._secret(name), _rand(), n]
        for x, y in pts:
            args += [x, y]
        args += [_rand() for _ in range(n)]
        v = self._prove(args)
        n = v[0]
        ring_sorted = [{'x': hex(v[1 + 2 * i]), 'y': hex(v[2 + 2 * i])}
                       for i in range(n)]
        o = 1 + 2 * n
        return {'ring': ring_sorted, 'c0': hex(v[o]),
                's': [hex(s) for s in v[o + 1:o + 1 + n]],
                'image': {'x': hex(v[o + 1 + n]), 'y': hex(v[o + 2 + n])},
                'tag': hex(v[o + 3 + n])}

    def verify(self, scope, msg, sig):
        """Off-chain check of a sign() result (same code as the contract)."""
        n = len(sig['ring'])
        args = [3, _felt(scope), _felt(msg), n]
        for p in sig['ring']:
            args += [int(p['x'], 16), int(p['y'], 16)]
        args += [int(sig['c0'], 16), *[int(s, 16) for s in sig['s']],
                 int(sig['image']['x'], 16), int(sig['image']['y'], 16)]
        return {'valid': self._prove(args) == [1]}

    def scope(self, kind, subnet, epoch, tag=0):
        """mine scope = H('zuma-mine', subnet, epoch); score scope adds tag.
        `subnet` is the contract address."""
        k = 1 if str(kind) in ('1', 'mine', 'miner') else 2
        return self._prove([5, k, _felt(subnet), int(epoch), _felt(tag)])[0]

    def mytag(self, name, epoch, network='devnet'):
        """Your own tag for an epoch — only the key holder can compute it."""
        addr = self._deployment(network)['address']
        sc = self.scope('mine', addr, epoch)
        return {'epoch': int(epoch),
                'tag': hex(self._prove([4, self._secret(name), sc])[2])}

    # ---- local devnet --------------------------------------------------------

    def _devnet_alive(self):
        try:
            urllib.request.urlopen(
                f'http://127.0.0.1:{DEVNET_PORT}/is_alive', timeout=1).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _devnet_get(self, path):
        return json.loads(urllib.request.urlopen(
            f'http://127.0.0.1:{DEVNET_PORT}{path}', timeout=5).read())

    def _devnet_post(self, path, body=None):
        req = urllib.request.Request(
            f'http://127.0.0.1:{DEVNET_PORT}{path}',
            data=json.dumps(body or {}).encode(),
            headers={'Content-Type': 'application/json'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def devnet(self, action='status'):
        """start | stop | reset | status for the local starknet-devnet on
        :51332 (seed 0; account 0 = owner, account 1 = relayer)."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{DEVNET_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.devnet('stop')
            time.sleep(0.5)
            d = _load(DEPLOYS, {})
            d.pop('devnet', None)
            _save(DEPLOYS, d)
            for f in ('devnet.json', 'accounts.json'):
                p = os.path.join(DEVNET_DIR, f)
                if os.path.exists(p):
                    os.remove(p)
            return self.devnet('start')
        if action == 'start' and not self._devnet_alive():
            exe = os.path.join(BIN, 'starknet-devnet')
            if not os.path.exists(exe):
                return {'up': False, 'error': 'starknet-devnet not in ~/.local/bin'}
            os.makedirs(DEVNET_DIR, exist_ok=True)
            log = open(os.path.join(DEVNET_DIR, 'devnet.log'), 'a')
            subprocess.Popen(
                [exe, '--host', '127.0.0.1', '--port', str(DEVNET_PORT),
                 '--seed', '0', '--accounts', '3', '--dump-on', 'block',
                 '--dump-path', os.path.join(DEVNET_DIR, 'devnet.json')],
                stdout=log, stderr=log, cwd=DEVNET_DIR, start_new_session=True)
            for _ in range(150):
                if self._devnet_alive():
                    break
                time.sleep(0.1)
            else:
                return {'up': False, 'error': 'devnet did not come up'}
        up = self._devnet_alive()
        if up and not os.path.exists(os.path.join(DEVNET_DIR, 'accounts.json')):
            self._devnet_accounts()
        out = {'up': up, 'port': DEVNET_PORT}
        if up:
            out['block'] = self._block('devnet')
        return out

    def _devnet_accounts(self):
        rows = self._devnet_get('/predeployed_accounts')
        cls = self._rpc('devnet', 'starknet_getClassHashAt',
                        ['latest', rows[0]['address']])
        book = {name: {'address': r['address'], 'class_hash': cls,
                       'deployed': True, 'legacy': False,
                       'private_key': r['private_key'],
                       'public_key': r['public_key'],
                       'type': 'open_zeppelin'}
                for name, r in zip(('owner', 'relayer', 'payee'), rows)}
        _save(os.path.join(DEVNET_DIR, 'accounts.json'),
              {NETWORKS['devnet']['section']: book}, private=True)

    def _rpc(self, network, method, params):
        req = urllib.request.Request(
            NETWORKS[network]['rpc'], data=json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': method,
                 'params': params}).encode(),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'zuma/0.2'})
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if 'error' in r:
            raise RuntimeError(r['error'])
        return r['result']

    def _block(self, network):
        return self._rpc(network, 'starknet_blockNumber', [])

    # ---- sncast --------------------------------------------------------------

    def _ctx(self, network, account=None, accounts_file=None, rpc=None):
        if network not in NETWORKS:
            raise ValueError(f'network must be one of {sorted(NETWORKS)}')
        net = dict(NETWORKS[network])
        if rpc:
            net['rpc'] = rpc
        if network == 'devnet':
            st = self.devnet('start')
            if not st.get('up'):
                raise RuntimeError(st.get('error', 'devnet down'))
            net['accounts_file'] = os.path.join(DEVNET_DIR, 'accounts.json')
            net['owner'] = account or 'owner'
            net['relayer'] = 'relayer'
        else:
            d = _load(DEPLOYS, {}).get(network, {})
            net['accounts_file'] = accounts_file or d.get(
                'accounts_file') or DEFAULT_ACCOUNTS
            net['owner'] = account or d.get('account')
            net['relayer'] = d.get('relayer') or net['owner']
            if not net['owner']:
                raise ValueError(f'{network}: pass account=<sncast account '
                                 'name> (create one with `sncast account '
                                 'create` and fund it)')
        return net

    def _sncast(self, net, args, account):
        cmd = [os.path.join(BIN, 'sncast'), '--accounts-file',
               net['accounts_file'], '--account', account, '--json']
        if not net['rpc'].startswith('http://127.0.0.1'):
            cmd.append('--wait')
        out = subprocess.run(cmd + args + ['--url', net['rpc']],
                             cwd=CONTRACTS, capture_output=True, text=True,
                             timeout=600, env=_env())
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

    def _deployment(self, network):
        d = _load(DEPLOYS, {}).get(network)
        if not d:
            raise RuntimeError(f'not deployed on {network}; run deploy first')
        return d

    def _invoke(self, network, fn, calldata, as_owner=False, **ctx):
        net = self._ctx(network, **ctx)
        addr = self._deployment(network)['address']
        args = ['invoke', '--contract-address', addr, '--function', fn,
                '--fee-token', net['fee']]
        if calldata:
            args += ['--calldata', *map(_hex, calldata)]
        r = self._sncast(net, args, net['owner'] if as_owner
                         else net['relayer'])
        return r.get('transaction_hash')

    def _call(self, network, fn, calldata=()):
        """View call over raw JSON-RPC (ms, not an sncast process per read)."""
        if network == 'devnet' and not self._devnet_alive():
            self._ctx(network)  # boots the devnet
        addr = self._deployment(network)['address']
        r = self._rpc(network, 'starknet_call', [{
            'contract_address': addr, 'entry_point_selector': _selector(fn),
            'calldata': [_hex(c) for c in calldata]}, 'latest'])
        return [int(x, 16) for x in r]

    # ---- deploy + registry ---------------------------------------------------

    def deploy(self, network='devnet', tempo=None, min_ring=3, emission=1000,
               account=None, accounts_file=None, rpc=None, relayer=None):
        """Declare + deploy the Zuma contract. devnet needs nothing; for
        sepolia/mainnet pass account= (an sncast account, funded with STRK)
        and optionally relayer= (a second account that posts the anonymous
        actions), accounts_file=, rpc=. tempo = blocks per epoch."""
        net = self._ctx(network, account, accounts_file, rpc)
        tempo = int(tempo or (50 if network == 'devnet' else 360))
        # declare is the slow step (sncast builds the package); skip it when
        # this exact source was already declared on this network's chain
        src = self._src_hash()
        known = _load(CLASSES, {}).get(network, {})
        class_hash = known.get('class_hash') if known.get('src') == src else None
        if class_hash and not self._class_declared(network, class_hash):
            class_hash = None
        if not class_hash:
            try:
                dec = self._sncast(net, ['declare', '--contract-name', 'Zuma',
                                         '--fee-token', net['fee']],
                                   net['owner'])
                class_hash = dec['class_hash']
            except RuntimeError as e:
                m = re.search(r'(0x[0-9a-fA-F]+) is already declared', str(e))
                if not m:
                    return {'ok': False, 'stage': 'declare', 'error': str(e)}
                class_hash = m.group(1)
            c = _load(CLASSES, {})
            c[network] = {'src': src, 'class_hash': class_hash}
            _save(CLASSES, c)
        owner_addr = _load(net['accounts_file'], {}).get(
            net['section'], {}).get(net['owner'], {}).get('address')
        if not owner_addr:
            return {'ok': False, 'error': f"account {net['owner']!r} not in "
                    f"{net['accounts_file']} [{net['section']}]"}
        ctor = [int(owner_addr, 16), tempo, int(min_ring), int(emission)]
        try:
            dep = self._sncast(net, ['deploy', '--class-hash', class_hash,
                                     '--fee-token', net['fee'],
                                     '--constructor-calldata',
                                     *map(_hex, ctor)], net['owner'])
        except RuntimeError as e:
            return {'ok': False, 'stage': 'deploy', 'error': str(e)}
        rec = {'address': dep['contract_address'], 'class_hash': class_hash,
               'tx': dep['transaction_hash'], 'owner': owner_addr,
               'tempo': tempo, 'min_ring': int(min_ring),
               'emission': int(emission), 'rpc': net['rpc'],
               'at': int(time.time())}
        if network != 'devnet':
            rec.update(account=net['owner'], relayer=relayer or net['owner'],
                       accounts_file=net['accounts_file'])
        d = _load(DEPLOYS, {})
        d[network] = rec
        _save(DEPLOYS, d)
        return {'ok': True, 'network': network, **rec}

    def _src_hash(self):
        import hashlib
        h = hashlib.sha256()
        root = os.path.join(HERE, 'contracts')
        for dp, dn, fn in sorted(os.walk(root)):
            dn[:] = sorted(d for d in dn if d != 'target')
            for f in sorted(fn):
                if f.endswith(('.cairo', '.toml')):
                    fp = os.path.join(dp, f)
                    h.update(fp[len(root):].encode())
                    h.update(open(fp, 'rb').read())
        return h.hexdigest()

    def _class_declared(self, network, class_hash):
        try:
            self._rpc(network, 'starknet_getClass', ['latest', class_hash])
            return True
        except Exception:  # noqa: BLE001
            return False

    def register(self, name, role, network='devnet'):
        """Owner registers a member key (public: set membership is what
        makes the subnet permissioned). role = miner | validator."""
        k = self.pubkey(name)
        r = ROLES[str(role)]
        tx = self._invoke(network, 'register',
                          [r, int(k['x'], 16), int(k['y'], 16)],
                          as_owner=True)
        return {'ok': True, 'name': name, 'role': role, 'tx': tx}

    def set_open(self, open=True, network='devnet'):
        """Let anyone register (open) or only the owner (closed)."""
        flag = str(open).lower() in ('1', 'true', 'yes', 'on')
        return {'open': flag, 'tx': self._invoke(network, 'set_open',
                                                 [int(flag)], as_owner=True)}

    def members(self, role, network='devnet'):
        r = ROLES[str(role)]
        n = self._call(network, 'member_count', [r])[0]
        out = []
        for i in range(n):
            x, y = self._call(network, 'member', [r, i])
            out.append({'x': hex(x), 'y': hex(y)})
        return out

    def epoch(self, network='devnet'):
        d = self._deployment(network)
        b = self._block(network)
        return {'epoch': self._call(network, 'epoch')[0], 'block': b,
                'tempo': d['tempo'],
                'blocks_left': d['tempo'] - b % d['tempo']}

    # ---- anonymous actions ---------------------------------------------------

    def _ring(self, name, role, network, ring_size=None):
        """Your key plus a random sample of the other members (default:
        everyone, capped at 64). Bigger ring = bigger anonymity set."""
        me = self.pubkey(name)
        everyone = self.members(role, network)
        others = [p for p in everyone if int(p['x'], 16) != int(me['x'], 16)]
        if len(others) == len(everyone):
            raise ValueError(f'{name!r} is not a registered {role}')
        size = min(int(ring_size or MAX_RING), MAX_RING, len(others) + 1)
        return random.sample(others, size - 1) + [me]

    @staticmethod
    def _sig_calldata(sig):
        cd = [len(sig['ring'])]
        for p in sig['ring']:
            cd += [int(p['x'], 16), int(p['y'], 16)]
        return cd, [int(sig['c0'], 16), len(sig['s']),
                    *[int(s, 16) for s in sig['s']],
                    int(sig['image']['x'], 16), int(sig['image']['y'], 16)]

    def submit(self, name, work, ring_size=None, network='devnet'):
        """Miner: post a work commitment for the current epoch, anonymously.
        Returns your tag (validators see only this)."""
        addr = self._deployment(network)['address']
        e = self._call(network, 'epoch')[0]
        sig = self.sign(name, self.scope('mine', addr, e), work,
                        self._ring(name, 'miner', network, ring_size))
        ring_cd, sig_cd = self._sig_calldata(sig)
        tx = self._invoke(network, 'submit', ring_cd + [_felt(work)] + sig_cd)
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'ring_size': len(sig['ring']), 'tx': tx}

    def score(self, name, tag, score, ring_size=None, network='devnet'):
        """Validator: score a tag (0-100) in the current epoch, anonymously."""
        addr = self._deployment(network)['address']
        e = self._call(network, 'epoch')[0]
        s = int(score)
        sig = self.sign(name, self.scope('score', addr, e, tag), s,
                        self._ring(name, 'validator', network, ring_size))
        ring_cd, sig_cd = self._sig_calldata(sig)
        tx = self._invoke(network, 'score',
                          ring_cd + [_felt(tag), s] + sig_cd)
        return {'ok': True, 'epoch': e, 'tag': hex(_felt(tag)), 'score': s,
                'nullifier': sig['tag'], 'tx': tx}

    def claim(self, name, epoch, recipient, ring_size=None, network='devnet'):
        """Miner: after `epoch` closed, credit your share to `recipient`."""
        addr = self._deployment(network)['address']
        to = _felt(recipient)
        sig = self.sign(name, self.scope('mine', addr, epoch), to,
                        self._ring(name, 'miner', network, ring_size))
        ring_cd, sig_cd = self._sig_calldata(sig)
        tx = self._invoke(network, 'claim',
                          [int(epoch)] + ring_cd + [to] + sig_cd)
        return {'ok': True, 'epoch': int(epoch), 'tag': sig['tag'],
                'recipient': hex(to),
                'earned': self._call(network, 'earned', [to])[0], 'tx': tx}

    def tally(self, epoch=None, network='devnet'):
        """Every tag in an epoch with its score sum/count — never identities."""
        e = int(epoch) if epoch is not None else self._call(network, 'epoch')[0]
        n = self._call(network, 'epoch_tags', [e])[0]
        rows = []
        for i in range(n):
            tag = self._call(network, 'tag_at', [e, i])[0]
            s, c = self._call(network, 'tally', [e, tag])
            rows.append({'tag': hex(tag), 'score_sum': s, 'scores': c,
                         'claimed': bool(self._call(network, 'claimed',
                                                    [e, tag])[0])})
        return {'epoch': e, 'total': self._call(network, 'epoch_total', [e])[0],
                'tags': rows}

    def earned(self, recipient, network='devnet'):
        return {'recipient': hex(_felt(recipient)),
                'earned': self._call(network, 'earned', [_felt(recipient)])[0]}

    def status(self, network='devnet'):
        out = {'health': self.health(), 'keys': self.keys(),
               'deployments': _load(DEPLOYS, {})}
        if network in out['deployments']:
            try:
                out['epoch'] = self.epoch(network)
            except Exception as e:  # noqa: BLE001
                out['epoch_error'] = str(e)
        return out

    # ---- end-to-end demo -----------------------------------------------------

    def _advance(self, network, to_block):
        while self._block(network) < to_block:
            self._devnet_post('/create_block')

    def simulate(self, miners=4, validators=4, on_event=None):
        """Full anonymous epoch on the local devnet: deploy, register sim
        keys, 3 miners submit, every validator scores every tag, roll the
        epoch, one miner claims, and its replay is rejected. Proves the
        contract end to end. `on_event(dict)` streams each step (the console
        animates them); the whole run is kept in data/last_sim.json."""
        t0 = time.time()
        net = 'devnet'
        events = []

        def ev(kind, **kw):
            e = {'kind': kind, 't': round(time.time() - t0, 2), **kw}
            events.append(e)
            if on_event:
                on_event(e)

        miners, validators = int(miners), int(validators)
        if not (3 <= miners <= MAX_RING and 3 <= validators <= MAX_RING):
            raise ValueError(f'miners/validators must be 3..{MAX_RING} '
                             '(min_ring is 3)')
        self.devnet('start')
        ev('devnet', block=self._block(net))
        ev('deploying')
        dep = self.deploy(net, tempo=60, min_ring=3, emission=1000)
        if not dep.get('ok'):
            ev('error', error=dep.get('error'), stage=dep.get('stage'))
            return dep
        ev('deploy', address=dep['address'], tempo=dep['tempo'],
           min_ring=dep['min_ring'], emission=dep['emission'])
        mk = [f'sim-m{i}' for i in range(miners)]
        vk = [f'sim-v{i}' for i in range(validators)]
        for n in mk + vk:
            self.keygen(n)  # no-op if it exists
        for role, names in (('miner', mk), ('validator', vk)):
            for n in names:
                self.register(n, role, net)
                ev('register', role=role, x=self.pubkey(n)['x'])
        tempo = dep['tempo']
        b = self._block(net)
        self._advance(net, (b // tempo + 1) * tempo)  # start of a fresh epoch
        subs = []
        for n in mk[:3]:
            s0 = time.time()
            s = self.submit(n, f'work from {n}', network=net)
            subs.append(s)
            ev('submit', epoch=s['epoch'], tag=s['tag'], ring=s['ring_size'],
               ms=int((time.time() - s0) * 1000), tx=s['tx'])
        e = subs[0]['epoch']
        for v in vk:
            for i, s in enumerate(subs):
                r = self.score(v, s['tag'], 90 - 20 * i, network=net)
                ev('score', tag=s['tag'], score=r['score'],
                   nullifier=r['nullifier'], ring=len(vk), tx=r['tx'])
        self._advance(net, (e + 1) * tempo)
        ev('close', epoch=e, block=self._block(net))
        payee = _load(os.path.join(DEVNET_DIR, 'accounts.json'), {})[
            NETWORKS[net]['section']]['payee']['address']
        c = self.claim(mk[0], e, payee, network=net)
        ev('claim', epoch=e, tag=c['tag'], recipient=c['recipient'],
           earned=c['earned'], tx=c['tx'])
        # the replay must fail: same key, same epoch -> same tag
        try:
            self.claim(mk[0], e, payee, network=net)
            replay = 'ACCEPTED (bug)'
        except RuntimeError as ex:
            replay = f'rejected: {ex}'
        ev('replay', ok=replay.startswith('rejected'), result=replay)
        out = {'ok': True, 'contract': dep['address'], 'epoch': e,
               'steps': [f"{x['kind']}" for x in events],
               'tally': self.tally(e, net), 'claim': c,
               'double_claim': replay,
               'seconds': round(time.time() - t0, 1)}
        ev('done', seconds=out['seconds'])
        _save(LAST_SIM, {'at': int(time.time()), 'events': events,
                         'result': out})
        return out

    def last_sim(self):
        """The most recent simulate() run, event by event (public replay)."""
        return _load(LAST_SIM, None)

    # ---- console view --------------------------------------------------------

    def board(self, network='devnet', epochs=6):
        """Everything the console draws in one read: params, epoch clock,
        both member SETS (public keys only), and the recent epochs' tags.
        Never touches a secret — it is exactly what any chain reader sees."""
        d = _load(DEPLOYS, {}).get(network)
        out = {'network': network, 'deployed': bool(d),
               'devnet_up': self._devnet_alive() if network == 'devnet'
               else None}
        if not d or (network == 'devnet' and not out['devnet_up']):
            return out
        owner, tempo, min_ring, emission, is_open = self._call(network,
                                                               'params')
        b = self._block(network)
        e = b // tempo
        out.update(contract=d['address'], owner=hex(owner), tempo=tempo,
                   min_ring=min_ring, emission=emission, open=bool(is_open),
                   block=b, epoch=e, blocks_left=tempo - b % tempo,
                   miners=self.members('miner', network),
                   validators=self.members('validator', network))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = self.tally(ep, network)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out

    # ---- HTTP server (console + API) ----------------------------------------

    def serve(self, port=None):
        """Run server.py: the console at /zuma and the JSON API at
        /zuma/api/<fn>, one stdlib process."""
        port = int(port or self.config['port'])
        subprocess.run([sys.executable, os.path.join(HERE, 'server.py'),
                        str(port)], cwd=HERE)

if __name__ == '__main__':
    m = Mod()
    argv = sys.argv[1:]
    if not argv:
        print(json.dumps(m.status(), indent=2, default=str))
    else:
        fn, kw = argv[0], {}
        for a in argv[1:]:
            k, _, v = a.partition('=')
            kw[k] = v
        print(json.dumps(getattr(m, fn)(**kw), indent=2, default=str))
