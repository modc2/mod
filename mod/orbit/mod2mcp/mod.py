"""
mod2mcp — any mod, compiled into an MCP server, smart-contract style.

A mod's Mod class is treated like contract source: `abi` compiles its public
methods into a deterministic interface (typed inputs, view/write mutability,
per-function selectors, a content-hash address), `deploy` registers it on the
gateway so agents can call it at /mcp/<mod>, and `call` executes one function
— with write calls reverting on readonly deployments.

CLI:
    m mod2mcp/mods                              # what can be compiled
    m mod2mcp/abi news                          # compile — reads are free
    m mod2mcp/deploy news                       # register at /mcp/news
    m mod2mcp/deploy polymarket readonly=1      # views only
    m mod2mcp/call news search query=bittensor
    m mod2mcp/serve | m mod2mcp/status | m mod2mcp/kill | m mod2mcp/test

MCP:
    claude mcp add --transport http mod2mcp http://localhost:51550/mcp
    claude mcp add --transport http news http://localhost:51550/mcp/news   # after deploy
    claude mcp add news -- python3 -m mod2mcp.bridge news                  # stdio, cwd=orbit/mod2mcp
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from mod2mcp import runtime  # noqa: E402

PM2_NAME = 'mod2mcp'


def _list(v):
    if v is None or isinstance(v, list):
        return v
    return [s for s in str(v).split(',') if s]


class Mod:
    description = 'Compile any mod into an MCP server — deterministic ABI, view/write mutability, deployable contracts'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51550))

    def forward(self, mod: str = '', **kwargs):
        return self.abi(mod, **kwargs) if mod else self.status()

    def mods(self) -> dict:
        """Every mod that can be compiled."""
        return {'mods': runtime.mods()}

    def abi(self, mod: str, fns=None, exclude=None, readonly: bool = False) -> dict:
        """Compile a mod into its ABI + MCP tool list. Same interface → same address."""
        return runtime.contract(mod, fns=_list(fns), exclude=_list(exclude),
                                readonly=bool(readonly))

    def compile(self, mod: str, **kwargs) -> dict:
        """Alias of abi."""
        return self.abi(mod, **kwargs)

    def deploy(self, mod: str, fns=None, exclude=None, readonly: bool = False) -> dict:
        """Register a mod as a live MCP contract at /mcp/<mod> on the gateway."""
        return runtime.deploy(mod, fns=_list(fns), exclude=_list(exclude),
                              readonly=bool(readonly))

    def undeploy(self, mod: str) -> dict:
        return runtime.undeploy(mod)

    def deployments(self) -> dict:
        return {'deployments': runtime.deployments()}

    def call(self, mod: str, fn: str, **args):
        """Execute one function on a mod (readonly deployments revert on writes)."""
        d = runtime.deployments().get(mod, {})
        return runtime.call(mod, fn, args, readonly=bool(d.get('readonly')))

    def tools(self, mod: str) -> list:
        """The MCP tool names a mod compiles to."""
        return [t['name'] for t in runtime.contract(mod)['tools']]

    def mcp(self) -> dict:
        """How to connect."""
        return {'control': f'http://localhost:{self.port}/mcp',
                'deployed': f'http://localhost:{self.port}/mcp/<mod>',
                'stdio': f'cd {MODULE_DIR} && python3 -m mod2mcp.bridge <mod>'}

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(MODULE_DIR),
               '--', '-m', 'mod2mcp.server', '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'url': f'http://{host}:{port}/health',
                'mcp': f'http://{host}:{port}/mcp',
                **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def status(self) -> dict:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e), 'hint': 'm mod2mcp/serve',
                    'deployments': list(runtime.deployments())}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
