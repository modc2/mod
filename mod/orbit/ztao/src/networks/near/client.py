"""ztao on NEAR, the wire layer: RPC, borsh txs, the sandbox, build/deploy
of contract/ (the Rust crate). adapter.py turns it into a Network.

Signatures come from the SAME Cairo prover (core/prover/). Only the scope's
`subnet` felt changes: it is felt(sha256(contract account)), which the
contract reports as `mine_scope(epoch)` / `score_scope(epoch, tag)`, so the
signer asks the chain for the scope rather than recomputing it.

Networks:
    near           local near-sandbox (neard 2.0, protocol 69) on :51503,
                   home data/near-sandbox/, root account test.near
    near-testnet   NEAR testnet; signer = an account in the near module's
                   keystore (~/.mod/near/keys.json), pass account=

Transactions are borsh-encoded and ed25519-signed here (PyNaCl), with no
near-cli and no node at runtime.
"""

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request

from ... import DATA

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, 'contract')
SANDBOX_HOME = os.path.join(DATA, 'near-sandbox')
SANDBOX_PORT = int(os.environ.get('ZTAO_NEAR_PORT', 51503))
# p2p port: NOT rpc+1 — :51504 is anvil (networks/eth)
SANDBOX_NET_PORT = int(os.environ.get('ZTAO_NEAR_NET_PORT', 51501))
SANDBOX_BIN = os.path.expanduser('~/.local/bin/near-sandbox')
# own target dir: the MVP flags below change every artifact, so sharing
# orbit/near's warm dir would make the two thrash (cold build ~1 min)
TARGET = os.environ.get('ZTAO_NEAR_TARGET', os.path.join(CRATE, 'target'))
# nearcore 2.0 (protocol 69) parses MVP wasm (+sign-ext) only; rustc >= 1.82
# emits reference-types/bulk-memory by default -> PrepareError: Deserialization
# `--cfg near` is what cargo-near passes: without it near-sdk-env (5.2x)
# compiles its LOCAL fallbacks into the wasm — panics become a bare
# `unreachable` trap (no message) and sha256 runs in wasm, not the host
RUSTFLAGS = '-C target-cpu=mvp -C link-arg=-s --cfg near'
RAW_WASM = os.path.join(TARGET, 'wasm32-unknown-unknown', 'release', 'ztao_near.wasm')
WASM = RAW_WASM.replace('.wasm', '.lowered.wasm')  # what gets deployed
LOWER_SRC = os.path.join(HERE, 'tools', 'wasmlower')
LOWER_BIN = os.path.join(TARGET, 'release', 'wasmlower')
NEAR_KEYS = os.path.expanduser('~/.mod/near/keys.json')
TGAS = 10 ** 12
YOCTO = 10 ** 24
ACTION_GAS = 300 * TGAS

NETS = {
    'near': {'rpc': f'http://127.0.0.1:{SANDBOX_PORT}', 'root': 'test.near'},
    'near-testnet': {'rpc': os.environ.get('ZTAO_NEAR_TESTNET_RPC',
                                           'https://rpc.testnet.fastnear.com'),
                     'root': None},
}

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
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return b'\0' * (len(s) - len(s.lstrip('1'))) + raw


def text_felt(s):
    """Same rule as the contract: sha256 with the top 5 bits cleared."""
    b = bytearray(hashlib.sha256(str(s).encode()).digest())
    b[0] &= 0x07
    return int.from_bytes(b, 'big')


# ---- borsh: exactly the transaction schema NEAR uses ------------------------

def _u32(n): return int(n).to_bytes(4, 'little')
def _u64(n): return int(n).to_bytes(8, 'little')
def _u128(n): return int(n).to_bytes(16, 'little')
def _bstr(s): b = s.encode(); return _u32(len(b)) + b
def _bbytes(b): return _u32(len(b)) + b
def _pk(raw): return b'\0' + raw


def a_create_account(): return b'\x00'
def a_deploy(code): return b'\x01' + _bbytes(code)
def a_call(method, args, gas=ACTION_GAS, deposit=0):
    return (b'\x02' + _bstr(method) + _bbytes(json.dumps(args).encode())
            + _u64(gas) + _u128(deposit))
def a_transfer(yocto): return b'\x03' + _u128(yocto)
def a_add_full_key(pub): return b'\x05' + _pk(pub) + _u64(0) + b'\x01'


class NearError(RuntimeError):
    pass


def _panic_text(failure):
    """Dig 'Smart contract panicked: X' out of a tx Failure -> X."""
    s = json.dumps(failure)
    for marker in ('Smart contract panicked: ', '"ExecutionError": "'):
        i = s.find(marker)
        if i >= 0:
            return s[i + len(marker):].split('"')[0]
    return s[:400]


class NearZTao:
    def __init__(self, data_dir):
        self.data = data_dir
        self.deploys_path = os.path.join(data_dir, 'deployments.json')

    # ---- json-rpc ----------------------------------------------------------

    def _rpc(self, net, method, params, timeout=60):
        req = urllib.request.Request(
            NETS[net]['rpc'], data=json.dumps({'jsonrpc': '2.0', 'id': 'z',
                                               'method': method,
                                               'params': params}).encode(),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'ztao/0.3'})
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        if 'error' in r:
            e = r['error']
            raise NearError((e.get('data') if isinstance(e.get('data'), str)
                             else None) or json.dumps(e)[:400])
        return r['result']

    def view(self, net, method, args=None, contract=None):
        contract = contract or self._dep(net)['account']
        r = self._rpc(net, 'query', {
            'request_type': 'call_function', 'finality': 'optimistic',
            'account_id': contract, 'method_name': method,
            'args_base64': base64.b64encode(
                json.dumps(args or {}).encode()).decode()})
        if r.get('error'):
            raise NearError(r['error'])
        return json.loads(bytes(r['result']))

    def block(self, net):
        return self._rpc(net, 'block', {'finality': 'optimistic'})['header']['height']

    # ---- keys --------------------------------------------------------------

    def _signer(self, net, account=None):
        """(account_id, seed). Sandbox: test.near's validator key, which also
        keys every sub-account created here. Testnet: the near keystore."""
        if net == 'near':
            k = json.load(open(os.path.join(SANDBOX_HOME, 'validator_key.json')))
            seed = b58decode(k['secret_key'].split(':', 1)[1])[:32]
            return account or k['account_id'], seed
        store = json.load(open(NEAR_KEYS))
        account = account or store.get('default', {}).get('testnet')
        e = store.get('accounts', {}).get(f'testnet:{account}')
        if not e:
            raise NearError(f'no testnet key for {account!r} in {NEAR_KEYS}')
        return account, b58decode(e['secret_key'].split(':', 1)[1])[:32]

    @staticmethod
    def _pub(seed):
        from nacl.signing import SigningKey
        return bytes(SigningKey(seed).verify_key)

    def _tx(self, net, signer, receiver, actions, account_seed=None):
        from nacl.signing import SigningKey
        account, seed = account_seed or self._signer(net, signer)
        pub = self._pub(seed)
        key = self._rpc(net, 'query', {
            'request_type': 'view_access_key', 'finality': 'optimistic',
            'account_id': account,
            'public_key': 'ed25519:' + b58encode(pub)})
        nonce = int(key['nonce']) + 1
        # optimistic head: after a sandbox fast-forward the final block can
        # sit hundreds of heights back, past the tx validity window (Expired)
        bh = b58decode(self._rpc(net, 'block', {'finality': 'optimistic'})
                       ['header']['hash'])
        tx = (_bstr(account) + _pk(pub) + _u64(nonce) + _bstr(receiver) + bh
              + _u32(len(actions)) + b''.join(actions))
        sig = SigningKey(seed).sign(hashlib.sha256(tx).digest()).signature
        signed = base64.b64encode(tx + b'\0' + sig).decode()
        r = self._rpc(net, 'send_tx', {'signed_tx_base64': signed,
                                       'wait_until': 'EXECUTED_OPTIMISTIC'},
                      timeout=120)
        gas = r['transaction_outcome']['outcome']['gas_burnt'] + sum(
            o['outcome']['gas_burnt'] for o in r.get('receipts_outcome', []))
        logs = [l for o in r.get('receipts_outcome', [])
                for l in o['outcome'].get('logs', [])]
        st = r.get('status', {})
        if 'Failure' in st:
            raise NearError(_panic_text(st['Failure']))
        val = st.get('SuccessValue')
        result = json.loads(base64.b64decode(val)) if val else None
        return {'hash': r['transaction']['hash'], 'tgas': round(gas / TGAS, 2),
                'result': result, 'logs': logs}

    # ---- local sandbox -----------------------------------------------------

    def _alive(self):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{SANDBOX_PORT}/status',
                                   timeout=1).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def sandbox(self, action='status'):
        """start | stop | reset | status for the local NEAR node on :51503."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{SANDBOX_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.sandbox('stop')
            time.sleep(1)
            subprocess.run(['rm', '-rf', SANDBOX_HOME])
            d = self._deploys()
            d.pop('near', None)
            self._save_deploys(d)
            return self.sandbox('start')
        if action == 'start' and not self._alive():
            if not os.path.exists(SANDBOX_BIN):
                return {'up': False, 'error': f'{SANDBOX_BIN} missing (neard '
                        'sandbox build; near-sandbox-utils ships one)'}
            if not os.path.exists(os.path.join(SANDBOX_HOME, 'genesis.json')):
                os.makedirs(SANDBOX_HOME, exist_ok=True)
                subprocess.run([SANDBOX_BIN, '--home', SANDBOX_HOME, 'init'],
                               capture_output=True, timeout=60)
            log = open(os.path.join(SANDBOX_HOME, 'sandbox.log'), 'a')
            subprocess.Popen(
                [SANDBOX_BIN, '--home', SANDBOX_HOME, 'run', '--rpc-addr',
                 f'127.0.0.1:{SANDBOX_PORT}', '--network-addr',
                 f'127.0.0.1:{SANDBOX_NET_PORT}'],
                stdout=log, stderr=log, cwd=SANDBOX_HOME,
                start_new_session=True)
            for _ in range(100):
                if self._alive():
                    break
                time.sleep(0.2)
            else:
                return {'up': False, 'error': 'sandbox did not come up'}
            while self.block('near') < 2:
                time.sleep(0.3)
        up = self._alive()
        return {'up': up, 'port': SANDBOX_PORT,
                **({'block': self.block('near')} if up else {})}

    def fast_forward(self, net, blocks):
        """Sandbox only: jump the chain `blocks` heights ahead."""
        if net != 'near':
            raise NearError('fast_forward is sandbox-only')
        target = self.block(net) + int(blocks)
        self._rpc(net, 'sandbox_fast_forward', {'delta_height': int(blocks)})
        while self.block(net) < target:
            time.sleep(0.2)
        # let finality catch up, so final-block reads see the new epoch too
        while self._rpc(net, 'block', {'finality': 'final'})['header'][
                'height'] < target:
            time.sleep(0.2)

    # ---- build + deploy ----------------------------------------------------

    def build(self):
        """cargo build (MVP flags) -> tools/wasmlower -> WASM. The std that
        rustup ships still carries memory.copy/fill and overlong
        call_indirect; wasmlower rewrites them and validates the result as
        MVP + sign-ext, which is what nearcore (sandbox AND testnet) loads."""
        env = dict(os.environ, CARGO_TARGET_DIR=TARGET, RUSTFLAGS=RUSTFLAGS)
        out = subprocess.run(['cargo', 'build', '--offline', '--release',
                              '--target', 'wasm32-unknown-unknown'],
                             cwd=CRATE, capture_output=True, text=True,
                             timeout=900, env=env)
        if out.returncode != 0 or not os.path.exists(RAW_WASM):
            return {'ok': False, 'stage': 'cargo', 'wasm': None,
                    'log': out.stderr.strip()[-1500:]}
        if not os.path.exists(LOWER_BIN):
            lb = subprocess.run(['cargo', 'build', '--offline', '--release'],
                                cwd=LOWER_SRC, capture_output=True, text=True,
                                timeout=600, env=dict(os.environ,
                                                      CARGO_TARGET_DIR=TARGET))
            if lb.returncode != 0:
                return {'ok': False, 'stage': 'wasmlower build', 'wasm': None,
                        'log': lb.stderr.strip()[-1500:]}
        lo = subprocess.run([LOWER_BIN, RAW_WASM, WASM], capture_output=True,
                            text=True, timeout=60)
        if lo.returncode != 0:
            return {'ok': False, 'stage': 'wasmlower', 'wasm': None,
                    'log': (lo.stdout + lo.stderr).strip()[-1500:]}
        return {'ok': True, 'wasm': WASM, 'bytes': os.path.getsize(WASM),
                'lowered': json.loads(lo.stdout.strip().splitlines()[-1]),
                'log': out.stderr.strip()[-600:]}

    def _stale(self):
        if not os.path.exists(WASM):
            return True
        t = os.path.getmtime(WASM)
        srcs = [os.path.join(CRATE, 'Cargo.toml')] + [
            os.path.join(CRATE, 'src', f) for f in os.listdir(
                os.path.join(CRATE, 'src'))]
        return any(os.path.getmtime(p) > t for p in srcs)

    def test(self):
        env = dict(os.environ, CARGO_TARGET_DIR=TARGET)
        out = subprocess.run(['cargo', 'test', '--offline', '--lib'],
                             cwd=CRATE, capture_output=True, text=True,
                             timeout=900, env=env)
        import re
        tests = re.findall(r'^test tests::(\w+)(?: - should panic)? \.\.\. (ok|FAILED)',
                           out.stdout, re.M)
        return {'ok': out.returncode == 0,
                'passed': sum(1 for _, s in tests if s == 'ok'),
                'failed': sum(1 for _, s in tests if s != 'ok'),
                'tests': [['PASS' if s == 'ok' else 'FAIL', n] for n, s in tests],
                'log': (out.stdout + out.stderr).strip()[-3000:]}

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
            raise NearError(f'not deployed on {net}; run deploy network={net}')
        return d

    def _ensure_account(self, net, name, fund_near):
        """Sandbox: create <name>.test.near (same key as test.near) once."""
        root = NETS[net]['root']
        acct = f'{name}.{root}'
        try:
            self._rpc(net, 'query', {'request_type': 'view_account',
                                     'finality': 'optimistic',
                                     'account_id': acct})
            return acct, False
        except NearError:
            pass
        _, seed = self._signer(net)
        self._tx(net, root, acct, [a_create_account(),
                                   a_transfer(int(fund_near * YOCTO)),
                                   a_add_full_key(self._pub(seed))])
        return acct, True

    def deploy(self, net='near', tempo=None, min_ring=3, emission=1000,
               account=None, contract=None):
        """Sandbox: a fresh ztao-<ts>.test.near per deploy. Testnet: deploys
        to `contract` (default: the signer `account` itself; NEAR accounts
        ARE contracts) — needs ~5 NEAR for storage of a 440 KB wasm."""
        if self._stale():
            b = self.build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log']}
        code = open(WASM, 'rb').read()
        tempo = int(tempo or (100 if net == 'near' else 600))
        if net == 'near':
            st = self.sandbox('start')
            if not st.get('up'):
                return {'ok': False, 'error': st.get('error')}
            self._ensure_account(net, 'relayer', 50)
            self._ensure_account(net, 'payee', 1)
            acct, _ = self._ensure_account(net, f'ztao-{int(time.time())}', 20)
            owner = NETS[net]['root']
            relayer, payee = f'relayer.{owner}', f'payee.{owner}'
        else:
            owner, _ = self._signer(net, account)
            acct = contract or owner
            relayer, payee = owner, owner
        init = {'owner': owner, 'tempo': tempo, 'min_ring': int(min_ring),
                'emission': str(int(emission))}
        try:
            r = self._tx(net, acct, acct, [a_deploy(code), a_call('new', init)])
        except NearError as e:
            return {'ok': False, 'stage': 'deploy', 'error': str(e)}
        rec = {'account': acct, 'owner': owner, 'relayer': relayer,
               'payee': payee, 'tempo': tempo, 'min_ring': int(min_ring),
               'emission': int(emission), 'tx': r['hash'], 'tgas': r['tgas'],
               'wasm_bytes': len(code),
               'wasm_sha256': hashlib.sha256(code).hexdigest(),
               'rpc': NETS[net]['rpc'], 'at': int(time.time())}
        d = self._deploys()
        d[net] = rec
        self._save_deploys(d)
        return {'ok': True, 'network': net, **rec}

    # ---- contract calls ----------------------------------------------------

    def invoke(self, net, method, args, as_owner=False):
        d = self._dep(net)
        signer = d['owner'] if as_owner else d['relayer']
        return self._tx(net, signer, d['account'], [a_call(method, args)])

    def board(self, net, epochs=6):
        d = self._deploys().get(net)
        out = {'network': net, 'chain': 'near', 'deployed': bool(d),
               'devnet_up': self._alive() if net == 'near' else None}
        if not d or (net == 'near' and not out['devnet_up']):
            return out
        p = self.view(net, 'params')
        b = self.block(net)
        e = b // p['tempo']
        out.update(contract=d['account'], owner=p['owner'], tempo=p['tempo'],
                   min_ring=p['min_ring'], emission=int(p['emission']),
                   open=p['open'], subnet=p['subnet'], block=b, epoch=e,
                   blocks_left=p['tempo'] - b % p['tempo'],
                   miners=self.view(net, 'members', {'role': 1}),
                   validators=self.view(net, 'members', {'role': 2}))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = self.tally(net, ep)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out

    def tally(self, net, epoch=None):
        e = int(epoch) if epoch is not None else self.view(net, 'epoch')
        t = self.view(net, 'tally', {'epoch': e})
        return {'epoch': e, 'total': int(t['total']),
                'validators': t['validators'],
                'rank_total': int(t['rank_total']), 'settled': t['settled'],
                'tags': [{'tag': r['tag'], 'score_sum': int(r['score_sum']),
                          'scores': r['scores'], 'claimed': r['claimed'],
                          'consensus': r['consensus'], 'rank': int(r['rank']),
                          'settled': r['settled']}
                         for r in t['tags']]}
