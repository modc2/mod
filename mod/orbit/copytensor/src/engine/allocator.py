"""
Portfolio allocator — many traders, one book, an explicit τ sleeve each.

The original engine ran one independent loop per copy, and each loop drove
OUR WHOLE portfolio to its own target's percentages. Two live copies fought
each other every poll: whoever synced last owned the book, and the "weight"
you set per trader never reached execution at all (it only became a daily
spend cap, on a SafetyManager the engine shared globally).

This module replaces that with the model the UI has always implied. Every
copy carries `alloc_tao` — the TAO you assign to that trader. Each trader's
own book gives their SHAPE (what fraction of their stake sits in each
subnet); the sleeve gives the SIZE. Blend them:

    desired[netuid] = Σ_i  alloc_tao_i · share_i(netuid)

Diff that once against the book we actually hold and you get one coherent
trade list. Sleeves compose instead of competing, and "40τ on A, 10τ on B"
means exactly that, whatever the two of them hold.

Everything here is pure: dicts in, plan out. No chain, no wallet, no clock —
so `POST /portfolio/plan` can show you the trades before they happen and the
tests can pin the arithmetic on fixtures.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# A sleeve worth less than this can't be mirrored into a book meaningfully —
# staking dust just burns fees. Sleeves below it are reported, not executed.
MIN_SLEEVE_TAO = 0.05
# Never emit a trade smaller than this, whatever the drift band works out to.
MIN_TRADE_TAO = 0.01


@dataclass
class Sleeve:
    """One trader you're copying, and the money you put behind them."""
    copy_id: str
    target_ss58: str
    alloc_tao: float
    label: Optional[str] = None
    # netuid -> fraction of THEIR book (sums to 1 when they hold anything)
    shares: Dict[int, float] = field(default_factory=dict)
    # Set when their book couldn't be read this pass; such a sleeve is held
    # (not zeroed), because "we can't see them" must never read as "sell".
    stale: bool = False
    error: Optional[str] = None

    @property
    def live(self) -> bool:
        return (not self.stale) and self.alloc_tao >= MIN_SLEEVE_TAO and bool(self.shares)


@dataclass
class PlanRow:
    """One subnet's move, with the sleeves that asked for it."""
    netuid: int
    action: str                 # stake | unstake | hold
    desired_tao: float
    current_tao: float
    amount_tao: float           # always positive; `action` carries the sign
    drift_tao: float            # desired - current (signed)
    # copy_id -> τ this sleeve wants in this subnet. The receipt for
    # "why am I buying SN64" is "A wants 2τ of it, B wants 1.2τ".
    contributors: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


@dataclass
class Plan:
    rows: List[PlanRow]
    desired: Dict[int, float]
    current: Dict[int, float]
    requested_tao: float        # Σ alloc_tao over live sleeves, as configured
    deployable_tao: float       # what the wallet can actually back
    scale: float                # applied to every sleeve (<1 = underfunded)
    band_tao: float             # drift under this is left alone
    sleeves: List[Sleeve] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # Set when the pass must not trade at all. Every row is forced to `hold`
    # and this says why — see `plan()`.
    blocked: Optional[str] = None

    @property
    def trades(self) -> List[PlanRow]:
        return [r for r in self.rows if r.action != "hold"]


def shares_of(positions) -> Dict[int, float]:
    """A trader's book -> {netuid: fraction of their total value}.

    Takes anything with `.positions` (each `.netuid`/`.value_tao`) and a
    `.total_value_tao`, i.e. chain.client.AccountPositions, or a plain dict
    of the same shape so the tests need no chain types.
    """
    if isinstance(positions, dict):
        rows = positions.get("positions") or []
        total = float(positions.get("total_value_tao") or 0.0)
        get = lambda p, k: p.get(k)            # noqa: E731
    else:
        rows = getattr(positions, "positions", []) or []
        total = float(getattr(positions, "total_value_tao", 0.0) or 0.0)
        get = lambda p, k: getattr(p, k)       # noqa: E731

    by_netuid: Dict[int, float] = {}
    for p in rows:
        try:
            netuid = int(get(p, "netuid"))
            value = float(get(p, "value_tao") or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            by_netuid[netuid] = by_netuid.get(netuid, 0.0) + value

    # Trust the positions we just summed over the reported total: a book read
    # mid-update can disagree with itself, and a share table that doesn't sum
    # to 1 would silently under- or over-deploy the sleeve.
    total = sum(by_netuid.values()) or total
    if total <= 0:
        return {}
    return {n: v / total for n, v in by_netuid.items()}


def blend(sleeves: Sequence[Sleeve], scale: float = 1.0) -> Dict[int, float]:
    """Sleeves -> desired τ per subnet. Stale/dust sleeves contribute nothing
    to the SHAPE, but see `plan()`: their money is held back, not reassigned."""
    desired: Dict[int, float] = {}
    for s in sleeves:
        if not s.live:
            continue
        size = s.alloc_tao * scale
        for netuid, share in s.shares.items():
            if share > 0:
                desired[netuid] = desired.get(netuid, 0.0) + size * share
    return desired


def contributions(sleeves: Sequence[Sleeve], netuid: int,
                  scale: float = 1.0) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for s in sleeves:
        if not s.live:
            continue
        share = s.shares.get(netuid, 0.0)
        if share > 0:
            out[s.copy_id] = round(s.alloc_tao * scale * share, 6)
    return out


def plan(sleeves: Sequence[Sleeve],
         current: Dict[int, float],
         free_tao: float = 0.0,
         *,
         threshold_pct: float = 5.0,
         min_balance_tao: float = 0.0,
         max_subnets: int = 0,
         subnet_allowlist: Optional[Sequence[int]] = None,
         subnet_denylist: Optional[Sequence[int]] = None) -> Plan:
    """Blend the sleeves and diff them against the book we hold.

    `current` is {netuid: τ we hold there}; `free_tao` is the unstaked
    balance. Sizing is absolute, so the sleeves are only honoured up to what
    the wallet can back — if you ask for more than you have, every sleeve is
    scaled by the same factor and the plan says so rather than filling the
    first traders and starving the last.
    """
    notes: List[str] = []
    sleeves = list(sleeves)

    for s in sleeves:
        if s.stale:
            notes.append(f"{s.copy_id}: target book unreadable, sleeve held "
                         f"({s.error or 'no data'})")
        elif s.alloc_tao < MIN_SLEEVE_TAO:
            notes.append(f"{s.copy_id}: {s.alloc_tao:.4f}τ is below the "
                         f"{MIN_SLEEVE_TAO}τ floor, not deployed")
        elif not s.shares:
            notes.append(f"{s.copy_id}: target holds nothing, sleeve idle")

    live = [s for s in sleeves if s.live]
    requested = sum(s.alloc_tao for s in live)

    # Two states where trading would be actively wrong, rather than merely
    # unnecessary. Both still produce a full diagnostic plan — you can see
    # what the book looks like — but every row is held.
    blocked: Optional[str] = None
    if not sleeves:
        # No copies configured is not an instruction to sell. Whatever is in
        # the book stays there until someone asks for it to move.
        blocked = "no copies configured — nothing is targeted, holding the book"
    elif any(s.stale for s in sleeves):
        # A stale sleeve's money is invisible, so the blend understates the
        # target by exactly that trader's allocation — and the diff would
        # liquidate their half of the book, then buy it back once the read
        # recovered. One bad RPC read must not cost a round trip in fees.
        names = ", ".join(s.copy_id for s in sleeves if s.stale)
        blocked = (f"holding: target book unreadable for {names}, and a "
                   f"partial view of the targets can't produce a correct "
                   f"blend. Retrying next pass.")

    # What the sleeves may actually claim: everything we already have staked
    # (it can be moved between subnets) plus free balance above the reserve.
    held = sum(v for v in current.values() if v > 0)
    spendable_free = max(0.0, free_tao - max(0.0, min_balance_tao))
    deployable = held + spendable_free

    scale = 1.0
    if requested > 0 and deployable < requested:
        scale = max(0.0, deployable / requested)
        notes.append(
            f"asked for {requested:.4f}τ, wallet backs {deployable:.4f}τ "
            f"({held:.4f} staked + {spendable_free:.4f} free) — every sleeve "
            f"scaled to {scale * 100:.1f}%")

    desired = blend(live, scale)

    if subnet_allowlist:
        allow = set(int(n) for n in subnet_allowlist)
        dropped = [n for n in desired if n not in allow]
        for n in dropped:
            desired.pop(n, None)
        if dropped:
            notes.append(f"allowlist dropped SN{sorted(dropped)} from the target book")
    for n in (subnet_denylist or []):
        if int(n) in desired:
            desired.pop(int(n), None)
            notes.append(f"denylist dropped SN{int(n)} from the target book")

    # Drift band scales with the portfolio, so a 5% threshold means 5% of the
    # book — not 5 percentage points of an allocation that may be tiny.
    total_target = sum(desired.values()) or deployable
    band = max(MIN_TRADE_TAO, total_target * max(0.0, threshold_pct) / 100.0)

    rows: List[PlanRow] = []
    for netuid in sorted(set(desired) | set(current)):
        want = round(desired.get(netuid, 0.0), 8)
        have = round(max(0.0, current.get(netuid, 0.0)), 8)
        drift = want - have
        # An exit is an exit: once a subnet leaves the target book entirely,
        # sell the whole position rather than leaving a sub-band stub behind.
        exiting = want <= 0 and have > MIN_TRADE_TAO
        if abs(drift) < band and not exiting:
            action, amount = "hold", 0.0
        else:
            action = "stake" if drift > 0 else "unstake"
            amount = abs(drift)
        rows.append(PlanRow(
            netuid=netuid,
            action=action,
            desired_tao=want,
            current_tao=have,
            amount_tao=round(amount, 8),
            drift_tao=round(drift, 8),
            contributors=contributions(live, netuid, scale),
            reason=(f"want {want:.4f}τ, hold {have:.4f}τ"
                    + ("" if action != "hold" else f" (within ±{band:.4f}τ)")),
        ))

    # Cap the number of moves per pass, biggest drift first — the rest close
    # on the next pass rather than firing a hundred transactions at once.
    if max_subnets and len([r for r in rows if r.action != "hold"]) > max_subnets:
        movers = sorted([r for r in rows if r.action != "hold"],
                        key=lambda r: r.amount_tao, reverse=True)
        for r in movers[max_subnets:]:
            r.action, r.amount_tao = "hold", 0.0
            r.reason += f" (deferred, {max_subnets}-subnet cap)"
        notes.append(f"{len(movers) - max_subnets} move(s) deferred to the "
                     f"next pass by the {max_subnets}-subnet cap")

    if blocked:
        for r in rows:
            r.action, r.amount_tao = "hold", 0.0
            r.reason += " — held, see notes"
        notes.insert(0, blocked)

    return Plan(
        rows=rows,
        desired=desired,
        current=dict(current),
        requested_tao=round(requested, 8),
        deployable_tao=round(deployable, 8),
        scale=round(scale, 8),
        band_tao=round(band, 8),
        sleeves=sleeves,
        notes=notes,
        blocked=blocked,
    )
