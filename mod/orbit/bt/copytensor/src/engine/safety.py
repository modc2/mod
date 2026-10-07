"""
Safety limits — enforces trading guardrails for copy trade execution.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("copytensor.safety")


@dataclass
class Delta:
    netuid: int
    action: str  # "stake" or "unstake"
    amount_tao: float
    pct_change: float
    reason: str = ""


class SafetyManager:
    """Enforces safety limits on copy trade execution."""

    def __init__(self, config: Dict):
        self.max_tao_per_tx = config.get("max_tao_per_tx", 10.0)
        self.daily_limit_tao = config.get("daily_limit_tao", 100.0)
        self.min_balance_tao = config.get("min_balance_tao", 1.0)
        self.subnet_allowlist = config.get("subnet_allowlist")  # None = all
        self.subnet_denylist = config.get("subnet_denylist", [])
        self.cooldown_sec = config.get("cooldown_sec", 60)
        self.max_subnets = config.get("max_subnets", 20)

        self._daily_spent = 0.0
        self._daily_reset_at = 0.0
        self._last_tx_time = 0.0

    def validate(self, deltas: List[Delta], balance_tao: float) -> List[Delta]:
        """Filter and cap deltas through all safety checks."""
        self._maybe_reset_daily()
        safe: List[Delta] = []

        for d in deltas:
            # Subnet allowlist/denylist
            if self.subnet_allowlist and d.netuid not in self.subnet_allowlist:
                log.debug("skip SN%d: not in allowlist", d.netuid)
                continue
            if d.netuid in self.subnet_denylist:
                log.debug("skip SN%d: in denylist", d.netuid)
                continue

            # Cap per-transaction amount
            if abs(d.amount_tao) > self.max_tao_per_tx:
                d.amount_tao = self.max_tao_per_tx * (1 if d.amount_tao > 0 else -1)

            # Daily limit
            if self._daily_spent + abs(d.amount_tao) > self.daily_limit_tao:
                remaining = self.daily_limit_tao - self._daily_spent
                if remaining <= 0:
                    log.warning("daily limit reached (%.2f TAO)", self.daily_limit_tao)
                    continue
                d.amount_tao = remaining * (1 if d.amount_tao > 0 else -1)

            # Min balance check (for stakes only)
            if d.action == "stake":
                if balance_tao - abs(d.amount_tao) < self.min_balance_tao:
                    available = balance_tao - self.min_balance_tao
                    if available <= 0:
                        log.warning("min balance guard: %.2f TAO remaining", balance_tao)
                        continue
                    d.amount_tao = available

            # Cooldown
            now = time.time()
            if now - self._last_tx_time < self.cooldown_sec:
                log.debug("cooldown: %ds remaining",
                          self.cooldown_sec - (now - self._last_tx_time))
                continue

            safe.append(d)

        # Max subnets cap
        if len(safe) > self.max_subnets:
            safe = safe[:self.max_subnets]

        return safe

    def record_trade(self, amount_tao: float):
        """Record a completed trade for daily tracking."""
        self._daily_spent += abs(amount_tao)
        self._last_tx_time = time.time()

    def _maybe_reset_daily(self):
        now = time.time()
        if now - self._daily_reset_at > 86400:
            self._daily_spent = 0.0
            self._daily_reset_at = now

    def get_limits(self) -> Dict:
        return {
            "max_tao_per_tx": self.max_tao_per_tx,
            "daily_limit_tao": self.daily_limit_tao,
            "daily_spent_tao": self._daily_spent,
            "daily_remaining_tao": max(0, self.daily_limit_tao - self._daily_spent),
            "min_balance_tao": self.min_balance_tao,
            "cooldown_sec": self.cooldown_sec,
            "max_subnets": self.max_subnets,
            "subnet_allowlist": self.subnet_allowlist,
            "subnet_denylist": self.subnet_denylist,
        }
