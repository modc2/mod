"""ztao on Solana, the wire layer: txs, the local validator, sessions and
units of contract/ (the sBPF program). adapter.py turns it into a Network.

Signatures come from the SAME Cairo prover (core/prover/). What is different on
Solana is the compute budget: a transaction gets at most 1.4M compute units,
and one STARK-curve double-scalar multiply alone costs more than that in
sBPF. So the program never verifies a signature in one go. The relayer
opens a SESSION account, uploads the signature plus HINTS (every challenge
c_i, every L_i/R_i point, the partial points inside each multiply, the
Poseidon sponge states of the digest), and then sends UNITS: each unit
re-derives one slice of the verification from its hints and sets one bit.
Units are independent, so they are sent all at once and land in any order;
`finish` applies the action once every bit is set. A wrong hint just makes
its unit fail — the hints can make verification cheaper, never different.

Networks:
    solana   local solana-test-validator on :51510 (ledger data/solana/),
             payer = data/solana/payer.json (airdropped)
    solana-devnet  api.devnet.solana.com with ZTAO_SOLANA_KEYPAIR (funded).
             Not exercised from here.

Transactions are built and ed25519-signed here (PyNaCl); the solana CLI is
only used to deploy the program.
"""

import base64
import hashlib
import json
import os
import struct
import subprocess
import time
import urllib.request

from ... import DATA
from ...core import stark

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, 'contract')
NEAR_SRC = os.path.join(HERE, '..', 'near', 'contract', 'src')  # fp + poseidon
SOL_DIR = os.path.join(DATA, 'solana')
LEDGER = os.path.join(SOL_DIR, 'ledger')
RPC_PORT = int(os.environ.get('ZTAO_SOLANA_PORT', 51510))
AGAVE = os.path.expanduser('~/.local/agave/solana-release/bin')
SO = os.path.join(CRATE, 'target', 'deploy', 'ztao_solana.so')
PROGRAM_KEY = os.path.join(SOL_DIR, 'program.json')
PAYER_KEY = os.path.join(SOL_DIR, 'payer.json')

NETS = {
    'solana': {'rpc': f'http://127.0.0.1:{RPC_PORT}'},
    'solana-devnet': {'rpc': os.environ.get('ZTAO_SOLANA_DEVNET_RPC',
                                            'https://api.devnet.solana.com')},
}

SYSTEM = bytes(32)
COMPUTE_BUDGET = None  # set below (base58 decode)
CLOCK = None
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58encode(raw):
    n, out = int.from_bytes(raw, 'big'), ''
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + out


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    out = b'\0' * (len(s) - len(s.lstrip('1'))) + raw
    return out.rjust(32, b'\0') if len(out) < 32 else out


COMPUTE_BUDGET = b58decode('ComputeBudget111111111111111111111111111111')
CLOCK = b58decode('SysvarC1ock11111111111111111111111111111111')


class SolError(RuntimeError):
    pass


# ---- keys ----------------------------------------------------------------------

class Keypair:
    def __init__(self, secret64):
        from nacl.signing import SigningKey
        self.sk = SigningKey(bytes(secret64[:32]))
        self.pub = bytes(self.sk.verify_key)

    @classmethod
    def generate(cls):
        seed = os.urandom(32)
        from nacl.signing import SigningKey
        sk = SigningKey(seed)
        return cls(seed + bytes(sk.verify_key))

    @classmethod
    def load(cls, path):
        return cls(bytes(json.load(open(path))))

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(list(bytes(self.sk) + self.pub), f)

    def sign(self, msg):
        return self.sk.sign(msg).signature

    @property
    def address(self):
        return b58encode(self.pub)


_ED_P = 2 ** 255 - 19
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P


def on_ed25519(b):
    """CompressedEdwardsY::decompress().is_some() — what Solana means by
    'on the curve'. (libsodium's is_valid_point also rejects torsion
    points, so it disagrees with Solana on some hashes.)"""
    y = int.from_bytes(b, 'little') & ((1 << 255) - 1)
    if y >= _ED_P:
        return False
    u, v = (y * y - 1) % _ED_P, (_ED_D * y * y + 1) % _ED_P
    x2 = u * pow(v, _ED_P - 2, _ED_P) % _ED_P
    if x2 == 0:
        return not (b[31] >> 7)  # x = 0 with the sign bit set is invalid
    return pow(x2, (_ED_P - 1) // 2, _ED_P) == 1


def find_pda(seeds, program_id):
    """find_program_address: highest bump whose hash is off the ed25519 curve."""
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b''.join(seeds) + bytes([bump]) + program_id +
                           b'ProgramDerivedAddress').digest()
        if not on_ed25519(h):
            return h, bump
    raise SolError('no PDA')


# ---- transactions (legacy message format) -----------------------------------------

def shortvec(n):
    out = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


class Ix:
    """program + [(pubkey, is_signer, is_writable)] + data"""

    def __init__(self, program, accounts, data):
        self.program, self.accounts, self.data = program, accounts, bytes(data)


def compile_tx(ixs, payer, signers, blockhash):
    keys = {}  # pubkey -> [signer, writable]
    order = [payer.pub]
    keys[payer.pub] = [True, True]
    for ix in ixs:
        for pk, s, w in ix.accounts:
            if pk not in keys:
                keys[pk] = [s, w]
                order.append(pk)
            else:
                keys[pk][0] |= s
                keys[pk][1] |= w
        if ix.program not in keys:
            keys[ix.program] = [False, False]
            order.append(ix.program)
    group = lambda pk: (0 if keys[pk][0] and keys[pk][1] else
                        1 if keys[pk][0] else 2 if keys[pk][1] else 3)
    acct = sorted(order, key=lambda pk: (group(pk), order.index(pk)))
    acct.remove(payer.pub)
    acct.insert(0, payer.pub)
    idx = {pk: i for i, pk in enumerate(acct)}
    n_sig = sum(1 for pk in acct if keys[pk][0])
    n_ro_sig = sum(1 for pk in acct if keys[pk][0] and not keys[pk][1])
    n_ro = sum(1 for pk in acct if not keys[pk][0] and not keys[pk][1])
    msg = bytes([n_sig, n_ro_sig, n_ro]) + shortvec(len(acct)) + b''.join(acct)
    msg += blockhash + shortvec(len(ixs))
    for ix in ixs:
        msg += bytes([idx[ix.program]]) + shortvec(len(ix.accounts))
        msg += bytes(idx[pk] for pk, _, _ in ix.accounts)
        msg += shortvec(len(ix.data)) + ix.data
    by_pub = {s.pub: s for s in [payer, *signers]}
    sigs = []
    for pk in acct[:n_sig]:
        if pk not in by_pub:
            raise SolError(f'missing signer {b58encode(pk)}')
        sigs.append(by_pub[pk].sign(msg))
    return shortvec(n_sig) + b''.join(sigs) + msg


def cu_limit(units):
    return Ix(COMPUTE_BUDGET, [], b'\x02' + struct.pack('<I', units))


def heap_frame(nbytes):
    return Ix(COMPUTE_BUDGET, [], b'\x01' + struct.pack('<I', nbytes))


def create_account(payer, new, lamports, space, owner):
    return Ix(SYSTEM, [(payer, True, True), (new, True, True)],
              struct.pack('<IQQ', 0, lamports, space) + owner)


class Rpc:
    def __init__(self, url):
        self.url = url

    def __call__(self, method, params=None, timeout=60):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                           'params': params or []}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            'Content-Type': 'application/json'})
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        if 'error' in r:
            raise SolError(f"{method}: {r['error'].get('message')} "
                           f"{json.dumps(r['error'].get('data', ''))[:600]}")
        return r['result']

    def blockhash(self):
        return b58decode(self('getLatestBlockhash',
                              [{'commitment': 'confirmed'}])['value']['blockhash'])

    def send(self, raw, skip_preflight=False):
        return self('sendTransaction', [base64.b64encode(raw).decode(), {
            'encoding': 'base64', 'skipPreflight': skip_preflight,
            'preflightCommitment': 'confirmed'}])

    def simulate(self, raw):
        return self('simulateTransaction', [base64.b64encode(raw).decode(), {
            'encoding': 'base64', 'commitment': 'confirmed',
            'sigVerify': False}])['value']

    def wait(self, sigs, timeout=60):
        """Wait until every signature is confirmed; return their statuses."""
        sigs = list(sigs)
        done = {}
        t0 = time.time()
        while len(done) < len(sigs):
            todo = [s for s in sigs if s not in done]
            for i in range(0, len(todo), 200):
                part = todo[i:i + 200]
                st = self('getSignatureStatuses', [part])['value']
                for s, v in zip(part, st):
                    if v and v.get('confirmationStatus') in ('confirmed',
                                                             'finalized'):
                        done[s] = v
            if len(done) < len(sigs):
                if time.time() - t0 > timeout:
                    raise SolError(f'{len(sigs) - len(done)} txs not confirmed')
                time.sleep(0.15)
        return [done[s] for s in sigs]

    def account(self, pk):
        v = self('getAccountInfo', [b58encode(pk), {
            'encoding': 'base64', 'commitment': 'confirmed'}])['value']
        if not v:
            return None
        return base64.b64decode(v['data'][0])

    def tx_meta(self, sig):
        for _ in range(40):
            r = self('getTransaction', [sig, {'encoding': 'json',
                                              'commitment': 'confirmed',
                                              'maxSupportedTransactionVersion': 0}])
            if r:
                return r['meta']
            time.sleep(0.15)
        return None


# ---- the program's layout (solana/contract/src/lib.rs) -------------------------------------

SEGS, DIG_EVERY = 4, 4
MAX_RING, MAX_MEMBERS = 32, 64
SUBNET_SIZE = 120 + 2 * MAX_MEMBERS * 96
SS_PUT, SS_ARRAYS = 280, 512
KINDS = {'submit': 1, 'score': 2, 'claim': 3}
UNIT_CU = 1_400_000

# epoch PDA (contract/src/lib.rs "epoch layout"): 56-byte header, 32 tags x 208
EP_MAGIC = b'zumaEP02'
EP_TAGS, TAG, MAX_TAGS, MAX_SCORE = 56, 208, 32, 100
EPOCH_SIZE = EP_TAGS + MAX_TAGS * TAG
SETTLE_CU = 200_000


def decode_epoch(raw):
    """Epoch account bytes -> totals + one row per tag (with its histogram).
    None when the account is missing or not a current epoch account."""
    if not raw or raw[:8] != EP_MAGIC:
        return None
    u = lambda o, n: int.from_bytes(raw[o:o + n], 'little')
    rows = []
    for k in range(u(24, 4)):
        o = EP_TAGS + k * TAG
        rows.append({'tag': hex(int.from_bytes(raw[o:o + 32], 'big')),
                     'work': hex(int.from_bytes(raw[o + 32:o + 64], 'big')),
                     'score_sum': u(o + 64, 16), 'scores': u(o + 80, 4),
                     'claimed': bool(raw[o + 84]), 'top': raw[o + 85],
                     'settled': bool(raw[o + 86]), 'consensus': raw[o + 87],
                     'rank': u(o + 88, 16),
                     'hist': list(raw[o + 104:o + 104 + MAX_SCORE + 1])})
    return {'total': u(8, 16), 'validators': u(28, 4), 'rank_total': u(32, 16),
            'nsettled': u(48, 4), 'tags': rows}


def encode_epoch(total=0, validators=0, rank_total=0, nsettled=0, tags=()):
    """The inverse of decode_epoch (tests): tags are decode_epoch rows."""
    b = bytearray(EPOCH_SIZE)
    b[:8] = EP_MAGIC
    b[8:24] = int(total).to_bytes(16, 'little')
    b[24:28] = len(tags).to_bytes(4, 'little')
    b[28:32] = int(validators).to_bytes(4, 'little')
    b[32:48] = int(rank_total).to_bytes(16, 'little')
    b[48:52] = int(nsettled).to_bytes(4, 'little')
    for k, t in enumerate(tags):
        o = EP_TAGS + k * TAG
        b[o:o + 32] = int(t['tag'], 16).to_bytes(32, 'big')
        b[o + 32:o + 64] = int(t.get('work', '0x0'), 16).to_bytes(32, 'big')
        b[o + 64:o + 80] = int(t.get('score_sum', 0)).to_bytes(16, 'little')
        b[o + 80:o + 84] = int(t.get('scores', 0)).to_bytes(4, 'little')
        b[o + 84] = int(bool(t.get('claimed')))
        b[o + 85] = int(t.get('top', 0))
        b[o + 86] = int(bool(t.get('settled')))
        b[o + 87] = int(t.get('consensus', 0))
        b[o + 88:o + 104] = int(t.get('rank', 0)).to_bytes(16, 'little')
        h = list(t.get('hist') or [0] * (MAX_SCORE + 1))
        b[o + 104:o + 104 + MAX_SCORE + 1] = bytes(h)
    return bytes(b)


def settle_data(epoch, tag):
    """Instruction 9 body: epoch u64 le, tag felt big-endian."""
    return b'\x09' + struct.pack('<Q', int(epoch)) + be(int(tag))


def felt_of(raw):
    """sha256 with the top 5 bits cleared (the program's felt_of)."""
    h = bytearray(hashlib.sha256(raw).digest())
    h[0] &= 0x07
    return int.from_bytes(h, 'big')


def n_digest(n):
    return -(-(n + 4) // DIG_EVERY)


def arrays(n):
    s = SS_ARRAYS
    c = s + 32 * n
    l = c + 32 * n
    r = l + 64 * n
    mids = r + 64 * n
    sponge = mids + 64 * n * 2 * (SEGS - 1)
    return {'s': s, 'c': c, 'l': l, 'r': r, 'mids': mids, 'sponge': sponge,
            'end': sponge + 96 * (n_digest(n) - 1)}


def be(v):
    return int(v).to_bytes(32, 'big')


def pt(p):
    return bytes(64) if p is None else be(p[0]) + be(p[1])


class SolZTao:
    def __init__(self, data_dir):
        self.deploys_path = os.path.join(data_dir, 'deployments.json')

    def rpc(self, net):
        return Rpc(NETS[net]['rpc'])

    # ---- local validator ---------------------------------------------------------

    def _alive(self):
        try:
            return Rpc(NETS['solana']['rpc'])('getHealth', timeout=1) == 'ok'
        except Exception:  # noqa: BLE001
            return False

    def validator(self, action='status'):
        """start | stop | reset | status for solana-test-validator on :51510."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{RPC_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.validator('stop')
            time.sleep(2)
            subprocess.run(['rm', '-rf', LEDGER])
            d = self._deploys()
            d.pop('solana', None)
            self._save_deploys(d)
            return self.validator('start')
        if action == 'start' and not self._alive():
            os.makedirs(SOL_DIR, exist_ok=True)
            log = open(os.path.join(SOL_DIR, 'validator.log'), 'a')
            subprocess.Popen(
                [os.path.join(AGAVE, 'solana-test-validator'), '--ledger', LEDGER,
                 '--rpc-port', str(RPC_PORT), '--faucet-port', str(RPC_PORT + 2),
                 '--gossip-port', str(RPC_PORT + 3),
                 '--dynamic-port-range', f'{RPC_PORT + 7}-{RPC_PORT + 34}',
                 '--bind-address', '127.0.0.1', '--quiet'],
                stdout=log, stderr=log, cwd=SOL_DIR, start_new_session=True)
            for _ in range(240):
                if self._alive():
                    break
                time.sleep(0.25)
            else:
                return {'up': False, 'error': 'validator did not come up'}
        up = self._alive()
        return {'up': up, 'port': RPC_PORT,
                **({'slot': self.slot('solana')} if up else {})}

    def slot(self, net):
        return self.rpc(net)('getSlot', [{'commitment': 'confirmed'}])

    def wait_slot(self, net, target):
        while self.slot(net) < target:
            time.sleep(0.2)

    # ---- keys --------------------------------------------------------------------------

    def _kp(self, name):
        path = os.path.join(SOL_DIR, f'{name}.json')
        if not os.path.exists(path):
            Keypair.generate().save(path)
        return Keypair.load(path)

    def _fund(self, net, kp, sol=100):
        r = self.rpc(net)
        bal = r('getBalance', [kp.address, {'commitment': 'confirmed'}])['value']
        if bal < sol * 10 ** 9 // 2 and net == 'solana':
            sig = r('requestAirdrop', [kp.address, sol * 10 ** 9])
            r.wait([sig])

    # ---- build / test ------------------------------------------------------------------

    def build(self):
        env = dict(os.environ, PATH=AGAVE + os.pathsep + os.environ.get('PATH', ''))
        out = subprocess.run(['cargo-build-sbf'], cwd=CRATE, capture_output=True,
                             text=True, timeout=900, env=env)
        ok = out.returncode == 0 and os.path.exists(SO)
        log = '\n'.join(l for l in (out.stdout + out.stderr).splitlines()
                        if 'overflows the maximum allowed frame' not in l)
        return {'ok': ok, 'so': SO if ok else None,
                'bytes': os.path.getsize(SO) if ok else None,
                'log': log.strip()[-1500:]}

    def _stale(self):
        if not os.path.exists(SO):
            return True
        t = os.path.getmtime(SO)
        srcs = [os.path.join(CRATE, 'Cargo.toml'),
                *[os.path.join(CRATE, 'src', f) for f in os.listdir(
                    os.path.join(CRATE, 'src'))],
                os.path.join(NEAR_SRC, 'fp.rs'),
                os.path.join(NEAR_SRC, 'poseidon.rs')]
        return any(os.path.getmtime(p) > t for p in srcs)

    def test(self):
        """cargo test (native): every verifier unit against hints computed
        from a Cairo-made signature, and every tampered hint rejected."""
        out = subprocess.run(['cargo', 'test', '--release', '--lib',
                              '--features', 'no-entrypoint'],
                             cwd=CRATE, capture_output=True, text=True,
                             timeout=900)
        import re
        tests = re.findall(r'^test (?:\w+::)*(\w+) \.\.\. (ok|FAILED)',
                           out.stdout, re.M)
        return {'ok': out.returncode == 0,
                'passed': sum(1 for _, s in tests if s == 'ok'),
                'failed': sum(1 for _, s in tests if s != 'ok'),
                'tests': [['PASS' if s == 'ok' else 'FAIL', n] for n, s in tests],
                'log': (out.stdout + out.stderr).strip()[-3000:]}

    # ---- deploy --------------------------------------------------------------------------

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

    def _dep(self, net):
        d = self._deploys().get(net)
        if not d:
            raise SolError(f'not deployed on {net}; run deploy network={net}')
        return d

    def _program_current(self, net, program):
        """The deployed program's bytes equal the local .so."""
        r = self.rpc(net)
        acct = r('getAccountInfo', [b58encode(program), {
            'encoding': 'base64', 'commitment': 'confirmed'}])['value']
        if not acct:
            return False
        pdata = b58decode(b58encode(base64.b64decode(acct['data'][0])[4:36]))
        raw = r.account(pdata)
        if not raw:
            return False
        so = open(SO, 'rb').read()
        return raw[45:45 + len(so)] == so

    def deploy(self, net='solana', tempo=None, min_ring=3, emission=1000):
        """Deploy (or reuse) the program, then a fresh SUBNET account: a
        new subnet per deploy, like a new contract on the other chains."""
        if self._stale():
            b = self.build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log']}
        if net == 'solana':
            st = self.validator('start')
            if not st.get('up'):
                return {'ok': False, 'stage': 'validator', 'error': st.get('error')}
        owner, relayer = self._kp('payer'), self._kp('relayer')
        prog = self._kp('program')
        self._fund(net, owner, 1000)
        self._fund(net, relayer, 1000)
        fresh = False
        if not self._program_current(net, prog.pub):
            env = dict(os.environ, PATH=AGAVE + os.pathsep + os.environ.get('PATH', ''))
            out = subprocess.run(
                ['solana', '-u', NETS[net]['rpc'], '-k', PAYER_KEY, 'program',
                 'deploy', '--program-id', PROGRAM_KEY, SO,
                 '--commitment', 'confirmed'],
                capture_output=True, text=True, timeout=600, env=env)
            if out.returncode != 0:
                return {'ok': False, 'stage': 'program deploy',
                        'error': (out.stdout + out.stderr).strip()[-800:]}
            fresh = True
            time.sleep(1)  # the program is usable from the next slot
        tempo = int(tempo or 300)
        r = self.rpc(net)
        sub = Keypair.generate()
        rent = r('getMinimumBalanceForRentExemption', [SUBNET_SIZE])
        data = b'\x00' + struct.pack('<QI', tempo, int(min_ring)) + \
            int(emission).to_bytes(16, 'little')
        try:
            t = self._send(net, [create_account(owner.pub, sub.pub, rent,
                                                SUBNET_SIZE, prog.pub),
                                 Ix(prog.pub, [(owner.pub, True, False),
                                               (sub.pub, False, True)], data)],
                           owner, [sub])
        except SolError as e:
            return {'ok': False, 'stage': 'init', 'error': str(e)}
        sub.save(os.path.join(SOL_DIR, 'subnet.json'))
        payee = self._kp('payee')
        rec = {'program': prog.address, 'subnet': sub.address,
               'owner': owner.address, 'relayer': relayer.address,
               'payee': payee.address, 'tempo': tempo,
               'min_ring': int(min_ring), 'emission': int(emission),
               'tx': t['sig'], 'program_bytes': os.path.getsize(SO),
               'program_fresh': fresh, 'rpc': NETS[net]['rpc'],
               'at': int(time.time())}
        d = self._deploys()
        d[net] = rec
        self._save_deploys(d)
        return {'ok': True, 'network': net, **rec}

    # ---- sending ---------------------------------------------------------------------------

    def _send(self, net, ixs, payer, signers=(), cu=None):
        r = self.rpc(net)
        pre = [cu_limit(cu)] if cu else []
        raw = compile_tx(pre + ixs, payer, list(signers), r.blockhash())
        try:
            sig = r.send(raw)
        except SolError as e:
            raise SolError(_program_error(str(e))) from None
        st = r.wait([sig])[0]
        if st.get('err'):
            raise SolError(self._failure(net, sig))
        return {'sig': sig}

    def _failure(self, net, sig):
        meta = self.rpc(net).tx_meta(sig) or {}
        return _program_error(' '.join(meta.get('logMessages') or [])) or \
            json.dumps(meta.get('err'))

    def _send_many(self, net, txs, payer):
        """Fire every tx at once (units are order-free), then wait for all."""
        r = self.rpc(net)
        bh = r.blockhash()
        sigs = [r.send(compile_tx(ixs, payer, list(signers), bh),
                       skip_preflight=True) for ixs, signers in txs]
        sts = r.wait(sigs)
        for sig, st in zip(sigs, sts):
            if st.get('err'):
                raise SolError(self._failure(net, sig))
        return sigs

    # ---- reads -------------------------------------------------------------------------------

    def _subnet(self, net):
        d = self._dep(net)
        raw = self.rpc(net).account(b58decode(d['subnet']))
        if not raw or raw[:8] != b'zumaSN01':
            raise SolError('subnet account missing (validator reset?)')
        u = lambda o, n: int.from_bytes(raw[o:o + n], 'little')
        out = {'owner': b58encode(raw[8:40]), 'tempo': u(40, 8),
               'start_slot': u(48, 8), 'min_ring': u(56, 4),
               'open': bool(raw[60]), 'emission': u(64, 16),
               'subnet_felt': int.from_bytes(raw[80:112], 'big'),
               'members': {}}
        for role in (1, 2):
            rows = []
            for i in range(u(112 + 4 * (role - 1), 4)):
                o = 120 + ((role - 1) * MAX_MEMBERS + i) * 96
                rows.append({'x': hex(int.from_bytes(raw[o:o + 32], 'big')),
                             'y': hex(int.from_bytes(raw[o + 32:o + 64], 'big')),
                             'key_id': int.from_bytes(raw[o + 64:o + 96], 'big')})
            out['members'][role] = rows
        return out

    def members(self, net, role):
        return [{'x': m['x'], 'y': m['y']}
                for m in self._subnet(net)['members'][int(role)]]

    def params(self, net):
        s = self._subnet(net)
        slot = self.slot(net)
        e = (slot - s['start_slot']) // s['tempo']
        return {**{k: v for k, v in s.items() if k != 'members'},
                'slot': slot, 'epoch': e,
                'slots_left': s['tempo'] - (slot - s['start_slot']) % s['tempo']}

    def _pda(self, net, *seeds):
        prog = b58decode(self._dep(net)['program'])
        return find_pda([s if isinstance(s, bytes) else s.encode() for s in seeds],
                        prog)[0]

    def _epoch_acct(self, net, e):
        return self._pda(net, 'epoch', b58decode(self._dep(net)['subnet']),
                         int(e).to_bytes(8, 'little'))

    def tally(self, net, epoch=None):
        e = int(epoch) if epoch is not None else self.params(net)['epoch']
        d = decode_epoch(self.rpc(net).account(self._epoch_acct(net, e)))
        if not d:
            return {'epoch': e, 'total': 0, 'validators': 0, 'rank_total': 0,
                    'settled': False, 'tags': []}
        tags = [{k: v for k, v in t.items() if k not in ('hist', 'work', 'top')}
                for t in d['tags']]
        return {'epoch': e, 'total': d['total'], 'validators': d['validators'],
                'rank_total': d['rank_total'], 'nsettled': d['nsettled'],
                'settled': bool(tags) and d['nsettled'] == len(tags),
                'tags': tags}

    def earned(self, net, recipient):
        sub = b58decode(self._dep(net)['subnet'])
        raw = self.rpc(net).account(self._pda(net, 'earned', sub,
                                               b58decode(recipient)))
        return int.from_bytes(raw[:16], 'little') if raw else 0

    def board(self, net, epochs=6):
        d = self._deploys().get(net)
        out = {'network': net, 'chain': 'solana', 'deployed': bool(d),
               'devnet_up': self._alive() if net == 'solana' else None}
        if not d or (net == 'solana' and not out['devnet_up']):
            return out
        try:
            p = self.params(net)
            s = self._subnet(net)
        except Exception as e:  # noqa: BLE001
            out.update(deployed=False, error=str(e))
            return out
        out.update(contract=d['subnet'], program=d['program'], owner=p['owner'],
                   tempo=p['tempo'], min_ring=p['min_ring'],
                   emission=p['emission'], open=p['open'],
                   subnet=hex(p['subnet_felt']), block=p['slot'],
                   epoch=p['epoch'], blocks_left=p['slots_left'],
                   miners=[{'x': m['x'], 'y': m['y']} for m in s['members'][1]],
                   validators=[{'x': m['x'], 'y': m['y']} for m in s['members'][2]])
        rows = []
        for ep in range(p['epoch'], max(-1, p['epoch'] - int(epochs)), -1):
            t = self.tally(net, ep)
            if t['tags']:
                t['open'] = ep == p['epoch']
                rows.append(t)
        out['epochs'] = rows
        return out

    # ---- writes --------------------------------------------------------------------------------

    def register(self, net, role, x, y):
        d = self._dep(net)
        owner = self._kp('payer')
        data = b'\x01' + bytes([int(role)]) + be(int(x, 16)) + be(int(y, 16))
        t = self._send(net, [Ix(b58decode(d['program']), [
            (owner.pub, True, False), (b58decode(d['subnet']), False, True)], data)],
            owner, cu=UNIT_CU)
        return t

    def set_open(self, net, flag):
        d = self._dep(net)
        owner = self._kp('payer')
        return self._send(net, [Ix(b58decode(d['program']), [
            (owner.pub, True, False), (b58decode(d['subnet']), False, True)],
            b'\x02' + bytes([int(bool(flag))]))], owner)

    def open_session(self, net, kind, n, epoch=0, msg=0, tag=0, recipient=None):
        """Create + open a session; returns (session keypair, its scope)."""
        d = self._dep(net)
        prog, sub = b58decode(d['program']), b58decode(d['subnet'])
        relayer = self._kp('relayer')
        ss = Keypair.generate()
        size = arrays(n)['end']
        rent = self.rpc(net)('getMinimumBalanceForRentExemption', [size])
        data = (b'\x03' + bytes([KINDS[kind], n]) + struct.pack('<Q', int(epoch)) +
                be(msg) + be(tag) + (b58decode(recipient) if recipient else bytes(32)))
        self._send(net, [create_account(relayer.pub, ss.pub, rent, size, prog),
                         Ix(prog, [(relayer.pub, True, False), (ss.pub, False, True),
                                   (sub, False, False)], data)],
                   relayer, [ss], cu=UNIT_CU)
        raw = self.rpc(net).account(ss.pub)
        return ss, int.from_bytes(raw[184:216], 'big'), \
            int.from_bytes(raw[80:88], 'little')

    def settle(self, net, epoch, tag):
        """Yuma for one tag of a closed epoch (instruction 9, anyone)."""
        d = self._dep(net)
        prog, sub = b58decode(d['program']), b58decode(d['subnet'])
        payer = self._kp('relayer')
        ep = self._epoch_acct(net, epoch)
        t = self._send(net, [Ix(prog, [(sub, False, False), (ep, False, True)],
                                settle_data(epoch, tag))], payer, cu=SETTLE_CU)
        m = self.rpc(net).tx_meta(t['sig']) or {}
        row = next((r for r in (decode_epoch(self.rpc(net).account(ep)) or
                                {'tags': []})['tags']
                    if int(r['tag'], 16) == int(tag)), {})
        return {'sig': t['sig'], 'cu': m.get('computeUnitsConsumed') or 0,
                'consensus': row.get('consensus', 0), 'rank': row.get('rank', 0)}

    def abort(self, net, ss):
        d = self._dep(net)
        relayer = self._kp('relayer')
        try:
            self._send(net, [Ix(b58decode(d['program']), [
                (relayer.pub, True, True), (ss.pub, False, True)], b'\x08')], relayer)
        except Exception:  # noqa: BLE001
            pass

    def act(self, net, ss, kind, ring_idx, sig, trace, epoch, tag=0,
            recipient=None):
        """Upload signature + hints, seal, run every unit, finish."""
        # hints come from core/stark.py (imported at module top)
        d = self._dep(net)
        prog, sub = b58decode(d['program']), b58decode(d['subnet'])
        relayer = self._kp('relayer')
        n = len(ring_idx)
        ar = arrays(n)
        I = lambda v: int(v, 16) if isinstance(v, str) else int(v)
        img = (I(sig['image']['x']), I(sig['image']['y']))
        blob = bytearray(ar['end'] - SS_PUT)

        def put_at(off, b):
            blob[off - SS_PUT:off - SS_PUT + len(b)] = b
        put_at(280, be(I(sig['c0'])))
        put_at(312, pt(img))
        put_at(376, pt(trace['hp']))
        put_at(440, be(trace['d']))
        put_at(472, struct.pack('<I', trace['hp_ctr']))
        put_at(480, bytes(ring_idx))
        for i in range(n):
            put_at(ar['s'] + 32 * i, be(I(sig['s'][i])))
            put_at(ar['c'] + 32 * i, be(trace['c'][i]))
            put_at(ar['l'] + 64 * i, pt(trace['L'][i]))
            put_at(ar['r'] + 64 * i, pt(trace['R'][i]))
            for w in range(2):
                for k in range(SEGS - 1):
                    put_at(ar['mids'] + 64 * ((i * 2 + w) * (SEGS - 1) + k),
                           pt(trace['mids'][i][w][k]))
        for k, st in enumerate(trace['sponge']):
            put_at(ar['sponge'] + 96 * k, b''.join(be(v) for v in st))
        acc = [(relayer.pub, True, False), (ss.pub, False, True)]
        t0 = time.time()
        chunk = 900
        puts = [([Ix(prog, acc, b'\x04' + struct.pack('<I', SS_PUT + o) +
                     bytes(blob[o:o + chunk]))], [])
                for o in range(0, len(blob), chunk)]
        self._send_many(net, puts, relayer)
        self._send(net, [Ix(prog, acc + [(sub, False, False)], b'\x05')], relayer)
        uacc = [(ss.pub, False, True), (sub, False, False)]
        units = []
        for i in range(n):
            for w in range(2):
                for k in range(SEGS):
                    units.append(bytes([0, i, w, k]))
            units.append(bytes([1, i, 0, 0]))
        for k in range(n_digest(n)):
            units.append(bytes([2, k, 0, 0]))
        for j in range(trace['hp_ctr'] + 1):
            units.append(bytes([3, j, 0, 0]))
        usigs = self._send_many(net, [([cu_limit(UNIT_CU), Ix(prog, uacc, b'\x06' + u)], [])
                                      for u in units], relayer)
        verify_s = round(time.time() - t0, 2)
        # finish: epoch PDA + the kind's extra account
        ep = self._epoch_acct(net, epoch)
        image_id = stark.key_id(img)
        if kind == 'score':
            extra = self._pda(net, 'null', sub, be(image_id))
        elif kind == 'claim':
            extra = self._pda(net, 'earned', sub, b58decode(recipient))
        else:
            extra = SYSTEM
        f = self._send(net, [Ix(prog, [(relayer.pub, True, True), (ss.pub, False, True),
                                       (sub, False, False), (ep, False, True),
                                       (extra, False, extra != SYSTEM),
                                       (SYSTEM, False, False)], b'\x07')],
                       relayer, cu=UNIT_CU)
        r = self.rpc(net)
        cus = []
        for s in usigs:
            m = r.tx_meta(s) or {}
            cus.append(m.get('computeUnitsConsumed') or 0)
        return {'sig': f['sig'], 'image_id': image_id, 'units': len(units),
                'txs': len(puts) + len(units) + 3, 'cu': sum(cus),
                'cu_max': max(cus), 'verify_s': verify_s}


def _program_error(text):
    import re
    m = re.findall(r'Program log: ztao: (.+?)(?= Program |"|\]|$)', text)
    if m:
        return m[-1].strip()
    m = re.search(r'(custom program error: 0x[0-9a-f]+|InstructionError[^}]*)', text)
    return m.group(1) if m else text[-400:]
