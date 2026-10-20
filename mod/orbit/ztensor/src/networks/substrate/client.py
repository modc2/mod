"""ztensor on Substrate, the wire: substrate-contracts-node, the ink! build,
a SCALE codec for the contract ABI, dry-runs and extrinsics.

Extrinsics are signed and submitted by substrate-interface (sr25519). The
contract ABI is done HERE, not through substrate-interface's ContractInstance:
its ink! metadata parser breaks next to cyscale (installed for bittensor),
and the messages only use a handful of SCALE shapes anyway (u8..u128, bool,
[u8; 32], Vec, Option, structs). Selectors come from the build's metadata.

Every call is a dry-run first (ContractsApi_call via state_call): it returns
the message's value, whether it reverted, and the weight it needs. A write
then goes on chain with that weight (+20%) as its gas limit; the dry-run's
value is what the action returns (the chain state can not move in between
on a local node, and a revert on chain raises).

Nets:
    substrate          local substrate-contracts-node --dev on :51337 (one
                       block per extrinsic; advance() seals empty remark blocks)
    substrate-testnet  any pallet-contracts chain: ZTENSOR_SUBSTRATE_RPC
                       (ws/wss) + ZTENSOR_SUBSTRATE_SEED (sr25519 URI/mnemonic)
"""
import hashlib
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, 'contract')
INK = os.path.join(CRATE, 'target', 'ink')
WASM = os.path.join(INK, 'ztensor_ink.wasm')
META = os.path.join(INK, 'ztensor_ink.json')
NODE_BIN = os.path.expanduser(os.environ.get(
    'ZTENSOR_SUBSTRATE_NODE', '~/.local/bin/substrate-contracts-node'))
RPC_PORT = int(os.environ.get('ZTENSOR_SUBSTRATE_PORT', 51337))
P2P_PORT = RPC_PORT + 1
# cargo-contract 5.0.3 passes -Zbuild-std-features=panic_immediate_abort,
# which rustc >= 1.91 rejects; lambdaworks-math 0.13 needs >= 1.87
TOOLCHAIN = os.environ.get('ZTENSOR_INK_TOOLCHAIN', '1.88.0')

NETS = {
    'substrate': {'url': f'ws://127.0.0.1:{RPC_PORT}', 'local': True},
    'substrate-testnet': {'url': os.environ.get('ZTENSOR_SUBSTRATE_RPC', ''),
                          'local': False},
}

# ink! 5 messages return Result<T, LangError>; ours wrap a Result<T, Error>
ERRORS = ['BadParams', 'NotAFelt', 'NotOnCurve', 'BadRole', 'RegistryClosed',
          'AlreadyRegistered', 'OnlyOwner', 'RingBelowMin', 'RingAboveMax',
          'NonMember', 'RingNotSorted', 'BadSignature', 'AlreadySubmitted',
          'NoSuchTag', 'ScoreRange', 'AlreadyScored', 'EpochOpen',
          'AlreadySettled', 'EpochNotSettled', 'AlreadyClaimed', 'Overflow']
# the words simulate() and the console already show for these on every chain
PLAIN = {'AlreadyClaimed': 'already claimed', 'AlreadySettled': 'already settled',
         'AlreadyScored': 'already scored this tag',
         'AlreadySubmitted': 'already submitted this epoch',
         'EpochNotSettled': 'epoch not settled', 'EpochOpen': 'epoch still open',
         'BadSignature': 'bad ring signature'}
REVERT = 1  # ExecReturnValue flags bit


class SubstrateError(RuntimeError):
    pass


# ---- SCALE ---------------------------------------------------------------

def compact(n):
    n = int(n)
    if n < 1 << 6:
        return bytes([n << 2])
    if n < 1 << 14:
        return ((n << 2) | 1).to_bytes(2, 'little')
    if n < 1 << 30:
        return ((n << 2) | 2).to_bytes(4, 'little')
    b = n.to_bytes((n.bit_length() + 7) // 8, 'little')
    return bytes([((len(b) - 4) << 2) | 3]) + b


def u(n, bits):
    return int(n).to_bytes(bits // 8, 'little')


def felt32(v):
    """felt -> [u8; 32] big-endian (the contract rejects values >= p)."""
    return int(str(v), 0).to_bytes(32, 'big')


def point(p):
    return felt32(p['x']) + felt32(p['y'])


def vec(items, enc):
    return compact(len(items)) + b''.join(enc(i) for i in items)


def option(v, enc):
    return b'\0' if v is None else b'\1' + enc(v)


def signature(s):
    """Signature { c0, s: Vec<F>, image: Point, hp: Option<Point> }"""
    return (felt32(s['c0']) + vec(s['s'], felt32) + point(s['image'])
            + option(s.get('hp'), point))


class Reader:
    def __init__(self, b):
        self.b, self.i = bytes(b), 0

    def take(self, n):
        if self.i + n > len(self.b):
            raise SubstrateError('SCALE: ran out of bytes')
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def u(self, bits):
        return int.from_bytes(self.take(bits // 8), 'little')

    def compact(self):
        m = self.b[self.i] & 3
        if m == 0:
            return self.u(8) >> 2
        if m == 1:
            return self.u(16) >> 2
        if m == 2:
            return self.u(32) >> 2
        n = (self.u(8) >> 2) + 4
        return int.from_bytes(self.take(n), 'little')

    def bool(self):
        return self.u(8) == 1

    def felt(self):
        return hex(int.from_bytes(self.take(32), 'big'))

    def point(self):
        return {'x': self.felt(), 'y': self.felt()}

    def account(self):
        return self.take(32)

    def vec(self, f):
        return [f() for _ in range(self.compact())]

    def bytes(self):
        return self.take(self.compact())


def ss58(pub, fmt=42):
    from substrateinterface.utils.ss58 import ss58_encode
    return ss58_encode(pub, fmt)


def account_bytes(addr):
    from substrateinterface.utils.ss58 import ss58_decode
    return bytes.fromhex(ss58_decode(addr))


def bytes_felt(data):
    """The contract's recipient/subnet rule: blake2_256, top 5 bits cleared."""
    d = bytearray(hashlib.blake2b(bytes(data), digest_size=32).digest())
    d[0] &= 0x07
    return int.from_bytes(d, 'big')


# ---- result decoders, one per message return type ------------------------

def _tagrow(r):
    return {'tag': r.felt(), 'work': r.felt(), 'score_sum': r.u(128),
            'scores': r.u(32), 'claimed': r.bool(), 'consensus': r.u(8),
            'rank': r.u(128), 'settled': r.bool()}


DECODE = {
    'F': lambda r: r.felt(),
    'u8': lambda r: r.u(8),
    'u32': lambda r: r.u(32),
    'u64': lambda r: r.u(64),
    'u128': lambda r: r.u(128),
    'bool': lambda r: r.bool(),
    'unit': lambda r: None,
    'point': lambda r: r.point(),
    'points': lambda r: r.vec(r.point),
    'settle': lambda r: (r.u(8), r.u(128)),
    'yuma': lambda r: {'hist': r.vec(lambda: (r.u(8), r.u(32))),
                       'consensus': r.u(8), 'rank': r.u(128),
                       'settled': r.bool()},
    'yuma_epoch': lambda r: {'vset': r.u(32), 'rank_total': r.u(128),
                             'nsettled': r.u(32)},
    'params': lambda r: {'owner': ss58(r.account()), 'tempo': r.u(64),
                         'min_ring': r.u(32), 'max_ring': r.u(32),
                         'emission': r.u(128), 'open': r.bool(),
                         'subnet': r.felt()},
    'tally': lambda r: {'epoch': r.u(64), 'total': r.u(128),
                        'validators': r.u(32), 'rank_total': r.u(128),
                        'settled': r.u(32), 'tags': r.vec(lambda: _tagrow(r))},
}

# message -> (return decoder, wraps Result<_, Error>)
MESSAGES = {
    'register': ('u32', True), 'set_open': ('unit', True),
    'transfer_ownership': ('unit', True),
    'submit': ('F', True), 'score': ('F', True), 'settle': ('settle', True),
    'claim': ('u128', True),
    'epoch': ('u64', False), 'block': ('u64', False), 'subnet': ('F', False),
    'mine_scope': ('F', False), 'score_scope': ('F', True),
    'recipient_felt': ('F', False), 'role_of': ('u8', True),
    'count': ('u32', False), 'members': ('points', False),
    'yuma': ('yuma', False), 'yuma_epoch': ('yuma_epoch', False),
    'tally': ('tally', False), 'earned': ('u128', False),
    'params': ('params', False), 'verify': ('bool', True),
    'hash_point': ('point', True),
}


def decode_return(name, data):
    """ink! output bytes -> value. Result<T, LangError> first (0 = Ok), then
    our Result<T, Error> for the messages that return one."""
    kind, wrapped = MESSAGES[name]
    r = Reader(data)
    if r.u(8) != 0:
        raise SubstrateError(f'{name}: ink! LangError (bad selector or input)')
    if wrapped and r.u(8) != 0:
        code = r.u(8)
        err = ERRORS[code] if code < len(ERRORS) else f'Error#{code}'
        raise SubstrateError(PLAIN.get(err, err))
    return DECODE[kind](r)


def selectors(meta_path=META):
    m = json.load(open(meta_path))
    spec = m['spec']
    out = {x['label']: bytes.fromhex(x['selector'][2:]) for x in spec['messages']}
    out['@new'] = bytes.fromhex(spec['constructors'][0]['selector'][2:])
    return out


def weight(r):
    """Weight is SCALE {ref_time: Compact<u64>, proof_size: Compact<u64>}."""
    return {'ref_time': r.compact(), 'proof_size': r.compact()}


def decode_dry_run(raw):
    """ContractResult<Result<ExecReturnValue, DispatchError>, Balance>:
    gas_consumed, gas_required, storage_deposit, debug_message, result."""
    r = Reader(raw)
    out = {'gas_consumed': weight(r), 'gas_required': weight(r)}
    tag = r.u(8)  # StorageDeposit::Refund(0) | Charge(1)
    amt = r.u(128)
    out['storage_deposit'] = amt if tag == 1 else -amt
    out['debug'] = r.bytes().decode(errors='replace')
    if r.u(8) != 0:
        out['error'] = 'DispatchError ' + r.b[r.i:r.i + 8].hex()
        return out
    out['flags'] = r.u(32)
    out['data'] = r.bytes()
    return out


# ---- the client ------------------------------------------------------------

class SubstrateZTensor:
    """One object per process: node lifecycle, build/test and the contract."""

    GAS_MARGIN = 1.2

    def __init__(self, data_dir):
        self.data = os.path.join(data_dir, 'substrate')
        os.makedirs(self.data, exist_ok=True)
        self.deploys_path = os.path.join(data_dir, 'substrate_deploys.json')
        self._apis, self._sel = {}, None

    # ---- node ------------------------------------------------------------

    def _alive(self):
        import socket
        try:
            socket.create_connection(('127.0.0.1', RPC_PORT), 0.3).close()
            return True
        except OSError:
            return False

    def node(self, action='status'):
        """start | stop | reset | status of substrate-contracts-node --dev."""
        if action == 'stop':
            self._apis.pop('substrate', None)
            r = subprocess.run(['fuser', '-k', f'{RPC_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.node('stop')
            time.sleep(1)
            subprocess.run(['rm', '-rf', os.path.join(self.data, 'chain')])
            d = self._deploys()
            d.pop('substrate', None)
            self._save_deploys(d)
            return self.node('start')
        if action == 'start' and not self._alive():
            if not os.path.exists(NODE_BIN):
                return {'up': False, 'error': f'{NODE_BIN} missing (substrate-'
                        'contracts-node v0.42 release binary)'}
            log = open(os.path.join(self.data, 'node.log'), 'a')
            subprocess.Popen(
                [NODE_BIN, '--dev', '--base-path', os.path.join(self.data, 'chain'),
                 '--rpc-port', str(RPC_PORT), '--port', str(P2P_PORT),
                 '--no-prometheus', '--no-telemetry'],
                stdout=log, stderr=log, start_new_session=True)
            for _ in range(60):
                if self._alive():
                    break
                time.sleep(0.25)
        return {'up': self._alive(), 'port': RPC_PORT, 'bin': NODE_BIN}

    def api(self, net):
        from substrateinterface import SubstrateInterface
        a = self._apis.get(net)
        if a is not None:
            try:
                a.get_block_number(None)
                return a
            except Exception:  # noqa: BLE001 - reconnect below
                self._apis.pop(net, None)
        url = NETS[net]['url']
        if not url:
            raise SubstrateError(f'{net}: set ZTENSOR_SUBSTRATE_RPC (ws/wss URL '
                                 'of a pallet-contracts chain)')
        if net == 'substrate' and not self._alive():
            raise SubstrateError('substrate-contracts-node is not running '
                                 '(m ztensor/devnet chain=substrate)')
        a = SubstrateInterface(url=url)
        self._apis[net] = a
        return a

    def keypair(self, net, who='owner'):
        from substrateinterface import Keypair
        if NETS[net]['local']:
            return Keypair.create_from_uri(
                {'owner': '//Alice', 'relayer': '//Bob', 'payee': '//Charlie'}[who])
        seed = os.environ.get('ZTENSOR_SUBSTRATE_SEED')
        if not seed:
            raise SubstrateError(f'{net}: set ZTENSOR_SUBSTRATE_SEED')
        return (Keypair.create_from_uri(seed) if seed.startswith('/')
                else Keypair.create_from_mnemonic(seed))

    def block(self, net):
        return int(self.api(net).get_block_number(None))

    def seal(self, net, n):
        """Local node seals one block per extrinsic: n empty remarks = n blocks."""
        a, k = self.api(net), self.keypair(net, 'owner')
        nonce = a.get_account_nonce(k.ss58_address)
        for i in range(int(n)):
            call = a.compose_call('System', 'remark', {'remark': '0x'})
            ex = a.create_signed_extrinsic(call=call, keypair=k, nonce=nonce + i)
            a.submit_extrinsic(ex, wait_for_inclusion=True)

    # ---- build / test ----------------------------------------------------

    def _stale(self):
        if not (os.path.exists(WASM) and os.path.exists(META)):
            return True
        t = min(os.path.getmtime(WASM), os.path.getmtime(META))
        srcs = [os.path.join(CRATE, 'src', f) for f in os.listdir(
            os.path.join(CRATE, 'src'))] + [os.path.join(CRATE, 'Cargo.toml')]
        srcs += [os.path.join(HERE, '..', 'near', 'contract', 'src', f)
                 for f in ('lsag.rs', 'fp.rs', 'poseidon.rs')]
        return any(os.path.getmtime(s) > t for s in srcs if os.path.exists(s))

    def build(self):
        """cargo contract build --release. TMPDIR=networks/substrate so the
        manifest's relative [patch] path resolves from cargo-contract's temp
        copy too (see contract/Cargo.toml)."""
        env = dict(os.environ, TMPDIR=HERE)
        out = subprocess.run(['cargo', f'+{TOOLCHAIN}', 'contract', 'build',
                              '--release'], cwd=CRATE, capture_output=True,
                             text=True, timeout=1800, env=env)
        log = re.sub(r'\x1b\[[0-9;]*m', '', out.stdout + out.stderr)
        if out.returncode != 0 or not os.path.exists(WASM):
            return {'ok': False, 'stage': 'cargo contract', 'log': log[-1500:]}
        self._sel = None
        return {'ok': True, 'wasm': WASM, 'bytes': os.path.getsize(WASM),
                'log': log[-600:]}

    def test(self):
        out = subprocess.run(['cargo', 'test', '--release', '--lib'], cwd=CRATE,
                             capture_output=True, text=True, timeout=1800)
        tests = re.findall(r'^test tests::(\w+) \.\.\. (ok|FAILED)',
                           out.stdout, re.M)
        return {'ok': out.returncode == 0,
                'passed': sum(1 for _, s in tests if s == 'ok'),
                'failed': sum(1 for _, s in tests if s != 'ok'),
                'tests': [['PASS' if s == 'ok' else 'FAIL', n] for n, s in tests],
                'log': (out.stdout + out.stderr).strip()[-3000:]}

    # ---- deploys -----------------------------------------------------------

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
            raise SubstrateError(f'not deployed on {net}; run deploy network={net}')
        return d

    def sel(self):
        if self._sel is None:
            self._sel = selectors()
        return self._sel

    # ---- calls -------------------------------------------------------------

    def dry_run(self, net, data, origin=None, dest=None):
        a = self.api(net)
        origin = origin or self.keypair(net, 'relayer').public_key
        dest = dest or account_bytes(self.dep(net)['address'])
        # ContractsApi_call(origin, dest, value, gas_limit: Option<Weight>,
        #                   storage_deposit_limit: Option<Balance>, input)
        params = (bytes(origin) + bytes(dest) + u(0, 128) + b'\0' + b'\0'
                  + compact(len(data)) + data)
        raw = a.rpc_request('state_call', ['ContractsApi_call', '0x' + params.hex()])
        return decode_dry_run(bytes.fromhex(raw['result'][2:]))

    def encode(self, name, args=b''):
        return self.sel()[name] + args

    def view(self, net, name, args=b''):
        r = self.dry_run(net, self.encode(name, args))
        if 'error' in r:
            raise SubstrateError(f'{name}: {r["error"]} {r["debug"]}'.strip())
        return decode_return(name, r['data'])

    def call(self, net, name, args=b'', who='relayer'):
        """Dry-run (value, revert, weight) then the extrinsic. Returns the
        value + what the chain charged: ref_time (ps), proof_size (bytes),
        and the fee actually paid."""
        k = self.keypair(net, who)
        data = self.encode(name, args)
        r = self.dry_run(net, data, origin=k.public_key)
        if 'error' in r:
            raise SubstrateError(f'{name}: {r["error"]} {r["debug"]}'.strip())
        value = decode_return(name, r['data'])  # raises on our Error
        if r['flags'] & REVERT:
            raise SubstrateError(f'{name}: reverted')
        a = self.api(net)
        g = r['gas_required']
        call = a.compose_call('Contracts', 'call', {
            'dest': {'Id': self.dep(net)['address']}, 'value': 0,
            'gas_limit': {'ref_time': int(g['ref_time'] * self.GAS_MARGIN),
                          'proof_size': int(g['proof_size'] * self.GAS_MARGIN)},
            'storage_deposit_limit': None, 'data': '0x' + data.hex()})
        rc = self._submit(net, call, k)
        return {'result': value, 'tx': rc['tx'], 'block': rc['block'],
                'ref_time': r['gas_consumed']['ref_time'],
                'proof_size': r['gas_consumed']['proof_size'],
                'weight': rc['weight'], 'fee': rc['fee'],
                'bytes': len(data)}

    def _submit(self, net, call, k):
        a = self.api(net)
        ex = a.create_signed_extrinsic(call=call, keypair=k)
        rc = a.submit_extrinsic(ex, wait_for_inclusion=True)
        if not rc.is_success:
            msg = rc.error_message
            raise SubstrateError(f'extrinsic failed: {msg}')
        fee = None
        for e in rc.triggered_events:
            ev = e.value.get('event', {}) if hasattr(e, 'value') else {}
            if ev.get('event_id') == 'TransactionFeePaid':
                fee = int(ev['attributes']['actual_fee'])
        w = rc.weight or {}
        return {'tx': rc.extrinsic_hash, 'block': rc.block_hash,
                'weight': {'ref_time': int(w.get('ref_time', 0)),
                           'proof_size': int(w.get('proof_size', 0))},
                'fee': fee, 'events': rc.triggered_events}

    # ---- deploy ------------------------------------------------------------

    def deploy(self, net, tempo, min_ring, emission):
        if self._stale():
            b = self.build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log']}
        if NETS[net]['local']:
            st = self.node('start')
            if not st.get('up'):
                return {'ok': False, 'error': st.get('error')}
        a, owner = self.api(net), self.keypair(net, 'owner')
        code = open(WASM, 'rb').read()
        data = self.sel()['@new'] + u(tempo, 64) + u(min_ring, 32) + u(emission, 128)
        call = a.compose_call('Contracts', 'instantiate_with_code', {
            'value': 0,
            'gas_limit': {'ref_time': 200_000_000_000, 'proof_size': 2_000_000},
            'storage_deposit_limit': None, 'code': '0x' + code.hex(),
            'data': '0x' + data.hex(), 'salt': '0x' + os.urandom(8).hex()})
        rc = self._submit(net, call, owner)
        addr = None
        for e in rc['events']:
            ev = e.value.get('event', {})
            if ev.get('module_id') == 'Contracts' and ev.get('event_id') == 'Instantiated':
                attrs = ev['attributes']
                addr = attrs['contract'] if isinstance(attrs, dict) else attrs[1]
        if not addr:
            return {'ok': False, 'error': 'no Contracts.Instantiated event'}
        rel = self.keypair(net, 'relayer')
        pay = self.keypair(net, 'payee') if NETS[net]['local'] else rel
        d = self._deploys()
        d[net] = {'address': addr, 'tempo': int(tempo), 'min_ring': int(min_ring),
                  'emission': int(emission), 'owner': owner.ss58_address,
                  'relayer': rel.ss58_address, 'payee': pay.ss58_address,
                  'code_hash': '0x' + hashlib.blake2b(code, digest_size=32).hexdigest(),
                  'wasm_bytes': len(code), 'at': int(time.time()),
                  'ref_time': rc['weight']['ref_time'], 'fee': rc['fee']}
        self._save_deploys(d)
        return {'ok': True, 'network': net, **d[net]}

    # ---- reads ---------------------------------------------------------------

    def members(self, net, role):
        n = self.view(net, 'count', u(role, 8))
        out = []
        while len(out) < n:
            page = self.view(net, 'members', u(role, 8) + u(len(out), 32) + u(128, 32))
            if not page:
                break
            out += page
        return out

    def tally(self, net, epoch=None):
        e = int(epoch) if epoch is not None else self.view(net, 'epoch')
        return self.view(net, 'tally', u(e, 64))

    def board(self, net, epochs=6):
        d = self._deploys().get(net)
        out = {'network': net, 'chain': 'substrate', 'deployed': bool(d),
               'devnet_up': self._alive() if NETS[net]['local'] else None}
        if not d or (NETS[net]['local'] and not out['devnet_up']):
            return out
        p, b = self.view(net, 'params'), self.block(net)
        e = b // p['tempo']
        out.update(contract=d['address'], owner=p['owner'], tempo=p['tempo'],
                   min_ring=p['min_ring'], emission=p['emission'], open=p['open'],
                   subnet=p['subnet'], block=b, epoch=e,
                   blocks_left=p['tempo'] - b % p['tempo'],
                   miners=self.members(net, 1), validators=self.members(net, 2))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = self.tally(net, ep)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out
