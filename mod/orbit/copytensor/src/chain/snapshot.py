"""
Periodic snapshot capture — snapshots watched accounts' subnet positions
into SQLite so PnL can be calculated without archive node access.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..db import Database
from .client import SubtensorClient

log = logging.getLogger("copytensor.snapshot")


class SnapshotManager:
    """Takes periodic snapshots of watched accounts' alpha positions."""

    def __init__(self, client: SubtensorClient, db: Database,
                 interval_sec: int = 1800):
        self.client = client
        self.db = db
        self.interval_sec = interval_sec
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

            log.info("snapshot %s block=%d value=%.4f subnets=%d",
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
        """Snapshot all watched accounts."""
        accounts = self.db.list_accounts()
        results = []
        for acct in accounts:
            result = self.take_snapshot(acct["ss58"])
            if result:
                results.append(result)
        return results

    async def run_loop(self):
        """Background loop: snapshot all watched accounts periodically."""
        self._running = True
        log.info("snapshot loop started, interval=%ds", self.interval_sec)
        while self._running:
            try:
                self.snapshot_all()
            except Exception as e:
                log.error("snapshot loop error: %s", e)
            await asyncio.sleep(self.interval_sec)

    def start(self):
        if self._task and not self._task.done():
            return
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self.run_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
