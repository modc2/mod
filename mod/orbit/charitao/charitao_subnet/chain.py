"""Chain adapters: where donations actually happen and get verified.

MockChain      — local JSON ledger of transfers; powers simulation and tests.
SubtensorChain — best-effort adapter for a live network. Verifying transfers
                 requires an archive node or indexer (e.g. taostats); scan()
                 raises with guidance unless a custom indexer fn is provided.
"""
import hashlib
import json
import os
import time
from typing import Callable, List, Optional

from .schemas import Transfer

MOCKCHAIN_PATH = '~/.mod/charitao/mockchain.json'


class MockChain:
    """Append-only transfer ledger persisted to a JSON file."""

    def __init__(self, path: str = MOCKCHAIN_PATH):
        self.path = os.path.expanduser(path)
        self._ledger: List[dict] = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self._ledger = json.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self._ledger, f, indent=2)

    @property
    def height(self) -> int:
        return len(self._ledger)

    def transfer(self, sender: str, recipient: str, amount: float) -> Transfer:
        amount = float(amount)
        if amount <= 0:
            raise ValueError('amount must be > 0')
        block = self.height + 1
        txid = hashlib.sha256(
            f'{block}:{sender}:{recipient}:{amount}'.encode()
        ).hexdigest()[:16]
        tx = Transfer(
            txid=txid, sender=sender, recipient=recipient,
            amount=amount, block=block, at=int(time.time()),
        )
        self._ledger.append(tx.to_dict())
        self._save()
        return tx

    def scan(self, since_block: int = 0) -> List[Transfer]:
        """All transfers with block > since_block."""
        return [Transfer(**t) for t in self._ledger if t['block'] > since_block]

    def reset(self):
        self._ledger = []
        self._save()


class SubtensorChain:
    """Live-network adapter.

    Balance transfers are not queryable from a plain RPC endpoint without an
    archive node or indexer, so scan() delegates to a pluggable `indexer`
    callable: (since_block) -> list[Transfer]. Wire one up against taostats,
    a subquery instance, or your own archive-node event scan.
    """

    def __init__(self, network: str = 'finney',
                 indexer: Optional[Callable[[int], List[Transfer]]] = None):
        self.network = network
        self.indexer = indexer
        self._subtensor = None

    @property
    def subtensor(self):
        if self._subtensor is None:
            import bittensor as bt
            self._subtensor = bt.Subtensor(network=self.network)
        return self._subtensor

    @property
    def height(self) -> int:
        return int(self.subtensor.get_current_block())

    def transfer(self, sender: str, recipient: str, amount: float) -> Transfer:
        raise NotImplementedError(
            'On a live network, donate directly from the miner coldkey wallet '
            '(btcli wallet transfer). The validator verifies it via scan().'
        )

    def scan(self, since_block: int = 0) -> List[Transfer]:
        if self.indexer is None:
            raise NotImplementedError(
                'SubtensorChain.scan needs an indexer callable '
                '(since_block -> list[Transfer]); plain RPC nodes cannot '
                'enumerate historical balance transfers.'
            )
        return self.indexer(since_block)
