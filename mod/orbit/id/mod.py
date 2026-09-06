"""id — one identity, many accounts. Wallets included.

    m id/info                                   what this is
    m id/chains                                 every chain it can check a signature from
    m id/services                               the accounts that prove themselves by publishing
    m id/challenge chain=eth address=0x…        the exact text to sign
    m id/submit nonce=… signature=0x…           the proof, and the link it makes
    m id/whois address=solana:9xQe…             which identity is this, and what else is in it
    m id/show id=id_…                           the identity document
    m id/audit id=id_…                          re-check every signature in the log, offline
    m id/export id=id_… > me.json               take it somewhere else
    m id/demo                                   the whole flow, with keys made on the spot
    m id/serve                                  API :50650 + console :50651

An identity here is a *set of accounts* — an Ethereum wallet, a Solana wallet, a
Bitcoin address, a GitHub login — each of which has signed to say it belongs.
There is no account table and no password. The identity is the log of proofs,
and every function below is a read or an append to that log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class Mod:
    description = ('One identity made of many accounts. Link an Ethereum wallet, a '
                   'Solana wallet, a Bitcoin address, a Cosmos key, a GitHub login — '
                   'each proves itself by signing, and the set becomes a single id. '
                   'Every signature is checked with primitives written here, so an '
                   'identity can be re-verified offline, on any machine, forever.')
    path = str(HERE)
    port = 50650
    app_port = 50651

    # ── what it is ───────────────────────────────────────────────────

    def forward(self, **kwargs: Any) -> Dict[str, Any]:
        """The null call: the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        from src import accounts, chains, identity, store
        return {
            'name': 'id',
            'description': self.description,
            'idea': 'an identity is a set of accounts, and the set is a log of signatures',
            'chains': [c['chain'] for c in chains.known()],
            'services': [s['service'] for s in accounts.known()],
            'proof_strengths': {
                'key': 'a signature — re-checkable offline, forever, by anyone',
                'publication': 'a token published where only the holder can write — '
                               'true while it is up, and no longer'},
            'rules': {
                'join': 'two signatures: the joining account, and a member consenting',
                'merge': 'two signatures: one member from each side',
                'leave': 'your own signature is always enough to remove yourself',
                'evict': 'only the root account removes somebody else'},
            'state': store.stats(),
            'urls': {'api': f'http://localhost:{self.port}',
                     'app': f'http://localhost:{self.app_port}/id'},
            'fns': [f for f in dir(self)
                    if not f.startswith('_') and callable(getattr(self, f))],
        }

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        from src import chains, store
        return {'ok': True, 'chains': len(chains.known()), **store.stats()}

    # ── what can be linked ───────────────────────────────────────────

    def chains(self) -> List[Dict[str, Any]]:
        """Every chain whose signatures this can check, and what each one signs."""
        from src import chains as registry
        return registry.known()

    def services(self) -> List[Dict[str, Any]]:
        """Accounts with no key — they prove themselves by publishing a token."""
        from src import accounts
        return accounts.known()

    def address(self, chain: str, address: str) -> Dict[str, Any]:
        """Is this an address on that chain, and how else is the same key printed?"""
        from src import chains as registry
        canonical = registry.parse(chain, address)
        return {'chain': registry.get(chain).name, 'address': canonical,
                'equivalents': registry.equivalents(chain, canonical),
                'linked_to': self._resolve(f'{registry.get(chain).name}:{canonical}')}

    def _resolve(self, account: str) -> Optional[str]:
        from src import store
        return store.resolve(account)

    # ── proving an account ───────────────────────────────────────────

    def challenge(self, chain: str = None, address: str = None, kind: str = None,
                  handle: str = None, op: str = 'link', id: str = None,
                  other: str = None, name: str = None, target: str = None,
                  ttl: int = 900) -> Dict[str, Any]:
        """The exact text to sign — or the one-line token to publish."""
        from src import identity
        return identity.challenge(kind=kind or chain, handle=handle or address, op=op,
                                  id=id, other=other, name=name, target=target,
                                  ttl=int(ttl))

    def submit(self, nonce: str, signature: str = None, pubkey: str = None,
               source: str = None, session: str = None) -> Dict[str, Any]:
        """Hand back the signature. The challenge remembers what it was for."""
        from src import identity
        return identity.submit(nonce, signature=signature, pubkey=pubkey,
                               source=source, session=session)

    def verify(self, chain: str, address: str, message: str,
               signature: str, pubkey: str = None) -> Dict[str, Any]:
        """Check one signature against one address. Nothing is stored, nothing linked."""
        from src import chains as registry
        return registry.verify(chain, address, message, signature, pubkey=pubkey)

    def claim(self, chain: str = None, address: str = None, id: str = None,
              **kwargs: Any) -> Dict[str, Any]:
        """Start a session by proving a member account — that session is the consent
        every later join is recorded against."""
        return self.challenge(chain=chain, address=address, op='claim', id=id, **kwargs)

    # ── reading identities ───────────────────────────────────────────

    def whois(self, address: str = None, chain: str = None,
              account: str = None) -> Dict[str, Any]:
        """Which identity does this account belong to, and what else is in it?"""
        from src import identity
        if address and not chain and ':' in address:
            account, address = address, None
        return identity.whois(kind=chain, handle=address, account=account)

    def show(self, id: str, proofs: bool = False) -> Dict[str, Any]:
        """The identity document — the accounts, and how each one got there."""
        from src import identity
        return identity.document(id, proofs=_flag(proofs))

    def ids(self) -> List[Dict[str, Any]]:
        """Every identity on this host."""
        from src import identity
        return identity.listing()

    def log(self, id: str) -> List[Dict[str, Any]]:
        """The raw append-only log. This is the identity; the rest is a view of it."""
        from src import store
        return store.events(store.follow(id))

    def audit(self, id: str, live: bool = False) -> Dict[str, Any]:
        """Replay the log and re-check every signature in it. The honest answer."""
        from src import identity
        return identity.audit(id, live=_flag(live))

    # ── changing them ────────────────────────────────────────────────

    def merge(self, id: str, other: str) -> Dict[str, Any]:
        """What each side has to sign to become one identity."""
        from src import identity, store
        survivor, absorbed = identity.merge_order(id, other)
        asks = []
        for side in (survivor, absorbed):
            state = identity.document(side, proofs=False)
            asks.append({'identity': side, 'name': state['name'],
                         'any_of': [a['account'] for a in state['accounts']],
                         'then': f'm id/challenge chain=<kind> address=<addr> '
                                 f'op=merge id={survivor} other={absorbed}'})
        return {'survivor': survivor, 'absorbed': absorbed,
                'sign': asks,
                'note': 'a member of each side signs the same pair — order is fixed by '
                        'age, so both sides sign identical words. The older identity '
                        'keeps its name; the other one still resolves to it afterwards.'}

    def unlink(self, chain: str = None, address: str = None, id: str = None,
               target: str = None) -> Dict[str, Any]:
        """Leave an identity (sign with the account itself), or evict one (sign as root)."""
        return self.challenge(chain=chain, address=address, op='unlink', id=id,
                              target=target)

    def name(self, name: str, chain: str = None, address: str = None,
             id: str = None) -> Dict[str, Any]:
        """Set a display name. Only the root account can."""
        return self.challenge(chain=chain, address=address, op='name', id=id, name=name)

    # ── moving them ──────────────────────────────────────────────────

    def export(self, id: str, path: str = None) -> Any:
        """Everything needed to re-check this identity elsewhere. No secrets in it."""
        from src import identity
        document = identity.export(id)
        if path:
            Path(path).expanduser().write_text(json.dumps(document, indent=2))
            return {'ok': True, 'id': document['id'], 'path': str(Path(path).expanduser()),
                    'accounts': document['count']}
        return document

    def load(self, path: str = None, document: Any = None,
             overwrite: bool = False) -> Dict[str, Any]:
        """Import an identity from another host — every proof is re-checked first."""
        from src import identity
        if document is None:
            if not path:
                raise ValueError('pass a path to an exported identity, or the document')
            document = json.loads(Path(path).expanduser().read_text())
        if isinstance(document, str):
            document = json.loads(document)
        return identity.import_document(document, overwrite=_flag(overwrite))

    def rebuild(self) -> Dict[str, Any]:
        """Recompute the index from the logs. Proves the index is only a cache."""
        from src import identity
        return identity.rebuild()

    # ── seeing it work without a wallet ──────────────────────────────

    def demo(self, keep: bool = False) -> Dict[str, Any]:
        """Make keys on the spot, run the whole flow, and show every step.

        Ethereum, Solana and Bitcoin keys are generated here, sign the real
        statements, and are thrown away. Nothing in the path being exercised is
        special-cased for the demo — it is the same code a wallet drives.
        """
        from src import demo as runner
        return runner.run(keep=_flag(keep))

    # ── running it ───────────────────────────────────────────────────

    def serve(self, port: int = None, app_port: int = None,
              background: bool = True) -> Dict[str, Any]:
        port = int(port or os.environ.get('ID_PORT', self.port))
        app_port = int(app_port or os.environ.get('ID_APP_PORT', self.app_port))
        api = subprocess.Popen([sys.executable, str(HERE / 'src/api.py'),
                                '--port', str(port)], cwd=str(HERE))
        app = subprocess.Popen([sys.executable, str(HERE / 'src/app.py'),
                                '--port', str(app_port),
                                '--api', f'http://127.0.0.1:{port}'], cwd=str(HERE))
        if not background:
            api.wait()
            app.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/id',
                'pids': [api.pid, app.pid]}

    def kill(self) -> Dict[str, Any]:
        killed = []
        for pattern in ('id/src/api.py', 'id/src/app.py'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def test(self) -> Dict[str, Any]:
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                              cwd=str(HERE), capture_output=True, text=True)
        return {'ok': done.returncode == 0,
                'output': (done.stdout or done.stderr)[-4000:]}


def _flag(value: Any) -> bool:
    """CLI arguments arrive as strings; `live=false` has to mean false."""
    if isinstance(value, str):
        return value.strip().lower() not in ('0', 'false', 'no', 'off', '')
    return bool(value)
