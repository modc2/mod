"""Taox — convert ETH and SOL to native TAO.

Front-end flow:
  1. User connects MetaMask (ETH source) and SubWallet (TAO destination, ss58).
     If swapping SOL, SubWallet also signs the Solana send.
  2. User picks `from` (eth|sol) and amount → `quote()` returns expected TAO out.
  3. User confirms → `swap()` opens an order with a deposit address and quoted rate.
  4. User wallet sends the source asset to that deposit address. The user reports
     the source tx hash via `confirm_deposit()`.
  5. Operator (or watcher) calls `mark_paid()` once the deposit confirms, then
     dispatches native TAO from the hot wallet to the destination ss58.

Pricing is read from CoinGecko (USD-denominated) and a configurable fee is
applied. No on-chain bridging primitive is used — this is a custodial
swap-by-deposit model intentionally kept simple.

Storage: ~/.taox/orders.json
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

CG_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
ORDER_STATES = (
    "awaiting_deposit",
    "deposit_seen",
    "confirming",
    "delivering",
    "completed",
    "failed",
    "cancelled",
)


class Mod:
    description = "Convert ETH and SOL to native TAO via custodial swap-by-deposit."

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        here = Path(__file__).parent
        # Module root is the taox/ directory: config.json + start.sh live there,
        # while this file is at taox/src/mod.py.
        self.module_dir = here.parent if (here.parent / "config.json").exists() else here
        self.config = config or self._load_config()

        self.store_dir = Path(os.path.expanduser("~/.taox"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.store_dir / "orders.json"
        self.rates_cache_path = self.store_dir / "rates_cache.json"

        self.port = int(self.config.get("port", 8870))
        self.app_port = int(self.config.get("app_port", 3870))
        self.fee_bps = int(self.config.get("fee_bps", 50))
        self.sources = self.config.get("sources", {})
        self.destination = self.config.get("destination", {})

    def _load_config(self) -> Dict[str, Any]:
        path = self.module_dir / "config.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    # ── Storage ────────────────────────────────────────────────────

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    def _write_json(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    def _load_orders(self) -> Dict[str, dict]:
        return self._read_json(self.orders_path, {})

    def _save_orders(self, orders: Dict[str, dict]) -> None:
        self._write_json(self.orders_path, orders)

    # ── Health / status ────────────────────────────────────────────

    def health(self) -> dict:
        return {
            "status": "ok",
            "module": "taox",
            "sources": list(self.sources.keys()),
            "destination": self.destination.get("chain", "bittensor"),
        }

    def status(self) -> dict:
        orders = self._load_orders()
        by_state: Dict[str, int] = {}
        for o in orders.values():
            by_state[o.get("state", "unknown")] = by_state.get(o.get("state", "unknown"), 0) + 1
        return {
            "orders": len(orders),
            "by_state": by_state,
            "fee_bps": self.fee_bps,
            "sources_supported": list(self.sources.keys()),
        }

    # ── Pricing ────────────────────────────────────────────────────

    def rates(self, refresh: bool = False) -> dict:
        """Return USD prices for ETH, SOL, TAO and the implied TAO/source rates.

        Cached on disk for 60s to avoid hammering CoinGecko.
        """
        cache = self._read_json(self.rates_cache_path, {})
        if not refresh and cache and time.time() - cache.get("ts", 0) < 60:
            return cache

        ids = ",".join(
            sorted({s["coingecko_id"] for s in self.sources.values()} | {self.destination["coingecko_id"]})
        )
        url = f"{CG_PRICE_URL}?ids={ids}&vs_currencies=usd"
        req = Request(url, headers={"User-Agent": "taox/0.1"})
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, TimeoutError) as e:
            logger.warning("rates fetch failed: %s", e)
            if cache:
                cache["stale"] = True
                return cache
            raise

        usd = {k: float(v["usd"]) for k, v in data.items() if "usd" in v}
        tao_usd = usd.get(self.destination["coingecko_id"], 0.0)
        pairs: Dict[str, float] = {}
        for sym, cfg in self.sources.items():
            src_usd = usd.get(cfg["coingecko_id"], 0.0)
            pairs[f"{sym}_to_tao"] = (src_usd / tao_usd) if tao_usd else 0.0

        out = {"ts": int(time.time()), "usd": usd, "pairs": pairs, "stale": False}
        self._write_json(self.rates_cache_path, out)
        return out

    def quote(self, from_token: str, amount: float) -> dict:
        """Return expected TAO output for `amount` of `from_token` after fee."""
        sym = from_token.lower()
        if sym not in self.sources:
            return {"error": f"unsupported source token '{from_token}'", "supported": list(self.sources)}
        amt = float(amount)
        if amt <= 0:
            return {"error": "amount must be positive"}

        r = self.rates()
        rate = r["pairs"].get(f"{sym}_to_tao", 0.0)
        if rate <= 0:
            return {"error": "rate unavailable", "rates": r}

        gross_tao = amt * rate
        fee_tao = gross_tao * self.fee_bps / 10_000
        net_tao = gross_tao - fee_tao
        return {
            "from": sym,
            "amount_in": amt,
            "rate": rate,
            "gross_tao": gross_tao,
            "fee_bps": self.fee_bps,
            "fee_tao": fee_tao,
            "tao_out": net_tao,
            "rates_ts": r["ts"],
            "rates_stale": r.get("stale", False),
        }

    # ── Deposit address ────────────────────────────────────────────

    def deposit_address(self, from_token: str) -> dict:
        sym = from_token.lower()
        cfg = self.sources.get(sym)
        if not cfg:
            return {"error": f"unsupported source token '{from_token}'"}
        addr = os.environ.get(cfg["deposit_env"], "")
        if not addr:
            return {
                "error": "deposit address not configured",
                "hint": f"set {cfg['deposit_env']} in the operator environment",
            }
        return {"from": sym, "chain": cfg["chain"], "deposit_address": addr}

    # ── Validation ─────────────────────────────────────────────────

    @staticmethod
    def _is_evm_address(addr: str) -> bool:
        a = (addr or "").strip()
        return len(a) == 42 and a.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in a[2:])

    @staticmethod
    def _is_ss58(addr: str) -> bool:
        a = (addr or "").strip()
        return 45 <= len(a) <= 50 and a.isalnum()

    @staticmethod
    def _is_solana_address(addr: str) -> bool:
        a = (addr or "").strip()
        return 32 <= len(a) <= 44 and a.isalnum()

    def _validate_source_address(self, sym: str, addr: str) -> Optional[str]:
        if sym == "eth":
            return None if self._is_evm_address(addr) else "invalid EVM address"
        if sym == "sol":
            return None if self._is_solana_address(addr) else "invalid Solana address"
        return f"unknown source '{sym}'"

    # ── Orders ─────────────────────────────────────────────────────

    def swap(
        self,
        from_token: str,
        amount: float,
        source_address: str,
        destination_ss58: str,
        slippage_bps: int = 100,
    ) -> dict:
        """Open a swap order. Returns the order with the deposit address to send to."""
        sym = from_token.lower()
        cfg = self.sources.get(sym)
        if not cfg:
            return {"error": f"unsupported source token '{from_token}'"}

        src_err = self._validate_source_address(sym, source_address)
        if src_err:
            return {"error": src_err}
        if not self._is_ss58(destination_ss58):
            return {"error": "invalid destination ss58 address"}

        q = self.quote(sym, amount)
        if "error" in q:
            return q

        deposit = self.deposit_address(sym)
        if "error" in deposit:
            return deposit

        oid = uuid.uuid4().hex[:16]
        order = {
            "id": oid,
            "created": int(time.time()),
            "from": sym,
            "amount_in": float(amount),
            "source_address": source_address,
            "destination_ss58": destination_ss58,
            "deposit_address": deposit["deposit_address"],
            "quoted_rate": q["rate"],
            "quoted_tao_out": q["tao_out"],
            "fee_bps": self.fee_bps,
            "slippage_bps": int(slippage_bps),
            "state": "awaiting_deposit",
            "source_tx": None,
            "delivery_tx": None,
            "history": [{"ts": int(time.time()), "state": "awaiting_deposit"}],
        }

        orders = self._load_orders()
        orders[oid] = order
        self._save_orders(orders)
        return order

    def order(self, order_id: str) -> dict:
        orders = self._load_orders()
        o = orders.get(order_id)
        return o or {"error": "not found", "order_id": order_id}

    def orders(self, source_address: Optional[str] = None, limit: int = 50) -> list:
        items = list(self._load_orders().values())
        if source_address:
            items = [o for o in items if o.get("source_address", "").lower() == source_address.lower()]
        items.sort(key=lambda o: o.get("created", 0), reverse=True)
        return items[: int(limit)]

    def confirm_deposit(self, order_id: str, source_tx: str) -> dict:
        """User-initiated: report the tx hash they sent from MetaMask / SubWallet."""
        orders = self._load_orders()
        o = orders.get(order_id)
        if not o:
            return {"error": "not found", "order_id": order_id}
        if o["state"] not in ("awaiting_deposit", "deposit_seen"):
            return {"error": f"cannot confirm in state '{o['state']}'"}
        o["source_tx"] = source_tx.strip()
        o["state"] = "deposit_seen"
        o["history"].append({"ts": int(time.time()), "state": "deposit_seen", "tx": source_tx})
        self._save_orders(orders)
        return o

    def mark_paid(self, order_id: str, delivery_tx: str) -> dict:
        """Operator-initiated: record on-chain TAO transfer to the user."""
        orders = self._load_orders()
        o = orders.get(order_id)
        if not o:
            return {"error": "not found", "order_id": order_id}
        o["delivery_tx"] = delivery_tx.strip()
        o["state"] = "completed"
        o["history"].append({"ts": int(time.time()), "state": "completed", "tx": delivery_tx})
        self._save_orders(orders)
        return o

    def cancel(self, order_id: str, reason: str = "user_cancelled") -> dict:
        orders = self._load_orders()
        o = orders.get(order_id)
        if not o:
            return {"error": "not found", "order_id": order_id}
        if o["state"] in ("completed", "cancelled"):
            return {"error": f"cannot cancel in state '{o['state']}'"}
        o["state"] = "cancelled"
        o["history"].append({"ts": int(time.time()), "state": "cancelled", "reason": reason})
        self._save_orders(orders)
        return o

    # ── Service control ────────────────────────────────────────────

    def serve(self, **_) -> dict:
        """Start the Rust API + Next.js app via `start.sh` in the module dir."""
        script = self.module_dir / "start.sh"
        if not script.exists():
            return {"error": "start.sh missing"}
        os.system(f"bash {script} &")
        return {"started": True, "api": f"http://localhost:{self.port}", "app": f"http://localhost:{self.app_port}"}

    def kill(self, **_) -> dict:
        script = self.module_dir / "stop.sh"
        if script.exists():
            os.system(f"bash {script}")
        return {"killed": True}

    def forward(self, **kwargs):
        return self.health()
