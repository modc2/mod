"""DAO governance: admin multisig today, token (bloctime) power tomorrow.

Everything that changes what the subnet pays for goes through a proposal:
whitelisting/removing/verifying charities, setting a charity's DAO weight,
the amount tiers, and the admin set itself. A proposal executes only when
its approvals reach the passing bar of the active power source:

  multisig — every admin has power 1; a proposal passes at `threshold`
             distinct admin approvals (M-of-N).
  token    — voting power is a token balance (designed for bloctime stake);
             a proposal passes when approving power >= quorum_pct% of total
             power. Swap sources with a `set_power_source` proposal — the
             admin multisig votes itself into token governance, no redeploy.

The DAO weighting is what prices donations: each charity carries a weight
(default 1.0) and the APR a donated amount earns is

    apr(charity, amount) = base_margin * (weight / mean_weight) * tier(amount)

normalized against the mean weight of the whitelist, so an all-default DAO
pays exactly the base coverage-ratio margin, up-weighted causes pay more,
down-weighted causes pay less, and a weight of 0 refunds donations at 0% APR
without ever paying below what was donated.

Bootstrap: a fresh install has no admins ("open mode" — curation is direct,
the local sim just works). `init(admins, threshold)` locks it down; from then
on every mutation needs signatures. Signer identity here is an address
string; live deployments verify ownership at the transport layer (signed
requests / wallet auth), which stays out of consensus state by design.
"""
import json
import os
import time
from typing import Callable, Dict, List, Optional

GOVERNANCE_PATH = '~/.mod/charitao/governance.json'

# actions the DAO can vote on. Registry actions are applied through the
# executor callback (wired to CharityRegistry by mod.py); the rest mutate
# governance state itself.
REGISTRY_ACTIONS = ('add_charity', 'remove_charity', 'verify_charity', 'flag_charity')
INTERNAL_ACTIONS = ('set_weight', 'set_amount_tiers', 'add_admin', 'remove_admin',
                    'set_threshold', 'set_power_source', 'set_token_balances')
ACTIONS = REGISTRY_ACTIONS + INTERNAL_ACTIONS


class Governance:

    def __init__(self, path: str = GOVERNANCE_PATH,
                 executor: Optional[Callable[[str, dict], dict]] = None):
        self.path = os.path.expanduser(path)
        self.executor = executor
        self.admins: List[str] = []
        self.threshold: int = 1
        self.power_source: dict = {'type': 'multisig'}
        self.token_balances: Dict[str, float] = {}
        self.weights: Dict[str, float] = {}          # charity_id -> DAO weight
        self.amount_tiers: List[dict] = [{'min': 0.0, 'mult': 1.0}]
        self.proposals: List[dict] = []
        self._load()

    # ── persistence ──────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                st = json.load(f)
            self.admins = st.get('admins', [])
            self.threshold = int(st.get('threshold', 1))
            self.power_source = st.get('power_source', {'type': 'multisig'})
            self.token_balances = st.get('token_balances', {})
            self.weights = st.get('weights', {})
            self.amount_tiers = st.get('amount_tiers', [{'min': 0.0, 'mult': 1.0}])
            self.proposals = st.get('proposals', [])

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump({
                'admins': self.admins,
                'threshold': self.threshold,
                'power_source': self.power_source,
                'token_balances': self.token_balances,
                'weights': self.weights,
                'amount_tiers': self.amount_tiers,
                'proposals': self.proposals,
            }, f, indent=2)

    # ── membership & power ───────────────────────────────────────

    @property
    def initialized(self) -> bool:
        return bool(self.admins)

    def init(self, admins: List[str], threshold: int = None) -> dict:
        """Bootstrap the DAO. Only allowed once — afterwards the admin set
        changes via add_admin / remove_admin proposals."""
        if self.initialized:
            return {'error': 'DAO already initialized — change admins via proposals'}
        admins = [a.strip() for a in admins if a and a.strip()]
        if not admins:
            return {'error': 'at least one admin required'}
        self.admins = list(dict.fromkeys(admins))
        self.threshold = self._clamp_threshold(
            int(threshold) if threshold is not None else (len(self.admins) // 2 + 1))
        self._save()
        return self.status()

    def _clamp_threshold(self, t: int) -> int:
        return max(1, min(int(t), len(self.admins) or 1))

    def power_of(self, signer: str) -> float:
        if self.power_source.get('type') == 'token':
            return float(self.token_balances.get(signer, 0.0))
        return 1.0 if signer in self.admins else 0.0

    def total_power(self) -> float:
        if self.power_source.get('type') == 'token':
            return sum(float(v) for v in self.token_balances.values())
        return float(len(self.admins))

    def passing_power(self) -> float:
        """Approving power needed for a proposal to execute."""
        if self.power_source.get('type') == 'token':
            quorum = float(self.power_source.get('quorum_pct', 51.0))
            return self.total_power() * quorum / 100.0
        return float(self.threshold)

    # ── APR pricing (the DAO weighting) ──────────────────────────

    def weight_of(self, charity_id: str) -> float:
        return float(self.weights.get(charity_id, 1.0))

    def tier_mult(self, amount: float) -> float:
        mult = 1.0
        for t in sorted(self.amount_tiers, key=lambda t: float(t.get('min', 0))):
            if float(amount) >= float(t.get('min', 0)):
                mult = float(t.get('mult', 1.0))
        return mult

    def apr_pct(self, charity_id: str, amount: float, base_margin_pct: float,
                charity_ids: Optional[List[str]] = None) -> float:
        """APR this donated amount earns, from the DAO weighting.

        Normalized against the mean weight of `charity_ids` (the whitelist),
        so an all-default DAO pays exactly the base margin. Never below 0.
        """
        ids = list(charity_ids) if charity_ids else list(self.weights) or [charity_id]
        mean = sum(self.weight_of(c) for c in ids) / len(ids)
        norm = self.weight_of(charity_id) / mean if mean > 0 else 1.0
        return max(0.0, float(base_margin_pct) * norm * self.tier_mult(amount))

    def rate(self, charity_id: str, amount: float, base_rate: float,
             charity_ids: Optional[List[str]] = None) -> float:
        """Payout multiplier (1 + apr) for the incentive engine."""
        base_margin_pct = (float(base_rate) - 1.0) * 100.0
        return 1.0 + self.apr_pct(charity_id, amount, base_margin_pct, charity_ids) / 100.0

    # ── proposals ────────────────────────────────────────────────

    def direct(self, action: str, params: dict) -> dict:
        """Open-mode (pre-DAO) direct apply — refused once admins exist."""
        if self.initialized:
            return {'error': 'DAO initialized — actions require a proposal'}
        if action not in ACTIONS:
            return {'error': f'unknown action {action!r} (one of {list(ACTIONS)})'}
        r = self._apply(action, params)
        self._save()
        return r

    def propose(self, action: str, params: dict, signer: str) -> dict:
        if action not in ACTIONS:
            return {'error': f'unknown action {action!r} (one of {list(ACTIONS)})'}
        if not self.initialized:
            return {'error': 'DAO not initialized — call init(admins=[...]) first'}
        if self.power_of(signer) <= 0:
            return {'error': f'{signer!r} has no voting power'}
        p = {
            'id': (max((q['id'] for q in self.proposals), default=0) + 1),
            'action': action,
            'params': dict(params or {}),
            'proposer': signer,
            'approvals': [signer],
            'rejections': [],
            'status': 'pending',
            'created_at': int(time.time()),
        }
        self.proposals.append(p)
        self._maybe_execute(p)
        self._save()
        return self.get(p['id'])

    def approve(self, proposal_id: int, signer: str) -> dict:
        return self._vote(proposal_id, signer, approve=True)

    def reject(self, proposal_id: int, signer: str) -> dict:
        return self._vote(proposal_id, signer, approve=False)

    def _vote(self, proposal_id: int, signer: str, approve: bool) -> dict:
        p = self._find(int(proposal_id))
        if p is None:
            return {'error': f'no proposal {proposal_id}'}
        if p['status'] != 'pending':
            return {'error': f"proposal {p['id']} is already {p['status']}"}
        if self.power_of(signer) <= 0:
            return {'error': f'{signer!r} has no voting power'}
        for side in ('approvals', 'rejections'):
            if signer in p[side]:
                p[side].remove(signer)
        p['approvals' if approve else 'rejections'].append(signer)
        # a rejecting majority (power that makes passing impossible) kills it
        if not approve:
            rejecting = sum(self.power_of(a) for a in p['rejections'])
            if self.total_power() - rejecting < self.passing_power():
                p['status'] = 'rejected'
        if approve:
            self._maybe_execute(p)
        self._save()
        return self.get(p['id'])

    def _maybe_execute(self, p: dict):
        approving = sum(self.power_of(a) for a in p['approvals'])
        if approving + 1e-12 < self.passing_power():
            return
        try:
            p['result'] = self._apply(p['action'], p['params'])
            p['status'] = ('failed' if isinstance(p['result'], dict)
                           and p['result'].get('error') else 'executed')
        except Exception as e:
            p['result'] = {'error': str(e)}
            p['status'] = 'failed'
        p['executed_at'] = int(time.time())

    def _apply(self, action: str, params: dict) -> dict:
        if action in REGISTRY_ACTIONS:
            if self.executor is None:
                return {'error': 'no executor wired for registry actions'}
            return self.executor(action, params)
        if action == 'set_weight':
            cid = params['charity']
            self.weights[cid] = max(0.0, float(params['weight']))
            return {'charity': cid, 'weight': self.weights[cid]}
        if action == 'set_amount_tiers':
            tiers = params['tiers']
            if isinstance(tiers, str):
                tiers = json.loads(tiers)
            self.amount_tiers = [{'min': float(t['min']), 'mult': float(t['mult'])}
                                 for t in tiers] or [{'min': 0.0, 'mult': 1.0}]
            return {'amount_tiers': self.amount_tiers}
        if action == 'add_admin':
            a = params['address'].strip()
            if a and a not in self.admins:
                self.admins.append(a)
            return {'admins': self.admins}
        if action == 'remove_admin':
            a = params['address'].strip()
            if a in self.admins:
                if len(self.admins) == 1:
                    return {'error': 'cannot remove the last admin'}
                self.admins.remove(a)
                self.threshold = self._clamp_threshold(self.threshold)
            return {'admins': self.admins, 'threshold': self.threshold}
        if action == 'set_threshold':
            self.threshold = self._clamp_threshold(int(params['threshold']))
            return {'threshold': self.threshold}
        if action == 'set_power_source':
            src = {'type': params.get('type', 'multisig')}
            if src['type'] not in ('multisig', 'token'):
                return {'error': "power source type must be 'multisig' or 'token'"}
            if src['type'] == 'token':
                src['token'] = params.get('token', 'bloctime')
                src['quorum_pct'] = float(params.get('quorum_pct', 51.0))
                balances = params.get('balances')
                if balances:
                    if isinstance(balances, str):
                        balances = json.loads(balances)
                    self.token_balances = {k: float(v) for k, v in balances.items()}
                if not self.token_balances:
                    return {'error': 'token power source needs balances '
                                     '(pass balances= or set_token_balances first)'}
            self.power_source = src
            return {'power_source': self.power_source}
        if action == 'set_token_balances':
            balances = params['balances']
            if isinstance(balances, str):
                balances = json.loads(balances)
            self.token_balances = {k: float(v) for k, v in balances.items()}
            return {'token_balances': self.token_balances}
        return {'error': f'unknown action {action!r}'}

    # ── queries ──────────────────────────────────────────────────

    def _find(self, proposal_id: int) -> Optional[dict]:
        return next((p for p in self.proposals if p['id'] == proposal_id), None)

    def get(self, proposal_id: int) -> Optional[dict]:
        p = self._find(int(proposal_id))
        if p is None:
            return None
        approving = sum(self.power_of(a) for a in p['approvals'])
        return {**p, 'approving_power': round(approving, 8),
                'passing_power': round(self.passing_power(), 8)}

    def list(self, status: Optional[str] = None) -> List[dict]:
        out = [self.get(p['id']) for p in self.proposals]
        if status:
            out = [p for p in out if p['status'] == status]
        return out

    def status(self) -> dict:
        return {
            'initialized': self.initialized,
            'admins': self.admins,
            'threshold': self.threshold,
            'power_source': self.power_source,
            'total_power': round(self.total_power(), 8),
            'passing_power': round(self.passing_power(), 8),
            'token_holders': len(self.token_balances),
            'weights': self.weights,
            'amount_tiers': self.amount_tiers,
            'pending': len([p for p in self.proposals if p['status'] == 'pending']),
            'proposals': len(self.proposals),
        }
