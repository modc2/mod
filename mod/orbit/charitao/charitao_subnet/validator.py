"""Charitao validator: scan → verify → credit → weight.

Each epoch the validator scans the chain for new transfers and credits the
ones that pass every filter:

  1. recipient is a *verified* whitelisted charity address,
  2. sender is a bound miner address (local: registered via add_miner /
     bind_address; live: the coldkey of a registered uid in the metagraph),
  3. txid not already processed (replay-proof),
  4. amount >= min_donation (dust filter).

Credited donations go through DonationIncentive.settle(), which enforces the
coverage-ratio budget, the carryover queue, and the burn weight — see
incentive.py for the profit-guarantee math.
"""
import json
import os
import time
from typing import Dict, List, Optional

from .chain import MockChain
from .governance import Governance
from .incentive import DonationIncentive
from .registry import CharityRegistry

STORAGE_PATH = '~/.mod/charitao'


class CharitaoValidator:

    def __init__(self, netuid: int = None, network: str = 'finney',
                 wallet_name: str = 'validator', hotkey: str = 'default',
                 local: bool = True, tempo: int = 300,
                 coverage_ratio: float = 0.8, burn_uid: int = 0,
                 min_donation: float = 0.001, epoch_emission: float = 1.0,
                 storage_path: str = STORAGE_PATH,
                 chain=None, registry: CharityRegistry = None,
                 governance: Governance = None, **kwargs):
        self.netuid = netuid
        self.network = network
        self.wallet_name = wallet_name
        self.hotkey = hotkey
        self.local = local
        self.tempo = tempo
        self.epoch_emission = float(epoch_emission)
        self.storage_path = os.path.expanduser(storage_path)

        self.chain = chain if chain is not None else (MockChain() if local else None)
        self.registry = registry or CharityRegistry()
        self.governance = governance or Governance(
            path=os.path.join(self.storage_path, 'governance.json'))

        self._incentive_path = os.path.join(self.storage_path, 'incentive_state.json')
        self.incentive = DonationIncentive.load(
            self._incentive_path, coverage_ratio=coverage_ratio,
            burn_uid=burn_uid, min_donation=min_donation)

        self._scan_path = os.path.join(self.storage_path, 'scan_state.json')
        self.last_block = 0
        self.bindings: Dict[str, int] = {}  # sender address -> uid
        self._load_scan_state()

        self.epoch_time = 0.0
        self._wallet = None
        self._subtensor = None

    # ── bindings ─────────────────────────────────────────────────

    def add_miner(self, miner) -> dict:
        """Bind a local miner object's address to its uid."""
        return self.bind_address(miner.address, miner.uid)

    def bind_address(self, address: str, uid: int) -> dict:
        self.bindings[address] = int(uid)
        self._save_scan_state()
        return {'address': address, 'uid': int(uid)}

    def sync_bindings_from_metagraph(self):
        """Live mode: coldkeys of registered uids are the donation senders."""
        import bittensor as bt
        metagraph = self.subtensor.metagraph(netuid=self.netuid)
        self.bindings = {ck: uid for uid, ck in enumerate(metagraph.coldkeys)}
        self._save_scan_state()
        return {'bound': len(self.bindings)}

    # ── epoch ────────────────────────────────────────────────────

    def epoch(self) -> dict:
        t0 = time.time()
        transfers = self.chain.scan(self.last_block)
        addresses = self.registry.addresses()  # verified only

        donations, skipped = [], []
        max_block = self.last_block
        whitelist = list(addresses.values())
        for tx in transfers:
            max_block = max(max_block, tx.block)
            charity_id = addresses.get(tx.recipient)
            uid = self.bindings.get(tx.sender)
            if charity_id is None:
                skipped.append({'txid': tx.txid, 'reason': 'recipient not whitelisted'})
                continue
            if uid is None:
                skipped.append({'txid': tx.txid, 'reason': 'sender not a registered miner'})
                continue
            # DAO weighting prices the donation: rate = 1 + apr(charity, amount)
            rate = self.governance.rate(charity_id, tx.amount,
                                        self.incentive.base_rate, whitelist)
            donations.append({'uid': uid, 'amount': tx.amount, 'txid': tx.txid,
                              'charity_id': charity_id, 'rate': round(rate, 8)})

        result = self.incentive.settle(donations, self.emission_estimate())
        result['donations_seen'] = len(donations)
        result['skipped'] = skipped
        result['scanned_to_block'] = max_block
        result['took_s'] = round(time.time() - t0, 3)

        if not self.local and self.netuid is not None:
            self._set_chain_weights(result['weights'])

        self.last_block = max_block
        self._save_scan_state()
        self.incentive.save(self._incentive_path)
        self._save_epoch(result)
        self.epoch_time = time.time()
        return result

    def emission_estimate(self) -> float:
        """TAO emitted to miners per epoch. Local: configured constant.
        Live: best-effort from subnet emission rate; falls back to config."""
        if self.local:
            return self.epoch_emission
        try:
            info = self.subtensor.subnet(self.netuid)
            # tao_in_emission is per-block; miners get 41% of subnet emission.
            per_block = float(getattr(info, 'tao_in_emission', 0)) * 0.41
            blocks = self.tempo / 12.0  # ~12s block time
            est = per_block * blocks
            return est if est > 0 else self.epoch_emission
        except Exception:
            return self.epoch_emission

    # ── chain weights ────────────────────────────────────────────

    @property
    def subtensor(self):
        if self._subtensor is None:
            import bittensor as bt
            self._subtensor = bt.Subtensor(network=self.network)
        return self._subtensor

    def _set_chain_weights(self, weights: Dict[str, float]):
        import bittensor as bt
        import torch
        if self._wallet is None:
            self._wallet = bt.Wallet(name=self.wallet_name, hotkey=self.hotkey)
        uids = [int(k) for k in weights.keys()]
        vals = list(weights.values())
        self.subtensor.set_weights(
            wallet=self._wallet, netuid=self.netuid,
            uids=torch.tensor(uids, dtype=torch.long),
            weights=torch.tensor(vals, dtype=torch.float32),
            wait_for_inclusion=True,
        )

    # ── persistence ──────────────────────────────────────────────

    def _load_scan_state(self):
        if os.path.exists(self._scan_path):
            with open(self._scan_path) as f:
                st = json.load(f)
            self.last_block = st.get('last_block', 0)
            self.bindings = st.get('bindings', {})

    def _save_scan_state(self):
        os.makedirs(self.storage_path, exist_ok=True)
        with open(self._scan_path, 'w') as f:
            json.dump({'last_block': self.last_block, 'bindings': self.bindings}, f, indent=2)

    def _save_epoch(self, result: dict):
        epoch_dir = os.path.join(self.storage_path, 'epochs')
        os.makedirs(epoch_dir, exist_ok=True)
        with open(os.path.join(epoch_dir, f"epoch_{result['epoch']}.json"), 'w') as f:
            json.dump(result, f, indent=2)

    def results(self, epoch: int = None) -> Optional[dict]:
        if epoch is None:
            epoch = self.incentive.epoch
        path = os.path.join(self.storage_path, 'epochs', f'epoch_{epoch}.json')
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def leaderboard(self) -> List[dict]:
        return self.incentive.leaderboard()

    # ── loop ─────────────────────────────────────────────────────

    def run(self, step_time: int = 10):
        if not self.local:
            self.sync_bindings_from_metagraph()
        print(f'charitao validator | tempo={self.tempo}s | local={self.local} '
              f'| coverage_ratio={self.incentive.coverage_ratio}')
        try:
            while True:
                elapsed = time.time() - self.epoch_time
                if elapsed < self.tempo:
                    time.sleep(min(step_time, self.tempo - elapsed))
                    continue
                try:
                    r = self.epoch()
                    print(f"[epoch {r['epoch']}] {r['donations_seen']} donations, "
                          f"budget {r['budget']}, carryover {r['carryover']}")
                except Exception as e:
                    print(f'  epoch error: {e}')
        except KeyboardInterrupt:
            print('validator stopped')
