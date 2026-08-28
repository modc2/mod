"""Charitao miner: mining = donating.

There is no axon and no query protocol — the miner's whole job is sending
TAO from its registered coldkey to a whitelisted charity address. The
validator watches the chain, so the coldkey→uid binding in the metagraph is
the proof of authorship; nothing to sign, nothing to serve.

Local mode donates on the MockChain. On a live network, donate from the
miner coldkey wallet (`btcli wallet transfer`) — `donate()` here just tells
you that.

Sizing strategy: donating more than the epoch budget is never lost (it
carries over at the same payout rate), but capital comes back faster if you
stay within your expected share of the budget. `suggest_donation()` does
that arithmetic.
"""
from typing import Optional

from .chain import MockChain
from .registry import CharityRegistry


class CharitaoMiner:

    def __init__(self, uid: int = 0, address: str = None,
                 wallet_name: str = 'miner', hotkey: str = 'default',
                 netuid: int = None, network: str = 'finney', local: bool = True,
                 charity: str = None, chain=None, registry: CharityRegistry = None):
        self.uid = int(uid)
        self.address = address or f'miner://{uid}'
        self.wallet_name = wallet_name
        self.hotkey = hotkey
        self.netuid = netuid
        self.network = network
        self.local = local
        self.charity = charity  # default charity id; None = first verified
        self.chain = chain if chain is not None else (MockChain() if local else None)
        self.registry = registry or CharityRegistry()

    def _pick_charity(self, charity_id: Optional[str] = None) -> dict:
        cid = charity_id or self.charity
        if cid:
            c = self.registry.get(cid)
            if c is None:
                raise ValueError(f'no charity {cid!r}')
            if not c['verified']:
                raise ValueError(f'charity {cid!r} is not verified — donation would not be credited')
            return c
        verified = self.registry.charities(verified_only=True)
        if not verified:
            raise ValueError('no verified charities in the registry')
        return verified[0]

    def donate(self, amount: float, charity_id: str = None) -> dict:
        """Send a donation. This IS the mining work."""
        charity = self._pick_charity(charity_id)
        if not self.local:
            return {
                'error': 'live-network donations are sent from your coldkey wallet',
                'how': f"btcli wallet transfer --dest {charity['address']} "
                       f"--amount {amount} --wallet.name {self.wallet_name}",
                'charity': charity['id'],
            }
        tx = self.chain.transfer(self.address, charity['address'], float(amount))
        return {'txid': tx.txid, 'block': tx.block, 'charity': charity['id'],
                'amount': tx.amount, 'from': self.address}

    def suggest_donation(self, epoch_emission: float, coverage_ratio: float = 0.8,
                         expected_donors: int = 1) -> dict:
        """Largest donation that settles in one epoch given your budget share."""
        budget = float(epoch_emission) * float(coverage_ratio)
        share = budget / max(1, int(expected_donors))
        return {
            'suggested': round(share, 8),
            'epoch_budget': round(budget, 8),
            'expected_donors': expected_donors,
            'payout_if_credited': round(share / float(coverage_ratio), 8),
            'margin_pct': round((1.0 / float(coverage_ratio) - 1.0) * 100, 4),
        }
