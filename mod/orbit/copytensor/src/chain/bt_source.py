"""
bt_source — read the chain through the `bt` module's local index.

copytensor used to answer every question by walking public RPCs itself:
prices per subnet, positions per account, and historical state from an
archive node that rate-limits, times out, or has simply discarded the
block. The `bt` module already keeps all of that on disk — it snapshots
every subnet pool and every tracked coldkey on an interval — so this
adapter points copytensor's reads at that index instead.

    BtSource        thin client over bt's one JSON surface (POST /api/call)
    BtBackedClient  a SubtensorClient whose reads come from bt, and which
                    falls back to its own RPC pool whenever bt can't answer

Nothing about writes changes: staking and unstaking still go out over
copytensor's own wallet + RPC pool.

Point at another bt with COPYTENSOR_BT_URL; disable entirely with
COPYTENSOR_BT=0.
"""

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .client import (
    ARCHIVE_POOL_SIZE,
    AccountPositions,
    AlphaPosition,
    SubnetInfo,
    SubtensorClient,
    is_valid_ss58,
)

log = logging.getLogger("copytensor.bt")

DEFAULT_BT_URL = os.environ.get("COPYTENSOR_BT_URL", "http://localhost:50280")
BLOCK_SEC = 12  # finney block time — maps a block number onto a wall clock

# Deep-block state lives only on archive nodes, which rate-limit, time out,
# or have discarded the block — the reason this adapter exists. When bt's
# index can't answer a historical question we say so and let PnL report a
# shorter, honest window. The leaderboard needs the opposite trade-off: a
# pool of hundreds of traders is only comparable if every one is priced over
# the SAME window, and only the archive can answer that for an account bt
# has never indexed — so the fallback defaults ON. COPYTENSOR_ARCHIVE_FALLBACK
# (0/1) overrides config, which overrides this default.
ARCHIVE_FALLBACK = os.environ.get("COPYTENSOR_ARCHIVE_FALLBACK")


class BtUnavailable(RuntimeError):
    """bt is not reachable, or cannot answer this particular question."""


class BtSource:
    """Client for the bt module's tool endpoint (`POST /api/call`)."""

    PING_TTL = 30

    def __init__(self, url: str = DEFAULT_BT_URL, timeout: int = 30):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._ping: Optional[Dict] = None
        self._ping_ts = 0.0
        self._lock = threading.Lock()

    # ── transport ────────────────────────────────────────────────────

    def call(self, tool: str, timeout: Optional[int] = None, **args) -> Any:
        try:
            r = requests.post(
                f"{self.url}/api/call",
                json={"tool": tool, "args": {k: v for k, v in args.items()
                                             if v is not None}},
                timeout=timeout or self.timeout,
            )
        except Exception as e:
            raise BtUnavailable(f"bt unreachable: {e}") from e
        try:
            body = r.json()
        except Exception as e:
            raise BtUnavailable(f"bt returned non-JSON ({r.status_code})") from e
        if not body.get("ok"):
            raise BtUnavailable(f"bt {tool} failed: {body.get('error')}")
        return body.get("result")

    def info(self, force: bool = False) -> Optional[Dict]:
        """bt's module info, cached — None when bt is down."""
        with self._lock:
            fresh = time.time() - self._ping_ts < self.PING_TTL
            if fresh and not force:
                return self._ping
            try:
                r = requests.get(f"{self.url}/api", timeout=3)
                self._ping = r.json() if r.ok else None
            except Exception:
                self._ping = None
            self._ping_ts = time.time()
            return self._ping

    def available(self) -> bool:
        return self.info() is not None

    # ── the surface copytensor uses ──────────────────────────────────

    def screener(self, sparks: bool = False) -> List[Dict]:
        """Every subnet pool. `sparks=True` also returns a 48-point price
        history per subnet — what the UI draws as a card sparkline."""
        return self.call("bt_screener", sparks=sparks).get("rows", [])

    def stats(self) -> Dict:
        """Network-wide totals: subnet count, market cap, 24h volume."""
        return self.call("bt_stats")

    def subnet(self, netuid: int) -> Dict:
        """One subnet's full pool state + on-chain identity."""
        return self.call("bt_subnet", netuid=netuid)

    def validators(self, netuid: int) -> Dict:
        """Validator rankings for a subnet (stake, trust, dividends)."""
        return self.call("bt_validators", netuid=netuid, timeout=60)

    def history(self, netuid: int, hours: int = 168) -> Dict:
        """Indexed price / mcap / volume series for a subnet."""
        return self.call("bt_history", netuid=netuid, hours=hours)

    def block(self) -> int:
        return int(self.call("bt_block")["block"])

    def account(self, ss58: str) -> Dict:
        """Live positions for any coldkey (bt reads the chain itself)."""
        return self.call("bt_account", address=ss58, timeout=60)

    def track(self, ss58: str, label: Optional[str] = None) -> Dict:
        return self.call("bt_track", address=ss58, label=label, timeout=90)

    def untrack(self, ss58: str) -> Dict:
        return self.call("bt_untrack", address=ss58)

    def traders(self, sort_by: str = "total_tao") -> Dict:
        return self.call("bt_traders", sort_by=sort_by)

    def trader(self, ss58: str, hours: int = 168) -> Dict:
        return self.call("bt_trader", address=ss58, hours=hours)

    def trader_history(self, ss58: str, hours: int = 168) -> Dict:
        return self.call("bt_trader_history", address=ss58, hours=hours)

    def trader_flows(self, ss58: Optional[str] = None, hours: int = 168,
                     limit: int = 100) -> Dict:
        return self.call("bt_trader_flows", address=ss58, hours=hours,
                         limit=limit)

    def positions_at(self, ss58: str, ts: Optional[int] = None,
                     tolerance_sec: int = 0) -> Dict:
        return self.call("bt_trader_at", address=ss58, ts=ts,
                         tolerance_sec=tolerance_sec)

    def prices_at(self, ts: Optional[int] = None) -> Dict[int, float]:
        out = self.call("bt_prices_at", ts=ts)
        return {int(k): v for k, v in (out.get("prices") or {}).items()}

    def snapshot(self, ss58: str) -> Dict:
        """Force bt to read this account from chain now and index it."""
        return self.call("bt_trader_snapshot", address=ss58, timeout=90)


class BtBackedClient(SubtensorClient):
    """SubtensorClient whose reads are served by bt's index.

    Every override degrades to the inherited RPC implementation when bt is
    unavailable or has no data, so copytensor keeps working exactly as
    before if bt is stopped.
    """

    # A snapshot older than this is refreshed from chain (through bt) before
    # it is used as "current" — the copy engine sizes real trades off it.
    STALE_SEC = 300

    def __init__(self, source: BtSource, *args, archive_fallback: bool = True,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.bt = source
        self.archive_fallback = (ARCHIVE_FALLBACK == "1"
                                 if ARCHIVE_FALLBACK is not None
                                 else bool(archive_fallback))
        self._names: Dict[int, str] = {}
        self._head: Optional[tuple] = None   # (fetched_at, block)

    # ── chain head ───────────────────────────────────────────────────

    def get_block(self) -> int:
        # One block lasts BLOCK_SEC — re-asking inside that window only
        # buys a slower answer, and every read path needs the head.
        if self._head and time.time() - self._head[0] < BLOCK_SEC:
            return self._head[1]
        try:
            block = self.bt.block()
        except BtUnavailable:
            block = super().get_block()
        self._head = (time.time(), block)
        return block

    # ── subnets ──────────────────────────────────────────────────────

    def _row_to_info(self, r: Dict) -> SubnetInfo:
        return SubnetInfo(
            netuid=r["netuid"],
            name=r.get("name") or f"SN{r['netuid']}",
            alpha_price_tao=r.get("price") or 0.0,
            total_stake_tao=r.get("tao_in") or 0.0,
            tempo=r.get("tempo") or 0,
            emission=r.get("emission") or 0.0,
        )

    def get_all_subnet_info(self, block: Optional[int] = None) -> List[SubnetInfo]:
        if block is None:
            try:
                rows = self.bt.screener()
                if rows:
                    infos = [self._row_to_info(r) for r in rows]
                    self._names = {i.netuid: i.name for i in infos}
                    return infos
            except BtUnavailable as e:
                log.warning("bt screener unavailable (%s) — falling back to RPC", e)
            return super().get_all_subnet_info()

        # Historical marks come from bt's price history, not an archive node.
        try:
            prices = self.bt.prices_at(self.block_to_ts(block))
            if prices:
                return [SubnetInfo(netuid=uid,
                                   name=self._names.get(uid, f"SN{uid}"),
                                   alpha_price_tao=price,
                                   total_stake_tao=0.0, tempo=0, emission=0.0)
                        for uid, price in prices.items()]
        except BtUnavailable as e:
            log.warning("bt prices_at unavailable (%s) — falling back to archive", e)
        return super().get_all_subnet_info(block=block)

    # ── time <-> block ───────────────────────────────────────────────

    def block_to_ts(self, block: int) -> int:
        """Wall-clock time of a block, from the current head at 12s/block."""
        head = self.get_block()
        return int(time.time()) - max(0, head - block) * BLOCK_SEC

    # ── account positions ────────────────────────────────────────────

    @staticmethod
    def _positions_from(ss58: str, block: int, positions: List[Dict]) -> AccountPositions:
        out, total = [], 0.0
        for p in positions:
            alpha = float(p.get("alpha") or 0.0)
            price = float(p.get("price") or 0.0)
            value = float(p.get("value_tao") if p.get("value_tao") is not None
                          else alpha * price)
            if alpha <= 0:
                continue
            total += value
            out.append(AlphaPosition(netuid=p["netuid"], hotkey=p.get("hotkey") or "",
                                     alpha_amount=alpha, alpha_price_tao=price,
                                     value_tao=value))
        return AccountPositions(ss58=ss58, block=block, total_value_tao=total,
                                positions=out)

    def get_stake_for_coldkey(self, ss58: str,
                              block: Optional[int] = None) -> AccountPositions:
        if not is_valid_ss58(ss58):
            raise ValueError(f"invalid ss58 address: {ss58}")

        if block is not None:
            # Historical: answered from bt's trader index instead of a deep
            # archive query. A snapshot only counts as a baseline if it
            # actually sits near the block asked for — otherwise today's
            # book would masquerade as last week's and PnL would read 0.
            ts = self.block_to_ts(block)
            age = max(0, int(time.time()) - ts)
            tolerance = int(min(86400, max(0.5 * age, 900)))
            try:
                snap = self.bt.positions_at(ss58, ts=ts, tolerance_sec=tolerance)
                if snap.get("found"):
                    return self._positions_from(ss58, block, snap["positions"])
                raise BtUnavailable(
                    f"no indexed snapshot within {tolerance}s of block {block}")
            except BtUnavailable as e:
                log.debug("bt has no history for %s at %d (%s)", ss58[:8], block, e)
                if not self.archive_fallback:
                    raise
                return super().get_stake_for_coldkey(ss58, block=block)

        cached = self._live_cached(ss58)
        if cached is not None:
            return cached

        try:
            snap = self.bt.positions_at(ss58)
            age = snap.get("age_sec")
            if not snap.get("found") or age is None or age > self.STALE_SEC:
                # Stale or unknown account: have bt read the chain now. That
                # also files the result in its index for everyone else.
                fresh = self.bt.snapshot(ss58)
                out = self._positions_from(ss58, self.get_block(),
                                           fresh.get("positions") or [])
            else:
                out = self._positions_from(ss58, self.get_block(),
                                           snap["positions"])
            self._live_store(ss58, out)
            return out
        except BtUnavailable as e:
            log.warning("bt positions unavailable for %s (%s) — falling back to RPC",
                        ss58[:8], e)
            return super().get_stake_for_coldkey(ss58)

    # ── health ───────────────────────────────────────────────────────

    def health(self) -> Dict:
        out = super().health()
        info = self.bt.info()
        out["reads"] = "bt" if info else "rpc"
        out["bt"] = {"url": self.bt.url, "available": info is not None,
                     "tools": (info or {}).get("tools"),
                     "traders": (info or {}).get("traders")}
        return out


def make_client(config: Dict) -> SubtensorClient:
    """The chain client copytensor should use: bt-backed when bt is up."""
    network = config.get("network", "finney")
    endpoint = config.get("subtensor_endpoint")
    archive_pool = int(config.get("archive_pool_size", ARCHIVE_POOL_SIZE))
    if os.environ.get("COPYTENSOR_BT") == "0":
        return SubtensorClient(network=network, endpoint=endpoint,
                               archive_pool_size=archive_pool)
    source = BtSource(config.get("bt_url") or DEFAULT_BT_URL)
    client = BtBackedClient(source, network=network, endpoint=endpoint,
                            archive_pool_size=archive_pool,
                            archive_fallback=bool(
                                config.get("archive_fallback", True)))
    if source.available():
        log.info("reads served by bt at %s", source.url)
    else:
        log.warning("bt not reachable at %s — reads fall back to public RPCs",
                    source.url)
    return client
