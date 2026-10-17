"""
agenti — the money layer for AI agents (nirholas/agenti) as a mod.

Upstream is a pnpm TypeScript monorepo that gives an agent a wallet (EVM +
Solana), lets it pay x402 APIs, receive USDC and check balances, and exposes
that as an SDK and an MCP server. It is vendored unmodified under src/ at
UPSTREAM_COMMIT; this file is a thin wrapper around it.

CLI:
    m agenti/info                    # upstream pin + packages
    m agenti/packages                # every workspace package with its description
    m agenti/readme [package=mcp]    # repo README, or one package's
    m agenti/install                 # pnpm install in src/ (via corepack/npx)
    m agenti/build                   # pnpm -r build
    m agenti/test                    # pnpm -r test
    m agenti/mcp                     # run the @agenti/mcp stdio server (needs build)
"""
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
SRC = MODULE_DIR / 'src'
UPSTREAM = 'https://github.com/nirholas/agenti'
UPSTREAM_COMMIT = '72cfb67a18432a3d8773d200db1141b32289fb0a'


def _pnpm() -> list:
    if shutil.which('pnpm'):
        return ['pnpm']
    return ['npx', '--yes', 'pnpm@9']


class Mod:
    description = 'Give AI agents a crypto wallet: pay x402 APIs, receive USDC, check balances (EVM + Solana), SDK + MCP'

    def info(self) -> dict:
        return {'name': 'agenti', 'upstream': UPSTREAM, 'commit': UPSTREAM_COMMIT,
                'src': str(SRC), 'packages': [p['name'] for p in self.packages()],
                'installed': (SRC / 'node_modules').is_dir()}

    def packages(self) -> list:
        out = []
        for d in sorted((SRC / 'packages').iterdir()):
            pj = d / 'package.json'
            if pj.is_file():
                meta = json.loads(pj.read_text())
                out.append({'dir': d.name, 'name': meta.get('name'), 'version': meta.get('version'),
                            'description': meta.get('description', '')})
            elif (d / 'pyproject.toml').is_file():
                out.append({'dir': d.name, 'name': d.name, 'version': None, 'description': 'python package'})
        return out

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
        return self._run('-r', 'test')

    def mcp(self):
        """Run the upstream MCP server on stdio (blocks)."""
        bin_js = SRC / 'packages' / 'mcp' / 'dist' / 'bin.js'
        if not bin_js.is_file():
            return {'ok': False, 'err': 'not built — run m agenti/install && m agenti/build'}
        return subprocess.call(['node', str(bin_js)], cwd=SRC)
