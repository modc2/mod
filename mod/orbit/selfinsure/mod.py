import json
import os
import sys

import mod as m

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # appended, not prepended — this file would otherwise shadow the protocol's `mod`
    sys.path.append(HERE)


class Mod:
    description = """Member-owned insurance mutuals as an Ethereum contract — the operator's
fee capped at 10% and published on chain, surplus returned pro rata, claims
adjudicated by agents with an optional oracle for real data, and a US health
mutual template with a 0% operator fee."""
    path = HERE

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        return {
            'name': 'selfinsure',
            'description': self.description,
            'path': self.path,
            'files': sorted(f for f in os.listdir(self.path) if not f.startswith('__')),
            'contracts': ['SelfInsure', 'SelfInsureFactory', 'SignedOracle'],
            'tools': list(self._mcp().TOOLS),
        }

    def readme(self):
        for name in ['README.md', 'readme.md']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    def health(self):
        import onchain as O
        return {'ok': True, 'contract_built': bool(O.artifact()),
                'tools': len(self._mcp().TOOLS)}

    # ── the contract ────────────────────────────────────────
    def contract(self):
        """What the contract guarantees in code, and every call it exposes."""
        return self._mcp().call_tool('si_contract', {'what': 'describe'})

    def source(self, contract='SelfInsure'):
        import onchain as O
        return O.source(contract)

    def abi(self, contract='SelfInsure'):
        import onchain as O
        return O.abi(contract)

    def compile(self, contract='SelfInsure'):
        return self._mcp().call_tool('si_contract', {'what': 'compile', 'contract': contract})

    def preset(self, preset='health', decimals=6, **overrides):
        """Template terms in an asset's base units (health / parametric / mutual)."""
        return self._mcp().call_tool('si_preset', {'preset': preset, 'decimals': decimals,
                                                   **overrides})

    def deploy(self, account, network=None, password=None, contract='SelfInsure',
               confirm=False, **config):
        """Deploy through the eth module's keystore; this module holds no keys."""
        return self._mcp().call_tool('si_deploy', {'account': account, 'network': network,
                                                   'password': password, 'contract': contract,
                                                   'confirm': confirm, **config})

    def transparency(self, address, network=None, decimals=None):
        """A live pool, read off the chain — including the provider's profit share."""
        return self._mcp().call_tool('si_onchain', {'address': address, 'network': network,
                                                    'decimals': decimals})

    def claim(self, address, claim, network=None):
        return self._mcp().call_tool('si_onchain_claim', {'address': address, 'claim': claim,
                                                          'network': network})

    # ── off-chain pools ─────────────────────────────────────
    def pools(self, q=None, state=None, limit=100):
        return self._mcp().call_tool('si_pools', {'q': q, 'state': state, 'limit': limit})

    def pool(self, pool, full=True):
        return self._mcp().call_tool('si_pool', {'pool': pool, 'full': full})

    def stats(self):
        return self._mcp().call_tool('si_stats', {})

    # ── agents ──────────────────────────────────────────────
    def tools(self):
        return self._mcp().tool_list()

    def call(self, tool, **args):
        """Run any si_* tool by name."""
        return self._mcp().call_tool(tool, args)

    def serve(self, port=50850):
        """REST + POST /mcp + the transparency page, on one port."""
        import api
        api.serve(port)

    def _mcp(self):
        import mcp
        return mcp
