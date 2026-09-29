"""
Venue adapters — the execution layer of the strat protocol.

Every adapter is a CLIENT of the fleet module that already owns that chain
(the defi-desk rule): this module holds no key and mints no credential — a
caller's bearer token is forwarded verbatim to the peer, so a compromise of
strat cannot sign. Peers are discovered locally from their own
orbit/<mod>/config.json (urls.api, else port), overridable per venue with
STRAT_<VENUE>_URL.

    raydium      -> solana module  (Jupiter routes; Raydium pools via /pools)
    uniswap      -> defi module    /dex/* (QuoterV2 + SwapRouter02 via eth)
    hyperliquid  -> hyperliquid module (MCP tools over /mcp)
    bittensor    -> bt module      (dTAO subnet pools, MCP tools)
    polymarket   -> polymarket module (REST, owner-gated upstream)

Live placement is guarded twice: place() returns needs_confirm until the
caller passes confirm=True (dry runs always pass), and the peer module's
own gate still applies underneath.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Optional

from protocol import ExecutionResult, Order, OrderSide, VenueTrade

ORBIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 15


class Peer:
    """A fleet module reachable over HTTP, REST and/or MCP."""

    def __init__(self, mod: str, env: str):
        self.mod = mod
        self.env = env

    def _config_urls(self) -> dict:
        try:
            with open(os.path.join(ORBIT, self.mod, "config.json")) as f:
                c = json.load(f)
        except Exception:
            return {}
        urls = dict(c.get("urls") or {})
        if not urls.get("api"):
            port = (c.get("ports") or {}).get("api") or c.get("port")
            if port:
                urls["api"] = f"http://localhost:{port}"
        return urls

    def base(self) -> str:
        url = os.environ.get(self.env)
        if url:
            return url.rstrip("/")
        api = self._config_urls().get("api")
        if api:
            return api.rstrip("/")
        return f"http://localhost:9000/api/{self.mod}"  # activator knock fallback

    def mcp_base(self) -> str:
        """MCP endpoint — a module may serve it off a different path than
        its REST api (bt: api at /api, mcp at /mcp)."""
        mcp = self._config_urls().get("mcp")
        if mcp and not os.environ.get(self.env):
            return mcp.rstrip("/")
        return self.base() + "/mcp"

    def _req(self, method: str, url: str, body: Any = None,
             token: Optional[str] = None, timeout: int = TIMEOUT) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", token if token.startswith("Bearer ")
                           else f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

    def get(self, path: str, token: Optional[str] = None) -> Any:
        return self._req("GET", self.base() + path, token=token)

    def post(self, path: str, body: Any, token: Optional[str] = None) -> Any:
        return self._req("POST", self.base() + path, body, token)

    def mcp(self, tool: str, args: dict, token: Optional[str] = None) -> Any:
        """Call one MCP tool over Streamable HTTP JSON-RPC."""
        # Cold reads (a trader's first fills fetch) can take the peer a
        # while — give tool calls more room than plain probes.
        out = self._req("POST", self.mcp_base(), {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, token, timeout=90)
        result = (out or {}).get("result", out)
        content = (result or {}).get("content")
        if isinstance(content, list) and content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except Exception:
                return content[0]["text"]
        return result

    def alive(self) -> bool:
        for probe in ("/health", "/"):
            try:
                self._req("GET", self.base() + probe, timeout=5)
                return True
            except urllib.error.HTTPError:
                return True   # answered, even if 4xx — the peer is up
            except Exception:
                continue
        return False


class VenueError(Exception):
    pass


class Venue:
    """Adapter contract every venue implements."""
    name = ""
    mod = ""            # backing fleet module
    currency = "USD"

    def __init__(self):
        self.peer = Peer(self.mod, f"STRAT_{self.name.upper()}_URL")

    def info(self, check: bool = False) -> dict:
        d = {"venue": self.name, "module": self.mod, "url": self.peer.base(),
             "currency": self.currency}
        if check:
            d["reachable"] = self.peer.alive()
        return d

    # Reads — best-effort: an unreachable peer yields [] / None, never a crash.
    def quote(self, symbol: str, side: OrderSide, size: float) -> Optional[float]:
        return None

    def trades(self, trader: str, since_ms: int,
               token: Optional[str] = None) -> list[VenueTrade]:
        return []

    # Writes — guarded. dry_run always passes; live needs confirm=True and
    # usually a forwarded token the PEER will judge.
    def place(self, order: Order, token: Optional[str] = None,
              confirm: bool = False, dry_run: bool = True) -> ExecutionResult:
        if not dry_run and not confirm:
            return ExecutionResult(order=order, success=False,
                                   error="needs_confirm: pass confirm=true for a live order")
        try:
            return self._place(order, token, dry_run)
        except Exception as e:
            return ExecutionResult(order=order, success=False, error=str(e))

    def _place(self, order: Order, token: Optional[str],
               dry_run: bool) -> ExecutionResult:
        return ExecutionResult(order=order, success=False,
                               error=f"{self.name}: placement not implemented")

    @staticmethod
    def _pair(symbol: str) -> tuple[str, str]:
        if "/" not in symbol:
            raise VenueError(f"symbol must be '<sell>/<buy>' pair, got {symbol!r}")
        a, b = symbol.split("/", 1)
        return a, b


class Raydium(Venue):
    """Solana AMM liquidity, executed through the solana module (Jupiter
    routes span Raydium; /pools exposes the Raydium pools themselves)."""
    name = "raydium"
    mod = "solana"
    currency = "USDC"

    def quote(self, symbol, side, size):
        base, quote = self._pair(symbol)
        inp, out, amt = (quote, base, size) if side == OrderSide.BUY else (base, quote, size)
        try:
            q = self.peer.get(f"/quote?input={inp}&output={out}&amount={amt}")
            out_amt = float((q.get("buy") or {}).get("amount")
                            or q.get("out_amount") or 0)
            return (size / out_amt) if side == OrderSide.BUY and out_amt else \
                   (out_amt / size) if out_amt else None
        except Exception:
            return None

    def pools(self, mint: str) -> Any:
        return self.peer.get(f"/pools?mint={mint}")

    def trades(self, trader, since_ms, token=None):
        try:
            h = self.peer.get(f"/history?address={trader}&limit=50", token=token)
            items = h if isinstance(h, list) else h.get("history") or h.get("items") or []
            out = []
            for i, t in enumerate(items):
                ts = int(t.get("block_time") or t.get("timestamp") or 0) * (
                    1000 if int(t.get("block_time") or t.get("timestamp") or 0) < 10**12 else 1)
                if ts < since_ms:
                    continue
                out.append(VenueTrade(
                    id=str(t.get("signature") or f"{trader}:{i}"), venue=self.name,
                    trader=trader, timestamp=ts,
                    symbol=str(t.get("mint") or t.get("token") or "SOL/USDC"),
                    side=OrderSide.BUY if str(t.get("side", "buy")).lower() == "buy" else OrderSide.SELL,
                    size=float(t.get("amount") or 0), price=float(t.get("price") or 0),
                    extras={"raw": t.get("type")}))
            return out
        except Exception:
            return []

    def _place(self, order, token, dry_run):
        base, quote = self._pair(order.symbol)
        inp, out = (quote, base) if order.side == OrderSide.BUY else (base, quote)
        amount = order.size * order.price if order.side == OrderSide.BUY else order.size
        r = self.peer.post("/swap", {
            "input": inp, "output": out, "amount": amount,
            "slippage_bps": 300, "dry_run": dry_run, "confirm": not dry_run,
        }, token)
        ok = bool(r.get("signature") or r.get("dry_run") or r.get("success"))
        return ExecutionResult(order=order, success=ok,
                               order_id=r.get("signature"),
                               error=None if ok else str(r),
                               filled_size=order.size if ok else 0.0,
                               filled_price=order.price if ok else 0.0)


class Uniswap(Venue):
    """Uniswap V3 on Ethereum/Base through the defi module's DEX desk,
    which quotes on QuoterV2 and signs via the eth module."""
    name = "uniswap"
    mod = "defi"
    currency = "USDC"

    def __init__(self, chain: str = "ethereum"):
        super().__init__()
        self.chain = chain

    def quote(self, symbol, side, size):
        base, quote = self._pair(symbol)
        sell, buy, amt = (quote, base, size) if side == OrderSide.BUY else (base, quote, size)
        try:
            q = self.peer.post("/dex/quote", {
                "chain": self.chain, "sell": sell, "buy": buy, "amount": amt})
            out_amt = float((q.get("buy") or {}).get("amount")
                            or q.get("expected_out") or 0)
            return (size / out_amt) if side == OrderSide.BUY and out_amt else \
                   (out_amt / size) if out_amt else None
        except Exception:
            return None

    def _place(self, order, token, dry_run):
        base, quote = self._pair(order.symbol)
        sell, buy = (quote, base) if order.side == OrderSide.BUY else (base, quote)
        amount = order.size * order.price if order.side == OrderSide.BUY else order.size
        r = self.peer.post("/dex/swap", {
            "chain": self.chain, "sell": sell, "buy": buy, "amount": amount,
            "account": order.tag or "default", "dryRun": dry_run,
            "confirm": not dry_run, "slippageBps": 300,
        }, token)
        if r.get("needs_confirm"):
            return ExecutionResult(order=order, success=False, error="needs_confirm (defi)")
        ok = bool(r.get("tx") or r.get("dryRun") or r.get("dry_run") or r.get("success"))
        return ExecutionResult(order=order, success=ok, order_id=r.get("tx"),
                               error=None if ok else str(r),
                               filled_size=order.size if ok else 0.0,
                               filled_price=order.price if ok else 0.0)


class Hyperliquid(Venue):
    """Perps/spot through the hyperliquid module's MCP tools."""
    name = "hyperliquid"
    mod = "hyperliquid"
    currency = "USDC"

    def quote(self, symbol, side, size):
        try:
            mids = self.peer.mcp("hl_mids", {})
            m = mids.get("mids", mids) if isinstance(mids, dict) else {}
            v = m.get(symbol)
            return float(v) if v is not None else None
        except Exception:
            return None

    def trades(self, trader, since_ms, token=None):
        try:
            items = []
            for attempt in range(3):
                fills = self.peer.mcp("hl_user_fills", {"address": trader}, token)
                if isinstance(fills, str):   # upstream 429s transiently
                    time.sleep(2 * (attempt + 1))
                    continue
                items = fills if isinstance(fills, list) else (fills or {}).get("fills") or []
                break
            out = []
            for f in items:
                ts = int(f.get("time") or 0)
                if ts < since_ms:
                    continue
                out.append(VenueTrade(
                    id=str(f.get("tid") or f.get("hash") or ts), venue=self.name,
                    trader=trader, timestamp=ts, symbol=str(f.get("coin") or ""),
                    side=OrderSide.BUY if str(f.get("side", "B")).upper().startswith("B") else OrderSide.SELL,
                    size=float(f.get("sz") or 0), price=float(f.get("px") or 0),
                    extras={"dir": f.get("dir")}))
            return out
        except Exception:
            return []

    def _place(self, order, token, dry_run):
        if dry_run:
            return ExecutionResult(order=order, success=True, order_id="dry-run",
                                   filled_size=order.size, filled_price=order.price)
        r = self.peer.mcp("hl_trade", {
            "coin": order.symbol, "is_buy": order.side == OrderSide.BUY,
            "sz": order.size, "limit_px": order.price, "order_type": "limit",
        }, token)
        ok = isinstance(r, dict) and r.get("status") in ("ok", "success")
        return ExecutionResult(order=order, success=ok,
                               order_id=str((r or {}).get("oid") or ""),
                               error=None if ok else str(r),
                               filled_size=order.size if ok else 0.0,
                               filled_price=order.price if ok else 0.0)


class Bittensor(Venue):
    """dTAO subnet pools (symbol 'SN<netuid>') through the bt module.
    Prices in TAO per alpha; a SELL amount is TAO-equivalent (bt_sell rule)."""
    name = "bittensor"
    mod = "bt"
    currency = "TAO"

    @staticmethod
    def _netuid(symbol: str) -> int:
        s = symbol.upper().lstrip("SN").lstrip("N")
        return int(s)

    def quote(self, symbol, side, size):
        try:
            p = self.peer.mcp("bt_price", {"netuid": self._netuid(symbol)})
            v = p.get("price") if isinstance(p, dict) else p
            return float(v) if v is not None else None
        except Exception:
            return None

    def trades(self, trader, since_ms, token=None):
        try:
            r = self.peer.mcp("bt_trader_flows", {"coldkey": trader}, token)
            items = r if isinstance(r, list) else (r or {}).get("flows") or []
            out = []
            for i, f in enumerate(items):
                ts = int(f.get("timestamp") or f.get("ts") or 0)
                ts = ts * 1000 if ts and ts < 10**12 else ts
                if ts < since_ms:
                    continue
                side_s = str(f.get("side") or f.get("direction") or "buy").lower()
                out.append(VenueTrade(
                    id=str(f.get("id") or f"{trader}:{ts}:{f.get('netuid')}:{side_s}"),
                    venue=self.name, trader=trader, timestamp=ts,
                    symbol=f"SN{f.get('netuid')}",
                    side=OrderSide.BUY if "buy" in side_s or "stake" in side_s else OrderSide.SELL,
                    size=float(f.get("alpha") or f.get("amount") or 0),
                    price=float(f.get("price") or 0),
                    extras={"tao_value": f.get("tao_value"), "block": f.get("block")}))
            return out
        except Exception:
            return []

    def _place(self, order, token, dry_run):
        if dry_run:
            return ExecutionResult(order=order, success=True, order_id="dry-run",
                                   filled_size=order.size, filled_price=order.price)
        netuid = self._netuid(order.symbol)
        if order.side == OrderSide.BUY:
            r = self.peer.mcp("bt_buy", {"netuid": netuid,
                                         "amount": order.size * order.price}, token)
        else:
            # bt_sell takes a TAO-equivalent amount, not alpha.
            r = self.peer.mcp("bt_sell", {"netuid": netuid,
                                          "amount": order.size * order.price}, token)
        ok = isinstance(r, dict) and not r.get("error")
        return ExecutionResult(order=order, success=ok, error=None if ok else str(r),
                               filled_size=order.size if ok else 0.0,
                               filled_price=order.price if ok else 0.0)


class Polymarket(Venue):
    """Prediction markets through the polymarket module (owner-gated REST).
    Direct order placement is deliberately NOT offered — the polymarket
    module's own live engine (sessions, autoExecute off by default) is the
    execution surface; this adapter reads the tape and manages sessions."""
    name = "polymarket"
    mod = "polymarket"
    currency = "USDC"

    def trades(self, trader, since_ms, token=None):
        try:
            r = self.peer.get(f"/trader/{trader}/trades?since={since_ms}", token=token)
            items = r if isinstance(r, list) else (r or {}).get("trades") or []
            out = []
            for t in items:
                ts = int(t.get("timestamp") or 0)
                ts = ts * 1000 if ts and ts < 10**12 else ts
                if ts < since_ms:
                    continue
                out.append(VenueTrade(
                    id=str(t.get("id") or t.get("transactionHash") or ts),
                    venue=self.name, trader=trader, timestamp=ts,
                    symbol=str(t.get("asset") or t.get("token_id") or ""),
                    side=OrderSide.BUY if str(t.get("side", "BUY")).upper() == "BUY" else OrderSide.SELL,
                    size=float(t.get("size") or 0), price=float(t.get("price") or 0),
                    extras={"market": t.get("market") or t.get("title")}))
            return out
        except Exception:
            return []

    def _place(self, order, token, dry_run):
        if dry_run:
            return ExecutionResult(order=order, success=True, order_id="dry-run",
                                   filled_size=order.size, filled_price=order.price)
        return ExecutionResult(
            order=order, success=False,
            error="polymarket orders go through the polymarket module's live "
                  "engine (POST /live/start, autoExecute defaults off) — not "
                  "placed directly from strat, by design")

    def start_copy(self, trader: str, bankroll: float,
                   token: Optional[str] = None, auto_execute: bool = False) -> Any:
        """Start a one-leader copy session (DRY RUN unless auto_execute)."""
        return self.peer.post("/live/start", {
            "traders": [{"address": trader, "weight": 1.0}],
            "bankroll": bankroll, "sizing": "bankroll",
            "autoExecute": bool(auto_execute),
        }, token)

    def stop_copy(self, session: str, token: Optional[str] = None) -> Any:
        """Stop a session. Never confirm-gated — exits must always work."""
        return self.peer.post("/live/stop", {"session": session}, token)


def registry() -> dict[str, Venue]:
    return {v.name: v for v in (Raydium(), Uniswap(), Hyperliquid(),
                                Bittensor(), Polymarket())}
