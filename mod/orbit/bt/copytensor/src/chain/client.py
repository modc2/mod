"""
Subtensor chain client — all on-chain reads/writes for copytensor.

Talks directly to the Bittensor blockchain via the open-source bittensor SDK
against a rotating pool of public RPC endpoints — no third-party APIs, no
single host of failure. Reads work without any wallet; only stake/unstake
operations require a wallet.
"""

import logging
import random
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import bittensor as bt

log = logging.getLogger("copytensor.chain")

BLOCKS_PER_DAY = 7200  # ~12s per block

# Public, free Bittensor RPC endpoints. We rotate across these so no single
# provider becomes a SPOF. Add community-run endpoints here as they appear.
PUBLIC_RPCS_FINNEY: List[str] = [
    "wss://entrypoint-finney.opentensor.ai:443",
    "wss://archive.chain.opentensor.ai:443",
    "wss://lite.chain.opentensor.ai:443",
    "wss://bittensor-finney.api.onfinality.io/public-ws",
]
PUBLIC_RPCS_TEST: List[str] = [
    "wss://test.finney.opentensor.ai:443",
]

# Deep-block state queries only work against archive nodes — lite nodes
# discard old state ("State discarded ..."), so historical reads must never
# rotate through the general pool.
ARCHIVE_RPC_FINNEY = "wss://archive.chain.opentensor.ai:443"

# How many archive websockets we keep open. A leaderboard over hundreds of
# traders is one deep query per trader; a single connection makes that
# strictly serial. Small enough to stay a polite public-RPC citizen.
ARCHIVE_POOL_SIZE = 4

# The delegate walk behind trader discovery costs ~30s, and the delegate set
# barely moves hour to hour.
UNIVERSE_TTL_SEC = 6 * 3600
# Generous multiple of the ~30s a healthy node takes — anything past this is
# an endpoint that has gone quiet on us, not a slow answer.
WALK_TIMEOUT_SEC = 150


def is_valid_ss58(ss58: str) -> bool:
    """Checksum-validate an ss58 address (seed lists can contain typos)."""
    try:
        from scalecodec.utils.ss58 import ss58_decode
        ss58_decode(ss58)
        return True
    except Exception:
        return False


def _close(sub: Optional[Any]):
    """Best-effort socket close — a dropped connection must not leak a fd."""
    if sub is None:
        return
    try:
        sub.close()
    except Exception:
        pass


def _endpoints_for(network: str, override: Optional[str] = None) -> List[str]:
    if override:
        return [override]
    if network == "test":
        return list(PUBLIC_RPCS_TEST)
    return list(PUBLIC_RPCS_FINNEY)


@dataclass
class SubnetInfo:
    netuid: int
    name: str
    alpha_price_tao: float
    total_stake_tao: float
    tempo: int
    emission: float


@dataclass
class AlphaPosition:
    netuid: int
    hotkey: str
    alpha_amount: float
    alpha_price_tao: float
    value_tao: float


@dataclass
class TraderCandidate:
    """A real coldkey found on-chain, ranked for the leaderboard pool.

    `stake_weight` is the sum of that coldkey's stake across every subnet as
    reported by get_delegates(). In dTAO those are per-subnet alpha units, so
    the number is a ranking heuristic — NOT a τ valuation. Every τ figure the
    leaderboard shows comes from priced positions, never from this.
    """
    ss58: str
    kind: str                    # "validator" (delegate owner) | "nominator"
    stake_weight: float
    subnets: int
    rank: int = 0
    hotkey: Optional[str] = None

    def as_dict(self) -> Dict:
        return {
            "ss58": self.ss58, "kind": self.kind,
            "stake_weight": self.stake_weight, "subnets": self.subnets,
            "rank": self.rank, "hotkey": self.hotkey,
        }


@dataclass
class AccountPositions:
    ss58: str
    block: int
    total_value_tao: float
    positions: List[AlphaPosition] = field(default_factory=list)


class SubtensorClient:
    """Wraps bt.Subtensor() with round-robin failover across public RPCs.

    Every call to `.sub` returns a connected subtensor; if a call fails we
    rotate to the next endpoint automatically via `_with_failover()`. The
    pool shuffles on init so different processes pin different primaries —
    no single provider sees all our traffic.
    """

    def __init__(self, network: str = "finney", endpoint: Optional[str] = None,
                 endpoints: Optional[List[str]] = None,
                 archive_pool_size: int = ARCHIVE_POOL_SIZE):
        self.network = network
        self.endpoint = endpoint
        pool = endpoints if endpoints else _endpoints_for(network, endpoint)
        random.shuffle(pool)
        self.endpoints: List[str] = pool
        self._idx = 0
        self._sub: Optional[bt.Subtensor] = None
        self._current: Optional[str] = None
        self._subnets_cache: Optional[Tuple[float, List["SubnetInfo"]]] = None
        self._subnets_hist_cache: Dict[int, List["SubnetInfo"]] = {}
        # A bt.Subtensor websocket is NOT thread-safe ("cannot call recv
        # while another thread is already running recv") — snapshot loop,
        # leaderboard warmers and request threads all share this client, so
        # every chain call is serialized per connection. RLock because
        # failover'd calls nest (e.g. stake query → subnet-info query).
        self._lock = threading.RLock()
        # Historical reads are the leaderboard's bottleneck (one deep archive
        # query per account per horizon), so the archive side gets a small
        # pool of connections instead of a single serialized one. Each is
        # still used by one thread at a time; nested archive calls reuse the
        # connection this thread already holds, so the pool can't deadlock.
        self._archive_pool_size = max(1, int(archive_pool_size))
        self._archive_sem = threading.BoundedSemaphore(self._archive_pool_size)
        self._archive_free: List[bt.Subtensor] = []
        self._archive_free_lock = threading.Lock()
        self._archive_held = threading.local()
        # Delegate walk (get_delegates) — the trader-universe source. It is a
        # ~30s full-set call, so it is cached process-wide.
        self._universe_cache: Optional[Tuple[float, List["TraderCandidate"]]] = None
        self._universe_lock = threading.Lock()
        # Live positions, briefly. Every leaderboard horizon asks the same
        # question ("where does this trader stand right now?"), so without
        # this a 5-horizon warm over a 250-trader pool is 1250 identical
        # reads. Well inside the staleness the copy engine already tolerates.
        self._live_cache: Dict[str, Tuple[float, "AccountPositions"]] = {}
        self._live_cache_lock = threading.Lock()

    @property
    def sub(self) -> bt.Subtensor:
        if self._sub is None:
            self._connect()
        return self._sub

    def _connect(self) -> bt.Subtensor:
        """Try endpoints in rotation until one connects."""
        last_err: Optional[Exception] = None
        for _ in range(len(self.endpoints)):
            url = self.endpoints[self._idx]
            try:
                self._sub = bt.Subtensor(network=url)
                _ = self._sub.block  # sanity probe
                self._current = url
                log.info("subtensor connected via %s", url)
                return self._sub
            except Exception as e:
                last_err = e
                log.warning("subtensor connect failed (%s): %s", url, e)
                self._idx = (self._idx + 1) % len(self.endpoints)
                self._sub = None
        raise RuntimeError(f"no Bittensor RPC reachable; last error: {last_err}")

    def _rotate(self):
        self._idx = (self._idx + 1) % len(self.endpoints)
        self._sub = None
        self._current = None

    def _with_failover(self, fn: Callable[[], Any], retries: Optional[int] = None) -> Any:
        """Run a callable that uses self.sub; rotate + retry on failure."""
        n = retries if retries is not None else max(1, len(self.endpoints))
        last_err: Optional[Exception] = None
        with self._lock:
            for _ in range(n):
                try:
                    return fn()
                except Exception as e:
                    last_err = e
                    log.warning("rpc call failed on %s: %s — rotating", self._current, e)
                    self._rotate()
        raise RuntimeError(f"all RPCs failed; last error: {last_err}")

    @property
    def archive_url(self) -> str:
        if self.network == "test":
            return self.endpoint or PUBLIC_RPCS_TEST[0]
        return self.endpoint or ARCHIVE_RPC_FINNEY

    def _archive(self) -> bt.Subtensor:
        """Check a connection out of the archive pool (opening one if empty)."""
        with self._archive_free_lock:
            if self._archive_free:
                return self._archive_free.pop()
        return bt.Subtensor(network=self.archive_url)

    def _release_archive(self, conn: Optional[bt.Subtensor]):
        if conn is None:
            return
        with self._archive_free_lock:
            if len(self._archive_free) < self._archive_pool_size:
                self._archive_free.append(conn)

    def _with_archive(self, fn: Callable[[bt.Subtensor], Any]) -> Any:
        """Run a historical query on an archive connection, one reconnect retry.

        Concurrency is capped by the pool semaphore. A nested archive call
        (stake query → historical subnet prices) reuses the connection this
        thread already holds rather than waiting on the semaphore it is
        itself holding.
        """
        held = getattr(self._archive_held, "conn", None)
        if held is not None:
            return fn(held)

        with self._archive_sem:
            conn = self._archive()
            self._archive_held.conn = conn
            try:
                try:
                    result = fn(conn)
                except Exception:
                    # Drop the suspect socket, retry once on a fresh one.
                    _close(conn)
                    conn = bt.Subtensor(network=self.archive_url)
                    self._archive_held.conn = conn
                    result = fn(conn)
            except Exception:
                self._archive_held.conn = None
                _close(conn)   # both attempts failed; never reuse this socket
                raise
            self._archive_held.conn = None
            self._release_archive(conn)
            return result

    # ── live-position cache ──────────────────────────────────────────

    LIVE_TTL_SEC = 120

    def _live_cached(self, ss58: str) -> Optional["AccountPositions"]:
        with self._live_cache_lock:
            hit = self._live_cache.get(ss58)
        if hit and time.time() - hit[0] < self.LIVE_TTL_SEC:
            return hit[1]
        return None

    def _live_store(self, ss58: str, positions: "AccountPositions"):
        with self._live_cache_lock:
            self._live_cache[ss58] = (time.time(), positions)

    def reconnect(self):
        self._sub = None
        return self.sub

    def current_endpoint(self) -> Optional[str]:
        return self._current

    def get_block(self) -> int:
        return self._with_failover(lambda: self.sub.block)

    def block_at_days_ago(self, days: int) -> int:
        current = self.get_block()
        return max(0, current - (days * BLOCKS_PER_DAY))

    def get_block_hash(self, block: int) -> str:
        return self._with_failover(lambda: self.sub.substrate.get_block_hash(block))

    # ── subnets ──────────────────────────────────────────────────────

    def get_all_netuids(self) -> List[int]:
        return self._with_failover(lambda: self.sub.get_all_subnets_netuid())

    def _dyn_to_info(self, d) -> SubnetInfo:
        def _tao(x):
            return float(getattr(x, "tao", x) or 0)
        return SubnetInfo(
            netuid=d.netuid,
            name=d.subnet_name or f"SN{d.netuid}",
            alpha_price_tao=_tao(d.price),
            total_stake_tao=_tao(d.tao_in),
            tempo=d.tempo,
            emission=_tao(d.emission),
        )

    def get_subnet_info(self, netuid: int, block: Optional[int] = None) -> SubnetInfo:
        d = self._with_failover(lambda: self.sub.subnet(netuid, block=block))
        if d is None:
            raise RuntimeError(f"subnet {netuid} not found")
        return self._dyn_to_info(d)

    SUBNETS_CACHE_SEC = 60
    SUBNETS_HIST_CACHE_MAX = 16

    def get_all_subnet_info(self, block: Optional[int] = None) -> List[SubnetInfo]:
        # all_subnets() is one of the heaviest calls and every PnL pass needs
        # it — cache current-block results briefly, and historical results by
        # block (a leaderboard build hits the same target block once per
        # account). Historical blocks are immutable so that cache never ages.
        if block is None:
            if self._subnets_cache:
                ts, cached = self._subnets_cache
                if time.time() - ts < self.SUBNETS_CACHE_SEC:
                    return cached
            subs = self._with_failover(lambda: self.sub.all_subnets()) or []
            infos = [self._dyn_to_info(d) for d in subs]
            self._subnets_cache = (time.time(), infos)
            return infos

        if block in self._subnets_hist_cache:
            return self._subnets_hist_cache[block]
        # deep-block state → archive only, never the general pool
        subs = self._with_archive(lambda s: s.all_subnets(block=block)) or []
        infos = [self._dyn_to_info(d) for d in subs]
        if len(self._subnets_hist_cache) >= self.SUBNETS_HIST_CACHE_MAX:
            self._subnets_hist_cache.pop(next(iter(self._subnets_hist_cache)))
        self._subnets_hist_cache[block] = infos
        return infos

    # ── alpha prices ─────────────────────────────────────────────────

    def get_alpha_price(self, netuid: int, block: Optional[int] = None) -> float:
        block_hash = self.get_block_hash(block) if block else None
        return self._get_alpha_price(netuid, block_hash)

    def _get_alpha_price(self, netuid: int, block_hash: Optional[str] = None) -> float:
        subnet_tao = self._query_value("SubnetTAO", [netuid], block_hash, default=0)
        subnet_alpha = self._query_value("SubnetAlphaOut", [netuid], block_hash, default=0)
        if not subnet_alpha or subnet_alpha == 0:
            return 0.0
        return subnet_tao / subnet_alpha

    def _get_total_subnet_stake(self, netuid: int, block_hash: Optional[str] = None) -> float:
        tao = self._query_value("SubnetTAO", [netuid], block_hash, default=0)
        return tao / 1e9 if tao > 1e9 else tao

    # ── account positions ────────────────────────────────────────────

    def get_stake_for_coldkey(self, ss58: str,
                              block: Optional[int] = None) -> AccountPositions:
        """Get all alpha positions for a coldkey across all subnets.

        One runtime-API call (StakeInfoRuntimeApi.get_stake_info_for_coldkey)
        + one all_subnets() call for prices — NOT a per-netuid storage scan.
        Historical blocks route to the archive node (lite nodes discard old
        state).
        """
        if not is_valid_ss58(ss58):
            raise ValueError(f"invalid ss58 address: {ss58}")

        def _tao(x):
            return float(getattr(x, "tao", x) or 0)

        def _go(sub):
            current_block = block or sub.block
            infos = sub.get_stake_info_for_coldkey(ss58, block=block) or []
            prices = {s.netuid: s.alpha_price_tao
                      for s in self.get_all_subnet_info(block=block)}
            positions: List[AlphaPosition] = []
            total_value = 0.0
            for i in infos:
                alpha = _tao(i.stake)
                if alpha <= 0:
                    continue
                price = prices.get(i.netuid, 0.0)
                value = alpha * price
                total_value += value
                positions.append(AlphaPosition(
                    netuid=i.netuid,
                    hotkey=i.hotkey_ss58,
                    alpha_amount=alpha,
                    alpha_price_tao=price,
                    value_tao=value,
                ))
            return AccountPositions(
                ss58=ss58,
                block=current_block,
                total_value_tao=total_value,
                positions=positions,
            )

        if block is not None:
            return self._with_archive(_go)

        cached = self._live_cached(ss58)
        if cached is not None:
            return cached
        positions = self._with_failover(lambda: _go(self.sub))
        self._live_store(ss58, positions)
        return positions

    def _get_hotkeys_for_coldkey(self, coldkey_ss58: str,
                                  block_hash: Optional[str] = None) -> List[str]:
        result = self._query("StakingHotkeys", [coldkey_ss58], block_hash)
        if result and hasattr(result, "value") and result.value:
            return list(result.value)
        # Fallback: try OwnedHotkeys
        result = self._query("OwnedHotkeys", [coldkey_ss58], block_hash)
        if result and hasattr(result, "value") and result.value:
            return list(result.value)
        return []

    def _get_alpha_stake(self, netuid: int, hotkey: str, coldkey: str,
                          block_hash: Optional[str] = None) -> float:
        result = self._query("Alpha", [netuid, hotkey, coldkey], block_hash)
        if result and hasattr(result, "value") and result.value:
            val = result.value
            return val / 1e9 if val > 1e9 else val
        return 0.0

    # ── discovery ────────────────────────────────────────────────────

    def discover_top_delegate_owners(self, n: int = 8) -> List[Dict]:
        """Find the owner coldkeys of the top-N delegates by total stake.

        These are real, checksum-valid accounts that actively hold alpha
        positions — the honest way to seed a leaderboard without inventing
        addresses. Kept as the validators-only slice of `discover_traders`.
        """
        return [
            {
                "ss58": c.ss58,
                "hotkey": c.hotkey,
                "delegate_stake_tao": c.stake_weight,
                "rank": c.rank,
            }
            for c in self.discover_traders(n=n, kinds=("validator",))
        ]

    # The universe: every coldkey the delegate set knows about — the delegate
    # owners themselves plus every nominator staking to them. On finney that
    # is ~2.3k owners and ~58k nominators, which is the honest answer to
    # "all the traders": real accounts holding real alpha, read from chain.
    def discover_traders(self, n: int = 100,
                         kinds: Tuple[str, ...] = ("validator", "nominator"),
                         min_stake_weight: float = 0.0,
                         force: bool = False) -> List[TraderCandidate]:
        """Rank the on-chain coldkey universe by stake and return the top n.

        One get_delegates() walk (~30s) yields the whole set; the result is
        cached for UNIVERSE_TTL_SEC because the delegate set barely moves.
        """
        universe = self._trader_universe(force=force)
        out: List[TraderCandidate] = []
        for c in universe:
            if c.kind not in kinds or c.stake_weight < min_stake_weight:
                continue
            # Rank is the position in what was actually asked for, so a
            # validators-only call ranks 1..n by delegate stake. Copied, not
            # mutated — the universe list is cached and shared.
            out.append(replace(c, rank=len(out) + 1))
            if len(out) >= n:
                break
        return out

    def universe_size(self, force: bool = False) -> Dict[str, int]:
        universe = self._trader_universe(force=force)
        validators = sum(1 for c in universe if c.kind == "validator")
        return {"total": len(universe), "validators": validators,
                "nominators": len(universe) - validators}

    def _walk_delegates(self) -> List[Any]:
        """get_delegates() on a private connection, rotating on failure.

        Endpoint order matters here more than anywhere else: this is the
        single heaviest runtime call we make, and a rate-limiting third-party
        node answers it by simply never answering — the socket read blocks
        forever. So: opentensor's own nodes first, archive after them (slower
        for runtime calls), third-party last, and a watchdog on each attempt
        so one silent endpoint can't wedge discovery.
        """
        def _rank(url: str) -> int:
            if "opentensor" not in url:
                return 2                     # third-party, unknown behaviour
            return 1 if "archive" in url else 0

        last_err: Optional[Exception] = None
        for url in sorted(self.endpoints, key=_rank):
            started = time.time()
            try:
                out = self._walk_once(url)
                log.info("delegate walk: %d delegates from %s in %.1fs",
                         len(out), url, time.time() - started)
                return out
            except Exception as e:
                last_err = e
                log.warning("delegate walk failed on %s after %.1fs: %s",
                            url, time.time() - started, e)
        raise RuntimeError(f"delegate walk failed on every RPC: {last_err}")

    def _walk_once(self, url: str) -> List[Any]:
        """One walk attempt, abandoned if the node goes quiet.

        The substrate client reads its websocket with no timeout, so the
        attempt runs on a daemon thread we can walk away from; a wedged
        socket leaks one thread until the OS tears the connection down,
        instead of stalling discovery for the life of the process.
        """
        box: Dict[str, Any] = {}
        done = threading.Event()

        def _run():
            sub = None
            try:
                sub = bt.Subtensor(network=url)
                box["out"] = sub.get_delegates() or []
            except Exception as e:                      # noqa: BLE001
                box["err"] = e
            finally:
                _close(sub)
                done.set()

        threading.Thread(target=_run, name="delegate-walk", daemon=True).start()
        if not done.wait(WALK_TIMEOUT_SEC):
            raise TimeoutError(f"no response within {WALK_TIMEOUT_SEC}s")
        if "err" in box:
            raise box["err"]
        return box.get("out") or []

    def _trader_universe(self, force: bool = False) -> List[TraderCandidate]:
        with self._universe_lock:
            cached = self._universe_cache
            if cached and not force and time.time() - cached[0] < UNIVERSE_TTL_SEC:
                return cached[1]

            def _tao(x):
                return float(getattr(x, "tao", x) or 0)

            def _sum_stake(stake) -> Tuple[float, int]:
                """(Σ stake across subnets, #subnets) for a delegate/nominator."""
                if isinstance(stake, dict):
                    vals = list(stake.values())
                    return sum(_tao(v) for v in vals), len(vals)
                return _tao(stake), 1

            # On its own connection, not the shared one: this is a ~30s
            # full-set call, and the shared socket is serialized behind every
            # snapshot and leaderboard read in flight. Those would starve the
            # walk (and be starved by it) for minutes.
            delegates = self._walk_delegates()

            owners: Dict[str, TraderCandidate] = {}
            noms: Dict[str, TraderCandidate] = {}
            for d in delegates:
                weight, subnets = _sum_stake(d.total_stake)
                cur = owners.get(d.owner_ss58)
                if cur is None:
                    owners[d.owner_ss58] = TraderCandidate(
                        ss58=d.owner_ss58, kind="validator",
                        stake_weight=weight, subnets=subnets,
                        hotkey=d.hotkey_ss58)
                else:
                    cur.stake_weight += weight
                    cur.subnets = max(cur.subnets, subnets)

                for ck, stake in (d.nominators or {}).items():
                    w, sn = _sum_stake(stake)
                    cur = noms.get(ck)
                    if cur is None:
                        noms[ck] = TraderCandidate(ss58=ck, kind="nominator",
                                                   stake_weight=w, subnets=sn)
                    else:
                        cur.stake_weight += w
                        cur.subnets = max(cur.subnets, sn)

            # A delegate owner is also listed as its own nominator; the
            # validator entry wins so the leaderboard never doubles it up.
            for ss58 in owners:
                noms.pop(ss58, None)

            universe = sorted(list(owners.values()) + list(noms.values()),
                              key=lambda c: c.stake_weight, reverse=True)
            for i, c in enumerate(universe):
                c.rank = i + 1
            self._universe_cache = (time.time(), universe)
            log.info("trader universe: %d coldkeys (%d validators, %d nominators)",
                     len(universe), len(owners), len(noms))
            return universe

    # ── balances ─────────────────────────────────────────────────────

    def get_balance(self, ss58: str) -> float:
        def _go():
            bal = self.sub.get_balance(ss58)
            return float(bal.tao) if hasattr(bal, "tao") else float(bal) / 1e9
        return self._with_failover(_go)

    # ── staking operations ───────────────────────────────────────────

    def stake(self, wallet: bt.Wallet, hotkey_ss58: str,
              netuid: int, amount_tao: float) -> Optional[str]:
        """Stake TAO into a subnet. Returns success flag."""
        try:
            with self._lock:
                result = self.sub.add_stake(
                    wallet=wallet,
                    hotkey_ss58=hotkey_ss58,
                    amount=bt.Balance.from_tao(amount_tao),
                    netuid=netuid,
                )
            return "ok" if result else None
        except Exception as e:
            raise RuntimeError(f"stake failed: {e}")

    def unstake(self, wallet: bt.Wallet, hotkey_ss58: str,
                netuid: int, amount_tao: float) -> Optional[str]:
        """Unstake from a subnet. Returns success flag."""
        try:
            with self._lock:
                result = self.sub.unstake(
                    wallet=wallet,
                    hotkey_ss58=hotkey_ss58,
                    amount=bt.Balance.from_tao(amount_tao),
                    netuid=netuid,
                )
            return "ok" if result else None
        except Exception as e:
            raise RuntimeError(f"unstake failed: {e}")

    # ── raw substrate queries ────────────────────────────────────────

    def _query(self, storage_fn: str, params: list,
               block_hash: Optional[str] = None) -> Any:
        def _go():
            return self.sub.substrate.query(
                module="SubtensorModule",
                storage_function=storage_fn,
                params=params,
                block_hash=block_hash,
            )
        try:
            return self._with_failover(_go)
        except Exception:
            return None

    def _query_value(self, storage_fn: str, params: list,
                     block_hash: Optional[str] = None, default: Any = 0) -> Any:
        result = self._query(storage_fn, params, block_hash)
        if result and hasattr(result, "value"):
            return result.value
        return default

    # ── health ───────────────────────────────────────────────────────

    def health(self) -> Dict:
        try:
            block = self.get_block()
            return {
                "connected": True,
                "network": self.network,
                "block": block,
                "endpoint": self._current,
                "pool_size": len(self.endpoints),
                "pool": self.endpoints,
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e),
                "network": self.network,
                "pool_size": len(self.endpoints),
                "pool": self.endpoints,
            }
