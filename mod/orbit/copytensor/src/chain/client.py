"""
Subtensor chain client — all on-chain reads/writes for copytensor.

Talks directly to the Bittensor blockchain via the open-source bittensor SDK
against a rotating pool of public RPC endpoints — no third-party APIs, no
single host of failure. Reads work without any wallet; only stake/unstake
operations require a wallet.
"""

import logging
import random
import time
from dataclasses import dataclass, field
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
class AccountPositions:
    ss58: str
    block: int
    total_value_tao: float
    positions: List[AlphaPosition] = field(default_factory=list)


class SubtensorClient:
    """Wraps bt.subtensor() with round-robin failover across public RPCs.

    Every call to `.sub` returns a connected subtensor; if a call fails we
    rotate to the next endpoint automatically via `_with_failover()`. The
    pool shuffles on init so different processes pin different primaries —
    no single provider sees all our traffic.
    """

    def __init__(self, network: str = "finney", endpoint: Optional[str] = None,
                 endpoints: Optional[List[str]] = None):
        self.network = network
        self.endpoint = endpoint
        pool = endpoints if endpoints else _endpoints_for(network, endpoint)
        random.shuffle(pool)
        self.endpoints: List[str] = pool
        self._idx = 0
        self._sub: Optional[bt.subtensor] = None
        self._current: Optional[str] = None

    @property
    def sub(self) -> bt.subtensor:
        if self._sub is None:
            self._connect()
        return self._sub

    def _connect(self) -> bt.subtensor:
        """Try endpoints in rotation until one connects."""
        last_err: Optional[Exception] = None
        for _ in range(len(self.endpoints)):
            url = self.endpoints[self._idx]
            try:
                self._sub = bt.subtensor(network=url)
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
        for _ in range(n):
            try:
                return fn()
            except Exception as e:
                last_err = e
                log.warning("rpc call failed on %s: %s — rotating", self._current, e)
                self._rotate()
        raise RuntimeError(f"all RPCs failed; last error: {last_err}")

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
        return self._with_failover(lambda: self.sub.get_subnets())

    def get_subnet_info(self, netuid: int, block: Optional[int] = None) -> SubnetInfo:
        block_hash = self.get_block_hash(block) if block else None

        name = self._query("SubnetIdentity", [netuid], block_hash)
        subnet_name = ""
        if name and hasattr(name, "value") and name.value:
            subnet_name = name.value.get("subnet_name", f"SN{netuid}")
        else:
            subnet_name = f"SN{netuid}"

        alpha_price = self._get_alpha_price(netuid, block_hash)
        total_stake = self._get_total_subnet_stake(netuid, block_hash)
        tempo = self._query_value("Tempo", [netuid], block_hash, default=360)
        emission = self._query_value("SubnetEmission", [netuid], block_hash, default=0)

        return SubnetInfo(
            netuid=netuid,
            name=subnet_name,
            alpha_price_tao=alpha_price,
            total_stake_tao=total_stake,
            tempo=tempo,
            emission=emission / 1e9 if emission else 0,
        )

    def get_all_subnet_info(self, block: Optional[int] = None) -> List[SubnetInfo]:
        netuids = self.get_all_netuids()
        results = []
        for netuid in netuids:
            try:
                info = self.get_subnet_info(netuid, block)
                results.append(info)
            except Exception:
                continue
        return results

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
        """Get all alpha positions for a coldkey across all subnets."""
        block_hash = self.get_block_hash(block) if block else None
        current_block = block or self.get_block()
        netuids = self.get_all_netuids()
        positions: List[AlphaPosition] = []
        total_value = 0.0

        # Get hotkeys associated with this coldkey
        hotkeys = self._get_hotkeys_for_coldkey(ss58, block_hash)

        for netuid in netuids:
            alpha_price = self._get_alpha_price(netuid, block_hash)
            for hotkey in hotkeys:
                alpha = self._get_alpha_stake(netuid, hotkey, ss58, block_hash)
                if alpha > 0:
                    value = alpha * alpha_price
                    total_value += value
                    positions.append(AlphaPosition(
                        netuid=netuid,
                        hotkey=hotkey,
                        alpha_amount=alpha,
                        alpha_price_tao=alpha_price,
                        value_tao=value,
                    ))

        return AccountPositions(
            ss58=ss58,
            block=current_block,
            total_value_tao=total_value,
            positions=positions,
        )

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

    # ── balances ─────────────────────────────────────────────────────

    def get_balance(self, ss58: str) -> float:
        def _go():
            bal = self.sub.get_balance(ss58)
            return float(bal.tao) if hasattr(bal, "tao") else float(bal) / 1e9
        return self._with_failover(_go)

    # ── staking operations ───────────────────────────────────────────

    def stake(self, wallet: bt.wallet, hotkey_ss58: str,
              netuid: int, amount_tao: float) -> Optional[str]:
        """Stake TAO into a subnet. Returns success flag."""
        try:
            result = self.sub.add_stake(
                wallet=wallet,
                hotkey_ss58=hotkey_ss58,
                amount=bt.Balance.from_tao(amount_tao),
                netuid=netuid,
            )
            return "ok" if result else None
        except Exception as e:
            raise RuntimeError(f"stake failed: {e}")

    def unstake(self, wallet: bt.wallet, hotkey_ss58: str,
                netuid: int, amount_tao: float) -> Optional[str]:
        """Unstake from a subnet. Returns success flag."""
        try:
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
