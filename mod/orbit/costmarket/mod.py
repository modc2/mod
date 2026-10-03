"""
costmarket — a prediction market on what an AI dev console costs to run.

The `build` module meters every agent task and publishes, per calendar month,
the **average cost per user**: total metered spend ÷ number of distinct users
who ran something. That single number is the thing traded here.

Why that number and not, say, total spend: total spend is mostly a function of
how many people showed up, which is a marketing question. Average cost *per
user* is a question about how the tool is actually used — whether people run
one careful Opus task or forty sloppy ones, whether prompt caching is landing,
whether the expensive model is worth it. People who use the console have a
real read on that, and nobody else does. That is what makes it worth a market
rather than a dashboard.

How it works:

  · **Membership** is monthly and has a floor (default $10). Paying it does
    two things: it lets you bet that month, and the amount you paid becomes
    your stake budget for that month. So the minimum subscription is also the
    minimum bet size, and nobody is betting money they didn't already commit.

  · **The market is parimutuel**, not an order book. Each epoch has a set of
    buckets over the average-cost range; you stake into the bucket you think
    the month lands in. At settlement the whole pool is split among everyone
    in the winning bucket, pro rata to stake, minus the house fee. No
    counterparty, no price to quote, no liquidity to bootstrap — which is the
    right shape for a market this small.

  · **Betting closes halfway through the month.** Two weeks of data is a real
    signal and two weeks is still unknown, so there is something to be right
    about. Settling on a month that is still running would let a late bettor
    trade on a nearly-finished number.

  · **Settlement reads the oracle**, `GET {build}/costs/epoch/{month}`, and
    refuses a month the oracle does not report as final. The oracle response
    is stored on the epoch, so a settlement can be re-checked later against
    what the market actually saw.

Identity is a wallet address (any string works for local play). Money here is
an off-chain ledger over on-chain payments: subscriptions are paid to the
treasury and verified by transaction hash against an EVM RPC when one is
configured; otherwise they land as `pending` for the owner to confirm.

Storage: ~/.mod/costmarket/  (market.json, members.json, bets.json)
"""

import json
import os
import time
import uuid
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m

USD = 1_000_000  # micro-dollars per dollar — matches build's `usd6`

# ERC-20 Transfer(address,address,uint256)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef3"


def _now() -> int:
    return int(time.time())


def _month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def _month_bounds(month: str) -> tuple:
    """(start, end-exclusive) unix seconds for 'YYYY-MM', UTC — the same
    boundaries build's costs.rs uses, so both sides agree on what a month is."""
    y, mo = (int(x) for x in month.split("-"))
    start = datetime(y, mo, 1, tzinfo=timezone.utc)
    days = monthrange(y, mo)[1]
    end = datetime(y + (mo == 12), 1 if mo == 12 else mo + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp()), days


def _usd(usd6: int) -> str:
    """Dollars as a decimal string, cents unless finer precision is real."""
    whole, frac = divmod(int(usd6), USD)
    if frac % 10_000 == 0:
        return f"{whole}.{frac // 10_000:02d}"
    return f"{whole}.{frac:06d}".rstrip("0")


def _parse_usd(v) -> int:
    """Accept 12.5, '12.50', '$12.50', '1,000' → micro-dollars."""
    if isinstance(v, (int, float)):
        return int(round(float(v) * USD))
    s = str(v).strip().replace("$", "").replace(",", "").replace("_", "")
    if not s:
        raise ValueError("amount required")
    return int(round(float(s) * USD))


class Costmarket:
    """A monthly prediction market on build's average cost per user."""

    def __init__(self):
        self.module_dir = Path(__file__).parent
        self.config = self._load_config()

        self.store_dir = Path.home() / ".mod" / "costmarket"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.market_path = self.store_dir / "market.json"
        self.members_path = self.store_dir / "members.json"
        self.bets_path = self.store_dir / "bets.json"

        self.port = int(self.config.get("port", 50490))
        self.app_port = int(self.config.get("app_port", 50491))

        mkt = self.config.get("market", {})
        self.min_sub_usd6 = _parse_usd(mkt.get("min_subscription_usd", 10))
        self.fee_bps = int(mkt.get("fee_bps", 500))          # 5% of the pool
        self.close_frac = float(mkt.get("close_fraction", 0.5))  # mid-month
        self.default_edges = [float(x) for x in mkt.get(
            "bucket_edges_usd", [0.25, 0.5, 1, 2, 5, 10, 25]
        )]
        self.treasury = str(mkt.get("treasury", "")).lower()

        oracle = self.config.get("oracle", {})
        self.oracle_base = os.environ.get(
            "COSTMARKET_ORACLE",
            oracle.get("url", "http://localhost:8890"),
        ).rstrip("/")
        self.oracle_module = oracle.get("module", "build")

        chain = self.config.get("chain", {})
        self.rpc = os.environ.get("COSTMARKET_RPC", chain.get("rpc", ""))
        self.token = str(chain.get("token", "")).lower()
        self.token_decimals = int(chain.get("token_decimals", 6))

    # ━━ Config / storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_config(self) -> Dict[str, Any]:
        p = self.module_dir / "config.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    def _load(self, path: Path, default):
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except (ValueError, OSError):
                return default
        return default

    def _save(self, path: Path, data):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    def _market(self) -> Dict[str, Any]:
        return self._load(self.market_path, {"epochs": {}})

    def _members(self) -> Dict[str, Any]:
        return self._load(self.members_path, {})

    def _bets(self) -> Dict[str, Any]:
        return self._load(self.bets_path, {})

    # ━━ Owner ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #
    # Settlement and manual payment confirmation are owner-only. The owner
    # lives off-tree (~/.mod/costmarket/owner.json) rather than in the
    # committed config, so a fork doesn't inherit someone else's authority.

    def _owner(self) -> str:
        p = self.store_dir / "owner.json"
        if p.exists():
            try:
                with open(p) as f:
                    return str(json.load(f).get("owner", "")).lower()
            except (ValueError, OSError):
                pass
        return str(self.config.get("owner", "")).lower()

    def is_owner(self, address: str = "") -> bool:
        owner = self._owner()
        if not owner:
            # No owner configured: single-player mode, everything is yours.
            return True
        return str(address or "").strip().lower() == owner

    def set_owner(self, address: str, key: str = ""):
        """First writer wins; after that only the current owner may rotate."""
        current = self._owner()
        if current and not self.is_owner(key or address):
            return {"error": "only the current owner can hand over ownership"}
        self._save(self.store_dir / "owner.json", {"owner": str(address).lower()})
        return {"ok": True, "owner": str(address).lower()}

    # ━━ Health / status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def health(self):
        return {
            "status": "ok",
            "module": "costmarket",
            "epochs": len(self._market().get("epochs", {})),
            "oracle": f"{self.oracle_base}/costs/epoch/",
        }

    def status(self):
        month = _month_of(_now())
        ep = self.epoch(month)
        members = self._members()
        active = sum(1 for a in members if self.is_member(a, month).get("active"))
        return {
            "epoch": month,
            "phase": ep.get("phase"),
            "closes_at": ep.get("close_ts"),
            "pool_usd": ep.get("pool_usd"),
            "bets": ep.get("bet_count"),
            "members_this_month": active,
            "members_all_time": len(members),
            "min_subscription_usd": _usd(self.min_sub_usd6),
            "fee_bps": self.fee_bps,
            "oracle": self.oracle_base,
            "treasury": self.treasury or None,
        }

    # ━━ Oracle ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def oracle(self, month: str = ""):
        """Read build's published cost epoch — the number this market is about.

        Kept as a thin passthrough on purpose: this module must never compute
        the settlement figure itself, or the two sides could disagree about
        what was true.
        """
        month = month or _month_of(_now())
        import urllib.request

        url = f"{self.oracle_base}/costs/epoch/{month}"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001 — surface any transport failure
            return {"error": f"oracle unreachable at {url}: {e}", "month": month}
        data["source"] = url
        data["fetched_at"] = _now()
        return data

    # ━━ Epochs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _buckets(self, edges: List[float]) -> List[Dict[str, Any]]:
        """Turn N edges into N+1 contiguous buckets covering [0, ∞)."""
        out = []
        lo = 0.0
        for i, hi in enumerate(edges):
            out.append({
                "id": i,
                "lo_usd6": int(round(lo * USD)),
                "hi_usd6": int(round(hi * USD)),
                "label": f"${lo:g} – ${hi:g}",
            })
            lo = hi
        out.append({
            "id": len(edges),
            "lo_usd6": int(round(lo * USD)),
            "hi_usd6": None,
            "label": f"over ${lo:g}",
        })
        return out

    def _ensure_epoch(self, month: str, market: Dict[str, Any]) -> Dict[str, Any]:
        """Epochs open themselves. Nobody should have to remember to start the
        month, and an epoch nobody bet in costs nothing to have created."""
        eps = market.setdefault("epochs", {})
        if month in eps:
            return eps[month]
        start, end, _days = _month_bounds(month)
        eps[month] = {
            "month": month,
            "start_ts": start,
            "end_ts": end,
            "close_ts": int(start + (end - start) * self.close_frac),
            "buckets": self._buckets(self.default_edges),
            "status": "open",
            "fee_bps": self.fee_bps,
            "oracle": None,
            "winning_bucket": None,
            "settled_at": None,
            "opened_at": _now(),
        }
        return eps[month]

    def _phase(self, ep: Dict[str, Any]) -> str:
        now = _now()
        if ep.get("status") == "settled":
            return "settled"
        if now < ep["close_ts"]:
            return "open"
        if now < ep["end_ts"]:
            return "closed"       # betting done, month still running
        return "awaiting_settlement"

    def epoch(self, month: str = ""):
        """One epoch with its live book."""
        month = month or _month_of(_now())
        market = self._market()
        ep = dict(self._ensure_epoch(month, market))
        self._save(self.market_path, market)

        bets = [b for b in self._bets().values() if b["month"] == month]
        per_bucket: Dict[int, int] = {}
        for b in bets:
            per_bucket[b["bucket"]] = per_bucket.get(b["bucket"], 0) + b["usd6"]
        pool = sum(per_bucket.values())

        for bucket in ep["buckets"]:
            staked = per_bucket.get(bucket["id"], 0)
            bucket["staked_usd6"] = staked
            bucket["staked_usd"] = _usd(staked)
            # Implied probability is just the pool share — in a parimutuel
            # market that IS the price, which is the nice part.
            bucket["implied_pct"] = round(100 * staked / pool, 1) if pool else 0.0
            # What $1 in this bucket returns if it wins, net of fee.
            net = pool * (10_000 - ep["fee_bps"]) // 10_000
            bucket["payout_per_usd"] = round(net / staked, 3) if staked else None

        ep["phase"] = self._phase(ep)
        ep["pool_usd6"] = pool
        ep["pool_usd"] = _usd(pool)
        ep["bet_count"] = len(bets)
        ep["bettors"] = len({b["address"] for b in bets})
        ep["now"] = _now()
        return ep

    def epochs(self, limit: int = 12):
        market = self._market()
        self._ensure_epoch(_month_of(_now()), market)
        self._save(self.market_path, market)
        months = sorted(market.get("epochs", {}).keys(), reverse=True)[: int(limit)]
        return [self.epoch(mo) for mo in months]

    def set_buckets(self, month: str, edges: List[float], key: str = ""):
        """Redraw an epoch's buckets — owner only, and only before any money
        is on the table. Moving the goalposts under a live book would void
        every position, so it is refused rather than handled."""
        if not self.is_owner(key):
            return {"error": "owner only"}
        market = self._market()
        ep = self._ensure_epoch(month, market)
        if any(b["month"] == month for b in self._bets().values()):
            return {"error": "this epoch already has bets — its buckets are fixed"}
        ep["buckets"] = self._buckets([float(e) for e in edges])
        self._save(self.market_path, market)
        return {"ok": True, "epoch": month, "buckets": ep["buckets"]}

    # ━━ Membership ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _member(self, members: Dict[str, Any], address: str) -> Dict[str, Any]:
        addr = str(address).strip().lower()
        if not addr:
            raise ValueError("address required")
        return members.setdefault(addr, {
            "address": addr,
            "subscriptions": {},   # month -> {usd6, status, tx, ts}
            "stake_left": {},      # month -> usd6 still available to bet
            "balance_usd6": 0,     # settled winnings, withdrawable
            "joined_at": _now(),
        })

    def subscribe(self, address: str, amount_usd=None, tx_hash: str = "", month: str = ""):
        """Pay the monthly minimum and get a stake budget for that month.

        The subscription *is* the stake: whatever you paid is what you can put
        on the table this month. That keeps the market honest without a
        deposit flow — there is no way to bet money you haven't paid in.
        """
        month = month or _month_of(_now())
        usd6 = self.min_sub_usd6 if amount_usd is None else _parse_usd(amount_usd)
        if usd6 < self.min_sub_usd6:
            return {
                "error": f"minimum subscription is ${_usd(self.min_sub_usd6)}",
                "min_usd": _usd(self.min_sub_usd6),
            }

        market = self._market()
        ep = self._ensure_epoch(month, market)
        self._save(self.market_path, market)
        if ep["status"] == "settled":
            return {"error": f"{month} is already settled"}

        verification = self._verify_payment(tx_hash, usd6, address)
        members = self._members()
        mem = self._member(members, address)

        prior = mem["subscriptions"].get(month)
        if prior and prior.get("status") == "paid":
            # Topping up an existing membership adds to the stake budget
            # rather than starting a second one.
            prior["usd6"] += usd6 if verification["status"] == "paid" else 0
        mem["subscriptions"][month] = {
            "usd6": (prior["usd6"] if prior and prior.get("status") == "paid" else usd6),
            "status": verification["status"],
            "tx": tx_hash or "",
            "verification": verification,
            "ts": _now(),
        }
        if verification["status"] == "paid":
            mem["stake_left"][month] = mem["stake_left"].get(month, 0) + usd6
        self._save(self.members_path, members)

        return {
            "ok": verification["status"] == "paid",
            "address": mem["address"],
            "month": month,
            "paid_usd": _usd(usd6),
            "status": verification["status"],
            "stake_available_usd": _usd(mem["stake_left"].get(month, 0)),
            "note": verification.get("note", ""),
        }

    def _verify_payment(self, tx_hash: str, usd6: int, address: str) -> Dict[str, Any]:
        """Check an ERC-20 transfer to the treasury.

        Three honest outcomes, never a silent pass:
          · no chain configured   → 'paid', flagged as unverified (local play)
          · configured + verified → 'paid'
          · configured + not      → 'pending', for the owner to confirm or reject
        """
        if not (self.rpc and self.token and self.treasury):
            return {
                "status": "paid",
                "verified": False,
                "note": "no chain configured — payment recorded on trust",
            }
        if not tx_hash:
            return {
                "status": "pending",
                "verified": False,
                "note": "tx_hash required: pay the treasury, then subscribe with the hash",
            }
        try:
            receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
        except Exception as e:  # noqa: BLE001
            return {"status": "pending", "verified": False, "note": f"rpc error: {e}"}
        if not receipt:
            return {"status": "pending", "verified": False, "note": "transaction not found yet"}
        if int(str(receipt.get("status", "0x1")), 16) != 1:
            return {"status": "rejected", "verified": False, "note": "transaction reverted"}

        want = usd6 * (10 ** self.token_decimals) // USD
        for log in receipt.get("logs", []):
            topics = log.get("topics", [])
            if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
                continue
            if str(log.get("address", "")).lower() != self.token:
                continue
            to_addr = "0x" + topics[2][-40:]
            if to_addr.lower() != self.treasury:
                continue
            value = int(log.get("data", "0x0"), 16)
            if value + 1 >= want:  # tolerate one wei of rounding
                return {
                    "status": "paid",
                    "verified": True,
                    "note": f"verified {tx_hash}",
                    "block": receipt.get("blockNumber"),
                }
        return {
            "status": "pending",
            "verified": False,
            "note": "no matching transfer to the treasury in that transaction",
        }

    def _rpc(self, method: str, params: list):
        import urllib.request

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        req = urllib.request.Request(
            self.rpc, data=body.encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out.get("result")

    def confirm_subscription(self, address: str, month: str = "", key: str = ""):
        """Owner override for a payment the chain check couldn't see (paid in
        another currency, paid off-chain, RPC was down)."""
        if not self.is_owner(key):
            return {"error": "owner only"}
        month = month or _month_of(_now())
        members = self._members()
        mem = self._member(members, address)
        sub = mem["subscriptions"].get(month)
        if not sub:
            return {"error": f"no subscription on record for {month}"}
        if sub["status"] == "paid":
            return {"ok": True, "note": "already paid"}
        sub["status"] = "paid"
        sub["verification"] = {"status": "paid", "verified": False, "note": "confirmed by owner"}
        mem["stake_left"][month] = mem["stake_left"].get(month, 0) + sub["usd6"]
        self._save(self.members_path, members)
        return {"ok": True, "address": mem["address"], "month": month,
                "stake_available_usd": _usd(mem["stake_left"][month])}

    def is_member(self, address: str, month: str = ""):
        month = month or _month_of(_now())
        mem = self._members().get(str(address).strip().lower())
        if not mem:
            return {"active": False, "month": month, "reason": "not subscribed"}
        sub = mem["subscriptions"].get(month)
        if not sub or sub["status"] != "paid":
            return {
                "active": False,
                "month": month,
                "reason": sub["status"] if sub else "not subscribed",
            }
        return {
            "active": True,
            "month": month,
            "paid_usd": _usd(sub["usd6"]),
            "stake_available_usd": _usd(mem["stake_left"].get(month, 0)),
        }

    def account(self, address: str):
        """Everything one person can see about their own position."""
        addr = str(address).strip().lower()
        mem = self._members().get(addr)
        if not mem:
            return {"address": addr, "member": False, "balance_usd": "0.00", "bets": []}
        month = _month_of(_now())
        mine = [b for b in self._bets().values() if b["address"] == addr]
        mine.sort(key=lambda b: b["ts"], reverse=True)
        won = sum(b.get("payout_usd6", 0) for b in mine if b.get("result") == "won")
        staked = sum(b["usd6"] for b in mine)
        return {
            "address": addr,
            "member": True,
            "joined_at": mem["joined_at"],
            "membership": self.is_member(addr, month),
            "balance_usd": _usd(mem["balance_usd6"]),
            "subscriptions": {
                mo: {"usd": _usd(s["usd6"]), "status": s["status"]}
                for mo, s in sorted(mem["subscriptions"].items(), reverse=True)
            },
            "lifetime_staked_usd": _usd(staked),
            "lifetime_won_usd": _usd(won),
            "net_usd": _usd(won - staked) if won >= staked else "-" + _usd(staked - won),
            "bets": [self._bet_view(b) for b in mine[:50]],
        }

    # ━━ Betting ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _bet_view(self, b: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(b)
        out["usd"] = _usd(b["usd6"])
        if b.get("payout_usd6") is not None:
            out["payout_usd"] = _usd(b["payout_usd6"])
        return out

    def bet(self, address: str, bucket: int, amount_usd=None, month: str = ""):
        """Stake into a bucket. Requires an active membership for the month,
        and spends from that month's stake budget."""
        month = month or _month_of(_now())
        addr = str(address).strip().lower()
        if not addr:
            return {"error": "address required"}

        market = self._market()
        ep = self._ensure_epoch(month, market)
        self._save(self.market_path, market)
        phase = self._phase(ep)
        if phase != "open":
            return {
                "error": f"betting on {month} is {phase}",
                "closed_at": ep["close_ts"],
                "hint": "bets close halfway through the month",
            }

        try:
            bucket = int(bucket)
        except (TypeError, ValueError):
            return {"error": "bucket must be a bucket id"}
        if not any(b["id"] == bucket for b in ep["buckets"]):
            return {"error": f"no bucket {bucket} in {month}",
                    "buckets": [b["label"] for b in ep["buckets"]]}

        members = self._members()
        mem = self._member(members, addr)
        sub = mem["subscriptions"].get(month)
        if not sub or sub["status"] != "paid":
            return {
                "error": f"subscribe for {month} before betting "
                         f"(minimum ${_usd(self.min_sub_usd6)})",
                "subscribed": bool(sub),
            }

        available = mem["stake_left"].get(month, 0)
        usd6 = available if amount_usd is None else _parse_usd(amount_usd)
        if usd6 <= 0:
            return {"error": "stake must be positive"}
        if usd6 > available:
            return {
                "error": f"stake ${_usd(usd6)} exceeds your ${_usd(available)} budget for {month}",
                "available_usd": _usd(available),
                "hint": "top up your subscription to raise the budget",
            }

        bet_id = uuid.uuid4().hex[:12]
        record = {
            "id": bet_id,
            "address": addr,
            "month": month,
            "bucket": bucket,
            "bucket_label": next(b["label"] for b in ep["buckets"] if b["id"] == bucket),
            "usd6": usd6,
            "ts": _now(),
            "result": "open",
            "payout_usd6": None,
        }
        bets = self._bets()
        bets[bet_id] = record
        self._save(self.bets_path, bets)

        mem["stake_left"][month] = available - usd6
        self._save(self.members_path, members)

        return {"ok": True, "bet": self._bet_view(record),
                "stake_left_usd": _usd(mem["stake_left"][month]),
                "epoch": self.epoch(month)}

    def book(self, month: str = ""):
        """The public order book — who is on what, without amounts per person
        being hidden. A market this small is more interesting read openly."""
        month = month or _month_of(_now())
        ep = self.epoch(month)
        bets = sorted(
            (b for b in self._bets().values() if b["month"] == month),
            key=lambda b: b["ts"],
            reverse=True,
        )
        return {
            "epoch": month,
            "phase": ep["phase"],
            "pool_usd": ep["pool_usd"],
            "buckets": ep["buckets"],
            "bets": [self._bet_view(b) for b in bets],
        }

    # ━━ Settlement ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _bucket_for(self, buckets: List[Dict[str, Any]], usd6: int) -> Optional[int]:
        for b in buckets:
            if usd6 >= b["lo_usd6"] and (b["hi_usd6"] is None or usd6 < b["hi_usd6"]):
                return b["id"]
        return None

    def settle(self, month: str = "", key: str = ""):
        """Resolve an epoch against the oracle and pay the winning bucket.

        Refuses anything but a finished month with a final oracle reading:
        a settlement on a live number is not a settlement.
        """
        if not self.is_owner(key):
            return {"error": "owner only"}
        month = month or _month_of(_now() - 86400 * 15)  # default: last month

        market = self._market()
        ep = self._ensure_epoch(month, market)
        if ep["status"] == "settled":
            return {"error": f"{month} is already settled",
                    "settled_at": ep["settled_at"],
                    "winning_bucket": ep["winning_bucket"]}
        if _now() < ep["end_ts"]:
            return {"error": f"{month} is not over yet", "ends_at": ep["end_ts"]}

        reading = self.oracle(month)
        if reading.get("error"):
            return {"error": f"cannot settle without the oracle: {reading['error']}"}
        if not reading.get("final"):
            return {"error": f"the oracle does not report {month} as final yet"}

        avg = int(reading.get("avg_usd6_per_user") or 0)
        winner = self._bucket_for(ep["buckets"], avg)

        bets = self._bets()
        month_bets = [b for b in bets.values() if b["month"] == month]
        pool = sum(b["usd6"] for b in month_bets)
        winners = [b for b in month_bets if b["bucket"] == winner]
        won_stake = sum(b["usd6"] for b in winners)

        fee = pool * ep["fee_bps"] // 10_000
        distributable = pool - fee
        members = self._members()

        if not winners:
            # Nobody called it. Refunding beats a house windfall: the market
            # failed to price the month, and keeping the money for that would
            # teach people not to play.
            fee = 0
            distributable = 0
            for b in month_bets:
                b["result"] = "refunded"
                b["payout_usd6"] = b["usd6"]
                self._member(members, b["address"])["balance_usd6"] += b["usd6"]
        else:
            paid = 0
            for b in month_bets:
                if b["bucket"] == winner:
                    # Pro rata, floor-rounded; the dust is handed to the
                    # largest winner at the end rather than vanishing.
                    share = distributable * b["usd6"] // won_stake
                    b["result"] = "won"
                    b["payout_usd6"] = share
                    paid += share
                    self._member(members, b["address"])["balance_usd6"] += share
                else:
                    b["result"] = "lost"
                    b["payout_usd6"] = 0
            dust = distributable - paid
            if dust > 0:
                top = max(winners, key=lambda b: b["usd6"])
                top["payout_usd6"] += dust
                self._member(members, top["address"])["balance_usd6"] += dust

        self._save(self.bets_path, bets)
        self._save(self.members_path, members)

        ep["status"] = "settled"
        ep["settled_at"] = _now()
        ep["oracle"] = reading
        ep["winning_bucket"] = winner
        ep["avg_usd6_per_user"] = avg
        ep["pool_usd6"] = pool
        ep["fee_usd6"] = fee
        self._save(self.market_path, market)

        return {
            "ok": True,
            "epoch": month,
            "avg_usd_per_user": _usd(avg),
            "winning_bucket": winner,
            "winning_label": next(
                (b["label"] for b in ep["buckets"] if b["id"] == winner), None
            ),
            "pool_usd": _usd(pool),
            "fee_usd": _usd(fee),
            "paid_out_usd": _usd(distributable),
            "winners": len(winners),
            "bets": len(month_bets),
            "oracle_source": reading.get("source"),
        }

    def withdraw(self, address: str, amount_usd=None):
        """Move settled winnings off the ledger. Payout itself is manual — the
        record here is the claim, and the owner settles it. Pretending to send
        money the module cannot send would be worse than saying so."""
        members = self._members()
        mem = self._member(members, address)
        bal = mem["balance_usd6"]
        usd6 = bal if amount_usd is None else _parse_usd(amount_usd)
        if usd6 <= 0 or usd6 > bal:
            return {"error": f"withdrawable balance is ${_usd(bal)}"}
        mem["balance_usd6"] = bal - usd6
        claims = mem.setdefault("withdrawals", [])
        claim = {"id": uuid.uuid4().hex[:12], "usd6": usd6, "ts": _now(), "status": "requested"}
        claims.append(claim)
        self._save(self.members_path, members)
        return {"ok": True, "claim": {**claim, "usd": _usd(usd6)},
                "balance_usd": _usd(mem["balance_usd6"]),
                "note": "payout is settled by the owner against this claim"}

    # ━━ Leaderboard ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def leaderboard(self, limit: int = 25):
        """Ranked by net profit — staked versus won, across every settled epoch."""
        tally: Dict[str, Dict[str, int]] = {}
        for b in self._bets().values():
            t = tally.setdefault(b["address"], {"staked": 0, "won": 0, "bets": 0, "hits": 0})
            t["staked"] += b["usd6"]
            t["bets"] += 1
            if b.get("result") == "won":
                t["won"] += b.get("payout_usd6", 0)
                t["hits"] += 1
            elif b.get("result") == "refunded":
                t["won"] += b.get("payout_usd6", 0)
        rows = []
        for addr, t in tally.items():
            net = t["won"] - t["staked"]
            rows.append({
                "address": addr,
                "bets": t["bets"],
                "hits": t["hits"],
                "hit_rate": round(100 * t["hits"] / t["bets"], 1) if t["bets"] else 0.0,
                "staked_usd": _usd(t["staked"]),
                "won_usd": _usd(t["won"]),
                "net_usd6": net,
                "net_usd": _usd(net) if net >= 0 else "-" + _usd(-net),
            })
        rows.sort(key=lambda r: r["net_usd6"], reverse=True)
        return rows[: int(limit)]

    def treasury_report(self):
        """Where the fee went. Small number, but it should not be a mystery."""
        fees = 0
        settled = 0
        for ep in self._market().get("epochs", {}).values():
            if ep.get("status") == "settled":
                settled += 1
                fees += int(ep.get("fee_usd6") or 0)
        subs = 0
        owed = 0
        for mem in self._members().values():
            subs += sum(s["usd6"] for s in mem["subscriptions"].values() if s["status"] == "paid")
            owed += mem.get("balance_usd6", 0)
        return {
            "settled_epochs": settled,
            "subscriptions_collected_usd": _usd(subs),
            "fees_earned_usd": _usd(fees),
            "owed_to_members_usd": _usd(owed),
            "treasury_address": self.treasury or None,
            "fee_bps": self.fee_bps,
        }

    # ━━ Serving ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        import subprocess

        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        pm2_cmd = ["pm2", "start", cmd[0], "--name", name]
        if cwd:
            pm2_cmd += ["--cwd", cwd]
        pm2_cmd += ["--"] + cmd[1:]
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        return r.returncode == 0

    def _pm2_kill(self, name):
        import subprocess

        return subprocess.run(["pm2", "delete", name],
                              capture_output=True, text=True).returncode == 0

    def serve_api(self, port=None):
        port = int(port or self.port)
        api_dir = self.module_dir / "api"
        mod_root = str(self.module_dir.parent.parent.parent)
        env = {
            "PYTHONPATH": f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
            "PORT": str(port),
        }
        cmd = ["python3", "-m", "uvicorn", "api:app", "--host", "0.0.0.0",
               "--port", str(port), "--app-dir", str(api_dir)]
        self._pm2_start("costmarket-api", cmd, env=env)
        return {"api": f"http://localhost:{port}", "pm2": "costmarket-api"}

    def serve_app(self, app_port=None):
        app_port = int(app_port or self.app_port)
        app_dir = self.module_dir / "app"
        cmd = ["python3", "-m", "http.server", str(app_port), "--bind", "0.0.0.0"]
        self._pm2_start("costmarket-app", cmd, cwd=str(app_dir))
        return {"app": f"http://localhost:{app_port}", "pm2": "costmarket-app"}

    def serve(self, port=None, app_port=None):
        out = {}
        out.update(self.serve_api(port))
        out.update(self.serve_app(app_port))
        return out

    def kill(self):
        killed = [n for n in ("costmarket-api", "costmarket-app") if self._pm2_kill(n)]
        return {"killed": killed}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def forward(self, action=None, **kwargs):
        """CLI entry: costmarket <action> [args]"""
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not callable(fn) or action.startswith("_"):
            return {"error": f"unknown action: {action}"}
        return fn(**kwargs)
