"""starknet.gas_meter — Gas meter.

Every fn listed in config.json "fns" is live at
    GET|POST /starknet/_api/apps/gas_meter/<fn>      (query string or JSON body = kwargs)
    m starknet.gas_meter/<fn>
and app/index.html calls them with api('<fn>', {...}). Saving this file is
enough: the server reloads it on the next call. Put a fn in "write_fns" too
if it changes anything — then only the owner may call it.

The chain helpers live next door in ../../api (chain.py, abi.py, stables.py,
strk20.py): chain.block_number(), chain.balance(addr, 'strk'),
abi.read(contract, fn, args), stables.stables() ...
"""
import os
import sys

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import chain  # noqa: E402


class Mod:

    def facts(self):
        """The numbers on this app's card in the Apps tab: [[label, value], ...]."""
        g = self.gas()
        return [['L2 gas', f"{g['l2_gas']['fri'] / 1e9:,.1f} gfri"],
                ['L1 gas', f"{g['l1_gas']['fri'] / 1e9:,.0f} gfri"],
                ['block', f"{g['block']:,}"]]

    def hello(self, who='starknet'):
        """The smallest possible fn — try it from the app."""
        return {'hello': who, 'from': 'starknet.gas_meter'}

    def gas(self, network=None):
        """Gas prices in the newest block. Starknet charges three kinds of gas:
        L2 gas (running your transaction), L1 gas and L1 data gas (posting it to
        Ethereum). Each is quoted in fri (1e-18 STRK) and wei (1e-18 ETH)."""
        b = chain.block('latest', network=network)
        out = {'block': b.get('block_number'), 'timestamp': b.get('timestamp')}
        for k in ('l2_gas_price', 'l1_gas_price', 'l1_data_gas_price'):
            p = b.get(k) or {}
            fri, wei = int(p.get('price_in_fri', '0x0'), 16), int(p.get('price_in_wei', '0x0'), 16)
            out[k.replace('_price', '')] = {'strk': fri / 1e18, 'gwei': wei / 1e9, 'fri': fri}
        return out

    def head(self, network=None):
        """The newest block: number, time, how many transactions."""
        b = chain.block('latest', network=network)
        return {'block': b.get('block_number'), 'timestamp': b.get('timestamp'),
                'txs': len(b.get('transactions') or [])}
