"""
Copy trade engine — mirrors a target account's subnet allocations.

Flow:
1. Read target's current allocations (% of total stake per subnet)
2. Read our current allocations
3. Compute deltas (where we're over/underweight vs target)
4. Apply safety checks
5. Execute unstakes first (frees up TAO), then stakes
6. Log all transactions
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import bittensor as bt

from ..chain.client import AccountPositions, SubtensorClient
from ..db import Database
from .safety import Delta, SafetyManager

log = logging.getLogger("copytensor.copier")


@dataclass
class TradeResult:
    action: str
    netuid: int
    amount_tao: float
    status: str  # "confirmed", "failed"
    tx_hash: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CopyConfig:
    id: str
    target_ss58: str
    our_hotkey: str
    rebalance_threshold_pct: float = 5.0
    poll_interval_sec: int = 300


class CopyEngine:
    """Mirrors a target account's subnet allocations."""

    def __init__(self, client: SubtensorClient, db: Database,
                 safety: SafetyManager):
        self.client = client
        self.db = db
        self.safety = safety
        self._wallet: Optional[bt.wallet] = None
        self._running_tasks: Dict[str, asyncio.Task] = {}

    def set_wallet(self, wallet: bt.wallet):
        """Set the wallet for signing transactions. Memory-only."""
        self._wallet = wallet

    def compute_deltas(self, target: AccountPositions,
                       ours: AccountPositions,
                       threshold_pct: float = 5.0) -> List[Delta]:
        """
        Compare target's percentage allocation to ours.
        Returns deltas describing what to change.
        """
        if target.total_value_tao <= 0:
            return []

        # Target allocation percentages
        target_alloc: Dict[int, float] = {}
        for p in target.positions:
            target_alloc[p.netuid] = target_alloc.get(p.netuid, 0) + p.value_tao
        for netuid in target_alloc:
            target_alloc[netuid] = target_alloc[netuid] / target.total_value_tao * 100

        # Our allocation percentages
        our_alloc: Dict[int, float] = {}
        our_total = max(ours.total_value_tao, 0.001)
        for p in ours.positions:
            our_alloc[p.netuid] = our_alloc.get(p.netuid, 0) + p.value_tao
        for netuid in our_alloc:
            our_alloc[netuid] = our_alloc[netuid] / our_total * 100

        # Compute deltas
        all_netuids = set(target_alloc.keys()) | set(our_alloc.keys())
        deltas: List[Delta] = []

        for netuid in all_netuids:
            target_pct = target_alloc.get(netuid, 0)
            our_pct = our_alloc.get(netuid, 0)
            diff = target_pct - our_pct

            if abs(diff) < threshold_pct:
                continue

            # Convert percentage diff to TAO amount
            amount_tao = abs(diff) / 100 * our_total
            if amount_tao < 0.001:
                continue

            action = "stake" if diff > 0 else "unstake"
            deltas.append(Delta(
                netuid=netuid,
                action=action,
                amount_tao=amount_tao,
                pct_change=diff,
                reason=f"target={target_pct:.1f}% ours={our_pct:.1f}%",
            ))

        return deltas

    def sync_once(self, copy_config: CopyConfig) -> List[TradeResult]:
        """Execute one sync cycle for a copy config."""
        if not self._wallet:
            raise RuntimeError("wallet not set — call set_wallet() first")

        our_ss58 = self._wallet.coldkey.ss58_address

        # Get current positions
        target = self.client.get_stake_for_coldkey(copy_config.target_ss58)
        ours = self.client.get_stake_for_coldkey(our_ss58)
        balance = self.client.get_balance(our_ss58)

        # Compute deltas
        deltas = self.compute_deltas(
            target, ours,
            threshold_pct=copy_config.rebalance_threshold_pct,
        )

        if not deltas:
            log.info("copy %s: no rebalance needed", copy_config.id)
            return []

        # Apply safety checks
        deltas = self.safety.validate(deltas, balance)

        trades: List[TradeResult] = []
        now = datetime.now(timezone.utc).isoformat()
        block = self.client.get_block()

        # Phase 1: Unstakes first (frees up TAO)
        for d in sorted(deltas, key=lambda x: x.amount_tao, reverse=True):
            if d.action != "unstake":
                continue
            result = self._execute_trade(d, copy_config, block, now)
            trades.append(result)

        # Phase 2: Stakes
        for d in sorted(deltas, key=lambda x: -x.amount_tao):
            if d.action != "stake":
                continue
            result = self._execute_trade(d, copy_config, block, now)
            trades.append(result)

        # Update last sync block
        self.db.update_copy(copy_config.id, last_sync_block=block)

        log.info("copy %s: executed %d trades", copy_config.id, len(trades))
        return trades

    def _execute_trade(self, delta: Delta, config: CopyConfig,
                       block: int, timestamp: str) -> TradeResult:
        """Execute a single stake/unstake trade."""
        trade_id = self.db.insert_trade(
            copy_id=config.id,
            block=block,
            timestamp=timestamp,
            action=delta.action,
            netuid=delta.netuid,
            amount_tao=delta.amount_tao,
            status="pending",
        )

        try:
            if delta.action == "stake":
                result = self.client.stake(
                    self._wallet, config.our_hotkey,
                    delta.netuid, delta.amount_tao,
                )
            else:
                result = self.client.unstake(
                    self._wallet, config.our_hotkey,
                    delta.netuid, delta.amount_tao,
                )

            self.db.update_trade(trade_id, status="confirmed", tx_hash=result)
            self.safety.record_trade(delta.amount_tao)

            return TradeResult(
                action=delta.action,
                netuid=delta.netuid,
                amount_tao=delta.amount_tao,
                status="confirmed",
                tx_hash=result,
            )
        except Exception as e:
            error = str(e)
            self.db.update_trade(trade_id, status="failed", error=error)
            log.error("trade failed SN%d %s %.4f: %s",
                      delta.netuid, delta.action, delta.amount_tao, error)
            return TradeResult(
                action=delta.action,
                netuid=delta.netuid,
                amount_tao=delta.amount_tao,
                status="failed",
                error=error,
            )

    async def run_poll_loop(self, copy_config: CopyConfig):
        """Background loop: periodically sync positions to match target."""
        log.info("copy loop started for %s (target=%s, interval=%ds)",
                 copy_config.id, copy_config.target_ss58[:8],
                 copy_config.poll_interval_sec)
        while True:
            try:
                # Check if copy is still active
                copy = self.db.get_copy(copy_config.id)
                if not copy or copy["status"] != "active":
                    log.info("copy %s no longer active, stopping loop", copy_config.id)
                    break

                self.sync_once(copy_config)
            except Exception as e:
                log.error("copy loop error %s: %s", copy_config.id, e)
            await asyncio.sleep(copy_config.poll_interval_sec)

    def start_copy(self, copy_config: CopyConfig):
        """Start a background copy loop."""
        if copy_config.id in self._running_tasks:
            task = self._running_tasks[copy_config.id]
            if not task.done():
                return
        loop = asyncio.get_event_loop()
        self._running_tasks[copy_config.id] = loop.create_task(
            self.run_poll_loop(copy_config)
        )

    def stop_copy(self, copy_id: str):
        """Stop a running copy loop."""
        task = self._running_tasks.pop(copy_id, None)
        if task:
            task.cancel()
