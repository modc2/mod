"""Proof-of-donation incentive with a guaranteed, predictable profit margin.

The invariant: every credited TAO donated earns exactly 1/coverage_ratio TAO
of miner emission. With coverage_ratio = 0.8, donating 1 TAO returns 1.25 TAO
— a fixed 25% margin, enforced two ways:

  1. Budget cap.  Per epoch, at most `budget = epoch_emission * coverage_ratio`
     TAO of donations is credited. Weights are `credited_i / budget`, so
     payout_i = weight_i * epoch_emission = credited_i / coverage_ratio.
     Donations beyond the budget are never diluted — they queue.

  2. Burn weight.  If donations under-fill the budget, the residual weight
     (1 - sum(w_i)) goes to `burn_uid` instead of being renormalized across
     miners. Without this, an epoch with one small donor would pay them the
     whole emission and the "margin" would be a lottery, not a rate.

The carryover queue is FIFO by epoch and pro-rata within an epoch: if this
epoch's batch exceeds the remaining budget, everyone in the batch is credited
the same fraction and the remainder stays queued. Nothing donated is ever
lost — oversubscribed epochs just settle over the following epochs at the
same fixed rate.

DAO weighting: a donation may carry a per-donation `rate` (payout multiplier,
default 1/coverage_ratio) set from the DAO's charity weights and amount
tiers. Settlement then caps by *payout cost* — crediting x at rate r consumes
x*r of the epoch emission — so differently-priced donations share one epoch
without ever paying out more than the emission. With every rate at the base
1/coverage_ratio this reduces exactly to the math above.
"""
import json
import os
from typing import Dict, List


class DonationIncentive:

    def __init__(self, coverage_ratio: float = 0.8, burn_uid: int = 0,
                 min_donation: float = 0.001):
        if not 0 < coverage_ratio <= 1:
            raise ValueError('coverage_ratio must be in (0, 1]')
        self.coverage_ratio = coverage_ratio
        self.burn_uid = burn_uid
        self.min_donation = min_donation

        self.epoch = 0
        # FIFO queue of not-yet-credited donation value:
        # [{'epoch': int, 'uid': int, 'amount': float, 'rate': float}]
        self.queue: List[dict] = []
        self.processed_txids: List[str] = []
        # uid -> {'donated', 'credited', 'projected_payout'}
        self.totals: Dict[int, Dict[str, float]] = {}

    @property
    def base_rate(self) -> float:
        """Payout multiplier at the base coverage ratio (1/rho)."""
        return 1.0 / self.coverage_ratio

    # ── settlement ───────────────────────────────────────────────

    def settle(self, donations: List[dict], epoch_emission: float) -> dict:
        """Run one epoch of settlement.

        donations: [{'uid': int, 'amount': float, 'txid': str, 'rate': float?}]
        — already verified against the whitelist and miner bindings by the
        validator; `rate` (payout multiplier) comes from the DAO weighting and
        defaults to the base 1/coverage_ratio. Returns weights (including the
        burn weight) and per-miner payouts.
        """
        self.epoch += 1
        epoch_emission = float(epoch_emission)
        budget = epoch_emission * self.coverage_ratio

        for d in donations:
            if d['txid'] in self.processed_txids:
                continue
            if d['amount'] < self.min_donation:
                continue
            self.processed_txids.append(d['txid'])
            self.queue.append({'epoch': self.epoch, 'uid': int(d['uid']),
                               'amount': float(d['amount']),
                               'rate': float(d.get('rate', self.base_rate))})
            t = self.totals.setdefault(int(d['uid']),
                                       {'donated': 0.0, 'credited': 0.0,
                                        'projected_payout': 0.0})
            t['donated'] += float(d['amount'])

        credited, paid = self._drain_queue(epoch_emission)

        weights: Dict[str, float] = {}
        payouts: Dict[str, float] = {}
        for uid, amount in credited.items():
            payout = paid[uid]
            w = payout / epoch_emission if epoch_emission > 0 else 0.0
            weights[str(uid)] = round(w, 8)
            payouts[str(uid)] = round(payout, 8)
            t = self.totals[uid]
            t['credited'] += amount
            t['projected_payout'] += payout

        burn_weight = max(0.0, 1.0 - sum(weights.values()))
        if burn_weight > 1e-9:
            weights[str(self.burn_uid)] = round(
                weights.get(str(self.burn_uid), 0.0) + burn_weight, 8)

        return {
            'epoch': self.epoch,
            'epoch_emission': epoch_emission,
            'budget': round(budget, 8),
            'credited': {str(k): round(v, 8) for k, v in credited.items()},
            'carryover': round(sum(q['amount'] for q in self.queue), 8),
            'weights': weights,
            'payouts': payouts,
            'payout_per_tao': round(self.base_rate, 8),
        }

    def _drain_queue(self, payout_budget: float):
        """Credit queued donations FIFO by epoch, pro-rata within an epoch.

        The cap is on payout cost: crediting x at rate r consumes x*r of the
        epoch emission. Returns (credited amount, payout) per uid.
        """
        credited: Dict[int, float] = {}
        paid: Dict[int, float] = {}
        remaining = payout_budget
        still_queued: List[dict] = []

        epochs = sorted({q['epoch'] for q in self.queue})
        for ep in epochs:
            batch = [q for q in self.queue if q['epoch'] == ep]
            batch_cost = sum(q['amount'] * q.get('rate', self.base_rate)
                             for q in batch)
            if remaining <= 1e-12:
                still_queued.extend(batch)
                continue
            scale = min(1.0, remaining / batch_cost) if batch_cost > 0 else 0.0
            for q in batch:
                rate = q.get('rate', self.base_rate)
                credit = q['amount'] * scale
                if credit > 0:
                    credited[q['uid']] = credited.get(q['uid'], 0.0) + credit
                    paid[q['uid']] = paid.get(q['uid'], 0.0) + credit * rate
                leftover = q['amount'] - credit
                if leftover > 1e-12:
                    still_queued.append({**q, 'amount': leftover})
            remaining -= batch_cost * scale

        self.queue = still_queued
        return credited, paid

    # ── reporting ────────────────────────────────────────────────

    def leaderboard(self) -> List[dict]:
        rows = []
        for uid, t in self.totals.items():
            rows.append({
                'uid': uid,
                'donated': round(t['donated'], 8),
                'credited': round(t['credited'], 8),
                'pending': round(t['donated'] - t['credited'], 8),
                'projected_payout': round(t['projected_payout'], 8),
                'projected_profit': round(t['projected_payout'] - t['credited'], 8),
            })
        rows.sort(key=lambda r: r['donated'], reverse=True)
        return rows

    def projection(self, donation: float, epoch_emission: float,
                   rate: float = None) -> dict:
        """What a miner gets back for donating `donation` TAO, and how fast.

        rate: DAO-weighted payout multiplier; defaults to the base
        1/coverage_ratio."""
        donation = float(donation)
        rate = float(rate) if rate is not None else self.base_rate
        # donation capacity per epoch at this rate (payout cost = emission)
        budget = float(epoch_emission) / rate if rate > 0 else 0.0
        payout = donation * rate
        epochs_to_settle = 1 if budget <= 0 else max(
            1, -(-int(donation * 1e9) // max(1, int(budget * 1e9))))
        return {
            'donation': donation,
            'payout': round(payout, 8),
            'profit': round(payout - donation, 8),
            'margin_pct': round((rate - 1.0) * 100, 4),
            'epochs_to_settle': epochs_to_settle,
            'note': 'payout is emission earned; assumes sole use of the budget '
                    'for epochs_to_settle (shared budgets settle pro-rata over '
                    'more epochs at the same rate)',
        }

    # ── persistence ──────────────────────────────────────────────

    def state_dict(self) -> dict:
        return {
            'coverage_ratio': self.coverage_ratio,
            'burn_uid': self.burn_uid,
            'min_donation': self.min_donation,
            'epoch': self.epoch,
            'queue': self.queue,
            'processed_txids': self.processed_txids,
            'totals': {str(k): v for k, v in self.totals.items()},
        }

    @classmethod
    def from_state_dict(cls, state: dict) -> 'DonationIncentive':
        im = cls(coverage_ratio=state.get('coverage_ratio', 0.8),
                 burn_uid=state.get('burn_uid', 0),
                 min_donation=state.get('min_donation', 0.001))
        im.epoch = state.get('epoch', 0)
        im.queue = state.get('queue', [])
        im.processed_txids = state.get('processed_txids', [])
        im.totals = {int(k): v for k, v in state.get('totals', {}).items()}
        return im

    def save(self, path: str):
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.state_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str, **defaults) -> 'DonationIncentive':
        path = os.path.expanduser(path)
        if os.path.exists(path):
            with open(path) as f:
                return cls.from_state_dict(json.load(f))
        return cls(**defaults)
