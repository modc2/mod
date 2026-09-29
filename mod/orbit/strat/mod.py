"""
strat — a marketplace of trading strategies as mods, on one unified
protocol across raydium, uniswap, hyperliquid, bittensor and polymarket.

A strat IS a mod: a directory whose config.json declares a `strat` block
and whose strat.py defines one subclass of the canonical Strat class
(protocol.py). The registry discovers them in two places — the built-ins
under this module's strats/ and any orbit module that declares a strat
block — so publishing a strategy to the marketplace is just shipping a mod.

Execution is delegated to the fleet module that owns each chain (venues.py)
— this module holds no keys, forwards auth verbatim, and defaults every
run to dry-run.
"""

import importlib.util
import json
import os
import shutil
import sys
import time

SELF = os.path.dirname(os.path.abspath(__file__))
ORBIT = os.path.dirname(SELF)
DATA = os.path.join(SELF, "data")


def _load_file(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# strat.py files inside strat mods do `from protocol import Strat`; loading
# protocol.py by path under that exact name is what makes them importable
# no matter how THIS file was loaded (mod system, tests, plain python).
if "protocol" not in sys.modules or not hasattr(sys.modules.get("protocol"), "Strat"):
    proto = _load_file(os.path.join(SELF, "protocol.py"), "protocol")
else:
    proto = sys.modules["protocol"]
if "strat_venues" not in sys.modules:
    venues_mod = _load_file(os.path.join(SELF, "venues.py"), "strat_venues")
else:
    venues_mod = sys.modules["strat_venues"]

STRAT_TEMPLATE = '''"""{name} — {description}"""

from protocol import Order, OrderSide, Strat, SyncResult


class {cls}(Strat):
    venues = {venues!r}

    def signal(self, sync: SyncResult) -> list[Order]:
        orders: list[Order] = []
        for t in sync.trades:
            if t.id in self._handled_trade_ids or t.price <= 0:
                continue
            # Your logic here: decide what to do with each observed trade.
            orders.append(Order(
                venue=t.venue, symbol=t.symbol, side=t.side,
                size=t.size * float(self.config.params.get("scale", 0.1)),
                price=t.price, source_trader=t.trader, source_trade_id=t.id,
                tag="{name}"))
        return orders
'''


class Mod:
    description = ("Marketplace of trading strategies as mods — one class-defined "
                   "Strat protocol across raydium, uniswap, hyperliquid, "
                   "bittensor and polymarket. Strats are discovered, verified, "
                   "forked, backtested and boarded; execution is delegated to "
                   "the module that owns each chain, keys never held here.")
    path = SELF

    # ── Overview ────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        reg = self._registry()
        return {
            "name": "strat",
            "protocol_version": proto.PROTOCOL_VERSION,
            "description": self.description,
            "venues": proto.VENUES,
            "strats": sorted(reg.keys()),
            "count": len(reg),
            "methods": proto.METHODS,
            "fns": ["info", "schema", "venues", "strats", "strat", "code",
                    "verify", "new", "fork", "publish", "backtest", "tick",
                    "board", "readme"],
        }

    def schema(self):
        """The machine-readable strat protocol contract."""
        return proto.schema()

    def venues(self, check=False):
        """The five venues, which fleet module signs for each, and (check=True)
        whether that module is reachable right now."""
        return {name: v.info(check=bool(check))
                for name, v in venues_mod.registry().items()}

    # ── Registry: mods ARE strats ───────────────────────────────────

    def _registry(self):
        """name -> {dir, config, source, origin} for every strat mod found."""
        found = {}
        builtin_root = os.path.join(SELF, "strats")
        roots = []
        if os.path.isdir(builtin_root):
            roots.append((builtin_root, "builtin"))
        roots.append((ORBIT, "orbit"))
        for root, origin in roots:
            for entry in sorted(os.listdir(root)):
                d = os.path.join(root, entry)
                cfg_path = os.path.join(d, "config.json")
                if not os.path.isdir(d) or not os.path.isfile(cfg_path):
                    continue
                if origin == "orbit" and d == SELF:
                    continue
                try:
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                except Exception:
                    continue
                block = cfg.get("strat")
                if not isinstance(block, dict) or "class" not in block:
                    continue
                name = cfg.get("name", entry)
                if name not in found:   # builtins win name collisions
                    found[name] = {"dir": d, "config": cfg, "strat": block,
                                   "origin": origin}
        return found

    def _entry(self, name):
        reg = self._registry()
        if name not in reg:
            raise KeyError(f"unknown strat {name!r} — have {sorted(reg.keys())}")
        return reg[name]

    def _class(self, name):
        """Import the strat mod's file and return its declared class."""
        e = self._entry(name)
        file_name, _, cls_name = e["strat"]["class"].partition(":")
        path = os.path.join(e["dir"], file_name or "strat.py")
        module = _load_file(path, f"strat_mod_{name}_{abs(hash(path)) % 10**6}")
        cls = getattr(module, cls_name)
        return cls

    def strats(self, venue=None):
        """The marketplace listing. Optionally filtered to one venue."""
        out = []
        for name, e in sorted(self._registry().items()):
            venues = e["strat"].get("venues", proto.VENUES)
            if venue and venue not in venues:
                continue
            out.append({
                "name": name,
                "description": e["config"].get("description", ""),
                "version": e["config"].get("version", "0"),
                "venues": venues,
                "params": e["strat"].get("params", {}),
                "origin": e["origin"],
                "dir": e["dir"],
            })
        return out

    def strat(self, name):
        """One strat's full card."""
        e = self._entry(name)
        card = {"name": name, "origin": e["origin"], "dir": e["dir"],
                "config": e["config"], "verify": self.verify(name)}
        return card

    def code(self, name):
        """The strat's source — the class IS the strategy."""
        e = self._entry(name)
        file_name = e["strat"]["class"].partition(":")[0] or "strat.py"
        with open(os.path.join(e["dir"], file_name)) as f:
            return f.read()

    def verify(self, name):
        """Does this mod actually honor the strat protocol contract?"""
        e = self._entry(name)
        issues = []
        try:
            cls = self._class(name)
        except Exception as exc:
            return {"ok": False, "issues": [f"class failed to load: {exc}"]}
        if not (isinstance(cls, type) and issubclass(cls, proto.Strat)):
            issues.append("declared class is not a subclass of protocol.Strat")
        else:
            for m_name in proto.METHODS:
                if not callable(getattr(cls, m_name, None)):
                    issues.append(f"missing method {m_name}()")
            if getattr(cls.signal, "__isabstractmethod__", False):
                issues.append("signal() not implemented")
        bad = [v for v in e["strat"].get("venues", []) if v not in proto.VENUES]
        if bad:
            issues.append(f"unknown venues: {bad}")
        return {"ok": not issues, "issues": issues,
                "protocol": e["strat"].get("protocol"),
                "class": e["strat"]["class"]}

    # ── Authoring: new / fork / publish ─────────────────────────────

    def new(self, name, venues=None, description="", orbit=False, params=None):
        """Scaffold a new strat mod. orbit=True writes it into the orbit tree
        as a first-class module; default keeps it under strats/ here."""
        name = str(name).strip().lower().replace(" ", "-")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"bad strat name {name!r}")
        if name in self._registry():
            raise ValueError(f"strat {name!r} already exists")
        venues = venues if isinstance(venues, list) else \
            [venues] if venues else list(proto.VENUES)
        bad = [v for v in venues if v not in proto.VENUES]
        if bad:
            raise ValueError(f"unknown venues {bad} — pick from {proto.VENUES}")
        target = os.path.join(ORBIT if orbit else os.path.join(SELF, "strats"), name)
        if os.path.exists(target):
            raise ValueError(f"{target} already exists")
        cls_name = "".join(p.capitalize() for p in name.replace("_", "-").split("-"))
        desc = description or f"{name} strategy on the strat protocol"
        os.makedirs(target)
        with open(os.path.join(target, "config.json"), "w") as f:
            json.dump({
                "name": name, "description": desc, "version": "0.1.0",
                "strat": {"protocol": proto.PROTOCOL_VERSION,
                          "class": f"strat.py:{cls_name}",
                          "venues": venues, "params": params or {"scale": 0.1}},
            }, f, indent=4)
        with open(os.path.join(target, "strat.py"), "w") as f:
            f.write(STRAT_TEMPLATE.format(name=name, description=desc,
                                          cls=cls_name, venues=venues))
        if orbit:
            # An orbit strat mod is also a plain mod: give it a mod.py face.
            with open(os.path.join(target, "mod.py"), "w") as f:
                f.write(self._orbit_mod_py(name))
        return {"created": name, "dir": target, "orbit": bool(orbit),
                "verify": self.verify(name)}

    def fork(self, name, new_name, orbit=False):
        """Copy an existing strat mod under a new name — the marketplace's
        remix button."""
        e = self._entry(name)
        new_name = str(new_name).strip().lower().replace(" ", "-")
        if new_name in self._registry():
            raise ValueError(f"strat {new_name!r} already exists")
        target = os.path.join(ORBIT if orbit else os.path.join(SELF, "strats"),
                              new_name)
        if os.path.exists(target):
            raise ValueError(f"{target} already exists")
        shutil.copytree(e["dir"], target,
                        ignore=shutil.ignore_patterns("__pycache__", "data"))
        cfg_path = os.path.join(target, "config.json")
        with open(cfg_path) as f:
            cfg = json.load(f)
        cfg["name"] = new_name
        cfg["forked_from"] = name
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=4)
        if orbit and not os.path.exists(os.path.join(target, "mod.py")):
            with open(os.path.join(target, "mod.py"), "w") as f:
                f.write(self._orbit_mod_py(new_name))
        return {"forked": name, "as": new_name, "dir": target,
                "verify": self.verify(new_name)}

    def publish(self, name):
        """Promote a builtin strat to a first-class orbit module."""
        e = self._entry(name)
        if e["origin"] == "orbit":
            return {"published": name, "dir": e["dir"], "already": True}
        if os.path.exists(os.path.join(ORBIT, name)):
            # Name collision with an existing orbit module — publish suffixed.
            return self.fork(name, f"{name}-strat", orbit=True)
        return self._promote(e, name)

    def _promote(self, e, name):
        target = os.path.join(ORBIT, name)
        if os.path.exists(target):
            raise ValueError(f"orbit already has a module named {name!r}")
        # Move, don't copy — one source of truth for the strat's code.
        shutil.copytree(e["dir"], target,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.rmtree(e["dir"])
        with open(os.path.join(target, "mod.py"), "w") as f:
            f.write(self._orbit_mod_py(name))
        return {"published": name, "dir": target}

    @staticmethod
    def _orbit_mod_py(name):
        return f'''import json
import os
import sys

SELF = os.path.dirname(os.path.abspath(__file__))
STRAT = os.path.join(os.path.dirname(SELF), "strat")


def _protocol():
    sys.path.insert(0, STRAT) if STRAT not in sys.path else None
    import importlib.util
    spec = importlib.util.spec_from_file_location("strat_host", os.path.join(STRAT, "mod.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


class Mod:
    description = "{name} — a strategy mod on the strat protocol"
    path = SELF

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        with open(os.path.join(SELF, "config.json")) as f:
            return json.load(f)

    def code(self):
        with open(os.path.join(SELF, "strat.py")) as f:
            return f.read()

    def verify(self):
        return _protocol().verify("{name}")

    def backtest(self, days=7, capital=1000.0, traders=None):
        return _protocol().backtest("{name}", days=days, capital=capital,
                                    traders=traders)
'''

    # ── Running: backtest / tick / board ────────────────────────────

    def _mount(self, name, capital, traders=None, token=None,
               dry_run=True, confirm=False):
        e = self._entry(name)
        cls = self._class(name)
        adapters = venues_mod.registry()
        watchlist = []
        for t in traders or []:
            if isinstance(t, str):
                venue, _, addr = t.partition(":")
                watchlist.append({"venue": venue if addr else "",
                                  "address": addr or venue, "weight": 1.0})
            else:
                watchlist.append(t)
        cfg = proto.StratConfig(
            name=name, capital=float(capital),
            venues=e["strat"].get("venues", list(proto.VENUES)),
            watchlist=watchlist,
            params=dict(e["strat"].get("params", {})),
            fetch_trades=lambda venue, addr, since: (
                adapters[venue].trades(addr, since, token) if venue in adapters
                else [t for v in adapters.values()
                      for t in v.trades(addr, since, token)]),
            fetch_cash=lambda: float(capital),
            place_order=lambda o: adapters[o.venue].place(
                o, token=token, confirm=confirm, dry_run=dry_run),
        )
        return cls(cfg)

    def backtest(self, name, days=7, capital=1000.0, traders=None, token=None):
        """Replay the strat over each watched trader's recent venue history.
        Pure read — nothing is placed anywhere."""
        strat = self._mount(name, capital, traders=traders, token=token)
        since = int(time.time() * 1000) - int(days) * 86_400_000
        history = []
        for w in strat.config.watchlist:
            history.extend(strat.config.fetch_trades(
                w.get("venue", ""), w["address"], since))
        if not history:
            return {"strat": name, "days": days, "trades": 0,
                    "note": "no upstream history — pass traders=['venue:address', ...] "
                            "and make sure that venue's module is running"}
        r = strat.backtest(history)
        return {"strat": name, "days": days, "capital": capital,
                "trades_seen": len(history), "trades_simulated": r.trades_simulated,
                "final_pnl": round(r.final_pnl, 4), "roi_pct": round(r.roi_pct, 4),
                "curve": r.pnl_curve[-200:], "notes": r.notes}

    def tick(self, name, capital=100.0, traders=None, token=None,
             dry_run=True, confirm=False):
        """One live cycle: sync -> signal -> execute through the venue
        modules. DRY RUN by default; a real order needs dry_run=False AND
        confirm=True AND a token the peer module accepts."""
        strat = self._mount(name, capital, traders=traders, token=token,
                            dry_run=dry_run, confirm=confirm)
        strat.setup()
        r = strat.tick()
        return {
            "strat": name, "dry_run": bool(dry_run),
            "trades_seen": len(r.sync.trades),
            "orders": [vars(o) for o in r.orders],
            "results": [{"order": vars(x.order), "success": x.success,
                         "order_id": x.order_id, "error": x.error}
                        for x in r.results],
            "state": strat.state(),
        }

    def board(self, days=7, capital=1000.0, traders=None, refresh=False,
              token=None):
        """The marketplace board: every strat, verified, with cached backtest
        performance when history is available."""
        os.makedirs(DATA, exist_ok=True)
        cache_path = os.path.join(DATA, "board.json")
        # Always load the cache — refresh forces re-runs but a failed
        # re-read must still fall back on the last good number.
        cache = {}
        if os.path.isfile(cache_path):
            try:
                with open(cache_path) as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        rows = []
        for s in self.strats():
            row = dict(s)
            row["ok"] = self.verify(s["name"])["ok"]
            perf = cache.get(s["name"])
            if traders and (refresh or not perf):
                try:
                    bt = self.backtest(s["name"], days=days, capital=capital,
                                       traders=traders, token=token)
                    if bt.get("roi_pct") is not None:
                        perf = {k: bt.get(k) for k in
                                ("roi_pct", "final_pnl", "trades_simulated", "days")}
                        perf["at"] = int(time.time())
                        cache[s["name"]] = perf
                    elif perf:
                        # Upstream read came back empty — keep the last good
                        # number rather than clobbering it, but say so.
                        perf = dict(perf, stale=True)
                    else:
                        perf = {"note": bt.get("note")}
                except Exception as exc:
                    perf = {"error": str(exc)}
            row["perf"] = perf
            rows.append(row)
        try:
            with open(cache_path, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass
        def _roi(row):
            perf = row.get("perf")
            roi = perf.get("roi_pct") if isinstance(perf, dict) else None
            return -(roi if isinstance(roi, (int, float)) else -1e9)
        rows.sort(key=_roi)
        return {"protocol": proto.PROTOCOL_VERSION, "venues": proto.VENUES,
                "strats": rows}

    # ── Misc ────────────────────────────────────────────────────────

    def readme(self):
        p = os.path.join(SELF, "README.md")
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None
