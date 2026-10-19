"""
proofs.py — proof of rent paid, without the tenant.

The bank is the one party that sees a rent payment whole: who paid, from
which account, how much. Everyone else — the landlord, a co-owner, a city,
the chain — only needs to know THAT the rent for property P in period T was
paid. This file is the off-chain half of making that true:

    program   proof/ (Cairo)  the statement: commitment = H(tenant, salt),
                              nullifier = H(commitment, property, period),
                              amount_paid >= rent_due. Private: tenant, salt,
                              amount. Public: version, property, period,
                              rent_due, commitment, nullifier.
    fact      keccak256(program_hash ‖ keccak256(outputs))  — the SHARP
              convention, so a STARK fact registry can carry the same fact
              later without changing a byte on-chain.
    receipt   outputs + the bank's signatures over the fact — ed25519 for
              NEAR, secp256k1 for Ethereum, and the post-quantum pair from
              pq.py (OHX-KECCAK hash-based + ML-DSA-65) that a quantum
              computer cannot forge. The receipt carries nothing about the
              tenant; the salt stays with the bank and the tenant.
    verify    anyone, offline: recompute the fact, check every signature,
              say whether the receipt is quantum-safe.
    open      the tenant hands (tenant, salt) to whoever needs convincing;
              the commitment either matches or it does not.
    submit    relay the receipt to near/ (wasm) or contracts/OpenHouseProofs.sol,
              through the classical door or the post-quantum one (door=pq);
              the chain re-checks the signature, pins the program hash and the
              property's rent due, and burns the nullifier so one proof books
              one month once.

No openhouse imports — liftable into any module. Hashing is keccak via
eth_hash (falls back to pycryptodome); ed25519 via PyNaCl; secp256k1 via
eth_account. The Cairo program runs through `scarb cairo-run`, the same way
zuma/zquorum run their provers, so signer and verifier never drift.
"""
import hashlib
import importlib.util
import json
import os
import re
import secrets as _secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

VERSION = 1
N_OUTPUTS = 6
FIELD_P = 2 ** 251 + 17 * 2 ** 192 + 1
UNIT = 10 ** 6                     # amounts are integers: deal currency × 1e6
def _real_home() -> str:
    """The account's home even when a test has pointed HOME elsewhere."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return os.path.expanduser('~')


BIN = Path(_real_home()) / '.local' / 'bin'
HERE = Path(__file__).parent
PROGRAM_DIR = HERE / 'proof'
SIERRA = PROGRAM_DIR / 'target' / 'dev' / 'openhouse_proof.sierra.json'
OUTPUT_NAMES = ('version', 'property_id', 'period', 'rent_due', 'commitment', 'nullifier')
_LOCK = threading.Lock()


def _pq():
    """pq.py, the sibling file, loaded by path — this file stays liftable."""
    name = 'openhouse_pq'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / 'pq.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


POLICIES = ('auto', 'classical', 'hybrid', 'pq')


class ProofError(Exception):
    """A refusal with a reason — surfaced as {'error': ...}, never a 500."""


# ── hashing ─────────────────────────────────────────────────────

def keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak as _k
        return bytes(_k(data))
    except Exception:
        from Crypto.Hash import keccak as _kk
        return _kk.new(digest_bits=256, data=data).digest()


def hex32(n: int) -> str:
    return '0x' + int(n).to_bytes(32, 'big').hex()


def word(n: int) -> bytes:
    return int(n).to_bytes(32, 'big')


def felt(x) -> int:
    """Parse a field element from an int, '0x..' hex or a decimal string."""
    if isinstance(x, bool):
        raise ProofError('a boolean is not a felt')
    if isinstance(x, int):
        v = x
    elif isinstance(x, (bytes, bytearray)):
        v = int.from_bytes(x, 'big')
    else:
        s = str(x).strip().lower()
        if not s:
            raise ProofError('empty felt')
        v = int(s, 16) if s.startswith('0x') else int(s)
    if v < 0 or v >= FIELD_P:
        raise ProofError(f'{x!r} is not in the field')
    return v


def tenant_felt(who) -> int:
    """Any identity → one felt. A 0x address is taken as its number (fits
    the field); anything else (a NEAR account, an email, an IBAN) is
    sha256'd and truncated to 251 bits so it fits too."""
    s = str(who).strip()
    if not s:
        raise ProofError('tenant required')
    if re.fullmatch(r'0x[0-9a-fA-F]{1,40}', s):
        return int(s, 16)
    return int.from_bytes(hashlib.sha256(s.lower().encode()).digest(), 'big') >> 5


def units(amount) -> int:
    """Deal-currency amount → integer units (× 1e6)."""
    return int(round(float(amount) * UNIT))


def period_of(ts: int) -> int:
    """Unix seconds → YYYYMM (UTC)."""
    t = time.gmtime(int(time.time() if ts is None else ts))
    return t.tm_year * 100 + t.tm_mon


def outputs_hash(outputs) -> bytes:
    return keccak(b''.join(word(felt(o)) for o in outputs))


def fact(program_hash: str, outputs) -> str:
    """keccak256(program_hash ‖ keccak256(outputs)) — the SHARP fact shape."""
    ph = bytes.fromhex(str(program_hash)[2:] if str(program_hash).startswith('0x') else str(program_hash))
    if len(ph) != 32:
        raise ProofError('program_hash must be 32 bytes')
    return '0x' + keccak(ph + outputs_hash(outputs)).hex()


# ── the Cairo program ───────────────────────────────────────────

def scarb() -> Optional[str]:
    p = BIN / 'scarb'
    return str(p) if p.exists() else shutil.which('scarb')


def _env():
    env = dict(os.environ)
    env['PATH'] = str(BIN) + os.pathsep + env.get('PATH', '')
    return env


def build(timeout: int = 600) -> dict:
    exe = scarb()
    if not exe:
        return {'ok': False, 'error': 'scarb not installed (~/.local/bin/scarb)'}
    out = subprocess.run([exe, 'build'], cwd=PROGRAM_DIR, capture_output=True, text=True,
                         timeout=timeout, env=_env())
    return {'ok': out.returncode == 0 and SIERRA.exists(),
            'log': (out.stdout + out.stderr).strip()[-2000:]}


def _ensure_built():
    if SIERRA.exists():
        return
    exe = scarb()
    if not exe:
        raise ProofError('scarb not installed — cannot run the Cairo program')
    b = build()
    if not b['ok']:
        raise ProofError('proof program build failed: ' + b.get('log', b.get('error', ''))[-600:])


def run(args, timeout: int = 120) -> list:
    """Run one op of proof/src/lib.cairo through `scarb cairo-run` and
    return the `P <felt>` lines as ints."""
    _ensure_built()
    out = subprocess.run(
        [scarb(), 'cairo-run', '--no-build', '--available-gas', '400000000',
         json.dumps([[felt(a) for a in args]])],
        cwd=PROGRAM_DIR, capture_output=True, text=True, timeout=timeout, env=_env())
    text = out.stdout + out.stderr
    if out.returncode != 0 or 'Run panicked' in text:
        m = re.search(r"Run panicked with \[[^\]]*\('([^']*)'\)", text) or \
            re.search(r'Run panicked with (.*)', text)
        raise ProofError('program refused: ' + (m.group(1) if m else text.strip()[-400:]))
    return [int(x) for x in re.findall(r'^P (\d+)$', out.stdout, re.M)]


_PH_CACHE = {}


def program_hash() -> str:
    """keccak256 of the compiled Sierra program — pins WHICH statement a
    receipt is about. Changing one line of lib.cairo changes it; on-chain
    verifiers refuse facts for any other hash."""
    _ensure_built()
    mtime = SIERRA.stat().st_mtime
    hit = _PH_CACHE.get('h')
    if hit and hit[0] == mtime:
        return hit[1]
    with open(SIERRA) as f:
        sierra = json.load(f)
    prog = sierra.get('sierra_program') if isinstance(sierra, dict) else None
    blob = json.dumps(prog if prog is not None else sierra, separators=(',', ':')).encode()
    h = '0x' + keccak(blob).hex()
    _PH_CACHE['h'] = (mtime, h)
    return h


def program_info() -> dict:
    src = PROGRAM_DIR / 'src' / 'lib.cairo'
    info = {'path': str(PROGRAM_DIR.relative_to(HERE)), 'version': VERSION,
            'outputs': list(OUTPUT_NAMES), 'built': SIERRA.exists(),
            'scarb': bool(scarb()),
            'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest() if src.exists() else None,
            'fact': 'keccak256(program_hash || keccak256(outputs as 32-byte words))',
            'private_inputs': ['tenant', 'salt', 'amount_paid'],
            'public_outputs': list(OUTPUT_NAMES)}
    try:
        info['program_hash'] = program_hash()
    except ProofError as e:
        info['program_hash'] = None
        info['error'] = str(e)
    return info


# ── the issuer (a bank's two signing keys) ──────────────────────

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, 'big')
    s = ''
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    pad = len(raw) - len(raw.lstrip(b'\0'))
    return '1' * pad + s


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    pad = len(s) - len(s.lstrip('1'))
    return b'\0' * pad + raw


def _write_private(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Issuer:
    """The bank's signing identity: one ed25519 key (NEAR verifies these
    natively) and one secp256k1 key (Ethereum's ecrecover). Both sign the
    same 32-byte fact. Secrets live in secrets_dir/proof_issuer.json, 0600;
    nothing here ever returns them."""

    def __init__(self, secrets_dir, name: str = ''):
        self.path = Path(secrets_dir) / 'proof_issuer.json'
        if self.path.exists():
            with open(self.path) as f:
                d = json.load(f)
        else:
            d = {'ed25519_seed': _secrets.token_hex(32), 'eth_key': _secrets.token_hex(32),
                 'name': name or 'openhouse node', 'created': int(time.time())}
            _write_private(self.path, d)
        self._seed = bytes.fromhex(d['ed25519_seed'])
        self._eth_key = d['eth_key']
        self.name = d.get('name') or name or 'openhouse node'
        self._pq_signer = None

    def pq(self):
        """The post-quantum keys (pq.Signer), beside the classical ones."""
        if self._pq_signer is None:
            self._pq_signer = _pq().Signer(self.path.parent)
        return self._pq_signer

    # keys
    def near_pub_raw(self) -> bytes:
        from nacl.signing import SigningKey
        return bytes(SigningKey(self._seed).verify_key)

    def near_pub(self) -> str:
        return 'ed25519:' + b58encode(self.near_pub_raw())

    def eth_address(self) -> str:
        from eth_account import Account
        return Account.from_key(self._eth_key).address

    def eth_key(self) -> str:
        """Private — the relayer key for Ethereum submits; callers keep it off every surface."""
        return self._eth_key

    def public(self, pq: bool = True) -> dict:
        out = {'name': self.name, 'near': self.near_pub(),
               'near_hex': '0x' + self.near_pub_raw().hex(), 'eth': self.eth_address()}
        if pq:
            out['pq'] = self.pq().public()
        return out

    def sign_pq(self, fact_hex: str) -> dict:
        return self.pq().sign(fact_hex)

    def sign(self, fact_hex: str, pq: bool = True) -> dict:
        from nacl.signing import SigningKey
        from eth_account import Account
        from eth_account.messages import encode_defunct
        f = bytes.fromhex(fact_hex[2:])
        near_sig = SigningKey(self._seed).sign(f).signature
        eth_sig = Account.from_key(self._eth_key).sign_message(encode_defunct(primitive=f)).signature
        out = {'near': '0x' + bytes(near_sig).hex(), 'eth': '0x' + bytes(eth_sig).hex()}
        if pq:
            out['pq'] = self.sign_pq(fact_hex)
        return out


def verify_signatures(fact_hex: str, bank: dict, sig: dict) -> list:
    """Both signatures over the fact, each a {claim, ok} check."""
    checks = []
    f = bytes.fromhex(fact_hex[2:])
    # ed25519 (NEAR)
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
        pk = str(bank.get('near') or '')
        raw = b58decode(pk.split(':', 1)[1]) if pk.startswith('ed25519:') else bytes.fromhex(str(bank.get('near_hex', ''))[2:])
        s = bytes.fromhex(str(sig.get('near', ''))[2:])
        try:
            VerifyKey(raw).verify(f, s)
            ok = True
        except BadSignatureError:
            ok = False
        except Exception:
            ok = False
    except ImportError:
        ok = None
    checks.append({'claim': f'ed25519 signature by {bank.get("near", "?")} (NEAR)', 'ok': ok})
    # secp256k1 (Ethereum)
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        who = Account.recover_message(encode_defunct(primitive=f),
                                      signature=bytes.fromhex(str(sig.get('eth', ''))[2:]))
        ok = who.lower() == str(bank.get('eth', '')).lower()
    except ImportError:
        ok = None
    except Exception:
        ok = False
    checks.append({'claim': f'secp256k1 signature by {bank.get("eth", "?")} (Ethereum)', 'ok': ok})
    return checks


# ── receipts ────────────────────────────────────────────────────

def _receipt_from_outputs(ph: str, outs: list, bank: dict, sig: dict, kind: str = 'attested') -> dict:
    outs = [felt(o) for o in outs]
    if len(outs) != N_OUTPUTS:
        raise ProofError(f'expected {N_OUTPUTS} outputs, got {len(outs)}')
    return {
        'kind': kind, 'version': outs[0], 'program_hash': ph,
        'outputs': [hex32(o) for o in outs],
        'property_id': outs[1], 'period': outs[2], 'rent_due': outs[3],
        'commitment': hex32(outs[4]), 'nullifier': hex32(outs[5]),
        'fact': fact(ph, outs), 'bank': bank, 'sig': sig,
        'issued': int(time.time()), 'chain': {},
    }


def verify_receipt(r: dict, expect_program_hash: Optional[str] = None,
                   rent_due_floor: Optional[int] = None, policy: str = 'auto') -> dict:
    """Everything a stranger can check with no secrets: shape, version,
    the fact, every signature, and (if told) the program hash and the
    property's rent due.

    policy — which signatures must hold:
      classical  ed25519 + secp256k1 (any PQ signature present must hold too)
      hybrid     classical AND post-quantum
      pq         post-quantum only — classical signatures are reported but
                 ignored, because after Q-day they prove nothing
      auto       hybrid when the receipt carries PQ signatures, else classical"""
    if policy not in POLICIES:
        return {'ok': False, 'checks': [], 'error': f'policy must be one of {", ".join(POLICIES)}'}
    checks = []
    try:
        outs = [felt(o) for o in (r.get('outputs') or [])]
    except ProofError as e:
        return {'ok': False, 'checks': [{'claim': 'outputs are field elements', 'ok': False}], 'error': str(e)}
    checks.append({'claim': f'{N_OUTPUTS} public outputs', 'ok': len(outs) == N_OUTPUTS})
    if len(outs) != N_OUTPUTS:
        return {'ok': False, 'checks': checks}
    checks.append({'claim': f'statement version {VERSION}', 'ok': outs[0] == VERSION})
    checks.append({'claim': 'outputs match the headline fields',
                   'ok': outs[1] == int(r.get('property_id', -1)) and outs[2] == int(r.get('period', -1))
                   and outs[3] == int(r.get('rent_due', -1))
                   and hex32(outs[4]) == str(r.get('commitment', '')).lower()
                   and hex32(outs[5]) == str(r.get('nullifier', '')).lower()})
    ph = str(r.get('program_hash') or '')
    try:
        f = fact(ph, outs)
        checks.append({'claim': 'fact = keccak(program_hash ‖ keccak(outputs))', 'ok': f == str(r.get('fact', '')).lower()})
    except ProofError:
        f = None
        checks.append({'claim': 'fact = keccak(program_hash ‖ keccak(outputs))', 'ok': False})
    if expect_program_hash:
        checks.append({'claim': 'program hash is the one this node runs', 'ok': ph.lower() == expect_program_hash.lower()})
    if rent_due_floor is not None:
        checks.append({'claim': f'attested rent due covers the property\'s {rent_due_floor}', 'ok': outs[3] >= int(rent_due_floor)})
    bank, sig = r.get('bank') or {}, r.get('sig') or {}
    has_pq = bool(sig.get('pq'))
    if policy == 'auto':
        policy = 'hybrid' if has_pq else 'classical'
    classical = verify_signatures(f, bank, sig) if f else []
    for c in classical:
        c['classical'] = True
    pqr = _pq().check(f, bank.get('pq') or {}, sig.get('pq') or {}) if (f and has_pq) else {'safe': False, 'checks': []}
    if policy == 'pq':
        for c in classical:
            c['ignored'] = 'policy=pq — a quantum attacker can forge this'
    checks += classical + pqr['checks']
    if policy in ('hybrid', 'pq'):
        checks.append({'claim': 'at least one post-quantum signature holds', 'ok': pqr['safe'], 'pq': True})
    checks.append({'claim': 'nothing about the tenant in the receipt',
                   'ok': not any(k in r for k in ('tenant', 'salt', 'amount', 'amount_paid', 'iban', 'payer'))})
    counted = [c for c in checks if not c.get('ignored')]
    return {'ok': all(c['ok'] for c in counted), 'checks': checks, 'fact': f,
            'nullifier': hex32(outs[5]), 'kind': r.get('kind', 'attested'), 'policy': policy,
            'quantum': {'safe': pqr['safe'],
                        'says': ('quantum-safe: a hash-based or lattice signature vouches for this fact'
                                 if pqr['safe'] else
                                 'classical only: forgeable by a large quantum computer — '
                                 'ask the bank to countersign it (proof_pq_upgrade)')}}


# ── the ETH verifier ABI (contracts/OpenHouseProofs.sol) ────────

ETH_ABI = json.loads('''[
 {"type":"function","name":"submit","stateMutability":"nonpayable","inputs":[{"name":"outputs","type":"uint256[]"},{"name":"sig","type":"bytes"}],"outputs":[{"name":"","type":"bytes32"}]},
 {"type":"function","name":"submitFact","stateMutability":"nonpayable","inputs":[{"name":"outputs","type":"uint256[]"}],"outputs":[{"name":"","type":"bytes32"}]},
 {"type":"function","name":"verify","stateMutability":"view","inputs":[{"name":"outputs","type":"uint256[]"},{"name":"sig","type":"bytes"}],"outputs":[{"name":"ok","type":"bool"},{"name":"why","type":"string"},{"name":"f","type":"bytes32"},{"name":"signer","type":"address"}]},
 {"type":"function","name":"fact","stateMutability":"view","inputs":[{"name":"outputs","type":"uint256[]"}],"outputs":[{"name":"","type":"bytes32"}]},
 {"type":"function","name":"paid","stateMutability":"view","inputs":[{"name":"propertyId","type":"uint256"},{"name":"period","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
 {"type":"function","name":"programHash","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"bytes32"}]},
 {"type":"function","name":"count","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"banks","stateMutability":"view","inputs":[{"name":"","type":"address"}],"outputs":[{"name":"name","type":"string"},{"name":"active","type":"bool"},{"name":"since","type":"uint64"}]},
 {"type":"function","name":"properties","stateMutability":"view","inputs":[{"name":"","type":"uint256"}],"outputs":[{"name":"owner","type":"address"},{"name":"rentDue","type":"uint256"},{"name":"exists","type":"bool"},{"name":"label","type":"string"}]},
 {"type":"function","name":"setBank","stateMutability":"nonpayable","inputs":[{"name":"bank","type":"address"},{"name":"name","type":"string"},{"name":"active","type":"bool"}],"outputs":[]},
 {"type":"function","name":"registerProperty","stateMutability":"nonpayable","inputs":[{"name":"id","type":"uint256"},{"name":"pOwner","type":"address"},{"name":"rentDue","type":"uint256"},{"name":"label","type":"string"}],"outputs":[]},
 {"type":"function","name":"setProgramHash","stateMutability":"nonpayable","inputs":[{"name":"h","type":"bytes32"}],"outputs":[]},
 {"type":"function","name":"submitPQ","stateMutability":"nonpayable","inputs":[{"name":"o","type":"uint256[]"},{"name":"key","type":"bytes32"},{"name":"sig","type":"bytes"}],"outputs":[{"name":"f","type":"bytes32"}]},
 {"type":"function","name":"verifyPQ","stateMutability":"view","inputs":[{"name":"o","type":"uint256[]"},{"name":"key","type":"bytes32"},{"name":"sig","type":"bytes"}],"outputs":[{"name":"ok","type":"bool"},{"name":"why","type":"string"},{"name":"f","type":"bytes32"}]},
 {"type":"function","name":"setPQBank","stateMutability":"nonpayable","inputs":[{"name":"pubSeed","type":"bytes32"},{"name":"root","type":"bytes32"},{"name":"name","type":"string"},{"name":"active","type":"bool"}],"outputs":[{"name":"key","type":"bytes32"}]},
 {"type":"function","name":"requirePQ","stateMutability":"nonpayable","inputs":[],"outputs":[]},
 {"type":"function","name":"pqRequired","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"bool"}]},
 {"type":"function","name":"leafUsed","stateMutability":"view","inputs":[{"name":"","type":"bytes32"},{"name":"","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]}
]''')


# ── the store ───────────────────────────────────────────────────

class Proofs:
    """Everything the module does with proofs, over one store + one secrets
    dir. `near` is a zero-arg callable returning the orbit/near Mod (its
    wallet does the NEAR signing); None disables NEAR submits."""

    def __init__(self, store_dir, secrets_dir, config: Optional[dict] = None,
                 network: str = 'simulation', near=None):
        self.dir = Path(store_dir) / 'proofs'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir = Path(secrets_dir)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_path = self.dir / 'receipts.json'
        self.salts_path = self.secrets_dir / 'proof_salts.json'
        self.book_path = self.secrets_dir / 'proof_book.json'
        self.config = config or {}
        self.network = network
        self._near = near
        self._issuer = None

    # -- files
    def _load(self, path, default):
        if Path(path).exists():
            with open(path) as f:
                return json.load(f)
        return default

    def _save(self, path, data, private=False):
        if private:
            _write_private(Path(path), data)
        else:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)

    def issuer(self) -> Issuer:
        if self._issuer is None:
            self._issuer = Issuer(self.secrets_dir, name=(self.config.get('proofs') or {}).get('issuer_name', ''))
        return self._issuer

    def salt_for(self, tenant) -> int:
        """One salt per tenant per node, drawn once and kept 0600. The
        tenant is handed it off-chain; with it they can open any of their
        commitments to a third party."""
        key = str(tenant).strip().lower()
        with _LOCK:
            salts = self._load(self.salts_path, {})
            if key not in salts:
                salts[key] = '0x' + _secrets.token_hex(31)
                self._save(self.salts_path, salts, private=True)
        return felt(salts[key])

    # -- status
    def chains(self) -> dict:
        """Where the verifiers live, per the module config."""
        cfg = self.config.get('proofs', {}) if isinstance(self.config, dict) else {}
        near = (cfg.get('near') or {}).get(self.network) or {}
        eth = (cfg.get('eth') or {}).get(self.network) or {}
        # Each verifier names the chain it actually lives on (`network` in
        # its config block); the stage this node runs at is only the default.
        return {'near': {'network': near.get('network', self.network), 'account': near.get('account', ''),
                         'explorer': near.get('explorer', '')},
                'eth': {'network': eth.get('network', self.network), 'address': eth.get('address', ''),
                        'url': eth.get('url', ''), 'chainId': eth.get('chainId', ''),
                        'explorer': eth.get('explorer', '')}}

    def status(self) -> dict:
        receipts = self._load(self.receipts_path, {})
        info = program_info()
        pub = None
        try:
            pub = self.issuer().public()
        except Exception as e:
            info['issuer_error'] = str(e)
        return {'program': info, 'issuer': pub, 'receipts': len(receipts),
                'submitted': {'near': sum(1 for r in receipts.values() if (r.get('chain') or {}).get('near', {}).get('ok')),
                              'eth': sum(1 for r in receipts.values() if (r.get('chain') or {}).get('eth', {}).get('ok'))},
                'chains': self.chains(), 'unit': f'amounts are integers × {UNIT} of the deal currency',
                'quantum': {'pq_receipts': sum(1 for r in receipts.values() if (r.get('sig') or {}).get('pq')),
                            'classical_only': sum(1 for r in receipts.values() if not (r.get('sig') or {}).get('pq'))},
                'kinds': {'attested': 'the bank executed the Cairo program and signed the fact (today)',
                          'stark': 'a STARK of the same program, same fact, carried by a fact registry (plug-in)'}}

    # -- issue / verify / open
    def issue(self, property_id, period, rent_due, tenant, amount, salt=None,
              source: Optional[dict] = None, note: str = '') -> dict:
        """The bank's move: run the statement over the private facts, sign
        the fact twice, keep the receipt. Refuses (with the program's own
        reason) when the payment does not cover the rent due."""
        pid, per = int(property_id), int(period)
        due, paid = int(rent_due), int(amount)
        if pid <= 0 or per <= 0:
            raise ProofError('property_id and period must be positive')
        if due <= 0:
            raise ProofError('rent_due must be positive')
        t = tenant_felt(tenant)
        s = felt(salt) if salt not in (None, '', 0) else self.salt_for(tenant)
        outs = run([1, pid, per, due, t, s, paid])
        ph = program_hash()
        iss = self.issuer()
        f = fact(ph, outs)
        r = _receipt_from_outputs(ph, outs, iss.public(), iss.sign(f))
        if note:
            r['note'] = str(note)[:200]
        if source:
            # a bank-side reference only (txn id, connection) — never the payer
            r['source'] = {k: v for k, v in source.items() if k in ('bank', 'txn', 'booked_at_bank', 'matched_by')}
        with _LOCK:
            receipts = self._load(self.receipts_path, {})
            if r['nullifier'] in receipts:
                raise ProofError(f'already issued for property {pid} period {per} — nullifier {r["nullifier"][:14]}… exists')
            receipts[r['nullifier']] = r
            self._save(self.receipts_path, receipts)
            book = self._load(self.book_path, {})
            book[r['nullifier']] = {'tenant': str(tenant), 'salt': hex32(s), 'amount': paid,
                                    'source': source or {}, 'issued': r['issued']}
            self._save(self.book_path, book, private=True)
        return r

    def get(self, nullifier: str) -> Optional[dict]:
        return self._load(self.receipts_path, {}).get(hex32(felt(nullifier)))

    def list(self, property_id=None, period=None) -> list:
        rows = list(self._load(self.receipts_path, {}).values())
        if property_id not in (None, '', 0):
            rows = [r for r in rows if int(r['property_id']) == int(property_id)]
        if period not in (None, '', 0):
            rows = [r for r in rows if int(r['period']) == int(period)]
        return sorted(rows, key=lambda r: (-int(r['period']), -int(r['issued'])))

    def verify(self, receipt=None, nullifier: str = '', rent_due_floor=None, policy: str = 'auto') -> dict:
        r = receipt if isinstance(receipt, dict) else None
        if r is None and nullifier:
            r = self.get(nullifier)
            if r is None:
                raise ProofError('no such receipt here — pass the receipt itself to verify a foreign one')
        if r is None:
            raise ProofError('receipt (object) or nullifier required')
        if isinstance(receipt, str):
            try:
                r = json.loads(receipt)
            except Exception:
                raise ProofError('receipt must be a JSON object')
        try:
            ph = program_hash()
        except ProofError:
            ph = None
        out = verify_receipt(r, expect_program_hash=ph, rent_due_floor=rent_due_floor, policy=policy)
        out['known_here'] = r.get('nullifier') in self._load(self.receipts_path, {})
        return out

    def open(self, tenant, salt, receipt=None, nullifier: str = '') -> dict:
        """The tenant's move: (tenant, salt) → commitment; does it match the
        receipt's? Nothing is stored and nothing is signed."""
        r = receipt if isinstance(receipt, dict) else (self.get(nullifier) if nullifier else None)
        if isinstance(receipt, str):
            r = json.loads(receipt)
        if r is None:
            raise ProofError('receipt or nullifier required')
        c = run([2, tenant_felt(tenant), felt(salt)])[0]
        match = hex32(c) == str(r.get('commitment', '')).lower()
        return {'matches': match, 'commitment': hex32(c), 'receipt_commitment': r.get('commitment'),
                'property_id': r.get('property_id'), 'period': r.get('period'),
                'says': (f'this tenant is the one who paid property {r.get("property_id")} for {r.get("period")}'
                         if match else 'this tenant did not make that payment (or the salt is wrong)')}

    # -- post-quantum
    def pq_status(self) -> dict:
        """The PQ keys, how many one-time leaves are left, which receipts are
        still classical-only, and the threat model in one table."""
        pqm = _pq()
        iss = self.issuer()
        receipts = self._load(self.receipts_path, {})
        return {'schemes': {'ohx': {'name': pqm.SCHEME, 'params': pqm.SCHEME_ID, 'kind': 'stateful hash-based (XMSS^MT shape, keccak256)',
                                    'sig_bytes': pqm.SIG_BYTES, 'verified_on': ['ethereum (contracts/OHX.sol)', 'near (near/src/lib.rs)', 'offline (pq.py)']},
                            'mldsa65': {'name': pqm.MLDSA, 'kind': 'lattice, NIST FIPS 204, stateless',
                                        'available': pqm.mldsa_available(), 'verified_on': ['offline (orbit/postquant)']}},
                'keys': iss.pq().public(), 'leaves': iss.pq().capacity(), 'retired': iss.pq().retired(),
                'receipts': {'total': len(receipts),
                             'pq': sum(1 for r in receipts.values() if (r.get('sig') or {}).get('pq')),
                             'classical_only': [r['nullifier'] for r in receipts.values() if not (r.get('sig') or {}).get('pq')]},
                'threats': pqm.threat_model(),
                'switch': 'requirePQ() on Ethereum / require_pq on NEAR shuts the classical door for good'}

    def pq_upgrade(self, nullifier: str = '') -> dict:
        """Countersign classical-only receipts with the PQ keys. The fact does
        not change, so nothing on-chain changes either — the receipt simply
        gains a signature a quantum computer cannot forge."""
        done, skipped = [], []
        with _LOCK:
            receipts = self._load(self.receipts_path, {})
        targets = [hex32(felt(nullifier))] if nullifier else list(receipts)
        iss = self.issuer()
        for n in targets:
            r = receipts.get(n)
            if r is None:
                raise ProofError(f'no receipt {n[:14]}… here')
            if (r.get('sig') or {}).get('pq'):
                skipped.append(n)
                continue
            if not verify_receipt(r, policy='classical')['ok']:
                skipped.append(n)   # never vouch for a receipt that does not verify today
                continue
            sig_pq, pub_pq = iss.sign_pq(r['fact']), iss.pq().public()
            with _LOCK:
                cur = self._load(self.receipts_path, {})
                cur[n].setdefault('sig', {})['pq'] = sig_pq
                cur[n].setdefault('bank', {})['pq'] = pub_pq
                cur[n]['pq_upgraded'] = int(time.time())
                self._save(self.receipts_path, cur)
            done.append(n)
        return {'upgraded': done, 'skipped': skipped, 'leaves': iss.pq().capacity()}

    def pq_rotate(self) -> dict:
        """Retire the OHX key and draw a new one (seat it on-chain before
        submitting with it). Old receipts keep verifying against the retired
        public key they carry."""
        return self.issuer().pq().rotate()

    # -- chains
    def _mark(self, nullifier: str, chain: str, result: dict):
        with _LOCK:
            receipts = self._load(self.receipts_path, {})
            if nullifier in receipts:
                receipts[nullifier].setdefault('chain', {})[chain] = result
                self._save(self.receipts_path, receipts)

    def submit_near(self, r: dict, account: str = '', signer: str = '', network: str = '',
                    door: str = 'classical') -> dict:
        if self._near is None:
            raise ProofError('NEAR submits need the orbit/near module')
        account = account or self.chains()['near']['account']
        if not account:
            raise ProofError('no NEAR verifier account configured (config.proofs.near.<network>.account)')
        near = self._near()
        if door == 'pq':
            pq_sig = ((r.get('sig') or {}).get('pq') or {}).get('ohx')
            if not pq_sig:
                raise ProofError('receipt has no OHX-KECCAK signature — proof_pq_upgrade it first')
            method, gas = 'submit_pq', 200
            args = {'outputs': r['outputs'], 'signature': pq_sig, 'key': r['bank']['pq']['ohx']['id']}
        else:
            method, gas = 'submit', 60
            args = {'outputs': r['outputs'], 'signature': r['sig']['near'], 'bank': r['bank']['near']}
        try:
            out = near.call(account, method, args, network=network or self.chains()['near']['network'],
                            account_id=signer or None, gas_tgas=gas)
        except Exception as e:
            raise ProofError(f'NEAR call failed: {e}')
        res = {'ok': bool(out.get('ok')), 'hash': out.get('hash'), 'explorer': out.get('explorer'),
               'contract': account, 'door': door, 'failure': out.get('failure'), 'result': out.get('result'),
               'at': int(time.time())}
        self._mark(r['nullifier'], 'near', res)
        if not res['ok']:
            raise ProofError('NEAR rejected the receipt: ' + json.dumps(out.get('failure'))[:300])
        return res

    def view_near(self, method: str, args: dict, account: str = '', network: str = ''):
        if self._near is None:
            raise ProofError('NEAR reads need the orbit/near module')
        account = account or self.chains()['near']['account']
        if not account:
            raise ProofError('no NEAR verifier account configured')
        return self._near().view(account, method, args, network=network or self.chains()['near']['network'])

    def _w3(self, url: str):
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 30}))
        if not w3.is_connected():
            raise ProofError(f'cannot reach {url}')
        return w3

    def eth_contract(self, address: str = '', url: str = ''):
        cfg = self.chains()['eth']
        address, url = address or cfg['address'], url or cfg['url']
        if not address or not url:
            raise ProofError('no Ethereum verifier configured (config.proofs.eth.<network>.address/url)')
        w3 = self._w3(url)
        from web3 import Web3
        return w3, w3.eth.contract(address=Web3.to_checksum_address(address), abi=ETH_ABI)

    def submit_eth(self, r: dict, address: str = '', url: str = '', key: str = '',
                   door: str = 'classical') -> dict:
        """Relay to OpenHouseProofs.sol. Signed by `key` or, by default, the
        issuer's own secp256k1 key — the bank relays its own attestation."""
        w3, c = self.eth_contract(address, url)
        from eth_account import Account
        acct = Account.from_key(key or self.issuer().eth_key())
        outs = [felt(o) for o in r['outputs']]
        if door == 'pq':
            pq_sig = ((r.get('sig') or {}).get('pq') or {}).get('ohx')
            if not pq_sig:
                raise ProofError('receipt has no OHX-KECCAK signature — proof_pq_upgrade it first')
            sig = bytes.fromhex(pq_sig[2:])
            pq_key = bytes.fromhex(r['bank']['pq']['ohx']['id'][2:])
            ok, why, _ = c.functions.verifyPQ(outs, pq_key, sig).call()
            fn = c.functions.submitPQ(outs, pq_key, sig)
        else:
            sig = bytes.fromhex(r['sig']['eth'][2:])
            ok, why, _, _ = c.functions.verify(outs, sig).call()
            fn = c.functions.submit(outs, sig)
        if not ok:
            res = {'ok': False, 'why': why, 'contract': c.address, 'door': door, 'at': int(time.time())}
            self._mark(r['nullifier'], 'eth', res)
            raise ProofError(f'Ethereum verifier refuses: {why}')
        tx = fn.build_transaction({
            'from': acct.address, 'nonce': w3.eth.get_transaction_count(acct.address),
            'chainId': w3.eth.chain_id})
        signed = acct.sign_transaction(tx)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        res = {'ok': rcpt.status == 1, 'hash': h.hex() if h.hex().startswith('0x') else '0x' + h.hex(),
               'block': rcpt.blockNumber, 'contract': c.address, 'chainId': w3.eth.chain_id, 'door': door,
               'at': int(time.time())}
        cfg = self.chains()['eth']
        if cfg.get('explorer'):
            res['explorer'] = f"{cfg['explorer'].rstrip('/')}/tx/{res['hash']}"
        self._mark(r['nullifier'], 'eth', res)
        return res

    def chain_status(self, r: dict) -> dict:
        """Ask both verifiers whether this property/period is paid."""
        out = {'near': {'configured': bool(self.chains()['near']['account'])},
               'eth': {'configured': bool(self.chains()['eth']['address'])}}
        if out['near']['configured']:
            try:
                v = self.view_near('paid', {'property_id': int(r['property_id']), 'period': int(r['period'])})
                out['near']['paid'] = bool(v.get('result') if isinstance(v, dict) else v)
            except Exception as e:
                out['near']['error'] = str(e)[:200]
        if out['eth']['configured']:
            try:
                _, c = self.eth_contract()
                out['eth']['paid'] = bool(c.functions.paid(int(r['property_id']), int(r['period'])).call())
            except Exception as e:
                out['eth']['error'] = str(e)[:200]
        out['recorded'] = r.get('chain', {})
        return out
