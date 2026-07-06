"""
OpenEvent — one map for every event, one composer for every platform.

The events world is splintered: Luma for tech mixers, Eventbrite for ticketed
shows, Meetup for communities, a dozen group chats for the rest. To *find* what's
on you check five apps; to *announce* something you re-type it into each one.

OpenEvent is the aggregator that collapses both sides:

  • DISCOVER  — one live, de-duplicated feed of what's happening near you, pulled
                straight from the platforms (Luma is live & key-free; Eventbrite/
                Meetup are best-effort + connectable). Same event cross-listed on
                two platforms collapses into one card that shows both pills.
  • BROADCAST — compose an event once and fan it out: a real draft on Eventbrite
                (with your connected token) and one-click, prefilled hand-offs to
                Luma & Meetup. Every event tracks where it landed and its live URL.

Identity:    a lightweight handle. No login to browse.
Secrets:     platform API tokens are BYOK and live OFF-CHAIN in
             ~/.openevent/credentials.json (chmod 600) — never in committed config.
Storage:     ~/.openevent/{events.json, credentials.json, cache.json, admin.json}
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mod as m
from connectors import Registry, _share_draft   # noqa: E402


# Cities OpenEvent knows how to read. Each maps to the per-platform place handle
# used for discovery (Luma discover-place slug, Eventbrite place slug, …) plus a
# map center. Adding a row here makes a city selectable. Luma slugs are verified
# discover places; Eventbrite/Meetup slugs are best-effort (those sites bot-gate
# server-side, so they light up opportunistically and report honestly when not).
CITIES: List[Dict[str, Any]] = [
    {"key": "sf", "label": "San Francisco", "country": "USA", "lat": 37.7749, "lng": -122.4194, "zoom": 12,
     "luma": "sf", "eventbrite": "ca--san-francisco", "meetup": "us--ca--san-francisco"},
    {"key": "nyc", "label": "New York", "country": "USA", "lat": 40.7128, "lng": -74.0060, "zoom": 12,
     "luma": "nyc", "eventbrite": "ny--new-york", "meetup": "us--ny--new-york"},
    {"key": "la", "label": "Los Angeles", "country": "USA", "lat": 34.0522, "lng": -118.2437, "zoom": 11,
     "luma": "la", "eventbrite": "ca--los-angeles", "meetup": "us--ca--los-angeles"},
    {"key": "london", "label": "London", "country": "UK", "lat": 51.5072, "lng": -0.1276, "zoom": 12,
     "luma": "london", "eventbrite": "united-kingdom--london", "meetup": "gb--london"},
    {"key": "toronto", "label": "Toronto", "country": "Canada", "lat": 43.6532, "lng": -79.3832, "zoom": 12,
     "luma": "toronto", "eventbrite": "canada--toronto", "meetup": "ca--on--toronto"},
    {"key": "nyc-bk", "label": "Brooklyn", "country": "USA", "lat": 40.6782, "lng": -73.9442, "zoom": 12,
     "luma": "brooklyn", "eventbrite": "ny--brooklyn", "meetup": "us--ny--brooklyn"},
    {"key": "austin", "label": "Austin", "country": "USA", "lat": 30.2672, "lng": -97.7431, "zoom": 11,
     "luma": "austin", "eventbrite": "tx--austin", "meetup": "us--tx--austin"},
    {"key": "miami", "label": "Miami", "country": "USA", "lat": 25.7617, "lng": -80.1918, "zoom": 11,
     "luma": "miami", "eventbrite": "fl--miami", "meetup": "us--fl--miami"},
    {"key": "berlin", "label": "Berlin", "country": "Germany", "lat": 52.5200, "lng": 13.4050, "zoom": 11,
     "luma": "berlin", "eventbrite": "germany--berlin", "meetup": "de--berlin"},
    {"key": "paris", "label": "Paris", "country": "France", "lat": 48.8566, "lng": 2.3522, "zoom": 12,
     "luma": "paris", "eventbrite": "france--paris", "meetup": "fr--paris"},
    {"key": "singapore", "label": "Singapore", "country": "Singapore", "lat": 1.3521, "lng": 103.8198, "zoom": 11,
     "luma": "singapore", "eventbrite": "singapore--singapore", "meetup": "sg--singapore"},
    {"key": "bangalore", "label": "Bangalore", "country": "India", "lat": 12.9716, "lng": 77.5946, "zoom": 11,
     "luma": "bangalore", "eventbrite": "india--bangalore", "meetup": "in--bangalore"},
]
_CITY_BY_KEY = {c["key"]: c for c in CITIES}


class Mod:
    description = (
        "Cross-platform events aggregator — one live feed of what's on near you "
        "(Luma/Eventbrite/Meetup) and one composer to broadcast an event to all of them."
    )

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser("~/.openevent"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.store_dir / "events.json"
        self.creds_path = self.store_dir / "credentials.json"
        self.cache_path = self.store_dir / "cache.json"
        self.admin_path = self.store_dir / "admin.json"

        self.port = int(self.config.get("port", 50170))
        self.app_port = int(self.config.get("app_port", 50171))
        self.default_city = self.config.get("default_city", "sf")
        self.cache_ttl = int(self.config.get("cache_ttl", 600))
        self.registry = Registry()

    def _load_config(self):
        p = Path(__file__).parent / "config.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _load(self, path, default):
        p = Path(path)
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                return default
        return default

    def _save(self, path, data, secret=False):
        tmp = Path(path).with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)
        if secret:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def _events(self) -> Dict[str, Any]:
        return self._load(self.events_path, {})

    # ━━ Health / status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def health(self):
        return {"status": "ok", "module": "openevent",
                "platforms": [c.key for c in self.registry.all()],
                "events_created": len(self._events())}

    def status(self):
        creds = self._load(self.creds_path, {})
        return {
            "platforms": len(self.registry.all()),
            "connected": [k for k in creds if creds.get(k)],
            "cities": len(CITIES),
            "events_created": len(self._events()),
            "default_city": self.default_city,
        }

    # ━━ Metadata ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def platforms(self):
        """Connector catalog + whether each is connected (BYOK) — no secrets leaked."""
        creds = self._load(self.creds_path, {})
        out = []
        for c in self.registry.all():
            out.append({**c.meta(), "connected": bool(creds.get(c.key))})
        return out

    def cities(self):
        return [{"key": c["key"], "label": c["label"], "country": c["country"],
                 "lat": c["lat"], "lng": c["lng"], "zoom": c["zoom"],
                 "default": c["key"] == self.default_city} for c in CITIES]

    def _area(self, city: Optional[str]) -> Dict[str, Any]:
        return _CITY_BY_KEY.get(city or self.default_city, _CITY_BY_KEY.get(self.default_city, CITIES[0]))

    # ━━ Discovery (cached) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def discover(self, city: str = None, query: str = "", sources: List[str] = None,
                 limit: int = 50, refresh: bool = False) -> Dict[str, Any]:
        """Live, de-duplicated feed for a city across platforms. Cached for cache_ttl."""
        area = self._area(city)
        srcs = sources or [c.key for c in self.registry.all()]
        ck = f"{area['key']}|{query.strip().lower()}|{','.join(sorted(srcs))}"
        cache = self._load(self.cache_path, {})
        hit = cache.get(ck)
        now = int(time.time())
        if hit and not refresh and (now - hit.get("ts", 0)) < self.cache_ttl:
            return {**hit["data"], "city": area["key"], "cached": True,
                    "age": now - hit["ts"]}

        res = self.registry.discover(area, sources=srcs, query=query, limit=limit)
        # overlay the events we created ourselves onto the local feed
        res["events"] = self._with_own(res["events"], area, query)
        res["total"] = len(res["events"])
        cache[ck] = {"ts": now, "data": res}
        # keep the cache bounded
        if len(cache) > 60:
            for k in sorted(cache, key=lambda k: cache[k]["ts"])[:20]:
                cache.pop(k, None)
        self._save(self.cache_path, cache)
        return {**res, "city": area["key"], "cached": False, "age": 0}

    def _with_own(self, events: List[Dict[str, Any]], area, query) -> List[Dict[str, Any]]:
        ql = (query or "").lower().strip()
        now = int(time.time())
        own = []
        for ev in self._events().values():
            if ev.get("city") not in (area["key"], None):
                continue
            if ev.get("starts_at") and ev["starts_at"] < now - 86400:
                continue
            if ql and ql not in ev.get("title", "").lower():
                continue
            own.append(self._public_event(ev, feed=True))
        # own events first if soonest, then merge-sort by start
        merged = own + events
        merged.sort(key=lambda e: (e.get("starts_at") is None, e.get("starts_at") or 1 << 62))
        return merged

    # ━━ Credentials (BYOK, off-chain) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def connect(self, platform: str, **fields) -> Dict[str, Any]:
        """Store a platform API credential off-chain (chmod 600). Never committed."""
        if not self.registry.get(platform):
            return {"error": f"unknown platform '{platform}'"}
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        if not fields:
            return {"error": "no credential fields provided"}
        creds = self._load(self.creds_path, {})
        creds[platform] = fields
        self._save(self.creds_path, creds, secret=True)
        return {"ok": True, "connected": platform}

    def disconnect(self, platform: str) -> Dict[str, Any]:
        creds = self._load(self.creds_path, {})
        if creds.pop(platform, None) is None:
            return {"error": "not connected"}
        self._save(self.creds_path, creds, secret=True)
        return {"ok": True, "disconnected": platform}

    def connections(self):
        creds = self._load(self.creds_path, {})
        return [{"platform": k, "fields": list(v.keys())} for k, v in creds.items() if v]

    # ━━ Create + broadcast across platforms ━━━━━━━━━━━━━━━━━━━━━━━
    def create_event(self, title: str, starts_at: int, ends_at: int = None,
                     description: str = "", venue: str = "", address: str = "",
                     city: str = None, lat: float = None, lng: float = None,
                     online: bool = False, cover_image: str = "", price: dict = None,
                     organizer: str = "", timezone: str = "UTC",
                     targets: List[str] = None) -> Dict[str, Any]:
        """Create a canonical event and broadcast it to the chosen platforms."""
        if not (title or "").strip():
            return {"error": "title required"}
        try:
            starts_at = int(starts_at)
        except (TypeError, ValueError):
            return {"error": "starts_at must be a unix timestamp"}
        if starts_at <= 0:
            return {"error": "starts_at required"}
        area = self._area(city)
        if lat is None and not online:
            lat, lng = area["lat"], area["lng"]   # fall back to city center for the map

        if price and float(price.get("amount", 0)) <= 0:
            price = None

        events = self._events()
        eid = secrets.token_hex(6)
        edit_key = secrets.token_urlsafe(16)
        ev = {
            "id": eid, "source": "openevent", "title": title.strip(),
            "description": (description or "").strip(),
            "starts_at": starts_at, "ends_at": int(ends_at) if ends_at else None,
            "timezone": timezone or "UTC",
            "venue": (venue or "").strip(), "address": (address or "").strip(),
            "city": area["key"], "lat": lat, "lng": lng, "online": bool(online),
            "cover_image": (cover_image or "").strip(),
            "free": price is None, "price": price,
            "organizer": (organizer or "").strip(),
            "edit_key": edit_key, "created_at": int(time.time()),
            "syndications": {},
        }
        # broadcast
        targets = [t for t in (targets or []) if self.registry.get(t)]
        for plat in targets:
            ev["syndications"][plat] = self._publish_to(plat, ev)
        events[eid] = ev
        self._save(self.events_path, events)
        out = self._public_event(ev)
        out["edit_key"] = edit_key   # surfaced once, to the creator
        return out

    def _publish_to(self, platform: str, ev: Dict[str, Any]) -> Dict[str, Any]:
        conn = self.registry.get(platform)
        creds = self._load(self.creds_path, {}).get(platform)
        try:
            res = conn.publish(ev, creds)
        except Exception as e:
            res = {"platform": platform, "status": "error", "message": str(e)[:160]}
        res["at"] = int(time.time())
        return res

    def broadcast(self, event_id: str, edit_key: str, targets: List[str]) -> Dict[str, Any]:
        """(Re)syndicate an existing event to additional platforms."""
        events = self._events()
        ev = events.get(event_id)
        if not ev:
            return {"error": "event not found"}
        if edit_key != ev.get("edit_key") and not self._is_sudo(edit_key):
            return {"error": "edit_key required"}
        for plat in [t for t in (targets or []) if self.registry.get(t)]:
            ev.setdefault("syndications", {})[plat] = self._publish_to(plat, ev)
        self._save(self.events_path, events)
        return self._public_event(ev)

    def draft_for(self, event_id: str, platform: str) -> Dict[str, Any]:
        """The composed, copy-paste-ready draft for an assisted hand-off."""
        ev = self._events().get(event_id)
        if not ev:
            return {"error": "event not found"}
        return _share_draft(ev)

    # ━━ Reads ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _public_event(self, ev: Dict[str, Any], feed: bool = False) -> Dict[str, Any]:
        synd = ev.get("syndications", {})
        platforms = [{"key": "openevent", "label": "OpenEvent", "color": "#7c5cff", "url": None}]
        for plat, r in synd.items():
            conn = self.registry.get(plat)
            platforms.append({"key": plat, "label": conn.label if conn else plat,
                              "color": conn.color if conn else "#888",
                              "url": r.get("url"), "status": r.get("status")})
        out = {
            "uid": f"openevent:{ev['id']}", "id": ev["id"], "source": "openevent",
            "title": ev["title"], "description": ev.get("description", ""),
            "starts_at": ev.get("starts_at"), "ends_at": ev.get("ends_at"),
            "timezone": ev.get("timezone", "UTC"),
            "url": None, "cover_image": ev.get("cover_image"),
            "venue": ev.get("venue", ""), "address": ev.get("address", ""),
            "city": ev.get("city"), "lat": ev.get("lat"), "lng": ev.get("lng"),
            "online": ev.get("online", False),
            "free": ev.get("free", True), "price": ev.get("price"),
            "organizer": ev.get("organizer", ""), "mine": True,
            "platforms": platforms,
        }
        if not feed:
            out["syndications"] = synd
        return out

    def event(self, event_id: str) -> Dict[str, Any]:
        ev = self._events().get(event_id)
        if not ev:
            return {"error": "event not found"}
        return self._public_event(ev)

    def my_events(self) -> List[Dict[str, Any]]:
        evs = [self._public_event(e) for e in self._events().values()]
        evs.sort(key=lambda e: e.get("starts_at") or 0, reverse=True)
        return evs

    def delete_event(self, event_id: str, edit_key: str) -> Dict[str, Any]:
        events = self._events()
        ev = events.get(event_id)
        if not ev:
            return {"error": "event not found"}
        if edit_key != ev.get("edit_key") and not self._is_sudo(edit_key):
            return {"error": "edit_key required"}
        events.pop(event_id)
        self._save(self.events_path, events)
        return {"ok": True, "deleted": event_id}

    # ━━ Module-admin (sudo) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _admin_secret(self) -> str:
        env = os.environ.get("OPENEVENT_ADMIN_KEY")
        if env:
            return env
        data = self._load(self.admin_path, {})
        if not data.get("secret"):
            data = {"secret": secrets.token_urlsafe(24), "created": int(time.time())}
            self._save(self.admin_path, data, secret=True)
        return data["secret"]

    def _is_sudo(self, token) -> bool:
        import hmac
        return bool(token) and hmac.compare_digest(str(token), self._admin_secret())

    def verify_sudo(self, admin_secret: str) -> Dict[str, Any]:
        return {"sudo": self._is_sudo(admin_secret)}

    def admin_delete_event(self, admin_secret: str, event_id: str) -> Dict[str, Any]:
        if not self._is_sudo(admin_secret):
            return {"error": "module-admin secret required"}
        events = self._events()
        if event_id not in events:
            return {"error": "event not found"}
        events.pop(event_id)
        self._save(self.events_path, events)
        return {"ok": True, "deleted": event_id}

    # ━━ Serve / register (mirrors openplay) ━━━━━━━━━━━━━━━━━━━━━━━
    def serve(self, port=None, app_port=None, dev=True):
        return self.serve_app(app_port=app_port, dev=dev)

    def kill(self):
        return self.kill_app()

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        import subprocess
        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        pm2_cmd = ["pm2", "start", cmd[0], "--name", name, "--"]
        pm2_cmd.extend(cmd[1:])
        if cwd:
            idx = pm2_cmd.index("--")
            pm2_cmd.insert(idx, cwd)
            pm2_cmd.insert(idx, "--cwd")
        result = subprocess.run(pm2_cmd, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        return result.returncode == 0

    def _pm2_kill(self, name):
        import subprocess
        return subprocess.run(["pm2", "delete", name], capture_output=True, text=True).returncode == 0

    def serve_api(self, port=None, reload=True):
        port = int(port or self.port)
        name = "openevent.api"
        api_dir = self.module_dir / "api"
        if not (api_dir / "api.py").exists():
            return {"error": "api/api.py not found"}
        mod_root = str(self.module_dir.parent.parent.parent)
        env = {
            "PYTHONPATH": f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
            "PORT": str(port),
        }
        cmd = ["python3", "-m", "uvicorn", "api:app", "--host", "0.0.0.0",
               "--port", str(port), "--app-dir", str(api_dir)]
        if reload:
            cmd.append("--reload")
        self._pm2_start(name, cmd, env=env)
        return {"api": f"http://localhost:{port}", "pm2": name, "docs": f"http://localhost:{port}/docs"}

    def kill_api(self):
        ok = self._pm2_kill("openevent.api")
        return {"killed": ["openevent.api"] if ok else [], "success": ok}

    def serve_app(self, app_port=None, dev=True):
        app_port = int(app_port or self.app_port)
        results = {}
        self.kill_app()
        results.update(self.serve_api(port=self.port, reload=dev))
        app_dir = self.module_dir / "app"
        if (app_dir / "package.json").exists():
            name = "openevent.app"
            env = {"NEXT_PUBLIC_API_URL": f"http://localhost:{self.port}", "PORT": str(app_port)}
            cmd = ["npx", "next", "dev" if dev else "start", "-p", str(app_port)]
            self._pm2_start(name, cmd, cwd=str(app_dir), env=env)
            results["app"] = f"http://localhost:{app_port}"
            results["pm2_app"] = name
        else:
            results["app"] = None
        results["dev"] = dev
        results["registration"] = self.register(
            app_url=f"http://localhost:{app_port}",
            api_url=f"http://localhost:{self.port}",
            owner=os.environ.get("OPENEVENT_OWNER", ""),
        )
        return results

    def register(self, app_url=None, api_url=None, owner=None, gateway="https://modc2.com"):
        app_url = app_url or f"http://localhost:{self.app_port}"
        api_url = api_url or f"http://localhost:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("openevent", app_url)
            ns.reg_app("openevent", app_url, owner=owner or "", port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/openevent"
            print(f"openevent registered → {public}  (app: {app_url}, api: {api_url})")
            return {"ok": True, "gateway": public, "app": app_url, "api": api_url}
        except Exception as e:
            print(f"openevent: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": app_url, "api": api_url}

    def deregister(self):
        try:
            ns = m.mod("server.namespace")()
            ns.dereg_app("openevent")
            return {"ok": True, "deregistered": "openevent"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kill_app(self):
        killed = []
        if self._pm2_kill("openevent.api"):
            killed.append("openevent.api")
        if self._pm2_kill("openevent.app"):
            killed.append("openevent.app")
        return {"killed": killed}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def forward(self, action=None, **kwargs):
        actions = {
            "health": lambda: self.health(),
            "status": lambda: self.status(),
            "platforms": lambda: self.platforms(),
            "cities": lambda: self.cities(),
            "discover": lambda: self.discover(
                city=kwargs.get("city"), query=kwargs.get("query", ""),
                sources=kwargs.get("sources"), limit=int(kwargs.get("limit", 50)),
                refresh=bool(kwargs.get("refresh", False))),
            "connect": lambda: self.connect(kwargs.pop("platform", ""), **kwargs),
            "disconnect": lambda: self.disconnect(kwargs.get("platform", "")),
            "connections": lambda: self.connections(),
            "create_event": lambda: self.create_event(**kwargs),
            "broadcast": lambda: self.broadcast(
                kwargs.get("event_id", ""), kwargs.get("edit_key", ""), kwargs.get("targets") or []),
            "draft_for": lambda: self.draft_for(kwargs.get("event_id", ""), kwargs.get("platform", "")),
            "event": lambda: self.event(kwargs.get("event_id", "")),
            "my_events": lambda: self.my_events(),
            "delete_event": lambda: self.delete_event(kwargs.get("event_id", ""), kwargs.get("edit_key", "")),
            "verify_sudo": lambda: self.verify_sudo(kwargs.get("admin_secret", "")),
            "admin_delete_event": lambda: self.admin_delete_event(
                kwargs.get("admin_secret", ""), kwargs.get("event_id", "")),
            "serve": lambda: self.serve(app_port=kwargs.get("app_port"), dev=kwargs.get("dev", True)),
            "kill": lambda: self.kill(),
            "serve_api": lambda: self.serve_api(port=kwargs.get("port"), reload=kwargs.get("reload", True)),
            "kill_api": lambda: self.kill_api(),
            "serve_app": lambda: self.serve_app(app_port=kwargs.get("app_port"), dev=kwargs.get("dev", True)),
            "kill_app": lambda: self.kill_app(),
            "register": lambda: self.register(
                app_url=kwargs.get("app_url"), api_url=kwargs.get("api_url"),
                owner=kwargs.get("owner"), gateway=kwargs.get("gateway", "https://modc2.com")),
            "deregister": lambda: self.deregister(),
        }
        if not action or action not in actions:
            return {
                "module": "openevent",
                "description": self.description,
                "actions": list(actions.keys()),
                "status": self.status(),
            }
        return actions[action]()
