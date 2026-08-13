"""
0xprof — a marketplace for zero-knowledge proofs, and five ways to check one.

The module surface, for the CLI and for other mods:

    m 0xprof                          what this is
    m 0xprof/systems                  the proof systems it carries
    m 0xprof/methods                  the verifiers, and which are usable now
    m 0xprof/verify proof=… vkey=…    check a proof with everything available
    m 0xprof/prove system=schnorr …   make one of the small proofs
    m 0xprof/proofs                   the market
    m 0xprof/publish …                list a proof, priced or free
    m 0xprof/recheck id=…             re-run the methods, signed by this box
    m 0xprof/checks id=…              every run on one proof, and who asked
    m 0xprof/verifiers                the addresses that have checked things here
    m 0xprof/bounties                 open requests for proofs
    m 0xprof/serve                    API :50610 + console :50611

Every function here is the same code path the HTTP API uses — the API is a
transport, not a second implementation, which is the only way the two can be
relied on to agree.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class Mod:
    description = ('A marketplace for zk proofs where every proof is checked by '
                   'several independent verifiers — this box\'s own pairing '
                   'arithmetic, iden3\'s snarkjs, an independent BigInt '
                   'implementation, Ethereum\'s bn128 precompiles, and the real '
                   'Solidity verifier contract run through an eth_call state '
                   'override — and the disagreements are published rather than '
                   'averaged away.')
    path = str(HERE)
    port = 50610
    app_port = 50611

    # ── what it is ───────────────────────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point — the null call returns the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        from src import systems, verify
        return {
            'name': '0xprof',
            'description': self.description,
            'systems': systems.names(),
            'methods': [m['method'] for m in verify.methods()],
            'available': [m['method'] for m in verify.methods() if m.get('available')],
            'quorum': verify.QUORUM,
            'statuses': ['unverified', 'claimed', 'verified', 'invalid', 'disputed'],
            'urls': {'api': f'http://localhost:{self.port}',
                     'app': f'http://localhost:{self.app_port}/0xprof'},
            'fns': [f for f in dir(self) if not f.startswith('_') and callable(getattr(self, f))],
        }

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        from src import verify
        ready = [m['method'] for m in verify.methods()
                 if m.get('available') and m.get('authoritative')]
        return {'ok': bool(ready), 'authoritative_methods': ready,
                'quorum_reachable': len(ready) >= verify.QUORUM}

    def status(self) -> Dict[str, Any]:
        from src import bounties, identity, proofs, storage, verify
        return {'auth': identity.status(), 'storage': storage.stats(),
                'market': proofs.stats(), 'methods': verify.methods(),
                'bounties': {'open': len(bounties.search(state='open'))}}

    # ── the verifier ─────────────────────────────────────────────────

    def systems(self, name: str = None) -> Any:
        from src import systems as registry
        from src import verify
        if name:
            return {**registry.get(name), 'available_here': verify.methods_for(name)}
        return registry.catalog()

    def methods(self) -> List[Dict[str, Any]]:
        from src import verify
        return verify.methods()

    def verify(self, proof: Any = None, vkey: Any = None, public_signals: Any = None,
               system: str = None, methods: Any = None, **kwargs) -> Dict[str, Any]:
        """Check a proof with every method that can check it.

        Paths are accepted anywhere JSON is, because the three files a prover
        actually produces are on disk and asking a human to paste them into a
        shell argument is asking them not to bother.

            m 0xprof/verify proof=fixtures/threshold_g16_proof.json \\
                            vkey=fixtures/threshold_g16_vkey.json \\
                            public_signals=fixtures/threshold_g16_public.json
        """
        from src import systems as registry
        from src import verify as engine
        proof = _load(proof if proof is not None else kwargs.get('p'))
        vkey = _load(vkey if vkey is not None else kwargs.get('statement'), default={})
        signals = _load(public_signals if public_signals is not None
                        else kwargs.get('public'), default=[])
        if not isinstance(proof, dict):
            raise ValueError('proof must be a JSON object or a path to one')
        resolved = (registry.resolve(system) if system
                    else registry.sniff({'proof': proof, 'vkey': vkey}))
        if isinstance(methods, str):
            methods = [m.strip() for m in methods.split(',') if m.strip()]
        return engine.verify(resolved, proof, vkey, signals, method_names=methods)

    def prove(self, system: str = 'schnorr', **kwargs) -> Dict[str, Any]:
        """Make a proof. schnorr/dleq/merkle here; the snarks need a zkey."""
        from src.methods import native, snarkjs
        from src import verify as engine
        if system == 'schnorr':
            made = native.prove_schnorr(kwargs.get('secret', 42),
                                        kwargs.get('context', ''))
        elif system == 'dleq':
            made = native.prove_dleq(kwargs.get('secret', 42), kwargs.get('H'),
                                     kwargs.get('context', ''))
        elif system == 'merkle':
            leaves = kwargs.get('leaves') or ['alice', 'bob', 'carol', 'dave']
            if isinstance(leaves, str):
                leaves = [x for x in leaves.split(',') if x]
            made = native.prove_merkle(leaves, int(kwargs.get('index', 0)),
                                       kwargs.get('hash', 'sha256'))
        elif system in ('groth16', 'plonk', 'fflonk'):
            zkey = Path(kwargs['zkey']).read_bytes()
            made = snarkjs.prove(zkey, Path(kwargs['wasm']).read_bytes(),
                                 _load(kwargs.get('inputs'), default={}), system)
            made['statement'] = snarkjs.export_vkey(zkey)
        else:
            raise ValueError(f'cannot prove {system} here')
        made['verification'] = engine.verify(made['system'], made['proof'],
                                             made.get('statement') or {},
                                             made.get('public_signals') or [])
        return made

    # ── the market ───────────────────────────────────────────────────

    def proofs(self, **filters) -> List[Dict[str, Any]]:
        from src import proofs as market
        return market.search(**{k: v for k, v in filters.items() if v is not None})

    def proof(self, id: str, caller: str = '') -> Dict[str, Any]:
        from src import proofs as market
        return market.view(id, caller)

    def publish(self, proof: Any, vkey: Any = None, public_signals: Any = None,
                system: str = None, author: str = None, title: str = '',
                price: float = 0.0, tags: Any = None, description: str = '') -> Dict[str, Any]:
        from src import proofs as market
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        return market.publish(system, _load(proof), _load(vkey, default={}),
                              _load(public_signals, default=[]),
                              author=author or _key_address(), title=title,
                              description=description, price=float(price or 0),
                              tags=tags or [])

    def recheck(self, id: str, methods: Any = None, key: str = None) -> Dict[str, Any]:
        """Re-run the methods, signed with this box's own key.

        The HTTP door will not re-verify without a signature, and the CLI does
        not get a quieter version of the same act: it signs the same message
        with the key this box holds, so a run started from a shell lands in the
        log looking exactly like a run started from a wallet — one address, one
        signature anyone can recover.
        """
        from src import identity
        from src import proofs as market
        if isinstance(methods, str):
            methods = [m.strip() for m in methods.split(',') if m.strip()]
        address, signature, message = _sign_action(id, key)
        signed = identity.from_action(id, address, message, signature)
        return market.listing(market.recheck(id, methods, by=signed['address'],
                                             signature=signed['signature']))

    def checks(self, id: str) -> Dict[str, Any]:
        """Every run of the methods on one proof, and who asked for it."""
        from src import proofs as market
        record = market.get(id)
        return {'proof': id, 'checks': market.checks_of(record),
                'verifiers': market.roster(record)}

    def verifiers(self, limit: int = 50) -> List[Dict[str, Any]]:
        """The addresses that have made this box check something."""
        from src import proofs as market
        return market.people(limit=int(limit))

    def buy(self, id: str, buyer: str = None) -> Dict[str, Any]:
        from src import proofs as market
        return market.buy(id, buyer or _key_address())

    def refund(self, id: str, buyer: str = None) -> Dict[str, Any]:
        from src import proofs as market
        return market.refund(id, buyer or _key_address())

    def bounties(self, **filters) -> List[Dict[str, Any]]:
        from src import bounties as board
        return board.search(**{k: v for k, v in filters.items() if v is not None})

    def bounty(self, system: str = 'groth16', reward: float = 10.0, title: str = '',
               vkey: Any = None, require: Any = None, poster: str = None,
               description: str = '') -> Dict[str, Any]:
        from src import bounties as board
        return board.create(poster or _key_address(), system, float(reward),
                            vkey=_load(vkey, default={}),
                            require=_load(require, default=[]) or [],
                            title=title, description=description)

    def submit(self, id: str, proof: Any, public_signals: Any = None,
               prover: str = None) -> Dict[str, Any]:
        from src import bounties as board
        return board.submit(id, prover or _key_address(), _load(proof),
                            _load(public_signals, default=[]))

    def sweep(self) -> Dict[str, Any]:
        from src import bounties as board
        return board.sweep()

    def account(self, address: str = None) -> Dict[str, Any]:
        from src import market
        return market.summary(address or _key_address())

    def grant(self, address: str, amount: float = 100.0) -> Dict[str, Any]:
        """Credits into an account. The only way they come into existence."""
        from src import market
        return market.summary(market.grant(address, float(amount))['address'])

    # ── running it ───────────────────────────────────────────────────

    def serve(self, port: int = None, app_port: int = None, background: bool = True):
        """Both halves: the API and the console, as separate processes."""
        import subprocess
        port = int(port or os.environ.get('ZKPROF_PORT', self.port))
        app_port = int(app_port or os.environ.get('ZKPROF_APP_PORT', self.app_port))
        api = subprocess.Popen([sys.executable, str(HERE / 'src/api/api.py'),
                                '--port', str(port)], cwd=str(HERE))
        console = subprocess.Popen([sys.executable, str(HERE / 'src/app/server.py'),
                                    '--port', str(app_port),
                                    '--api', f'http://127.0.0.1:{port}'], cwd=str(HERE))
        if not background:
            api.wait()
            console.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/0xprof',
                'pids': [api.pid, console.pid]}

    def kill(self) -> Dict[str, Any]:
        import subprocess
        killed = []
        for pattern in ('0xprof/src/api/api.py', '0xprof/src/app/server.py'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def test(self) -> Dict[str, Any]:
        """The test suite, which is mostly the verifiers disagreeing on cue."""
        import subprocess
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                              cwd=str(HERE), capture_output=True, text=True)
        return {'ok': done.returncode == 0,
                'output': (done.stdout or done.stderr)[-4000:]}


# ── helpers ──────────────────────────────────────────────────────────

def _load(value: Any, default: Any = None) -> Any:
    """JSON, a path to JSON, or a JSON string — all the same thing here."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value)
    candidate = Path(text)
    try:
        if candidate.exists() and candidate.is_file():
            return json.loads(candidate.read_text())
    except OSError:
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _sign_action(record_id: str, key_name: str = None,
                 action: str = 're-verify') -> tuple:
    """personal_sign, from the shell, with this box's key.

    Signed with eth_account rather than the protocol key's own `sign`, because
    the signature has to be the same shape a browser wallet produces — the
    server checks one thing, not two, and a second signature format is a second
    thing that can be subtly wrong.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    from src import identity
    from src.storage import protocol
    try:
        key = protocol().key(key_name) if key_name else protocol().key()
        private = key.private_key
        if isinstance(private, (bytes, bytearray)):
            private = private.hex()
        account = Account.from_key(private)
    except Exception as e:
        raise RuntimeError(f'cannot sign a {action} with this box\'s key ({e}) — '
                           'a re-verification is published under an address, so it '
                           'is signed or it does not happen') from e
    challenge = identity.action_challenge(record_id, account.address, action)
    signed = Account.sign_message(encode_defunct(text=challenge['message']),
                                  private_key=private)
    # 0x-prefixed, because a wallet's is: the log is read by people comparing
    # rows, and two spellings of the same signature is a difference that means
    # nothing and looks like it means something.
    raw = signed.signature.hex()
    return (account.address, raw if raw.startswith('0x') else f'0x{raw}',
            challenge['message'])


def _key_address() -> str:
    """This box's own key, for CLI calls that need an author or a buyer."""
    from src.storage import protocol
    try:
        return protocol().key().address
    except Exception:
        return 'local'
