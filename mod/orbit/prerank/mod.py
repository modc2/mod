"""
prerank — a daily prediction market over which model ranks first.

The idea in one paragraph: once a day a round opens over a field of models.
You back one to finish first, and your bet is a hash until the reveal window,
so the odds you take are the odds of being early rather than the odds of
having waited to see where the money went. Separately, every metered call on
a model has a spend and a cost, and the house's margin between them is handed
back to the caller as a claim on that model — weighted by how early the call
was. Using a good model early *is* the position.

    m prerank                          what this is
    m prerank/round                    the round that is taking bets
    m prerank/bet model=opus amount=5  seal a bet (the hash is computed here)
    m prerank/reveal                   open this box's sealed bets
    m prerank/models                   the earliness curve, per model
    m prerank/verify                   replay the whole log and check it
    m prerank/serve                    API :50630 + console :50631

Every function here is a thin call over the HTTP API, and the API is a thin
call over `src/api/src/engine.rs`, where the rules actually live. Two things
this file does *not* delegate: the commitment hash and the signature. Both are
computed on this machine, because a market that hides your bet from other
bettors but not from the server it is running on is not a sealed market.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
API_DIR = HERE / 'src' / 'api'
APP_DIR = HERE / 'src' / 'app'

MICRO = 1_000_000


class Mod:
    description = ('A once-a-day, sealed-bid prediction market over model ranks. '
                   'Bets are hashes until the reveal, the log is hash-chained and '
                   'replayable by anyone, rankings need a quorum of graders to '
                   'agree, and the house margin on early usage of a model is paid '
                   'back to the user as a weighted position in it.')
    path = str(HERE)
    port = 50630
    app_port = 50631

    # ── what it is ───────────────────────────────────────────────────

    def forward(self, **kwargs):
        """The null call returns the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        card = {
            'name': 'prerank',
            'description': self.description,
            'urls': {'api': self._api(),
                     'app': f'http://localhost:{self.app_port}/prerank'},
            'address': self.address(),
            'identity': _identity_kind(),
            'fns': [f for f in dir(self) if not f.startswith('_')
                    and callable(getattr(self, f))],
        }
        # The live card if the API is up, the static one if it is not — asking
        # for a module's description should never be the thing that fails.
        try:
            card['live'] = self._get('/')
        except Exception as e:
            card['live'] = None
            card['note'] = f'api not reachable ({e}) — try: m prerank/serve'
        return card

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        return self._get('/health')

    def status(self) -> Dict[str, Any]:
        return self._get('/status')

    # ── the market ───────────────────────────────────────────────────

    def round(self, id: str = None) -> Dict[str, Any]:
        """The round taking bets, or one named round."""
        return self._get(f'/rounds/{id}') if id else self._get('/round')

    def rounds(self) -> List[Dict[str, Any]]:
        return self._get('/rounds')['rounds']

    def models(self) -> Dict[str, Any]:
        """Every model that has had credits spent on it, and what a credit of
        margin on it is worth right now."""
        return self._get('/models')

    def leaderboard(self) -> Dict[str, Any]:
        """Who has actually been winning the rounds."""
        return self._get('/leaderboard')

    def account(self, address: str = None) -> Dict[str, Any]:
        return self._get(f'/account/{address or self.address()}')

    # ── betting ──────────────────────────────────────────────────────

    def bet(self, model: str, amount: float = 1.0, round: str = None,
            salt: str = None) -> Dict[str, Any]:
        """Seal a bet on `model` for `amount` credits.

        The commitment is hashed here and the salt is written to
        `~/.mod/prerank/bets.json` — the server is told a hash, an amount and a
        signature, and learns which model only when you reveal. Lose the salt
        before the reveal window and the stake forfeits to the pool, which is
        the price of the server not being able to open your bet for you.
        """
        import secrets
        round = round or self.round()['id']
        micro = int(round_credits(amount))
        salt = salt or secrets.token_hex(16)
        owner = self.address()
        commitment = commitment_hash(round, owner, model, micro, salt)
        nonce = self.account()['next_nonce']
        out = self._post('/commit', {
            'address': owner,
            'signature': self._sign(f'prerank:commit:{round}:{commitment}:{micro}:{nonce}'),
            'round': round, 'commitment': commitment, 'amount': micro, 'nonce': nonce,
        })
        _remember({'round': round, 'owner': owner, 'model': model, 'amount': micro,
                   'salt': salt, 'commitment': commitment, 'at': int(time.time())})
        return {**out, 'model_kept_local': model, 'salt_stored_in': str(_bets_path())}

    def bets(self) -> List[Dict[str, Any]]:
        """The sealed bets this box holds salts for."""
        return _remembered()

    def reveal(self, round: str = None, commitment: str = None) -> List[Dict[str, Any]]:
        """Open this box's sealed bets for a round.

        Safe to call repeatedly — an already-opened bet reports why it was
        skipped rather than failing the whole batch.
        """
        round = round or self.round()['id']
        out = []
        for bet in _remembered():
            if bet['round'] != round:
                continue
            if commitment and bet['commitment'] != commitment:
                continue
            try:
                out.append(self._post('/reveal', {
                    'round': bet['round'], 'commitment': bet['commitment'],
                    'model': bet['model'], 'salt': bet['salt'],
                }))
                _mark_revealed(bet['commitment'])
            except Exception as e:
                out.append({'commitment': bet['commitment'], 'error': str(e)})
        return out or [{'note': f'no local bets for round {round}'}]

    def transfer(self, round: str, model: str, to: str, units: float) -> Dict[str, Any]:
        """Hand over some of a round's model token. Only between the seal and
        the settlement — moving one earlier would announce your position."""
        micro = int(round_credits(units))
        nonce = self.account()['next_nonce']
        msg = f'prerank:transfer:{round}:{model}:{to.lower()}:{micro}:{nonce}'
        return self._post('/transfer', {
            'address': self.address(), 'signature': self._sign(msg),
            'round': round, 'model': model, 'to': to, 'units': micro, 'nonce': nonce,
        })

    # ── grading ──────────────────────────────────────────────────────

    def attest(self, round: str = None, ranking: Any = None) -> Dict[str, Any]:
        """Submit a ranking for a sealed round, best first.

            m prerank/attest ranking=opus,sonnet,haiku

        A quorum of registered graders has to land on the same order. Two
        different answers void the round rather than picking one.
        """
        round = round or self.round()['id']
        if isinstance(ranking, str):
            ranking = [m.strip() for m in ranking.split(',') if m.strip()]
        if not ranking:
            raise ValueError('a ranking is the round\'s field, in order, best first')
        digest = _sha256('|'.join(['prerank:rank', round, '>'.join(ranking)]))
        return self._post('/attest', {
            'address': self.address(),
            'signature': self._sign(f'prerank:attest:{round}:{digest}'),
            'round': round, 'ranking': ranking,
        })

    # ── metering ─────────────────────────────────────────────────────

    def usage(self, user: str, model: str, spend: float, cost: float = 0.0,
              id: str = None) -> Dict[str, Any]:
        """Post a metered call. This box must be a registered meter.

        `spend` is what the user paid and `cost` is what the call cost to
        serve; the difference is the house's margin, and the margin is the
        only thing that becomes a position. A user can never be handed back
        more than was made on them.
        """
        import secrets
        receipt = {
            'id': id or secrets.token_hex(8),
            'user': user.lower(),
            'model': model,
            'spend': int(round_credits(spend)),
            'cost': int(round_credits(cost)),
            'at': int(time.time()),
            'meter': self.address(),
        }
        message = ('prerank:usage:{id}:{user}:{model}:{spend}:{cost}:{at}').format(**receipt)
        receipt['signature'] = self._sign(message)
        return self._post('/usage', receipt)

    # ── owner ────────────────────────────────────────────────────────

    def owner(self, address: str = None) -> Dict[str, Any]:
        """Claim an unclaimed market, or hand it to someone else."""
        target = (address or self.address()).lower()
        return self._post('/owner', {
            'address': self.address(),
            'signature': self._sign(f'prerank:owner:{target}'),
            'owner': target,
        })

    def roster(self, models: Any = None) -> Dict[str, Any]:
        """Set the field. Takes effect at the next round — a round already
        taking bets keeps the field it opened with."""
        if models is None:
            return {'roster': self.status().get('roster', [])}
        if isinstance(models, str):
            models = [m.strip() for m in models.split(',') if m.strip()]
        models = sorted(set(models))
        return self._post('/roster', {
            'address': self.address(),
            'signature': self._sign('prerank:roster:' + ','.join(models)),
            'models': models,
        })

    def grader(self, address: str, label: str = 'cli', remove: bool = False) -> Dict[str, Any]:
        """Register (or drop) a grader. A round needs a quorum of them to
        agree before it can pay out."""
        verb = 'rm' if remove else 'add'
        return self._role('/attestors', f'prerank:attestor:{verb}:{address.lower()}',
                          address, label, remove)

    def meter(self, address: str, label: str = 'cli', remove: bool = False) -> Dict[str, Any]:
        verb = 'rm' if remove else 'add'
        return self._role('/meters', f'prerank:meter:{verb}:{address.lower()}',
                          address, label, remove)

    def grant(self, account: str, amount: float, memo: str = 'cli') -> Dict[str, Any]:
        """Issue credits to an account, or to the word `treasury` — which is
        what funds the early-user positions."""
        micro = int(round_credits(amount))
        # The engine normalises the target before it checks the signature, so
        # signing the checksummed form of an address would authenticate a
        # message the server never reads. Normalise here first.
        account = account if account == 'treasury' else account.strip().lower()
        return self._post('/credits/grant', {
            'address': self.address(),
            'signature': self._sign(f'prerank:credit:{account}:{micro}'),
            'account': account, 'amount': micro, 'memo': memo,
        })

    def tick(self) -> Dict[str, Any]:
        """Advance the clock: seal what is past sealing, settle what is past
        grading, open today's round. The server does this on a timer and on
        every read; this is for when you want it now."""
        return self._post('/tick', {})

    # ── audit ────────────────────────────────────────────────────────

    def verify(self) -> Dict[str, Any]:
        """Replay the log from genesis and check the running state against it,
        the hashes against each other, and the credits against themselves."""
        return self._get('/verify')

    def proof(self, round: str = None, commitment: str = None) -> Dict[str, Any]:
        """The Merkle inclusion proof for a commitment against its round's
        sealed root. Defaults to this box's most recent bet."""
        mine = _remembered()
        if not commitment and mine:
            commitment = mine[-1]['commitment']
            round = round or mine[-1]['round']
        return self._get(f'/proof/{round}/{commitment}')

    def chain(self, start: int = 0, limit: int = 50) -> Dict[str, Any]:
        return self._get(f'/chain?from={start}&limit={limit}')

    # ── running it ───────────────────────────────────────────────────

    def build(self, release: bool = True) -> Dict[str, Any]:
        """Build both halves: the Rust binary and the Next.js console."""
        out = {}
        cmd = ['cargo', 'build'] + (['--release'] if release else [])
        api = subprocess.run(cmd, cwd=str(API_DIR), capture_output=True, text=True)
        out['api'] = {'ok': api.returncode == 0, 'stderr_tail': api.stderr[-2000:]}
        app = subprocess.run(['bash', 'build.sh'], cwd=str(APP_DIR),
                             capture_output=True, text=True)
        out['app'] = {'ok': app.returncode == 0, 'stderr_tail': app.stderr[-2000:]}
        return out

    def serve(self, port: int = None, app_port: int = None, open_mode: bool = False,
              background: bool = True) -> Dict[str, Any]:
        """Both halves, as separate processes."""
        port = int(port or os.environ.get('PRERANK_PORT', self.port))
        app_port = int(app_port or os.environ.get('PRERANK_APP_PORT', self.app_port))
        env = {**os.environ, 'PORT': str(port)}
        if open_mode:
            # Unsigned actions and the development wallets. The console and
            # /health both say so out loud when it is on.
            env['PRERANK_OPEN'] = '1'
        api = subprocess.Popen(['bash', str(API_DIR / 'start.sh')], cwd=str(API_DIR), env=env)
        app_env = {**os.environ, 'PORT': str(app_port), 'API_PORT': str(port),
                   'PRERANK_API_URL': f'http://127.0.0.1:{port}'}
        app = subprocess.Popen(['bash', str(APP_DIR / 'start.sh')], cwd=str(APP_DIR), env=app_env)
        if not background:
            api.wait()
            app.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/prerank',
                'open_mode': bool(open_mode), 'pids': [api.pid, app.pid]}

    def kill(self) -> Dict[str, Any]:
        killed = []
        for pattern in ('prerank-api', 'prerank/src/app'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def logs(self, lines: int = 60) -> str:
        log = API_DIR / 'api.log'
        if not log.exists():
            return 'no api.log yet'
        return '\n'.join(log.read_text().splitlines()[-int(lines):])

    def test(self, rust: bool = True, python: bool = True) -> Dict[str, Any]:
        """The suites. The Rust one is the interesting half — `cheatproof.rs`
        is one test per way of cheating."""
        out = {}
        if rust:
            done = subprocess.run(['cargo', 'test'], cwd=str(API_DIR),
                                  capture_output=True, text=True)
            out['rust'] = {'ok': done.returncode == 0,
                           'output': (done.stdout or done.stderr)[-4000:]}
        if python:
            done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                                  cwd=str(HERE), capture_output=True, text=True)
            out['python'] = {'ok': done.returncode == 0,
                             'output': (done.stdout or done.stderr)[-4000:]}
        out['ok'] = all(v.get('ok') for v in out.values() if isinstance(v, dict))
        return out

    # ── identity ─────────────────────────────────────────────────────

    def address(self) -> str:
        """This box's address — the same key the rest of the protocol uses."""
        return _key().address.lower()

    def _sign(self, message: str) -> str:
        """EIP-191 `personal_sign`, so a browser wallet and this CLI are the
        same kind of caller as far as the market is concerned."""
        from eth_account import Account
        from eth_account.messages import encode_defunct
        signed = Account.sign_message(encode_defunct(text=message), private_key=_key().key)
        return '0x' + signed.signature.hex().removeprefix('0x')

    def _role(self, path: str, message: str, address: str, label: str, remove: bool):
        return self._post(path, {
            'address': self.address(), 'signature': self._sign(message),
            'target': address, 'label': label, 'remove': remove,
        })

    # ── transport ────────────────────────────────────────────────────

    def _api(self) -> str:
        return os.environ.get('PRERANK_API', f'http://127.0.0.1:{self.port}')

    def _get(self, path: str) -> Any:
        return _request('GET', self._api() + path)

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        return _request('POST', self._api() + path, body)


# ── helpers ──────────────────────────────────────────────────────────

def _request(method: str, url: str, body: Dict[str, Any] = None) -> Any:
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            # The API's refusals are the most informative thing it says —
            # surface the reason, not the status code.
            raise RuntimeError(json.loads(detail).get('error', detail))
        except json.JSONDecodeError:
            raise RuntimeError(detail or str(e))


_PROTOCOL = None


def _protocol_mod():
    """Import the protocol's `mod` package rather than this file.

    A module's own `mod.py` shadows the protocol package whenever the module
    directory comes first on `sys.path` — which it does under pytest and under
    a direct import both, and the failure is silent: `import mod` succeeds and
    returns this file. Drop the shadow for the length of the import.
    """
    global _PROTOCOL
    if _PROTOCOL is not None:
        return _PROTOCOL
    import importlib
    here = str(HERE)
    dropped = [p for p in list(sys.path) if p in ('', '.', here)]
    for p in dropped:
        sys.path.remove(p)
    shadow = sys.modules.pop('mod', None)
    try:
        _PROTOCOL = importlib.import_module('mod')
        return _PROTOCOL
    finally:
        # Put the world back exactly as it was; the real package is cached in
        # `_PROTOCOL`, so the restored shadow costs nothing.
        if shadow is not None:
            sys.modules['mod'] = shadow
        for p in reversed(dropped):
            sys.path.insert(0, p)


def _key():
    """The signer, as an `eth_account` account.

    Preferably the box's own protocol key: it is already a secp256k1
    Ethereum key, so using it means the CLI is the same identity the rest of
    the fleet knows rather than a second one. Off-fleet — a checkout with no
    protocol installed — fall back to a key of our own so the module still
    works, and say which one is in use on the info card.
    """
    from eth_account import Account
    try:
        return Account.from_key(_protocol_mod().get_key().private_key)
    except Exception:
        return _local_key()


def _local_key():
    """A module-local key, created once, for when there is no protocol key."""
    from eth_account import Account
    path = Path(os.environ.get('PRERANK_DIR', Path.home() / '.mod' / 'prerank'))
    path.mkdir(parents=True, exist_ok=True)
    keyfile = path / 'key.json'
    if keyfile.exists():
        return Account.from_key(json.loads(keyfile.read_text())['private_key'])
    account = Account.create()
    keyfile.write_text(json.dumps({'address': account.address,
                                   'private_key': account.key.hex()}, indent=2))
    keyfile.chmod(0o600)
    return account


def _identity_kind() -> str:
    try:
        _protocol_mod().get_key()
        return 'protocol'
    except Exception:
        return 'local'


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def commitment_hash(round: str, owner: str, model: str, amount: int, salt: str) -> str:
    """The same field layout `types.rs` and the console hash. Change one,
    change all three — the reveal is a comparison against this."""
    return _sha256('|'.join(['prerank:bet', round, owner.lower(), model,
                             str(int(amount)), salt]))


def round_credits(amount: Any) -> int:
    """Credits in, micro-credits out. Accepts `5`, `5.25` or `"5.25"`."""
    return int(round(float(amount) * MICRO))


def _bets_path() -> Path:
    directory = Path(os.environ.get('PRERANK_DIR',
                                    Path.home() / '.mod' / 'prerank'))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / 'bets.json'


def _remembered() -> List[Dict[str, Any]]:
    path = _bets_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return []


def _remember(bet: Dict[str, Any]) -> None:
    bets = [b for b in _remembered() if b['commitment'] != bet['commitment']]
    bets.append(bet)
    _bets_path().write_text(json.dumps(bets, indent=2))


def _mark_revealed(commitment: str) -> None:
    bets = _remembered()
    for bet in bets:
        if bet['commitment'] == commitment:
            bet['revealed'] = True
    _bets_path().write_text(json.dumps(bets, indent=2))
