"""cshare — fractional compute marketplace (compute/1.0).

Compute nodes (GPU/CPU machines) are listed as assets split into shares:

    ownership   every node mints `total_shares`; the issuer holds them all
                at listing and can offer any slice on the order book
    trade       sell() places an ask (shares stay dividend-earning but are
                locked), buy() fills it — partial fills allowed
    rental      rent() books a node exclusively by the hour; the fee is
                distributed pro-rata to ALL shareholders at booking time
    ledger      self-contained credit balances per address; deposit() is
                the test faucet

State is a single JSON document under ~/.mod/cshare/ (off-chain, off-repo).

CLI (via `m`):
    m cshare/deposit address=0xme amount=1000
    m cshare/list_node address=0xme name=gpu-01 gpu=H100 rate_hour=2.5
    m cshare/nodes
    m cshare/buy address=0xme order_id=o1 shares=100
    m cshare/rent address=0xme node_id=n1 hours=4
    m cshare/portfolio address=0xme
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL = "compute/1.0"


class Mod:
    description = ("Compute marketplace: fractionalized ownership of compute "
                   "nodes (shares traded on an order book) plus an hourly "
                   "rental market whose income streams pro-rata to shareholders")
    path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        cfg = {}
        try:
            cfg = json.loads((Path(self.path) / "config.json").read_text())
        except Exception:
            pass
        self.port = int(cfg.get("port", 50290))
        self.app_port = int(cfg.get("app_port", 50291))
        self.state_dir = Path(os.environ.get(
            "CSHARE_STATE", Path.home() / ".mod" / "cshare"))
        self.state_file = self.state_dir / "state.json"

    # ── state ────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {"accounts": {}, "nodes": {}, "holdings": {},
                    "orders": {}, "rentals": {}, "trades": [], "seq": 0}

    def _save(self, st: Dict):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(self.state_file)

    @staticmethod
    def _next(st: Dict, prefix: str) -> str:
        st["seq"] += 1
        return f"{prefix}{st['seq']}"

    @staticmethod
    def _acct(st: Dict, address: str) -> Dict:
        if not address:
            raise ValueError("address required")
        return st["accounts"].setdefault(
            address, {"balance": 0.0, "earned": 0.0, "spent": 0.0})

    # ── ledger ───────────────────────────────────────────────────────

    def deposit(self, address: str, amount: float = 1000.0) -> Dict:
        """Test faucet: credit an address with marketplace credits."""
        amount = float(amount)
        if amount <= 0:
            raise ValueError("amount must be positive")
        st = self._load()
        acct = self._acct(st, address)
        acct["balance"] += amount
        self._save(st)
        return {"address": address, "balance": acct["balance"]}

    def balance(self, address: str) -> Dict:
        st = self._load()
        return {"address": address, **self._acct(st, address)}

    # ── listing (fractionalize a node) ───────────────────────────────

    def list_node(self, address: str, name: str, gpu: str = "",
                  gpu_count: int = 1, vcpus: int = 8, ram_gb: int = 32,
                  disk_gb: int = 256, region: str = "", rate_hour: float = 1.0,
                  total_shares: int = 1000, share_price: float = None,
                  offer_shares: int = 0, description: str = "",
                  demo: bool = False) -> Dict:
        """List a compute node as a fractionalized asset. All shares mint to
        the issuer; `offer_shares` > 0 also places an initial ask at
        `share_price` so the market opens with inventory."""
        if not name:
            raise ValueError("name required")
        total_shares = int(total_shares)
        if total_shares < 1:
            raise ValueError("total_shares must be >= 1")
        rate_hour = float(rate_hour)
        if rate_hour <= 0:
            raise ValueError("rate_hour must be positive")
        if share_price is None:
            # default: a share is priced to pay back in ~90 days of full rental
            share_price = round(rate_hour * 24 * 90 / total_shares, 4)
        st = self._load()
        self._acct(st, address)
        nid = self._next(st, "n")
        st["nodes"][nid] = {
            "id": nid, "name": name, "issuer": address,
            "specs": {"gpu": gpu, "gpu_count": int(gpu_count),
                      "vcpus": int(vcpus), "ram_gb": int(ram_gb),
                      "disk_gb": int(disk_gb), "region": region},
            "rate_hour": rate_hour, "total_shares": total_shares,
            "share_price": float(share_price), "description": description,
            "revenue": 0.0, "hours_rented": 0, "created": time.time(),
            "demo": bool(demo),
        }
        st["holdings"][nid] = {address: total_shares}
        out = {"node": st["nodes"][nid]}
        if int(offer_shares) > 0:
            oid = self._place_ask(st, address, nid, int(offer_shares),
                                  float(share_price))
            out["order_id"] = oid
        self._save(st)
        return out

    # ── market views ─────────────────────────────────────────────────

    def _locked(self, st: Dict, node_id: str, address: str) -> int:
        return sum(o["shares"] for o in st["orders"].values()
                   if o["node_id"] == node_id and o["seller"] == address)

    def _active_rental(self, st: Dict, node_id: str) -> Optional[Dict]:
        now = time.time()
        for r in st["rentals"].values():
            if r["node_id"] == node_id and r["end"] > now:
                return r
        return None

    def _floor(self, st: Dict, node_id: str) -> Optional[float]:
        asks = [o["price"] for o in st["orders"].values()
                if o["node_id"] == node_id]
        return min(asks) if asks else None

    def _node_view(self, st: Dict, n: Dict) -> Dict:
        nid = n["id"]
        holds = st["holdings"].get(nid, {})
        rental = self._active_rental(st, nid)
        floor = self._floor(st, nid)
        return {
            **n,
            "holders": len([a for a, s in holds.items() if s > 0]),
            "issuer_pct": round(100.0 * holds.get(n["issuer"], 0)
                                / n["total_shares"], 2),
            "floor_price": floor,
            "for_sale": sum(o["shares"] for o in st["orders"].values()
                            if o["node_id"] == nid),
            "status": "rented" if rental else "available",
            "rented_until": rental["end"] if rental else None,
            "market_cap": round((floor if floor is not None
                                 else n["share_price"]) * n["total_shares"], 2),
        }

    def nodes(self, q: str = None, region: str = None, gpu: str = None,
              status: str = None) -> List[Dict]:
        """The marketplace: every listed node with ownership, order-book
        floor, and rental status."""
        st = self._load()
        out = [self._node_view(st, n) for n in st["nodes"].values()]
        if q:
            ql = q.lower()
            out = [n for n in out if ql in n["name"].lower()
                   or ql in n["description"].lower()
                   or ql in n["specs"]["gpu"].lower()]
        if region:
            out = [n for n in out
                   if region.lower() in n["specs"]["region"].lower()]
        if gpu:
            out = [n for n in out if gpu.lower() in n["specs"]["gpu"].lower()]
        if status:
            out = [n for n in out if n["status"] == status]
        return sorted(out, key=lambda n: n["created"], reverse=True)

    def node(self, node_id: str) -> Dict:
        """Full detail: cap table, open asks, rental history, trade tape."""
        st = self._load()
        n = st["nodes"].get(node_id)
        if n is None:
            raise KeyError(f"node not found: {node_id}")
        holds = st["holdings"].get(node_id, {})
        return {
            **self._node_view(st, n),
            "cap_table": sorted(
                [{"address": a, "shares": s,
                  "pct": round(100.0 * s / n["total_shares"], 2),
                  "locked": self._locked(st, node_id, a)}
                 for a, s in holds.items() if s > 0],
                key=lambda h: -h["shares"]),
            "orders": self.market(node_id),
            "rentals": sorted(
                [r for r in st["rentals"].values()
                 if r["node_id"] == node_id],
                key=lambda r: -r["start"])[:20],
            "trades": [t for t in st["trades"]
                       if t["node_id"] == node_id][-20:],
        }

    def market(self, node_id: str = None) -> List[Dict]:
        """Open asks across the order book, cheapest first."""
        st = self._load()
        out = [o for o in st["orders"].values()
               if node_id is None or o["node_id"] == node_id]
        return sorted(out, key=lambda o: (o["node_id"], o["price"]))

    # ── share trading ────────────────────────────────────────────────

    def _place_ask(self, st: Dict, address: str, node_id: str,
                   shares: int, price: float) -> str:
        n = st["nodes"].get(node_id)
        if n is None:
            raise KeyError(f"node not found: {node_id}")
        if shares < 1:
            raise ValueError("shares must be >= 1")
        if price <= 0:
            raise ValueError("price must be positive")
        held = st["holdings"].get(node_id, {}).get(address, 0)
        free = held - self._locked(st, node_id, address)
        if shares > free:
            raise ValueError(
                f"only {free} unlocked shares available (held {held})")
        oid = self._next(st, "o")
        st["orders"][oid] = {
            "id": oid, "node_id": node_id,
            "node_name": n["name"], "seller": address,
            "shares": shares, "price": float(price), "created": time.time(),
        }
        return oid

    def sell(self, address: str, node_id: str, shares: int,
             price: float) -> Dict:
        """Place an ask. Shares stay in your holding (still earn rental
        income) but are locked against double-selling."""
        st = self._load()
        oid = self._place_ask(st, address, node_id, int(shares), float(price))
        self._save(st)
        return {"order": st["orders"][oid]}

    def cancel(self, address: str, order_id: str) -> Dict:
        st = self._load()
        o = st["orders"].get(order_id)
        if o is None:
            raise KeyError(f"order not found: {order_id}")
        if o["seller"] != address:
            raise PermissionError("only the seller can cancel an order")
        del st["orders"][order_id]
        self._save(st)
        return {"cancelled": order_id}

    def buy(self, address: str, order_id: str, shares: int = None) -> Dict:
        """Fill an ask (fully or partially): credits transfer to the seller,
        shares transfer to the buyer."""
        st = self._load()
        o = st["orders"].get(order_id)
        if o is None:
            raise KeyError(f"order not found: {order_id}")
        shares = int(shares) if shares else o["shares"]
        if shares < 1 or shares > o["shares"]:
            raise ValueError(f"shares must be 1..{o['shares']}")
        if address == o["seller"]:
            raise ValueError("cannot buy your own order")
        cost = round(shares * o["price"], 6)
        buyer = self._acct(st, address)
        if buyer["balance"] < cost:
            raise ValueError(
                f"insufficient balance: need {cost}, have {buyer['balance']}")
        seller = self._acct(st, o["seller"])
        buyer["balance"] -= cost
        buyer["spent"] += cost
        seller["balance"] += cost
        holds = st["holdings"].setdefault(o["node_id"], {})
        holds[o["seller"]] = holds.get(o["seller"], 0) - shares
        holds[address] = holds.get(address, 0) + shares
        o["shares"] -= shares
        if o["shares"] == 0:
            del st["orders"][order_id]
        trade = {"ts": time.time(), "node_id": o["node_id"],
                 "shares": shares, "price": o["price"], "cost": cost,
                 "buyer": address, "seller": o["seller"]}
        st["trades"].append(trade)
        self._save(st)
        return {"trade": trade, "balance": buyer["balance"]}

    # ── rental market ────────────────────────────────────────────────

    def rent(self, address: str, node_id: str, hours: int = 1) -> Dict:
        """Book a node exclusively for `hours`. The fee is charged now and
        distributed pro-rata to every shareholder (locked shares included).
        Returns the rental with an access token."""
        hours = int(hours)
        if hours < 1 or hours > 720:
            raise ValueError("hours must be 1..720")
        st = self._load()
        n = st["nodes"].get(node_id)
        if n is None:
            raise KeyError(f"node not found: {node_id}")
        active = self._active_rental(st, node_id)
        if active:
            raise ValueError(
                f"node is rented until {time.strftime('%H:%M', time.localtime(active['end']))}")
        cost = round(n["rate_hour"] * hours, 6)
        renter = self._acct(st, address)
        if renter["balance"] < cost:
            raise ValueError(
                f"insufficient balance: need {cost}, have {renter['balance']}")
        renter["balance"] -= cost
        renter["spent"] += cost
        # distribute pro-rata to the cap table as of now
        payouts = {}
        for holder, shares in st["holdings"].get(node_id, {}).items():
            if shares <= 0:
                continue
            cut = round(cost * shares / n["total_shares"], 6)
            acct = self._acct(st, holder)
            acct["balance"] += cut
            acct["earned"] += cut
            payouts[holder] = cut
        n["revenue"] = round(n["revenue"] + cost, 6)
        n["hours_rented"] += hours
        now = time.time()
        rid = self._next(st, "r")
        st["rentals"][rid] = {
            "id": rid, "node_id": node_id, "node_name": n["name"],
            "renter": address, "hours": hours, "rate_hour": n["rate_hour"],
            "cost": cost, "start": now, "end": now + hours * 3600,
            "access": {"host": f"{node_id}.cshare.local",
                       "token": uuid.uuid4().hex},
        }
        self._save(st)
        return {"rental": st["rentals"][rid], "payouts": payouts,
                "balance": renter["balance"]}

    def rentals(self, address: str = None, node_id: str = None) -> List[Dict]:
        st = self._load()
        now = time.time()
        out = []
        for r in st["rentals"].values():
            if address and r["renter"] != address:
                continue
            if node_id and r["node_id"] != node_id:
                continue
            out.append({**r, "active": r["end"] > now})
        return sorted(out, key=lambda r: -r["start"])

    # ── portfolio / stats ────────────────────────────────────────────

    def portfolio(self, address: str) -> Dict:
        """Everything one address owns, earns, rents, and has on offer."""
        st = self._load()
        acct = self._acct(st, address)
        holdings = []
        for nid, holds in st["holdings"].items():
            shares = holds.get(address, 0)
            if shares <= 0:
                continue
            n = st["nodes"][nid]
            mark = self._floor(st, nid)
            mark = mark if mark is not None else n["share_price"]
            holdings.append({
                "node_id": nid, "name": n["name"],
                "shares": shares, "total_shares": n["total_shares"],
                "pct": round(100.0 * shares / n["total_shares"], 2),
                "locked": self._locked(st, nid, address),
                "mark": mark, "value": round(shares * mark, 2),
                "income_share": round(
                    n["revenue"] * shares / n["total_shares"], 4),
            })
        return {
            "address": address, **acct,
            "holdings": sorted(holdings, key=lambda h: -h["value"]),
            "portfolio_value": round(sum(h["value"] for h in holdings), 2),
            "orders": [o for o in st["orders"].values()
                       if o["seller"] == address],
            "rentals": self.rentals(address=address),
        }

    def stats(self) -> Dict:
        st = self._load()
        now = time.time()
        views = [self._node_view(st, n) for n in st["nodes"].values()]
        return {
            "protocol": PROTOCOL,
            "nodes": len(views),
            "market_cap": round(sum(n["market_cap"] for n in views), 2),
            "total_revenue": round(sum(n["revenue"] for n in views), 4),
            "hours_rented": sum(n["hours_rented"] for n in views),
            "active_rentals": sum(1 for r in st["rentals"].values()
                                  if r["end"] > now),
            "open_orders": len(st["orders"]),
            "trades": len(st["trades"]),
            "accounts": len(st["accounts"]),
        }

    # ── demo inventory (explicit, clearly flagged) ───────────────────

    def demo(self) -> Dict:
        """Populate a demo market: four nodes with initial share offerings
        and a couple of investors/renters. Never runs automatically."""
        issuer, alice, bob = "0xDEMO-issuer", "0xDEMO-alice", "0xDEMO-bob"
        self.deposit(issuer, 5000)
        for a in (alice, bob):
            self.deposit(a, 15000)  # enough to clear the initial offerings
        specs = [
            dict(name="hopper-01", gpu="H100 SXM", gpu_count=8, vcpus=192,
                 ram_gb=2048, disk_gb=8000, region="us-east",
                 rate_hour=24.0, total_shares=1000, offer_shares=600,
                 description="8×H100 SXM5 pod, NVLink, 3.2Tbps IB"),
            dict(name="ada-04", gpu="RTX 4090", gpu_count=4, vcpus=64,
                 ram_gb=256, disk_gb=4000, region="eu-west",
                 rate_hour=3.2, total_shares=800, offer_shares=500,
                 description="4×4090 inference box"),
            dict(name="ampere-12", gpu="A100 80GB", gpu_count=2, vcpus=48,
                 ram_gb=512, disk_gb=2000, region="us-west",
                 rate_hour=4.4, total_shares=500, offer_shares=250,
                 description="2×A100 fine-tuning node"),
            dict(name="epyc-cpu-02", gpu="", gpu_count=0, vcpus=128,
                 ram_gb=1024, disk_gb=16000, region="ap-south",
                 rate_hour=1.1, total_shares=400, offer_shares=200,
                 description="CPU render/build farm node"),
        ]
        created = []
        for s in specs:
            r = self.list_node(address=issuer, demo=True, **s)
            created.append(r)
        # secondary activity so the market looks lived-in
        offers = [c["order_id"] for c in created]
        self.buy(alice, offers[0], 150)
        self.buy(bob, offers[0], 50)
        self.buy(alice, offers[1], 200)
        self.buy(bob, offers[2], 100)
        self.rent(bob, created[1]["node"]["id"], 3)
        self.rent(alice, created[3]["node"]["id"], 12)
        return {"created": [c["node"]["id"] for c in created],
                "stats": self.stats()}

    def reset(self, confirm: bool = False) -> Dict:
        """Wipe all marketplace state (requires confirm=true)."""
        if not confirm:
            raise ValueError("pass confirm=true to wipe all marketplace state")
        if self.state_file.exists():
            self.state_file.unlink()
        return {"reset": True}

    # ── mod protocol ─────────────────────────────────────────────────

    def card(self) -> Dict:
        """The protocol card: what this marketplace is and where to reach it."""
        return {
            "protocol": PROTOCOL,
            "name": "cshare",
            "description": self.description,
            "version": "2.0.0",
            "concepts": {
                "node": "a compute machine listed as an asset, minted into shares",
                "share": "fractional ownership of a node; earns rental income pro-rata",
                "order": "an ask on the order book (shares locked, partial fills)",
                "rental": "exclusive hourly booking; the fee streams to shareholders",
            },
            "endpoints": {
                "nodes": "GET /nodes?q=&region=&gpu=&status=",
                "node": "GET /nodes/{id}",
                "list": "POST /nodes {address, name, gpu, rate_hour, total_shares, offer_shares, ...}",
                "market": "GET /market?node_id=",
                "sell": "POST /orders {address, node_id, shares, price}",
                "cancel": "POST /orders/{id}/cancel {address}",
                "buy": "POST /buy {address, order_id, shares?}",
                "rent": "POST /rent {address, node_id, hours}",
                "rentals": "GET /rentals?address=&node_id=",
                "portfolio": "GET /portfolio/{address}",
                "deposit": "POST /deposit {address, amount}",
                "stats": "GET /stats",
            },
            "urls": {
                "api": f"http://localhost:{self.port}",
                "app": f"http://localhost:{self.app_port}/cshare",
            },
        }

    def schema(self) -> Dict:
        return {
            "card": {}, "stats": {}, "demo": {},
            "deposit": {"address": "str", "amount": "float"},
            "balance": {"address": "str"},
            "list_node": {"address": "str", "name": "str", "gpu": "str?",
                          "rate_hour": "float", "total_shares": "int",
                          "share_price": "float?", "offer_shares": "int?"},
            "nodes": {"q": "str?", "region": "str?", "gpu": "str?",
                      "status": "str?"},
            "node": {"node_id": "str"},
            "market": {"node_id": "str?"},
            "sell": {"address": "str", "node_id": "str", "shares": "int",
                     "price": "float"},
            "cancel": {"address": "str", "order_id": "str"},
            "buy": {"address": "str", "order_id": "str", "shares": "int?"},
            "rent": {"address": "str", "node_id": "str", "hours": "int"},
            "rentals": {"address": "str?", "node_id": "str?"},
            "portfolio": {"address": "str"},
            "reset": {"confirm": "bool"},
        }

    def forward(self, action: str = None, **kwargs):
        """Mod protocol entry point. Null call returns the protocol card."""
        if not action:
            return self.card()
        fn = getattr(self, action, None)
        if fn is None or action.startswith("_") or not callable(fn):
            raise ValueError(
                f"unknown action: {action}. one of {list(self.schema())}")
        return fn(**kwargs)

    def info(self):
        return self.card()

    def readme(self):
        for name in ["README.md", "readme.md"]:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return Path(p).read_text()
        return None

    # ── serving (pm2, same pattern as sibling modules) ───────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        import subprocess
        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        pm2_cmd = ["pm2", "start", cmd[0], "--name", name]
        if cwd:
            pm2_cmd += ["--cwd", cwd]
        pm2_cmd += ["--"] + cmd[1:]
        full_env = {**os.environ, **(env or {})}
        subprocess.run(pm2_cmd, env=full_env, capture_output=True, text=True)
        return name

    def serve_api(self, port=None):
        port = int(port or self.port)
        api_dir = os.path.join(self.path, "api")
        env = {"PORT": str(port)}
        cmd = ["python3", "-m", "uvicorn", "api:app", "--host", "0.0.0.0",
               "--port", str(port), "--app-dir", api_dir]
        self._pm2_start("cshare.api", cmd, env=env)
        return {"api": f"http://localhost:{port}", "pm2": "cshare.api"}

    def serve_app(self, app_port=None):
        app_port = int(app_port or self.app_port)
        env = {"PORT": str(app_port), "BASE_PATH": "/cshare",
               "API_URL": f"http://localhost:{self.port}"}
        cmd = ["node", os.path.join(self.path, "app", "server.js")]
        self._pm2_start("cshare.app", cmd, env=env)
        return {"app": f"http://localhost:{app_port}/cshare", "pm2": "cshare.app"}

    def serve(self, port=None, app_port=None):
        api, app = self.serve_api(port), self.serve_app(app_port)
        return {"api": api["api"], "app": app["app"],
                "pm2": [api["pm2"], app["pm2"]]}

    def kill(self):
        import subprocess
        killed = []
        for name in ["cshare.api", "cshare.app"]:
            r = subprocess.run(["pm2", "delete", name],
                               capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {"killed": killed}
