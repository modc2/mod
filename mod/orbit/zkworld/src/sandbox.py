"""sandbox.py — a local Starknet devnet every contract's app runs against.

Why a devnet and not a simulation: the per-contract app (/zkworld/<name>/) should
drive the real compiled contract, with real storage, events, caller addresses
and panics. starknet-devnet (0.2.4, RPC 0.7 — the version sncast 0.31 speaks)
gives that in-process-cheap: declare/deploy/invoke take well under a second.

Who does what:
  - starknet-devnet  the chain. One process on DEVNET_PORT, detached so it
                     outlives server restarts; state dumped to .sandbox/ every
                     block, so a devnet restart reloads it.
  - sncast           anything that needs a signature (declare, deploy,
                     invoke) — signing is the one thing not worth
                     reimplementing in stdlib Python.
  - JSON-RPC direct  reads (starknet_call), receipts and events: no process
                     spawn, and clean revert reasons.
  - this file        the ABI codec: typed UI values <-> felts, both ways,
                     driven by the contract class's own ABI. Plus the
                     Keccak-256 that selectors need (hashlib's sha3 is
                     NIST SHA-3, a different padding).

Sandbox accounts are devnet's seed-0 predeployed accounts, named alice / bob /
carol / dave. Their keys are public test keys; nothing here touches a real
network.
"""

import glob
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

SRC = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(SRC, '.sandbox')
DUMP = os.path.join(STATE, 'devnet.json')
ACCOUNTS_FILE = os.path.join(STATE, 'accounts.json')
DEPLOYS = os.path.join(STATE, 'deployments.json')
LOG = os.path.join(STATE, 'devnet.log')
BIN = os.path.expanduser('~/.local/bin')
DEVNET_PORT = int(os.environ.get('ZKWORLD_DEVNET_PORT', 51351))
RPC = f'http://127.0.0.1:{DEVNET_PORT}'
NAMES = ['alice', 'bob', 'carol', 'dave']
FEE_TOKEN = 'eth'
P = 2 ** 251 + 17 * 2 ** 192 + 1  # the Stark field prime
# sncast picks the accounts-file section from the chain id (SN_SEPOLIA)
SNCAST_NETWORK = 'alpha-sepolia'

# invokes from one account must not race for the same nonce; deploys write
# deployments.json. One lock is plenty for a sandbox.
TX_LOCK = threading.Lock()
START_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Keccak-256 (Ethereum/Starknet flavour) — selectors are sn_keccak(name)
# ---------------------------------------------------------------------------

_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
       0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
       0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
       0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
       0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
       0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccak_f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y])
              for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b'\x01'
    msg += b'\x00' * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            a[x][y] ^= int.from_bytes(block[8 * i:8 * i + 8], 'little')
        a = _keccak_f(a)
    out = b''.join(a[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))
    return out


def selector(name: str) -> str:
    v = int.from_bytes(keccak256(name.encode()), 'big') & ((1 << 250) - 1)
    return hex(v)


# ---------------------------------------------------------------------------
# ABI codec: typed values <-> felts
# ---------------------------------------------------------------------------

ONE_FELT = re.compile(
    r'^core::(felt252|bool|integer::(u|i)(8|16|32|64|128)|integer::usize|'
    r'starknet::contract_address::ContractAddress|'
    r'starknet::class_hash::ClassHash|'
    r'starknet::eth_address::EthAddress|bytes_31::bytes31|'
    r'starknet::storage_access::StorageAddress)$')
U256 = 'core::integer::u256'
BYTEARRAY = 'core::byte_array::ByteArray'
ADDRESS = 'core::starknet::contract_address::ContractAddress'


def _generic(ty):
    """'core::array::Span::<core::felt252>' -> ('core::array::Span', 'core::felt252')."""
    m = re.match(r'^([\w:]+)::<(.*)>$', ty)
    return (m.group(1), m.group(2)) if m else (ty, None)


def _split_top(s):
    """Split a comma list at depth 0 (types inside tuples / generics)."""
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '<(':
            depth += 1
        elif ch in '>)':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _short_text(v):
    """felt -> 'text' when it is a printable Cairo short string."""
    if v <= 0 or v.bit_length() > 248:
        return None
    b = v.to_bytes((v.bit_length() + 7) // 8, 'big')
    if all(32 <= c < 127 for c in b):
        return b.decode()
    return None


class Codec:
    def __init__(self, abi, aliases=None):
        self.types = {}
        for item in abi:
            if item.get('type') in ('struct', 'enum'):
                self.types[item['name']] = item
        self.aliases = {k.lower(): v for k, v in (aliases or {}).items()}
        self.names = {int(v, 16): k for k, v in (aliases or {}).items()}

    # ---- encode -------------------------------------------------------------

    def _felt(self, v, ty):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            n = v
        else:
            s = str(v).strip()
            if s.lower() in self.aliases:
                return int(self.aliases[s.lower()], 16)
            if re.fullmatch(r'-?0x[0-9a-fA-F]+', s):
                n = int(s, 16)
            elif re.fullmatch(r'-?\d+', s):
                n = int(s)
            elif ty.endswith(('felt252', 'bytes31')) and s:
                txt = s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in '\'"' else s
                if len(txt.encode()) > 31:
                    raise ValueError(f'short string over 31 bytes: {txt!r}')
                n = int.from_bytes(txt.encode(), 'big')
            elif s == '':
                raise ValueError(f'missing value for {ty.split("::")[-1]}')
            else:
                raise ValueError(f'{s!r} is not a number for {ty.split("::")[-1]}')
        if n < 0:
            n += P
        if not 0 <= n < P:
            raise ValueError(f'{v} is out of felt range')
        return n

    @staticmethod
    def _list(v):
        if isinstance(v, (list, tuple)):
            return list(v)
        s = str(v).strip()
        if not s:
            return []
        if s[0] in '[{':
            return json.loads(s)
        return [x for x in re.split(r'[\s,]+', s) if x]

    def encode(self, ty, v):
        if ty == 'core::bool':
            if isinstance(v, str):
                v = v.strip().lower() in ('true', '1', 'yes')
            return [1 if v else 0]
        if ONE_FELT.match(ty):
            n = self._felt(v, ty)
            m = re.match(r'core::integer::u(\d+)$', ty)
            if m and n >= 1 << int(m.group(1)):
                raise ValueError(f'{v} does not fit in a u{m.group(1)}')
            return [n]
        if ty == U256:
            # parsed here, not via _felt: a u256 may exceed the felt range
            s = str(v).strip().replace('_', '')
            try:
                n = int(s, 16) if s.lower().startswith('0x') else int(s)
            except ValueError:
                raise ValueError(f'{s!r} is not a u256') from None
            if not 0 <= n < 1 << 256:
                raise ValueError(f'{s} is out of u256 range')
            return [n & ((1 << 128) - 1), n >> 128]
        if ty == BYTEARRAY:
            b = str(v).encode()
            words = [b[i:i + 31] for i in range(0, len(b), 31)]
            pending = words.pop() if words and len(words[-1]) < 31 else b''
            return ([len(words)] + [int.from_bytes(w, 'big') for w in words]
                    + [int.from_bytes(pending, 'big') if pending else 0,
                       len(pending)])
        base, inner = _generic(ty)
        if base in ('core::array::Span', 'core::array::Array'):
            items = self._list(v)
            out = [len(items)]
            for x in items:
                out += self.encode(inner, x)
            return out
        if ty.startswith('(') and ty.endswith(')'):
            parts = _split_top(ty[1:-1])
            items = self._list(v)
            if len(items) != len(parts):
                raise ValueError(f'expected {len(parts)} values for {ty}')
            return [f for t, x in zip(parts, items) for f in self.encode(t, x)]
        t = self.types.get(ty)
        if t and t['type'] == 'struct':
            members = t['members']
            if isinstance(v, str):
                v = json.loads(v) if v.strip()[:1] in '[{' else self._list(v)
            if isinstance(v, dict):
                v = [v.get(m['name']) for m in members]
            return [f for m, x in zip(members, v)
                    for f in self.encode(m['type'], x)]
        if t and t['type'] == 'enum':
            if isinstance(v, str):
                s = v.strip()
                v = json.loads(s) if s[:1] == '{' else {s: None}
            (vname, val), = v.items()
            for i, var in enumerate(t['variants']):
                if var['name'] == vname:
                    return [i] + ([] if var['type'] == '()'
                                  else self.encode(var['type'], val))
            raise ValueError(f'{vname!r} is not a variant of {ty}')
        raise ValueError(f'cannot encode type {ty}')

    def encode_inputs(self, inputs, args):
        """inputs: ABI [{name,type}]; args: dict by name or positional list."""
        args = args if args is not None else {}
        if isinstance(args, str):
            args = json.loads(args) if args.strip() else {}
        out = []
        for i, inp in enumerate(inputs):
            v = args.get(inp['name']) if isinstance(args, dict) else \
                (args[i] if i < len(args) else None)
            if v is None:
                raise ValueError(f"missing argument {inp['name']!r}")
            try:
                out += self.encode(inp['type'], v)
            except ValueError as e:
                raise ValueError(f"{inp['name']}: {e}") from None
        return out

    # ---- decode -------------------------------------------------------------

    def decode(self, ty, felts, i=0):
        """-> (value, next_index). Values are JSON-friendly display values."""
        if ty == 'core::bool':
            return felts[i] != 0, i + 1
        if ty == ADDRESS or ty.endswith(('ClassHash', 'EthAddress')):
            v = felts[i]
            name = self.names.get(v)
            return (f'{hex(v)} ({name})' if name else hex(v)), i + 1
        if ty.startswith('core::integer::i'):
            v = felts[i]
            return str(v - P if v > P // 2 else v), i + 1
        if ONE_FELT.match(ty):
            v = felts[i]
            if ty.endswith(('felt252', 'bytes31')):
                txt = _short_text(v)
                shown = str(v) if v < 10 ** 15 else hex(v)
                return (f"{shown} '{txt}'" if txt and v > 255 else shown), i + 1
            return str(v), i + 1
        if ty == U256:
            return str(felts[i] + (felts[i + 1] << 128)), i + 2
        if ty == BYTEARRAY:
            n = felts[i]
            words = felts[i + 1:i + 1 + n]
            pend, plen = felts[i + 1 + n], felts[i + 2 + n]
            b = b''.join(w.to_bytes(31, 'big') for w in words)
            b += pend.to_bytes(plen, 'big') if plen else b''
            return b.decode('utf-8', 'replace'), i + 3 + n
        base, inner = _generic(ty)
        if base in ('core::array::Span', 'core::array::Array'):
            n, i = felts[i], i + 1
            out = []
            for _ in range(n):
                x, i = self.decode(inner, felts, i)
                out.append(x)
            return out, i
        if ty.startswith('(') and ty.endswith(')'):
            out = []
            for t in _split_top(ty[1:-1]):
                x, i = self.decode(t, felts, i)
                out.append(x)
            return out, i
        t = self.types.get(ty)
        if t and t['type'] == 'struct':
            out = {}
            for m in t['members']:
                out[m['name']], i = self.decode(m['type'], felts, i)
            return out, i
        if t and t['type'] == 'enum':
            var = t['variants'][felts[i]]
            if var['type'] == '()':
                return var['name'], i + 1
            val, i = self.decode(var['type'], felts, i + 1)
            return {var['name']: val}, i
        return hex(felts[i]), i + 1  # unknown: show the raw felt

    def decode_outputs(self, outputs, felts):
        felts = [int(f, 16) if isinstance(f, str) else f for f in felts]
        out, i = [], 0
        for o in outputs:
            try:
                v, i = self.decode(o['type'], felts, i)
            except (IndexError, ValueError):
                return [hex(f) for f in felts[i:]]
            out.append(v)
        return out


def event_index(abi):
    """selector(variant) -> event struct, for decoding receipts and logs."""
    idx = {}
    for item in abi:
        if item.get('type') == 'event' and item.get('kind') == 'struct':
            short = item['name'].split('::')[-1]
            idx[int(selector(short), 16)] = item
    return idx


def decode_event(codec, idx, ev):
    keys = [int(k, 16) for k in ev.get('keys', [])]
    data = [int(d, 16) for d in ev.get('data', [])]
    item = idx.get(keys[0]) if keys else None
    if not item:
        return {'event': None, 'keys': ev.get('keys'), 'data': ev.get('data')}
    fields, ki, di = {}, 1, 0
    for m in item.get('members', []):
        if m.get('kind') == 'key':
            fields[m['name']], ki = codec.decode(m['type'], keys, ki)
        else:
            fields[m['name']], di = codec.decode(m['type'], data, di)
    return {'event': item['name'].split('::')[-1], 'fields': fields,
            'block': ev.get('block_number'), 'tx': ev.get('transaction_hash')}


# ---------------------------------------------------------------------------
# the devnet process
# ---------------------------------------------------------------------------

def _rpc(method, params, timeout=15):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params}).encode()
    req = urllib.request.Request(RPC + '/rpc', body,
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if 'error' in out:
        err = out['error']
        raise RuntimeError(_reason(json.dumps(err.get('data') or err)) or
                           err.get('message', str(err)))
    return out['result']


def _get(path, timeout=3):
    with urllib.request.urlopen(RPC + path, timeout=timeout) as r:
        return json.loads(r.read() or b'null')


def alive():
    try:
        urllib.request.urlopen(RPC + '/is_alive', timeout=1).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def _reason(text):
    """Pull the human panic reason out of a starknet execution error."""
    text = text or ''
    m = re.findall(r"Failure reason: (?:0x[0-9a-f]+ )?\(?'([^']*)'\)?", text)
    if m:
        return m[-1]
    m = re.search(r'Entry point EntryPointSelector\((0x[0-9a-f]+)\) not found',
                  text)
    if m:
        return 'no such function on this contract'
    m = re.search(r'Failure reason: (0x[0-9a-f]+)', text)
    if m:
        return _short_text(int(m.group(1), 16)) or m.group(1)
    m = re.search(r'execution_error: \\?"([^"\\]+)', text)
    return m.group(1).strip() if m else None


def start():
    """Start devnet if it is not up. Detached (own session) so a zkworld server
    restart doesn't take the chain with it; reloads the last dump."""
    with START_LOCK:
        if alive():
            return {'up': True, 'started': False}
        os.makedirs(STATE, exist_ok=True)
        exe = os.path.join(BIN, 'starknet-devnet')
        if not os.path.exists(exe):
            return {'up': False, 'error': 'starknet-devnet not installed in '
                    '~/.local/bin (see README: sandbox)'}
        cmd = [exe, '--host', '127.0.0.1', '--port', str(DEVNET_PORT),
               '--seed', '0', '--accounts', str(len(NAMES)),
               '--dump-on', 'block', '--dump-path', DUMP]
        log = open(LOG, 'a')
        subprocess.Popen(cmd, stdout=log, stderr=log, cwd=STATE,
                         start_new_session=True)
        for _ in range(100):
            if alive():
                break
            time.sleep(0.1)
        else:
            return {'up': False, 'error': f'devnet did not come up; see {LOG}'}
        _write_accounts()
        return {'up': True, 'started': True}


def stop():
    out = subprocess.run(['fuser', '-k', f'{DEVNET_PORT}/tcp'],
                         capture_output=True, text=True)
    return {'stopped': out.returncode == 0}


def reset():
    """Throw the chain away: stop, drop the dump and every deployment."""
    stop()
    time.sleep(0.3)
    for f in (DUMP, DEPLOYS):
        if os.path.exists(f):
            os.remove(f)
    return {'reset': True, **start()}


def accounts():
    try:
        rows = _get('/predeployed_accounts')
    except Exception:  # noqa: BLE001
        return []
    return [{'name': NAMES[i] if i < len(NAMES) else f'acct{i}',
             'address': r['address'], 'private_key': r['private_key'],
             'public_key': r['public_key']} for i, r in enumerate(rows)]


def aliases():
    return {a['name']: a['address'] for a in accounts()}


def _write_accounts():
    accts = accounts()
    if not accts:
        return
    cls = _rpc('starknet_getClassHashAt', ['latest', accts[0]['address']])
    book = {a['name']: {'address': a['address'], 'class_hash': cls,
                        'deployed': True, 'legacy': False,
                        'private_key': a['private_key'],
                        'public_key': a['public_key'],
                        'type': 'open_zeppelin'} for a in accts}
    with open(ACCOUNTS_FILE, 'w') as f:
        json.dump({SNCAST_NETWORK: book}, f, indent=2)


def ensure():
    s = start()
    if not s.get('up'):
        raise RuntimeError(s.get('error', 'devnet is down'))
    if not os.path.exists(ACCOUNTS_FILE):
        _write_accounts()


def _sncast(args, cwd, account='alice', timeout=300):
    exe = os.path.join(BIN, 'sncast')
    env = dict(os.environ)
    env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
    cmd = [exe, '--accounts-file', ACCOUNTS_FILE, '--account', account,
           '--json', *args, '--url', RPC + '/rpc']
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                         timeout=timeout, env=env)
    # sncast --json prints scarb's build chatter first; the answer is the
    # last JSON line carrying a "command" key
    for line in reversed((out.stdout + '\n' + out.stderr).splitlines()):
        line = line.strip()
        if line.startswith('{') and '"command"' in line:
            return json.loads(line)
    tail = (out.stderr or out.stdout).strip().splitlines()[-3:]
    return {'error': '\n'.join(tail) or f'sncast exited {out.returncode}'}


# ---------------------------------------------------------------------------
# deployments
# ---------------------------------------------------------------------------

def deployments():
    try:
        return json.load(open(DEPLOYS))
    except Exception:  # noqa: BLE001
        return {}


def _save_deployments(d):
    os.makedirs(STATE, exist_ok=True)
    tmp = DEPLOYS + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, DEPLOYS)


def deployment(pkg):
    """The live deployment of a contract, or None. Checked against the chain:
    a devnet reset leaves stale rows behind."""
    row = deployments().get(pkg)
    if not row or not alive():
        return row and {**row, 'live': False}
    try:
        _rpc('starknet_getClassHashAt', ['latest', row['address']])
        return {**row, 'live': True}
    except Exception:  # noqa: BLE001
        return {**row, 'live': False}


def class_abi(d, contract=None):
    """(contract_name, full ABI) of a built contract class in target/dev."""
    pkg = os.path.basename(d)
    arts = [p for p in sorted(glob.glob(os.path.join(
        d, 'target', 'dev', '*.contract_class.json')))
        if '.test.' not in p and not p.endswith('.compiled_contract_class.json')]
    for p in arts:
        base = os.path.basename(p)
        name = base[len(pkg) + 1:].split('.')[0] \
            if base.startswith(pkg + '_') else base.split('.')[0]
        if contract in (None, name):
            return name, json.load(open(p)).get('abi', [])
    raise RuntimeError('not built yet' if not arts else
                       f'no contract {contract!r} in this package')


def deploy(pkg, d, args=None, contract=None, account='alice'):
    ensure()
    contract, abi = class_abi(d, contract)
    codec = Codec(abi, aliases())
    ctor = next((i for i in abi if i.get('type') == 'constructor'), None)
    calldata = codec.encode_inputs(ctor['inputs'], args) if ctor else []
    with TX_LOCK:
        dec = _sncast(['declare', '--contract-name', contract,
                       '--fee-token', FEE_TOKEN], d, account)
        if 'error' in dec:
            m = re.search(r'Class with hash (0x[0-9a-fA-F]+) is already declared',
                          dec['error'])
            if not m:
                return {'ok': False, 'stage': 'declare',
                        'error': _reason(dec['error']) or dec['error'][-600:]}
            class_hash = hex(int(m.group(1), 16))
        else:
            class_hash = dec['class_hash']
        dep_args = ['deploy', '--class-hash', class_hash,
                    '--fee-token', FEE_TOKEN]
        if calldata:
            dep_args += ['--constructor-calldata', *map(hex, calldata)]
        dep = _sncast(dep_args, d, account)
        if 'error' in dep:
            return {'ok': False, 'stage': 'deploy',
                    'error': _reason(dep['error']) or dep['error'][-600:]}
        all_d = deployments()
        all_d[pkg] = {'contract': contract, 'address': dep['contract_address'],
                      'class_hash': class_hash, 'tx': dep['transaction_hash'],
                      'args': args or {}, 'by': account, 'at': time.time()}
        _save_deployments(all_d)
    return {'ok': True, **all_d[pkg], 'live': True}


def _find_fn(abi, fn):
    for item in abi:
        if item.get('type') == 'interface':
            for f in item.get('items', []):
                if f['name'] == fn:
                    return f
        elif item.get('type') == 'function' and item['name'] == fn:
            return item
    return None


def call(pkg, d, fn, args=None, account='alice'):
    """Run one entrypoint: a view goes straight to starknet_call; anything
    external is signed by `account` through sncast and its receipt events
    are decoded back."""
    ensure()
    row = deployment(pkg)
    if not row or not row.get('live'):
        return {'ok': False, 'error': 'not deployed on the sandbox yet'}
    _, abi = class_abi(d, row.get('contract'))
    f = _find_fn(abi, fn)
    if not f:
        return {'ok': False, 'error': f'no function {fn!r}'}
    codec = Codec(abi, aliases())
    try:
        calldata = codec.encode_inputs(f.get('inputs', []), args)
    except (ValueError, json.JSONDecodeError) as e:
        return {'ok': False, 'error': str(e)}
    addr = row['address']
    if f.get('state_mutability') == 'view':
        try:
            res = _rpc('starknet_call', [{
                'contract_address': addr, 'entry_point_selector': selector(fn),
                'calldata': [hex(x) for x in calldata]}, 'latest'])
        except RuntimeError as e:
            return {'ok': False, 'error': str(e)}
        return {'ok': True, 'kind': 'view', 'fn': fn, 'raw': res,
                'result': codec.decode_outputs(f.get('outputs', []), res)}
    if account not in NAMES:
        return {'ok': False, 'error': f'unknown sandbox account {account!r}'}
    with TX_LOCK:
        inv = _sncast(['invoke', '--contract-address', addr, '--function', fn,
                       '--fee-token', FEE_TOKEN,
                       *(['--calldata', *map(hex, calldata)] if calldata else [])],
                      d, account)
    if 'error' in inv:
        return {'ok': False, 'kind': 'invoke', 'fn': fn,
                'error': _reason(inv['error']) or inv['error'][-600:]}
    tx = inv['transaction_hash']
    events = []
    try:
        rcpt = _rpc('starknet_getTransactionReceipt', [tx])
        if rcpt.get('execution_status') == 'REVERTED':
            return {'ok': False, 'kind': 'invoke', 'fn': fn, 'tx': tx,
                    'error': _reason(rcpt.get('revert_reason')) or
                    rcpt.get('revert_reason')}
        idx = event_index(abi)
        events = [decode_event(codec, idx, e) for e in rcpt.get('events', [])
                  if int(e.get('from_address', '0x0'), 16) == int(addr, 16)]
    except Exception:  # noqa: BLE001  (receipt is a nicety)
        pass
    return {'ok': True, 'kind': 'invoke', 'fn': fn, 'tx': tx, 'by': account,
            'events': events}


def events(pkg, d, limit=50):
    row = deployment(pkg)
    if not row or not row.get('live'):
        return {'events': []}
    _, abi = class_abi(d, row.get('contract'))
    codec, idx = Codec(abi, aliases()), event_index(abi)
    got, token = [], None
    for _ in range(20):  # page through; sandbox chains are short
        flt = {'address': row['address'], 'from_block': {'block_number': 0},
               'to_block': 'latest', 'chunk_size': 200}
        if token:
            flt['continuation_token'] = token
        res = _rpc('starknet_getEvents', [flt])
        got += res.get('events', [])
        token = res.get('continuation_token')
        if not token:
            break
    rows = [decode_event(codec, idx, e) for e in got[-int(limit):]]
    return {'events': list(reversed(rows))}
