"""The wire to a postquant node: start our own, deploy the pqc1 blob, run it
off chain (the node signs a pqr1 receipt with an ML-DSA wallet and anchors
it), read and decode the contract state, replay-verify receipts.

The contract's byte layout lives in contract/src/lib.rs; `encode_*` /
`decode_state` here are its Python mirror (pinned by tests/test_postquant.py
against the Rust suite's expectations).

Local node: postquant's own src/api.py (orbit/postquant), launched with its
data + keystore under data/postquant/ and chain id ztao-pq, so nothing
here touches the fleet's live postquant-1 chain. The block loop is parked
(POSTQUANT_BLOCK_SECONDS=3600): every write mines its own block and
advance() seals empty ones, so the epoch clock only moves when we move it.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, 'contract')
TARGET = os.path.join(CRATE, 'target')
WASM = os.path.join(TARGET, 'wasm32-unknown-unknown', 'release',
                    'ztao_pq.wasm')
POSTQUANT = os.environ.get(
    'ZTAO_POSTQUANT_SRC',
    os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', 'postquant',
                                  'src')))
LOCAL_PORT = int(os.environ.get('ZTAO_PQ_PORT', 51506))
LOCAL_CHAIN = 'ztao-pq'
REMOTE_URL = os.environ.get('ZTAO_PQ_URL', 'http://127.0.0.1:51030')
REMOTE_TOKEN = os.environ.get('ZTAO_PQ_TOKEN', '')
REMOTE_WALLET = os.environ.get('ZTAO_PQ_WALLET', '')
WALLET = 'relayer'          # local: owns the contract, runs and anchors it
PAYEE = 'payee'             # local: a pq address to claim to
LEASE_DAYS = 30

PRIME = 2 ** 251 + 17 * 2 ** 192 + 1
MAX_RING = 64

# contract/src/lib.rs E_* -> message
ERRORS = {
    -1: 'bad input', -2: 'state over the output cap', -3: 'bad state',
    -10: 'height went backwards', -11: 'already initialised',
    -12: 'not initialised', -13: 'role is 1 or 2', -14: 'key not on curve',
    -15: 'key already registered', -16: 'ring below min_ring',
    -17: 'ring above MAX_RING', -18: 'ring has a non-member',
    -19: 'ring not sorted/unique', -20: 'bad ring signature',
    -21: 'already submitted this epoch', -22: 'no such tag this epoch',
    -23: 'already scored this tag', -24: 'score is 0..100',
    -25: 'epoch still open', -26: 'already settled',
    -27: 'epoch not settled', -28: 'already claimed', -29: 'overflow',
    -30: 'bad params', -31: 'epoch pruned from state',
}


class PQError(RuntimeError):
    pass


# ---- the contract's wire (mirror of lib.rs) ------------------------------

def text_felt(s):
    """sha256 with the top 5 bits cleared: always < 2^251 < PRIME (NEAR's
    rule, reused for the subnet id and claim recipients)."""
    d = bytearray(hashlib.sha256(str(s).encode()).digest())
    d[0] &= 0x07
    return int.from_bytes(d, 'big')


def _f(v):
    v = int(str(v), 0) if not isinstance(v, int) else v
    if not 0 <= v < PRIME:
        raise ValueError(f'{v:#x} is not a felt')
    return v.to_bytes(32, 'big')


def _pt(p):
    return _f(p['x']) + _f(p['y'])


def _u(v, n):
    return int(v).to_bytes(n, 'little')


def head(op, height):
    return bytes([op]) + _u(height, 8)


def enc_init(height, subnet, tempo, min_ring, emission):
    return head(0, height) + _f(subnet) + _u(tempo, 8) + _u(min_ring, 4) + \
        _u(emission, 16)


def enc_register(height, role, key):
    return head(1, height) + bytes([int(role)]) + _pt(key)


def enc_open(height, flag):
    return head(2, height) + bytes([1 if flag else 0])


def enc_sig(sig, hp=None):
    ring = sig['ring']
    return (bytes([len(ring)]) + b''.join(_pt(p) for p in ring) +
            _f(sig['c0']) + b''.join(_f(s) for s in sig['s']) +
            _pt(sig['image']) + (_pt(hp) if hp else bytes(64)))


def enc_submit(height, work, sig, hp=None):
    return head(3, height) + _f(work) + enc_sig(sig, hp)


def enc_score(height, tag, score, sig, hp=None):
    return head(4, height) + _f(tag) + bytes([int(score)]) + enc_sig(sig, hp)


def enc_settle(height, epoch, tag):
    return head(5, height) + _u(epoch, 8) + _f(tag)


def enc_claim(height, epoch, recipient, sig, hp=None):
    return head(6, height) + _u(epoch, 8) + _f(recipient) + enc_sig(sig, hp)


def enc_tick(height):
    return head(7, height)


class _Rd:
    def __init__(self, b):
        self.b, self.i = b, 0

    def take(self, n):
        if self.i + n > len(self.b):
            raise PQError('state truncated')
        s = self.b[self.i:self.i + n]
        self.i += n
        return s

    def u(self, n):
        return int.from_bytes(self.take(n), 'little')

    def f(self):
        return hex(int.from_bytes(self.take(32), 'big'))

    def pt(self):
        return {'x': self.f(), 'y': self.f()}


def decode_state(b):
    """State bytes (lib.rs State::write) -> dict."""
    if not b:
        return None
    r = _Rd(bytes(b))
    if r.take(4) != b'ZTP1':
        raise PQError('not a ztao pqc1 state')
    s = {'subnet': r.f(), 'tempo': r.u(8), 'min_ring': r.u(4),
         'emission': r.u(16), 'open': bool(r.u(1)), 'height': r.u(8)}
    s['miners'] = [r.pt() for _ in range(r.u(4))]
    s['validators'] = [r.pt() for _ in range(r.u(4))]
    eps = []
    for _ in range(r.u(4)):
        ep = {'epoch': r.u(8), 'vset': r.u(4), 'total': r.u(16),
              'rank_total': r.u(16), 'nsettled': r.u(4), 'tags': []}
        for _ in range(r.u(4)):
            t = {'tag': r.f(), 'work': r.f(), 'score_sum': r.u(16),
                 'scores': r.u(4), 'claimed': bool(r.u(1)),
                 'settled': bool(r.u(1)), 'consensus': r.u(1),
                 'rank': r.u(16)}
            t['hist'] = [(r.u(1), r.u(4)) for _ in range(r.u(1))]
            ep['tags'].append(t)
        ep['nullifiers'] = [r.f() for _ in range(r.u(4))]
        eps.append(ep)
    s['epochs'] = eps
    s['credit'] = {who: amt for who, amt in
                   ((r.f(), r.u(16)) for _ in range(r.u(4)))}
    if r.i != len(r.b):
        raise PQError('trailing bytes after state')
    return s


# ---- the node ---------------------------------------------------------------

class PostquantZTao:
    def __init__(self, data):
        self.home = os.path.join(data, 'postquant')
        self.deploys_path = os.path.join(data, 'postquant_deploys.json')

    # where each net lives
    def url(self, net):
        return f'http://127.0.0.1:{LOCAL_PORT}' if net == 'postquant' \
            else REMOTE_URL.rstrip('/')

    def wallet(self, net):
        if net == 'postquant':
            return WALLET
        if not REMOTE_WALLET:
            raise PQError('set ZTAO_PQ_WALLET (a funded wallet in that '
                          "node's keystore) and ZTAO_PQ_TOKEN (its write "
                          'bearer) to run on ' + net)
        return REMOTE_WALLET

    def call(self, net, route, timeout=60, **args):
        req = urllib.request.Request(
            self.url(net) + '/' + route, data=json.dumps(args).encode(),
            headers={'content-type': 'application/json',
                     **({'authorization': 'Bearer ' + REMOTE_TOKEN}
                        if net != 'postquant' and REMOTE_TOKEN else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:  # noqa: BLE001
                body = {}
            raise PQError(f'postquant /{route}: {body.get("error") or e}') \
                from None

    def _alive(self, net='postquant'):
        try:
            with urllib.request.urlopen(self.url(net) + '/health',
                                        timeout=2) as r:
                return json.loads(r.read()).get('ok', False)
        except Exception:  # noqa: BLE001
            return False

    def node(self, action='status'):
        """start | stop | reset | status of our local postquant node."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{LOCAL_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.node('stop')
            time.sleep(1)
            subprocess.run(['rm', '-rf', os.path.join(self.home, LOCAL_CHAIN)])
            d = self._deploys()
            d.pop('postquant', None)
            self._save_deploys(d)
            return self.node('start')
        if action == 'start' and not self._alive():
            api = os.path.join(POSTQUANT, 'api.py')
            if not os.path.exists(api):
                return {'up': False, 'error': f'{api} missing (orbit/postquant '
                        'is the node; ZTAO_POSTQUANT_SRC points elsewhere)'}
            os.makedirs(self.home, exist_ok=True)
            env = dict(os.environ, POSTQUANT_DATA_DIR=self.home,
                       POSTQUANT_KEY_DIR=self.home,
                       POSTQUANT_PORT=str(LOCAL_PORT),
                       POSTQUANT_BIND='127.0.0.1',
                       POSTQUANT_CHAIN_ID=LOCAL_CHAIN,
                       POSTQUANT_BLOCK_SECONDS='3600',
                       POSTQUANT_HEARTBEAT='3600')
            env.pop('POSTQUANT_SCHEME', None)
            log = open(os.path.join(self.home, 'node.log'), 'a')
            subprocess.Popen([sys.executable, api], cwd=POSTQUANT, env=env,
                             stdout=log, stderr=log, start_new_session=True)
            for _ in range(100):
                if self._alive():
                    break
                time.sleep(0.2)
            else:
                return {'up': False, 'error': 'postquant node did not come up '
                        f'(see {self.home}/node.log)'}
            self._wallets()
        up = self._alive()
        return {'up': up, 'port': LOCAL_PORT, 'chain_id': LOCAL_CHAIN,
                **({'block': self.block('postquant')} if up else {})}

    def _wallets(self):
        """relayer (owner/runner, funded from the genesis treasury) + payee."""
        have = {w['name']: w for w in
                self.call('postquant', 'wallet', action='list')['wallets']}
        for name in (WALLET, PAYEE):
            if name not in have:
                have[name] = self.call('postquant', 'wallet', action='create',
                                       name=name)
        bal = float((have[WALLET].get('balance') or {}).get('pq', 0) or 0)
        if bal < 100:
            self.call('postquant', 'faucet', wallet=WALLET, amount='1000')
        return have

    def address(self, net, name):
        for w in self.call(net, 'wallet', action='list')['wallets']:
            if w['name'] == name:
                return w['address']
        raise PQError(f'no wallet {name!r} on {net}')

    def block(self, net):
        with urllib.request.urlopen(self.url(net) + '/health', timeout=5) as r:
            return int(json.loads(r.read())['height'])

    def mine(self, net, n):
        for _ in range(int(n)):
            self.call(net, 'mine', force=True)

    # ---- build / test -----------------------------------------------------

    def build(self):
        out = subprocess.run(
            ['cargo', 'build', '--offline', '--release', '--target',
             'wasm32-unknown-unknown'], cwd=CRATE, capture_output=True,
            text=True, timeout=1200,
            env=dict(os.environ, CARGO_TARGET_DIR=TARGET))
        return {'ok': out.returncode == 0 and os.path.exists(WASM),
                'wasm': WASM, 'bytes': os.path.getsize(WASM)
                if os.path.exists(WASM) else None,
                'log': (out.stdout + out.stderr).strip()[-3000:]}

    def test(self):
        out = subprocess.run(['cargo', 'test', '--offline', '--release',
                              '--lib'], cwd=CRATE, capture_output=True,
                             text=True, timeout=1200,
                             env=dict(os.environ, CARGO_TARGET_DIR=TARGET))
        tests = re.findall(r'^test tests::(\w+) \.\.\. (ok|FAILED)',
                           out.stdout, re.M)
        return {'ok': out.returncode == 0,
                'passed': sum(1 for _, s in tests if s == 'ok'),
                'failed': sum(1 for _, s in tests if s != 'ok'),
                'tests': [['PASS' if s == 'ok' else 'FAIL', n]
                          for n, s in tests],
                'log': (out.stdout + out.stderr).strip()[-3000:]}

    # ---- deployments --------------------------------------------------------

    def _deploys(self):
        try:
            return json.load(open(self.deploys_path))
        except Exception:  # noqa: BLE001
            return {}

    def _save_deploys(self, d):
        tmp = self.deploys_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.deploys_path)

    def dep(self, net):
        d = self._deploys().get(net)
        if not d:
            raise PQError(f'not deployed on {net}; run deploy network={net}')
        return d

    def deploy(self, net, tempo, min_ring, emission):
        if not os.path.exists(WASM):
            b = self.build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log'][-800:]}
        code = open(WASM, 'rb').read()
        w = self.wallet(net)
        chain_id = self.call(net, 'head').get('chain_id') or net
        name = f'ztao-{int(time.time() * 1000)}'
        subnet = text_felt(f'{chain_id}/contract/{name}')
        d = self.call(net, 'contract', timeout=120, action='deploy',
                      name=name, lang='wasm', wallet=w, days=LEASE_DAYS,
                      code_b64=base64.b64encode(code).decode())
        if not (d.get('pqc1') or {}).get('abi'):
            return {'ok': False, 'stage': 'deploy',
                    'error': (d.get('pqc1') or {}).get('reason') or str(d)}
        rec = {'contract': name, 'subnet': hex(subnet), 'code': d['cid'],
               'chain_id': chain_id, 'tempo': int(tempo),
               'min_ring': int(min_ring), 'emission': int(emission),
               'owner': self.address(net, w), 'wasm_bytes': len(code),
               'at': int(time.time())}
        d2 = self._deploys()
        d2[net] = rec
        self._save_deploys(d2)
        r = self.run(net, enc_init(self.block(net), subnet, tempo, min_ring,
                                   emission))
        return {'ok': True, **rec, 'gas': r['gas'], 'run_ms': r['run_ms'],
                'receipt': r['receipt']}

    # ---- runs -------------------------------------------------------------

    def run(self, net, inp):
        """One off-chain run, receipt signed + anchored. Reverts raise."""
        d = self.dep(net)
        r = self.call(net, 'contract', timeout=120, action='run',
                      name=d['contract'], wallet=self.wallet(net),
                      input_b64=base64.b64encode(inp).decode(),
                      days=LEASE_DAYS)
        if r.get('reverted'):
            why = ERRORS.get(r.get('ret'), r.get('trap') or f'code {r.get("ret")}')
            raise PQError(f'reverted: {why}')
        tx = (r.get('anchor') or {}).get('receipt') or {}
        return {'receipt': r['receipt'], 'n': r['n'], 'tx': tx.get('hash') or
                (r.get('anchor') or {}).get('hash'), 'gas': tx.get('gas'),
                'run_ms': round(float(r.get('ms') or 0), 3),
                'state': decode_state(bytes.fromhex(r['state_out_hex'])),
                'signed_by': r.get('signed_by'), 'scheme': r.get('scheme')}

    def state(self, net):
        d = self.dep(net)
        r = self.call(net, 'contract', action='state', name=d['contract'])
        return decode_state(bytes.fromhex(r.get('state_hex') or ''))

    def verify(self, net):
        """Replay the anchored receipt: signature, re-run, owner, anchor."""
        d = self.dep(net)
        return self.call(net, 'contract', timeout=120, action='verify',
                         name=d['contract'])

    def audit(self, net, limit=200):
        """Replay EVERY receipt of the contract, newest first, and check the
        links: each run's state_in is the previous run's state_out. This is
        what a third party does to trust the subnet's books without trusting
        the runner — signature (ML-DSA), re-execution, chain of custody."""
        d = self.dep(net)
        chain = self.call(net, 'contract', action='receipts',
                          name=d['contract'], limit=limit)['receipts']
        bad, ms, linked, schemes = [], 0.0, True, set()
        for i, r in enumerate(chain):
            if r.get('missing'):
                bad.append({'receipt': r['receipt'], 'why': 'blob missing'})
                continue
            if i + 1 < len(chain) and not chain[i + 1].get('missing') and \
                    chain[i + 1]['state_out'] != r['state_in']:
                linked = False
            v = self.call(net, 'contract', timeout=120, action='verify',
                          receipt=r['receipt'])
            ms += float((v.get('replay') or {}).get('ms') or 0)
            schemes.add((v.get('signature') or {}).get('scheme'))
            if not v.get('valid'):
                bad.append({'receipt': r['receipt'], 'n': r.get('n'),
                            'checks': v.get('checks')})
        return {'ok': not bad and linked, 'receipts': len(chain),
                'linked': linked, 'invalid': bad,
                'replay_ms': round(ms, 2), 'schemes': sorted(filter(None, schemes)),
                'contract': d['contract']}
