"""
Periodic snapshot capture — snapshots watched accounts' subnet positions
into SQLite so PnL can be calculated without archive node access.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..db import Database
from .client import SubtensorClient

log = logging.getLogger("copytensor.snapshot")


class SnapshotManager:
    """Takes periodic snapshots of watched accounts' alpha positions."""

    def __init__(self, client: SubtensorClient, db: Database,
                 interval_sec: int = 1800, workers: int = 8,
                 hold: Optional[Callable[[], bool]] = None):
        self.client = client
        self.db = db
        self.interval_sec = interval_sec
        self.workers = max(1, int(workers))
        # Returns True while some other heavy chain phase (pool discovery)
        # is running — everything shares one process, so overlapping the two
        # only makes both slower.
        self.hold = hold or (lambda: False)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def take_snapshot(self, ss58: str) -> Optional[Dict]:
        """Take a single snapshot of an account's current positions."""
        try:
            positions = self.client.get_stake_for_coldkey(ss58)
            block = positions.block
            timestamp = datetime.now(timezone.utc).isoformat()

            allocations = [
                {
                    "netuid": p.netuid,
                    "hotkey": p.hotkey,
                    "alpha": p.alpha_amount,
                    "price_tao": p.alpha_price_tao,
                    "value_tao": p.value_tao,
                }
                for p in positions.positions
            ]

            self.db.insert_snapshot(
                ss58=ss58,
                block=block,
                timestamp=timestamp,
                total_value_tao=positions.total_value_tao,
                allocations=allocations,
            )

            log.debug("snapshot %s block=%d value=%.4f subnets=%d",
                      ss58[:8], block, positions.total_value_tao, len(allocations))

            return {
                "ss58": ss58,
                "block": block,
                "timestamp": timestamp,
                "total_value_tao": positions.total_value_tao,
                "allocations": allocations,
            }
        except Exception as e:
            log.error("snapshot failed for %s: %s", ss58[:8], e)
            return None

    def snapshot_all(self) -> List[Dict]:
        """Snapshot all watched accounts, a few at a time.

        The pool is hundreds of accounts; serially that outlasts the
        interval itself, so each cycle would start already behind.
        """
        accounts = self.db.list_accounts()
        if not accounts:
            return []
        started = time.time()
        n = min(self.workers, len(accounts))
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="snap") as pool:
            results = [r for r in pool.map(
                lambda a: self.take_snapshot(a["ss58"]), accounts) if r]
        log.info("snapshot pass: %d/%d accounts in %.1fs",
                 len(results), len(accounts), time.time() - started)
        return results

    async def run_loop(self):
        """Background loop: snapshot all watched accounts periodically."""
        self._running = True
        log.info("snapshot loop started, interval=%ds", self.interval_sec)
        while self._running:
            try:
                while self._running and self.hold():
                    await asyncio.sleep(5)
                # chain reads are sync/blocking — keep them off the event loop
                await asyncio.to_thread(self.snapshot_all)
            except Exception as e:
                log.error("snapshot loop error: %s", e)
            await asyncio.sleep(self.interval_sec)

    def start(self):
        if self._task and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self.run_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
