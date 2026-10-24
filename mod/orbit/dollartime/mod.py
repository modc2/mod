"""dollartime — collective ownership of one revenue-generating thing, paid
for in dollars and time.

StakeTime weighed stake by time locked. DollarTime weighs DOLLARS by time
locked: put dollars into the treasury that buys the thing (a house, a GPU
box), lock them for 0..1460 days, and your share of that day's emission is
dollars x (365 + lock_days) over everyone's. The token (DTT) is the deed:
revenue the thing earns is split by DTT held, and the steward's valuation
of the thing prices every DTT.

    cap       21,000,000 DTT, ever
    emission  at most 7,200 DTT a day (2916 days x 7,200 + 4,800 on the last)
    burn      a day nobody buys is burned - gone, still counted against the cap

The same rules run three times: paper here (engine.py), Solidity on the
staketime EVM stack (evm/, Base Sepolia) and Rust on NEAR (near/).

    m dollartime                                  # null call → info()
    m dollartime/join name=alice                  # → token + 10,000 paper dollars
    m dollartime/contribute token=<t> dollars=100 lock_days=365
    m dollartime/claim token=<t>                  # mint what closed days owe you
    m dollartime/me token=<t>
    m dollartime/revenue token=<t> dollars=40 memo=rent
    m dollartime/withdraw token=<t>
    m dollartime/spend token=<t> to=seller dollars=150 memo="buy the machine"   # steward
    m dollartime/set_asset token=<t> name="GPU box" valuation=1000              # steward
    m dollartime/stats · days · holders · history · chains
    m dollartime/evm_test · near_test · near_build · near_deploy
    m dollartime/serve · test · kill

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``.
"""

import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)

import engine  # noqa: E402

NEAR_TARGET = os.path.expanduser('~/mod/mod/orbit/near/contracts/.target')
NEAR_WASM = os.path.join(NEAR_TARGET, 'wasm32-unknown-unknown', 'release', 'dollartime.wasm')


def usd(x):
    """Human dollars ("12.50") → integer micro-dollars. Integers ≥ 1e6 that
    look like they already are micro-dollars are passed through unchanged
    only if the caller says so with a trailing 'u' ("12500000u")."""
    s = str(x).strip()
    if s.endswith('u'):
        return int(s[:-1])
    try:
        return int((Decimal(s) * engine.USD).to_integral_value())
    except (InvalidOperation, ValueError):
        raise ValueError(f'bad dollar amount: {x!r}')


def dtt(x):
    """Human DTT ("1000" or "0.5") → 18-decimal integer; 'w' suffix = raw wei."""
    s = str(x).strip()
    if s.endswith('w'):
        return int(s[:-1])
    try:
        return int((Decimal(s) * 10**18).to_integral_value())
    except (InvalidOperation, ValueError):
        raise ValueError(f'bad DTT amount: {x!r}')


def _truthy(v):
    return str(v).lower() in ('1', 'true', 'yes', 'y', 'on')


class Mod:
    description = """
    dollartime — collective ownership of a revenue-generating thing (a house,
    a machine). Dollars into the treasury, locked for time, earn that day's
    share of a 7,200-a-day emission capped at 21,000,000 DTT; empty days
    burn. DTT holders split the revenue and own the valuation. Paper ledger
    here, same contract on EVM (staketime stack) and NEAR.
    """

    def __init__(self, port=None, path=None, **kwargs):
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51410))
        self.base = cfg.get('base_path', '/dollartime')
        self._path = path
        self._led = None

    @property
    def led(self):
        if self._led is None:
            self._led = engine.Ledger(self._path or default_path())
        return self._led

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    # ── about ─────────────────────────────────────────────────────

    def info(self):
        """What this module is, the rules, every route, and the chains."""
        cfg = self.config()
        return {'name': 'dollartime', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'rules': cfg.get('rules', {}), 'stats': self.led.stats(),
                'chains': self.chains(), 'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        return {'ok': True, 'port': self.port, 'day': self.led.stats()['day']}

    def readme(self):
        p = os.path.join(HERE, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ── paper: identity ───────────────────────────────────────────

    def join(self, name):
        """Pick a name; get a token (keep it) and 10,000 paper dollars. The
        first to join is the steward."""
        return self.led.join(name)

    def me(self, token):
        """Your dollars, DTT (locked/unlocked), revenue owed, share and value."""
        return self.led.me(token)

    # ── paper: the rules in motion ────────────────────────────────

    def contribute(self, token, dollars, lock_days=0):
        """Put dollars in the treasury for lock_days (0..1460). Your weight
        for today = dollars x (365 + lock_days)."""
        return self.led.contribute(token, usd(dollars), lock_days)

    def claim(self, token, id=None):
        """Mint the DTT every closed day owes you (one position with id=)."""
        return self.led.claim(token, id)

    def transfer(self, token, to, amount):
        """Send unlocked DTT to another name."""
        return self.led.transfer(token, to, dtt(amount))

    def revenue(self, token, dollars, memo=''):
        """Deposit what the thing earned; split across every DTT alive now."""
        return self.led.deposit_revenue(token, usd(dollars), memo)

    def withdraw(self, token):
        """Take the revenue dollars owed to you."""
        return self.led.withdraw_revenue(token)

    def set_asset(self, token, name=None, description=None, valuation=None):
        """Steward: name the thing and mark its valuation (dollars)."""
        return self.led.set_asset(token, name, description,
                                  None if valuation is None else usd(valuation))

    def spend(self, token, to, dollars, memo=''):
        """Steward: pay treasury dollars out (to a name here, or a memo'd
        outside party) to buy or run the thing."""
        return self.led.spend(token, to, usd(dollars), memo)

    # ── paper: views ──────────────────────────────────────────────

    def stats(self):
        return self.led.stats()

    def days(self, limit=30):
        """The last N days: sold, burned or open."""
        return self.led.days(limit)

    def holders(self, limit=50):
        return self.led.holders(limit)

    def history(self, limit=50):
        return self.led.history(limit)

    def position(self, id):
        pos = self.led.s['positions'].get(str(int(id)))
        if not pos:
            raise ValueError('no such position')
        return dict(pos, claimable=self.led.claimable(id))

    # ── chains ────────────────────────────────────────────────────

    def chains(self):
        """Where the same contract lives on EVM and NEAR."""
        cfg = self.config().get('chains', {})
        out = {'evm': dict(cfg.get('evm', {})), 'near': dict(cfg.get('near', {}))}
        p = os.path.join(HERE, 'evm', 'deployments.json')
        if os.path.exists(p):
            with open(p) as f:
                out['evm']['deployments'] = json.load(f)
        out['near']['wasm'] = self._near_wasm_info()
        return out

    def evm_test(self):
        """Run the hardhat suite (evm/test) against an in-process chain."""
        r = subprocess.run([os.path.join(HERE, 'evm', 'hh'), 'test'],
                           cwd=os.path.join(HERE, 'evm'), capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def evm_deploy(self, network='base_sepolia', dollar='', asset='', symbol='DTT'):
        """Deploy DollarTime (and MockUSD if no dollar= stablecoin is given)
        with evm/scripts/deploy.js. Needs PRIVATE_KEY in evm/.env."""
        env = dict(os.environ, SYMBOL=symbol)
        if dollar:
            env['DOLLAR'] = dollar
        if asset:
            env['ASSET'] = asset
        r = subprocess.run([os.path.join(HERE, 'evm', 'hh'), 'run', 'scripts/deploy.js',
                            '--network', network],
                           cwd=os.path.join(HERE, 'evm'), capture_output=True, text=True, env=env)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:],
                'chains': self.chains()}

    def _near_wasm_info(self):
        if not os.path.exists(NEAR_WASM):
            return None
        b = open(NEAR_WASM, 'rb').read()
        return {'path': NEAR_WASM, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}

    def near_test(self):
        """cargo test on near/ (22 unit tests, the same numbers as the paper
        and EVM suites)."""
        r = subprocess.run(['cargo', 'test', '--offline'], cwd=os.path.join(HERE, 'near'),
                           capture_output=True, text=True,
                           env=dict(os.environ, CARGO_TARGET_DIR=NEAR_TARGET, CARGO_TERM_COLOR='never'))
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def near_build(self):
        """cargo build → dollartime.wasm (shared warm target dir with orbit/near)."""
        r = subprocess.run(['cargo', 'build', '--offline', '--target', 'wasm32-unknown-unknown', '--release'],
                           cwd=os.path.join(HERE, 'near'), capture_output=True, text=True,
                           env=dict(os.environ, CARGO_TARGET_DIR=NEAR_TARGET, CARGO_TERM_COLOR='never'))
        return {'ok': r.returncode == 0 and os.path.exists(NEAR_WASM),
                'log': (r.stdout + r.stderr)[-1500:], 'wasm': self._near_wasm_info()}

    def near_deploy(self, account='', dollar='', asset='', symbol='DTT', signer='',
                    network='testnet', create=False, fund_near=5.0, confirm=False):
        """Deploy near/ through the orbit/near keystore and init it:
        new(dollar, asset_name, symbol). dollar= is the NEP-141 stablecoin
        (testnet USDC = usdc.fakes.testnet). account= defaults to the
        keystore default; create=true makes it as a sub-account first."""
        if not os.path.exists(NEAR_WASM):
            b = self.near_build()
            if not b['ok']:
                return {'error': 'near build failed', **b}
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'orbit_near_mod', os.path.expanduser('~/mod/mod/orbit/near/mod.py'))
            near_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(near_mod)
            near = near_mod.Mod()
        except Exception as e:  # noqa: BLE001
            return {'error': f'orbit/near unavailable: {e}'}
        steps = []
        try:
            if _truthy(create) and account:
                steps.append({'step': 'create_account', 'result': near.create_account(
                    account, initial_near=fund_near, network=network, confirm=_truthy(confirm))})
            target = account or None
            dollar = dollar or self.config().get('chains', {}).get('near', {}).get(
                network, {}).get('dollar', 'usdc.fakes.testnet')
            dep = near.deploy(wasm_path=NEAR_WASM, account_id=target, init_method='new',
                              init_args={'dollar': dollar, 'asset_name': asset or 'unnamed asset',
                                         'symbol': symbol},
                              network=network, confirm=_truthy(confirm))
            steps.append({'step': 'deploy', 'result': dep})
            deployed = dep.get('deployed_to')
            if deployed:
                cfg_path = os.path.join(HERE, 'config.json')
                cfg = self.config()
                cfg.setdefault('chains', {}).setdefault('near', {})[network] = {
                    'account': deployed, 'dollar': dollar,
                    'explorer': 'https://testnet.nearblocks.io' if network == 'testnet' else 'https://nearblocks.io'}
                with open(cfg_path, 'w') as f:
                    json.dump(cfg, f, indent=4)
                    f.write('\n')
            return {'ok': True, 'account': deployed, 'steps': steps}
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e), 'steps': steps}

    # ── surfaces ──────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the console and the API on one port."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/'}

    def kill(self, port=None):
        """Stop whatever holds the port. By port, never by name — a pattern
        kill on this box takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self, chains=False):
        """The paper suite (fast). chains=true adds hardhat + cargo."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        out = {'ok': r.returncode == 0, 'paper': (r.stdout + r.stderr)[-2000:]}
        if _truthy(chains):
            e, n = self.evm_test(), self.near_test()
            out.update(evm=e, near=n, ok=out['ok'] and e['ok'] and n['ok'])
        return out


def default_path():
    d = os.environ.get('DOLLARTIME_DIR') or os.path.expanduser('~/.mod/dollartime')
    return os.path.join(d, 'ledger.json')
