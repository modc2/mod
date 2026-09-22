import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this file would shadow the protocol's own
    # `mod` package for anything importing us afterwards.
    sys.path.append(HERE)


class Mod:
    description = """A post-quantum L1 whose entire state machine is a market
    in key/value space. ML-DSA signatures, ML-KEM key exchange, SHA3-256
    commitments — no elliptic curve anywhere. A key maps to a value, usually a
    32-byte hash of something kept off-chain, and holding that pair costs
    money continuously: write gas per byte, witness gas per signature byte
    (an ML-DSA signature is 2420 bytes and the chain bills for it), and rent
    per byte-hour against a prepaid escrow. Expired entries pay whoever sweeps
    them. REST, the browser console and 22 MCP tools on one port, off the
    same functions."""
    path = HERE

    # ── the surfaces ──────────────────────────────────────────────

    def serve(self, port=None, bind=None):
        """Run the node: REST + console + MCP on one port, plus the block
        loop. This call blocks."""
        import api
        return api.serve(int(port or api.PORT), bind=bind)

    def mcp(self, request: dict):
        """One MCP JSON-RPC message in, one response out."""
        import mcp as mcpsrv
        return mcpsrv.handle(request)

    def call(self, tool: str, **kwargs):
        """Run any pq_* tool by name — the same door REST and MCP use."""
        import mcp as mcpsrv
        return mcpsrv.call_tool(tool if tool.startswith('pq_')
                                else 'pq_' + tool, kwargs)

    def tools(self):
        """The MCP tool registry."""
        import mcp as mcpsrv
        return mcpsrv.tool_list()

    # ── the chain, as direct calls ────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point: the chain tip."""
        return self.head()

    def head(self):
        return self.call('head')

    def market(self, **kw):
        return self.call('market', **kw)

    def keys(self, **kw):
        return self.call('keys', **kw)

    def get(self, key):
        return self.call('get', key=key)

    def quote(self, key, **kw):
        return self.call('quote', key=key, **kw)

    def set(self, key, **kw):
        return self.call('set', key=key, **kw)

    def prove(self, key):
        return self.call('prove', key=key)

    def check(self, key, **kw):
        return self.call('check', key=key, **kw)

    def wallet(self, action='list', **kw):
        return self.call('wallet', action=action, **kw)

    def faucet(self, **kw):
        return self.call('faucet', **kw)

    def account(self, **kw):
        return self.call('account', **kw)

    def transfer(self, to, amount, **kw):
        return self.call('transfer', to=to, amount=amount, **kw)

    def verify(self, signatures=False):
        return self.call('verify', signatures=signatures)

    def mine(self, force=False):
        return self.call('mine', force=force)

    # ── housekeeping ──────────────────────────────────────────────

    def info(self):
        """Module card: what it is, the tip, every way in."""
        import api
        return api.info()

    def test(self):
        """Run the test suite."""
        import subprocess
        r = subprocess.run([sys.executable, '-m', 'pytest',
                            os.path.join(HERE, 'tests'), '-q'],
                           capture_output=True, text=True, cwd=HERE)
        return {'ok': r.returncode == 0,
                'out': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
