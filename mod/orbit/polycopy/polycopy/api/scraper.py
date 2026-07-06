"""
polycopy scraper - historical swap event scanner

Scans V2/V3 swap events across all chains for a configurable lookback
period (default 7 days). Discovers active traders by counting trades
per address per day, then filters for addresses exceeding a threshold.

Block time estimates (used to compute how far back to scan):
    Base:     ~2s   → ~302,400 blocks/week
    Polygon:  ~2s   → ~302,400 blocks/week
    Arbitrum: ~0.26s → ~2,323,200 blocks/week
    Ethereum: ~12s  → ~50,400 blocks/week

Usage:
    scraper = Scraper(min_trades_per_day=10)
    await scraper.run()              # scan all enabled chains
    traders = scraper.get_traders()  # filtered results
"""
import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiohttp

# ── constants ───────────────────────────────────────────────────────

SWAP_TOPIC_V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SWAP_TOPIC_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

# avg block time in seconds per chain
BLOCK_TIME = {
    8453:  2.0,     # base
    137:   2.0,     # polygon
    42161: 0.26,    # arbitrum
    1:     12.0,    # ethereum
}

CHAIN_RPCS = {
    8453: [
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
        "https://base-rpc.publicnode.com",
        "https://base.drpc.org",
    ],
    137: [
        "https://polygon-rpc.com",
        "https://polygon.llamarpc.com",
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.drpc.org",
    ],
    42161: [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum.llamarpc.com",
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arbitrum.drpc.org",
    ],
    1: [
        "https://eth.llamarpc.com",
        "https://ethereum-rpc.publicnode.com",
        "https://eth.drpc.org",
    ],
}

CHAIN_NAMES = {8453: "base", 137: "polygon", 42161: "arbitrum", 1: "ethereum"}

# max blocks per getLogs call — conservative for public RPCs
CHUNK_SIZE = {
    8453:  5000,
    137:   5000,
    42161: 5000,
    1:     5000,
}

# concurrent requests per chain
CONCURRENCY = 3

# ── types ───────────────────────────────────────────────────────────

@dataclass
class ScrapeProgress:
    chain_id: int
    chain_name: str
    total_blocks: int = 0
    scanned_blocks: int = 0
    swaps_found: int = 0
    phase: str = "pending"    # pending | scanning | done | error
    error: Optional[str] = None

    @property
    def pct(self):
        if self.total_blocks == 0:
            return 0
        return round(100 * self.scanned_blocks / self.total_blocks, 1)

    def to_dict(self):
        return {
            "chain_id": self.chain_id,
            "chain": self.chain_name,
            "total_blocks": self.total_blocks,
            "scanned_blocks": self.scanned_blocks,
            "swaps_found": self.swaps_found,
            "pct": self.pct,
            "phase": self.phase,
            "error": self.error,
        }


@dataclass
class Scraper:
    """Historical swap scanner with active-trader filtering."""

    min_trades_per_day: int = 10
    lookback_days: int = 7
    chains: list = field(default_factory=lambda: [8453, 137, 42161])

    # internals
    # trader_address -> { "YYYY-MM-DD": count }
    _trades: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    _progress: dict = field(default_factory=dict)
    _running: bool = False
    _done: bool = False
    _total_swaps: int = 0

    # ── rpc helpers ─────────────────────────────────────────────────

    async def _rpc_call(self, session, chain_id, method, params, rpc_idx=0):
        rpcs = CHAIN_RPCS.get(chain_id, [])
        if not rpcs:
            return None

        # try each rpc starting from rpc_idx
        for i in range(len(rpcs)):
            url = rpcs[(rpc_idx + i) % len(rpcs)]
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if "error" in data:
                        continue
                    return data.get("result")
            except Exception:
                continue
        return None

    async def _get_block_number(self, session, chain_id):
        result = await self._rpc_call(session, chain_id, "eth_blockNumber", [])
        if result:
            return int(result, 16)
        return None

    async def _get_block_timestamp(self, session, chain_id, block_num):
        result = await self._rpc_call(session, chain_id, "eth_getBlockByNumber", [hex(block_num), False])
        if result and "timestamp" in result:
            return int(result["timestamp"], 16)
        return None

    async def _get_logs(self, session, chain_id, from_block, to_block, topic, rpc_idx=0):
        params = [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [topic],
        }]
        return await self._rpc_call(session, chain_id, "eth_getLogs", params, rpc_idx)

    # ── log parsing ─────────────────────────────────────────────────

    def _extract_traders_from_logs(self, logs, dex):
        """Extract trader addresses from swap logs. Returns list of (trader, block_num)."""
        results = []
        for log in (logs or []):
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            block_hex = log.get("blockNumber", "0x0")
            block_num = int(block_hex, 16)

            # V3: topics = [sig, sender, recipient]
            # V2: topics = [sig, sender, to]
            # both topic[1] and topic[2] are 32-byte padded addresses
            addr1 = "0x" + topics[1][-40:]
            addr2 = "0x" + topics[2][-40:]

            # record both — we filter by frequency later
            results.append((addr1.lower(), block_num))
            results.append((addr2.lower(), block_num))

        return results

    # ── block-to-date mapping ───────────────────────────────────────

    async def _build_block_date_map(self, session, chain_id, from_block, to_block):
        """Sample a few blocks to build a block_number -> date interpolation.
        Returns (start_ts, end_ts, blocks_per_sec) for linear interpolation."""
        start_ts = await self._get_block_timestamp(session, chain_id, from_block)
        end_ts = await self._get_block_timestamp(session, chain_id, to_block)
        if not start_ts or not end_ts or end_ts == start_ts:
            # fallback to estimated block time
            bt = BLOCK_TIME.get(chain_id, 2.0)
            return (time.time() - (to_block - from_block) * bt, time.time(), 1.0 / bt)

        blocks_per_sec = (to_block - from_block) / (end_ts - start_ts)
        return (start_ts, end_ts, blocks_per_sec)

    def _block_to_date(self, block_num, from_block, start_ts, blocks_per_sec):
        """Estimate date string for a block number."""
        elapsed_blocks = block_num - from_block
        ts = start_ts + (elapsed_blocks / blocks_per_sec) if blocks_per_sec > 0 else start_ts
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    # ── chain scanner ───────────────────────────────────────────────

    async def _scan_chain(self, chain_id):
        """Scan one chain for the full lookback period."""
        name = CHAIN_NAMES.get(chain_id, str(chain_id))
        progress = ScrapeProgress(chain_id=chain_id, chain_name=name, phase="scanning")
        self._progress[chain_id] = progress

        chunk = CHUNK_SIZE.get(chain_id, 5000)

        async with aiohttp.ClientSession() as session:
            # get current block
            current = await self._get_block_number(session, chain_id)
            if not current:
                progress.phase = "error"
                progress.error = "could not get block number"
                return

            # estimate how many blocks to go back
            bt = BLOCK_TIME.get(chain_id, 2.0)
            blocks_back = int(self.lookback_days * 86400 / bt)
            from_block = max(1, current - blocks_back)
            to_block = current

            progress.total_blocks = to_block - from_block

            # build block->date interpolation
            start_ts, end_ts, bps = await self._build_block_date_map(
                session, chain_id, from_block, to_block
            )

            # generate chunks
            chunks = []
            b = from_block
            while b <= to_block:
                end = min(b + chunk - 1, to_block)
                chunks.append((b, end))
                b = end + 1

            # scan chunks with bounded concurrency
            sem = asyncio.Semaphore(CONCURRENCY)

            async def scan_chunk(idx, fb, tb):
                async with sem:
                    for topic, dex in [(SWAP_TOPIC_V3, "v3"), (SWAP_TOPIC_V2, "v2")]:
                        logs = await self._get_logs(session, chain_id, fb, tb, topic, rpc_idx=idx % len(CHAIN_RPCS.get(chain_id, [1])))
                        if logs:
                            traders = self._extract_traders_from_logs(logs, dex)
                            for addr, blk in traders:
                                date = self._block_to_date(blk, from_block, start_ts, bps)
                                self._trades[addr][date] += 1
                            progress.swaps_found += len(logs)
                            self._total_swaps += len(logs)

                    progress.scanned_blocks += (tb - fb + 1)

            tasks = [scan_chunk(i, fb, tb) for i, (fb, tb) in enumerate(chunks)]
            await asyncio.gather(*tasks)

        progress.phase = "done"

    # ── public interface ────────────────────────────────────────────

    async def run(self):
        """Scan all configured chains in parallel."""
        self._running = True
        self._done = False
        self._trades.clear()
        self._progress.clear()
        self._total_swaps = 0

        tasks = [self._scan_chain(cid) for cid in self.chains]
        await asyncio.gather(*tasks, return_exceptions=True)

        self._running = False
        self._done = True

    def get_progress(self):
        """Return scan progress for all chains."""
        return {
            "running": self._running,
            "done": self._done,
            "total_swaps": self._total_swaps,
            "chains": [p.to_dict() for p in self._progress.values()],
        }

    def get_traders(self, min_trades_per_day=None):
        """Return traders that meet the minimum trades/day threshold.

        Returns list of {address, chain_trades, daily_counts, avg_trades_per_day, active_days}
        sorted by avg trades/day descending.
        """
        n = min_trades_per_day if min_trades_per_day is not None else self.min_trades_per_day
        result = []

        for addr, daily in self._trades.items():
            # skip zero addresses and common contract addresses
            if addr == "0x" + "0" * 40:
                continue

            total_trades = sum(daily.values())
            active_days = len(daily)
            if active_days == 0:
                continue

            avg_per_day = total_trades / active_days

            # filter: at least N trades/day on average
            if avg_per_day < n:
                continue

            # also require activity on at least 2 days to filter out one-off bots
            if active_days < 2:
                continue

            result.append({
                "address": addr,
                "total_trades": total_trades,
                "active_days": active_days,
                "avg_trades_per_day": round(avg_per_day, 1),
                "daily_counts": dict(sorted(daily.items())),
            })

        result.sort(key=lambda x: x["avg_trades_per_day"], reverse=True)
        return result

    def get_all_trades(self):
        """Return raw trade counts for all addresses (unfiltered)."""
        return {
            "total_addresses": len(self._trades),
            "total_swaps": self._total_swaps,
            "lookback_days": self.lookback_days,
            "min_trades_per_day": self.min_trades_per_day,
        }
