"""
agenti — the money layer for AI agents (nirholas/agenti) as a mod, with an app.

Upstream is a pnpm TypeScript monorepo that gives an agent a wallet (EVM +
Solana), lets it pay x402 APIs, receive USDC and check balances, and exposes
that as an SDK and an MCP server. It is vendored unmodified under src/ at
UPSTREAM_COMMIT.

The app (agentiapp/ + web/index.html) is a Python port of the parts an owner
needs day to day: agent wallets (same derivation paths as upstream), balances
on Base/Ethereum/Arbitrum/Polygon/Solana, x402 pay with spend limits, payment
requests with QR, an activity ledger, a toolkit browser and its own MCP.

CLI:
    m agenti/serve                   # app + API + MCP on :50129 (foreground)
    m agenti/wallets                 # agent wallets (addresses only)
    m agenti/create_wallet name=bot  # new 12-word wallet
    m agenti/balances [wallet=bot]   # native + USDC on every chain
    m agenti/probe url=https://...   # what an x402 API costs
    m agenti/pay url=https://...     # call it paid, inside the limits
    m agenti/policy                  # spend limits + today's spend
    m agenti/info | packages | readme [package=mcp]
    m agenti/install | build | test | mcp   # upstream pnpm workspace
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
SRC = MODULE_DIR / 'src'
UPSTREAM = 'https://github.com/nirholas/agenti'
UPSTREAM_COMMIT = '72cfb67a18432a3d8773d200db1141b32289fb0a'
PORT = json.loads((MODULE_DIR / 'config.json').read_text()).get('port', 50129)

if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))


def _pnpm() -> list:
    if shutil.which('pnpm'):
        return ['pnpm']
    return ['npx', '--yes', 'pnpm@9']


class Mod:
    description = 'Give AI agents a crypto wallet: pay x402 APIs, receive USDC, check balances (EVM + Solana) — app, SDK + MCP'
    port = PORT

    # ── app ──
    def serve(self, port: int = None, host: str = '0.0.0.0'):
        """App + API + MCP on one port (blocks)."""
        from agentiapp import server
        return server.serve(int(port or PORT), host)

    def wallets(self) -> list:
        from agentiapp import wallets
        return wallets.list_wallets()

    def create_wallet(self, name: str = 'agent') -> dict:
        from agentiapp import wallets
        return wallets.create(name)

    def balances(self, wallet: str = None) -> dict:
        from agentiapp import chains, wallets
        w = wallets.get(wallet)
        return {'wallet': w, **chains.balances(w['evm'], w['solana'])}

    def probe(self, url: str, method: str = 'GET', body: str = None) -> dict:
        from agentiapp import pay
        return pay.probe(url, method, body)

    def pay(self, url: str, method: str = 'GET', body: str = None, wallet: str = None,
            index: int = None, max_usd: float = None) -> dict:
        from agentiapp import pay
        return pay.pay(url, method, body, wallet, index, max_usd)

    def policy(self, **kw) -> dict:
        from agentiapp import pay
        return pay.set_policy(**kw) if kw else {**pay.policy(), 'spent_today': pay.spent_today()}

    # ── upstream ──
    def info(self) -> dict:
        return {'name': 'agenti', 'upstream': UPSTREAM, 'commit': UPSTREAM_COMMIT, 'port': PORT,
                'app': f'http://localhost:{PORT}/agenti/', 'mcp': f'http://localhost:{PORT}/mcp',
                'src': str(SRC), 'packages': [p['name'] for p in self.packages()],
                'installed': (SRC / 'node_modules').is_dir()}

    def packages(self) -> list:
        from agentiapp import toolkit
        return toolkit.packages()

    def readme(self, package: Optional[str] = None) -> str:
        path = SRC / 'packages' / package / 'README.md' if package else SRC / 'README.md'
        return path.read_text() if path.is_file() else f'no README at {path}'

    def _run(self, *args: str, timeout: int = 1800) -> dict:
        p = subprocess.run(_pnpm() + list(args), cwd=SRC, capture_output=True, text=True, timeout=timeout)
        return {'ok': p.returncode == 0, 'code': p.returncode, 'out': p.stdout[-4000:], 'err': p.stderr[-4000:]}

    def install(self) -> dict:
        return self._run('install', '--frozen-lockfile')

    def build(self) -> dict:
        return self._run('-r', 'build')

    def test(self) -> dict:
        p = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'], cwd=MODULE_DIR,
                           capture_output=True, text=True, timeout=600)
        return {'ok': p.returncode == 0, 'out': p.stdout[-4000:], 'err': p.stderr[-2000:]}

    def test_upstream(self) -> dict:
        return self._run('-r', 'test')

    def mcp(self):
        """Run the upstream @agenti/mcp server on stdio (blocks)."""
        bin_js = SRC / 'packages' / 'mcp' / 'dist' / 'bin.js'
        if not bin_js.is_file():
            return {'ok': False, 'err': 'not built — run m agenti/install && m agenti/build'}
        return subprocess.call(['node', str(bin_js)], cwd=SRC)
