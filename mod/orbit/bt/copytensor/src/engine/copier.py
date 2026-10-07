"""
Copy trade engine — mirrors a set of traders, each on its own τ sleeve.

Flow (one pass over the WHOLE portfolio, not one pass per trader):
1. Read every active copy's target book -> its shape (share per subnet)
2. Size each shape by that copy's `alloc_tao` and blend them into one
   desired book  (engine/allocator.py)
3. Diff the blend against what we actually hold
4. Apply safety checks
5. Execute unstakes first (frees up TAO), then stakes
6. Log every transaction with the sleeves that paid for it

The per-copy loop this replaced drove our ENTIRE book to one target's
percentages, so a second live copy just undid the first one every poll. See
allocator.py for the model and why absolute τ is the unit.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import bittensor as bt

from ..chain.client import AccountPositions, SubtensorClient
from ..db import Database
from .allocator import Plan, PlanRow, Sleeve, plan as build_plan, shares_of
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
    # The money behind this trader. This — not the weight, and not the daily
    # spend cap — is what decides how much of the book they get.
    alloc_tao: float = 0.0
    rebalance_threshold_pct: float = 5.0
    poll_interval_sec: int = 300
    label: Optional[str] = None


class CopyEngine:
    """Mirrors a SET of traders, each sized by its own τ sleeve."""

    def __init__(self, client: SubtensorClient, db: Database,
                 safety: SafetyManager):
        self.client = client
        self.db = db
        self.safety = safety
        self._wallet: Optional[bt.Wallet] = None
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._portfolio_task: Optional[asyncio.Task] = None
        self._sync_lock = threading.Lock()
        self._last_plan: Optional[Plan] = None
        self._last_pass_at: float = 0.0
        self._last_error: Optional[str] = None

    def set_wallet(self, wallet: bt.Wallet):
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

    # ── the portfolio pass ───────────────────────────────────────────
    #
    # One pass covers every active copy at once. That is not an optimisation:
    # sleeves only compose if they are diffed against the book together.

    def copy_configs(self) -> List[CopyConfig]:
        """Active copies from the DB, as CopyConfigs. The DB is the source of
        truth every pass, so adding, re-sizing or pausing a copy takes effect
        on the next tick without touching the loop."""
        out: List[CopyConfig] = []
        for row in self.db.list_copies(status="active"):
            cfg = row.get("config") or {}
            out.append(CopyConfig(
                id=row["id"],
                target_ss58=row["target_ss58"],
                our_hotkey=cfg.get("our_hotkey", ""),
                alloc_tao=float(cfg.get("alloc_tao") or 0.0),
                rebalance_threshold_pct=float(
                    cfg.get("rebalance_threshold_pct") or 5.0),
                poll_interval_sec=int(cfg.get("poll_interval_sec") or 300),
                label=row.get("label"),
            ))
        return out

    def build_sleeves(self, copies: List[CopyConfig]) -> List[Sleeve]:
        """Read each target's book and turn it into a sized sleeve.

        A target we can't read is marked `stale` and held at its current
        weight rather than dropped — a failed RPC read must never look like
        "they sold everything" and trigger a liquidation.
        """
        sleeves: List[Sleeve] = []
        for c in copies:
            sleeve = Sleeve(copy_id=c.id, target_ss58=c.target_ss58,
                            alloc_tao=c.alloc_tao, label=c.label)
            try:
                positions = self.client.get_stake_for_coldkey(c.target_ss58)
                sleeve.shares = shares_of(positions)
                if not sleeve.shares:
                    sleeve.error = "target holds no stake"
            except Exception as e:
                sleeve.stale = True
                sleeve.error = str(e)
                log.warning("sleeve %s: target %s unreadable: %s",
                            c.id, c.target_ss58[:8], e)
            sleeves.append(sleeve)
        return sleeves

    def our_book(self) -> Dict[str, object]:
        """What we hold: τ per subnet, free balance, and the coldkey."""
        our_ss58 = self._wallet.coldkey.ss58_address if self._wallet else None
        if not our_ss58:
            return {"ss58": None, "by_netuid": {}, "free_tao": 0.0, "staked_tao": 0.0}
        positions = self.client.get_stake_for_coldkey(our_ss58)
        by_netuid: Dict[int, float] = {}
        for p in positions.positions:
            by_netuid[p.netuid] = by_netuid.get(p.netuid, 0.0) + p.value_tao
        free = 0.0
        try:
            free = float(self.client.get_balance(our_ss58))
        except Exception as e:
            log.warning("balance read failed: %s", e)
        return {"ss58": our_ss58, "by_netuid": by_netuid, "free_tao": free,
                "staked_tao": sum(by_netuid.values())}

    def plan_portfolio(self, copies: Optional[List[CopyConfig]] = None) -> Plan:
        """Blend every active sleeve and diff it against our book. Pure read —
        this is exactly what `sync_portfolio` will execute, which is what makes
        it usable as a dry run."""
        copies = self.copy_configs() if copies is None else copies
        sleeves = self.build_sleeves(copies)
        book = self.our_book()

        # The tightest threshold among the copies wins: the portfolio has one
        # drift band, and honouring the fussiest sleeve is the safe rounding.
        thresholds = [c.rebalance_threshold_pct for c in copies
                      if c.rebalance_threshold_pct > 0]
        threshold = min(thresholds) if thresholds else 5.0

        plan = build_plan(
            sleeves,
            current=book["by_netuid"],
            free_tao=book["free_tao"],
            threshold_pct=threshold,
            min_balance_tao=self.safety.min_balance_tao,
            max_subnets=self.safety.max_subnets,
            subnet_allowlist=self.safety.subnet_allowlist,
            subnet_denylist=self.safety.subnet_denylist,
        )
        if not book["ss58"]:
            plan.notes.append("no wallet set — plan is a preview only")
        self._last_plan = plan
        return plan

    def sync_portfolio(self) -> List[TradeResult]:
        """Execute one portfolio pass. Serialized: two overlapping passes
        would each see the other's half-applied book and double-trade."""
        if not self._wallet:
            raise RuntimeError("wallet not set — call set_wallet() first")
        if not self._sync_lock.acquire(blocking=False):
            log.info("portfolio sync already running, skipping this tick")
            return []
        try:
            return self._sync_portfolio_locked()
        finally:
            self._sync_lock.release()

    def _sync_portfolio_locked(self) -> List[TradeResult]:
        copies = self.copy_configs()
        self._last_pass_at = time.time()
        if not copies:
            log.info("portfolio: no active copies")
            return []

        plan = self.plan_portfolio(copies)
        if plan.blocked:
            log.warning("portfolio pass held: %s", plan.blocked)
            return []
        movers = plan.trades
        if not movers:
            log.info("portfolio: %d sleeve(s), %.4fτ target, no rebalance needed",
                     len(copies), sum(plan.desired.values()))
            return []

        balance = self.our_book()["free_tao"]
        # Safety still guards execution (per-tx cap, daily spend, cooldown),
        # but it no longer decides SIZE — the sleeves do.
        deltas = self.safety.validate(
            [Delta(netuid=r.netuid, action=r.action, amount_tao=r.amount_tao,
                   pct_change=0.0, reason=r.reason) for r in movers],
            balance)
        by_netuid = {r.netuid: r for r in movers}

        trades: List[TradeResult] = []
        now = datetime.now(timezone.utc).isoformat()
        block = self.client.get_block()
        hotkey = self._portfolio_hotkey(copies)

        # Unstakes first — they free the TAO the stakes are about to spend.
        for phase in ("unstake", "stake"):
            for d in sorted(deltas, key=lambda x: x.amount_tao, reverse=True):
                if d.action != phase:
                    continue
                row = by_netuid.get(d.netuid)
                trades.append(self._execute_row(d, row, hotkey, block, now))

        for c in copies:
            self.db.update_copy(c.id, last_sync_block=block)

        log.info("portfolio: %d sleeve(s) -> %d trade(s) at block %d",
                 len(copies), len(trades), block)
        return trades

    def _portfolio_hotkey(self, copies: List[CopyConfig]) -> str:
        """Our staking hotkey. Every copy stakes from the same wallet, so the
        first one that names a hotkey wins; the wallet's own is the fallback."""
        for c in copies:
            if c.our_hotkey:
                return c.our_hotkey
        if self._wallet:
            try:
                return self._wallet.hotkey.ss58_address
            except Exception:
                pass
        raise RuntimeError("no hotkey to stake from")

    def _execute_row(self, delta: Delta, row: Optional[PlanRow], hotkey: str,
                     block: int, timestamp: str) -> TradeResult:
        """Execute one blended move and file it under the sleeve that paid
        the most for it, with the full split alongside."""
        contributors = dict(row.contributors) if row else {}
        # Safety may have shrunk the move; scale the split so the receipt adds
        # up to what actually went out rather than to what was planned.
        if row and row.amount_tao > 0 and contributors:
            factor = delta.amount_tao / row.amount_tao
            contributors = {k: round(v * factor, 6) for k, v in contributors.items()}
        # A full exit has no contributors — nobody wants that subnet any more,
        # which is precisely why we're selling it.
        owner = max(contributors, key=contributors.get) if contributors else "portfolio"

        trade_id = self.db.insert_trade(
            copy_id=owner, block=block, timestamp=timestamp,
            action=delta.action, netuid=delta.netuid,
            amount_tao=delta.amount_tao, status="pending",
            contributors=contributors or None,
        )
        try:
            fn = self.client.stake if delta.action == "stake" else self.client.unstake
            result = fn(self._wallet, hotkey, delta.netuid, delta.amount_tao)
            self.db.update_trade(trade_id, status="confirmed", tx_hash=result)
            self.safety.record_trade(delta.amount_tao)
            return TradeResult(action=delta.action, netuid=delta.netuid,
                               amount_tao=delta.amount_tao, status="confirmed",
                               tx_hash=result)
        except Exception as e:
            error = str(e)
            self.db.update_trade(trade_id, status="failed", error=error)
            log.error("trade failed SN%d %s %.4f: %s",
                      delta.netuid, delta.action, delta.amount_tao, error)
            return TradeResult(action=delta.action, netuid=delta.netuid,
                               amount_tao=delta.amount_tao, status="failed",
                               error=error)

    def sync_once(self, copy_config: Optional[CopyConfig] = None) -> List[TradeResult]:
        """Kept for callers that used to sync one copy. Syncing a single
        sleeve in isolation is exactly the bug this engine was rewritten to
        fix, so it runs the whole portfolio — which is the only way that
        copy's allocation can be right."""
        return self.sync_portfolio()

    # ── the loop ─────────────────────────────────────────────────────

    def poll_interval(self) -> int:
        intervals = [c.poll_interval_sec for c in self.copy_configs()
                     if c.poll_interval_sec > 0]
        return min(intervals) if intervals else 300

    async def run_portfolio_loop(self):
        """One loop for the whole book, re-reading active copies every tick."""
        log.info("portfolio loop started")
        while True:
            interval = 300
            try:
                interval = self.poll_interval()
                if self._wallet and self.copy_configs():
                    await asyncio.to_thread(self.sync_portfolio)
                    self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._last_error = str(e)
                log.error("portfolio loop error: %s", e)
            await asyncio.sleep(max(30, interval))

    def start_portfolio(self):
        """Idempotent — safe to call on every /copy create."""
        if self._portfolio_task and not self._portfolio_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._portfolio_task = loop.create_task(self.run_portfolio_loop())

    def stop_portfolio(self):
        if self._portfolio_task:
            self._portfolio_task.cancel()
            self._portfolio_task = None

    # Old per-copy lifecycle. Starting a loop per copy is what made two
    # copies fight, so these now just make sure the single portfolio loop is
    # up; membership comes from the DB.
    def start_copy(self, copy_config: Optional[CopyConfig] = None):
        self.start_portfolio()

    def stop_copy(self, copy_id: str):
        return

    def status(self) -> Dict[str, object]:
        copies = self.copy_configs()
        return {
            "running": bool(self._portfolio_task and not self._portfolio_task.done()),
            "sleeves": len(copies),
            "allocated_tao": round(sum(c.alloc_tao for c in copies), 6),
            "poll_interval_sec": self.poll_interval(),
            "last_pass_at": self._last_pass_at or None,
            "last_error": self._last_error,
            "wallet_set": self._wallet is not None,
        }
