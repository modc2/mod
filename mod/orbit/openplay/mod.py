"""
OpenPlay — pickup sports across the city.

One place to see what's going on: soccer, hockey, basketball (and more).
Anyone can create a game; players join with just a handle. Admins run
recurring games, invite & kick players, and — only if they choose — charge
a per-seat fee in USDC/USDT on Base. By default every game is FREE.

Why it exists: organizing pickup games over a dozen WhatsApp groups is a
mess. OpenPlay is a single city-wide board + map.

Identity:  lightweight handle (display name). A wallet is only needed to
           pay for (or get paid for) a game that charges a fee.
Admin:     each game returns a one-time ``admin_key`` to its creator; that
           key gates invite/kick/cancel/edit. Stored off-chain.
Storage:   ~/.openplay/games.json  (one record per game/series)
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m


SPORTS = {
    "soccer": {"label": "Soccer", "emoji": "⚽", "color": "#34d399"},
    "basketball": {"label": "Basketball", "emoji": "\U0001f3c0", "color": "#fb923c"},
    "hockey": {"label": "Hockey", "emoji": "\U0001f3d2", "color": "#60a5fa"},
    "tennis": {"label": "Tennis", "emoji": "\U0001f3be", "color": "#facc15"},
    "volleyball": {"label": "Volleyball", "emoji": "\U0001f3d0", "color": "#f472b6"},
    "football": {"label": "Football", "emoji": "\U0001f3c8", "color": "#a78bfa"},
}

# Cities you can play in. Toronto is the default. Each has a map center + zoom.
# Adding a city here (or via the UI's "add a spot") makes it selectable.
CITIES = [
    {"key": "toronto", "label": "Toronto", "country": "Canada", "lat": 43.6532, "lng": -79.3832, "zoom": 12},
    {"key": "nyc", "label": "New York", "country": "USA", "lat": 40.7128, "lng": -74.0060, "zoom": 12},
    {"key": "vancouver", "label": "Vancouver", "country": "Canada", "lat": 49.2827, "lng": -123.1207, "zoom": 12},
    {"key": "london", "label": "London", "country": "UK", "lat": 51.5072, "lng": -0.1276, "zoom": 12},
    {"key": "la", "label": "Los Angeles", "country": "USA", "lat": 34.0522, "lng": -118.2437, "zoom": 11},
    {"key": "chicago", "label": "Chicago", "country": "USA", "lat": 41.8781, "lng": -87.6298, "zoom": 11},
]

# Curated, real pickup spots per city — suggestions so a game lands on the map
# with correct coordinates. Not games; just venue presets. Users add their own
# via add_venue() (persisted to ~/.openplay/venues.json) for any city.
VENUES = [
    # ── Toronto (default) ──
    {"city": "toronto", "name": "Trinity Bellwoods Park", "neighborhood": "Trinity Bellwoods", "lat": 43.6469, "lng": -79.4131, "sports": ["soccer", "basketball"]},
    {"city": "toronto", "name": "Christie Pits Park", "neighborhood": "Christie Pits", "lat": 43.6645, "lng": -79.4205, "sports": ["soccer", "basketball"]},
    {"city": "toronto", "name": "Regent Park Athletic Grounds", "neighborhood": "Regent Park", "lat": 43.6605, "lng": -79.3625, "sports": ["soccer", "basketball"]},
    {"city": "toronto", "name": "Moss Park", "neighborhood": "Moss Park", "lat": 43.6545, "lng": -79.3700, "sports": ["basketball"]},
    {"city": "toronto", "name": "Greenwood Park Rink", "neighborhood": "Leslieville", "lat": 43.6679, "lng": -79.3265, "sports": ["hockey"]},
    {"city": "toronto", "name": "Dufferin Grove Park Rink", "neighborhood": "Dufferin Grove", "lat": 43.6573, "lng": -79.4326, "sports": ["hockey", "soccer"]},
    {"city": "toronto", "name": "Withrow Park Rink", "neighborhood": "Riverdale", "lat": 43.6766, "lng": -79.3490, "sports": ["hockey"]},
    {"city": "toronto", "name": "The Bentway Skate Trail", "neighborhood": "Fort York", "lat": 43.6377, "lng": -79.4035, "sports": ["hockey"]},
    {"city": "toronto", "name": "High Park", "neighborhood": "High Park", "lat": 43.6465, "lng": -79.4637, "sports": ["soccer", "tennis"]},
    {"city": "toronto", "name": "Riverdale Park East", "neighborhood": "Riverdale", "lat": 43.6694, "lng": -79.3559, "sports": ["soccer", "football"]},
    {"city": "toronto", "name": "Sir Winston Churchill Park Tennis", "neighborhood": "Casa Loma", "lat": 43.6810, "lng": -79.4015, "sports": ["tennis"]},
    {"city": "toronto", "name": "Stan Wadlow Park", "neighborhood": "East York", "lat": 43.6917, "lng": -79.3110, "sports": ["soccer", "football"]},
    {"city": "toronto", "name": "Wallace Emerson Park", "neighborhood": "Dovercourt", "lat": 43.6640, "lng": -79.4420, "sports": ["basketball", "hockey"]},
    # ── New York ──
    {"city": "nyc", "name": "West 4th Street Courts (The Cage)", "neighborhood": "Greenwich Village", "lat": 40.7314, "lng": -74.0027, "sports": ["basketball"]},
    {"city": "nyc", "name": "Pier 40", "neighborhood": "Hudson River Park", "lat": 40.7297, "lng": -74.0110, "sports": ["soccer", "football"]},
    {"city": "nyc", "name": "Chelsea Park", "neighborhood": "Chelsea", "lat": 40.7506, "lng": -74.0021, "sports": ["basketball", "soccer"]},
    {"city": "nyc", "name": "Sky Rink at Chelsea Piers", "neighborhood": "Chelsea", "lat": 40.7480, "lng": -74.0095, "sports": ["hockey"]},
    {"city": "nyc", "name": "Lasker Rink (Central Park)", "neighborhood": "Harlem", "lat": 40.7967, "lng": -73.9573, "sports": ["hockey"]},
    {"city": "nyc", "name": "Riverside Park Soccer Fields", "neighborhood": "Upper West Side", "lat": 40.8030, "lng": -73.9720, "sports": ["soccer"]},
    {"city": "nyc", "name": "Tompkins Square Park", "neighborhood": "East Village", "lat": 40.7265, "lng": -73.9815, "sports": ["basketball"]},
    {"city": "nyc", "name": "McCarren Park", "neighborhood": "Williamsburg, Brooklyn", "lat": 40.7204, "lng": -73.9520, "sports": ["soccer", "volleyball"]},
    {"city": "nyc", "name": "Rucker Park", "neighborhood": "Harlem", "lat": 40.8296, "lng": -73.9365, "sports": ["basketball"]},
    {"city": "nyc", "name": "Brooklyn Bridge Park Pier 5", "neighborhood": "Brooklyn Heights", "lat": 40.6975, "lng": -73.9990, "sports": ["soccer", "football"]},
]

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_CITY_KEYS = {c["key"] for c in CITIES}


class Mod:
    description = (
        "Pickup sports across the city — create games, invite & RSVP, run recurring "
        "games, free by default with optional USDC/USDT on Base."
    )

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser("~/.openplay"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.games_path = self.store_dir / "games.json"
        self.venues_path = self.store_dir / "venues.json"   # custom venues + hidden presets
        self.admin_path = self.store_dir / "admin.json"     # off-chain module-admin secret
        self.accounts_path = self.store_dir / "accounts.json"  # claimed name → key registry

        self.port = int(self.config.get("port", 50150))
        self.app_port = int(self.config.get("app_port", 50151))
        self.default_city = self.config.get("default_city", "toronto")
        self.payments = self.config.get("payments", {})
        self.chain = self.payments.get("chain", "base")

    def _load_config(self):
        p = self.module_dir / "config.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _load_json(self, path, default=None):
        p = Path(path)
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return default if default is not None else {}

    def _save_json(self, path, data):
        tmp = Path(path).with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    def _load_games(self) -> Dict[str, Any]:
        if self.games_path.exists():
            with open(self.games_path) as f:
                return json.load(f)
        return {}

    def _save_games(self, data):
        tmp = self.games_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(self.games_path)

    # ━━ Health / status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def health(self):
        return {"status": "ok", "module": "openplay", "games": len(self._load_games())}

    def status(self):
        games = self._load_games()
        occ = self.games()  # upcoming occurrences across the city
        by_sport: Dict[str, int] = {}
        going = 0
        for o in occ:
            by_sport[o["sport"]] = by_sport.get(o["sport"], 0) + 1
            going += o["going"]
        return {
            "games": len(games),
            "upcoming": len(occ),
            "players_going": going,
            "by_sport": by_sport,
            "free_by_default": True,
            "chain": self.chain,
        }

    def sports(self):
        return [{"key": k, **v} for k, v in SPORTS.items()]

    # ━━ Cities & venues ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def cities(self):
        """Selectable cities (default first). Includes a live venue count."""
        all_venues = self._all_venues()
        counts: Dict[str, int] = {}
        for v in all_venues:
            counts[v.get("city", "")] = counts.get(v.get("city", ""), 0) + 1
        out = []
        for c in CITIES:
            out.append({**c, "venues": counts.get(c["key"], 0),
                        "default": c["key"] == self.default_city})
        # surface ad-hoc cities that only exist in custom venues
        known = {c["key"] for c in CITIES}
        for city, n in counts.items():
            if city and city not in known:
                out.append({"key": city, "label": city.title(), "country": "",
                            "lat": None, "lng": None, "zoom": 12, "venues": n, "default": False})
        return out

    def _venue_store(self):
        return self._load_json(self.venues_path, {"custom": [], "hidden": []})

    def _all_venues(self):
        store = self._venue_store()
        hidden = set(store.get("hidden", []))
        out = []
        for v in VENUES:
            if v["name"] in hidden:
                continue
            out.append({"id": "preset:" + v["name"], "builtin": True, **v})
        for v in store.get("custom", []):
            if v.get("name") in hidden:
                continue
            out.append({"builtin": False, **v})
        return out

    def venues(self, city: str = None):
        """Venue presets for a city (built-in + user-added). Omit city for all."""
        vs = self._all_venues()
        if city:
            vs = [v for v in vs if v.get("city") == city]
        return vs

    def add_venue(self, name: str, lat: float, lng: float, city: str = None,
                  neighborhood: str = "", sports: list = None) -> Dict[str, Any]:
        """Add a reusable venue/spot (anyone can). Persisted for everyone."""
        name = (name or "").strip()
        if not name:
            return {"error": "venue name required"}
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return {"error": "valid lat/lng required (tap the map to place it)"}
        city = (city or self.default_city)
        sports = [s for s in (sports or []) if s in SPORTS] or list(SPORTS)[:3]
        store = self._venue_store()
        for v in store.get("custom", []):
            if v["name"].lower() == name.lower() and v.get("city") == city:
                return {"error": "a spot with that name already exists in this city"}
        venue = {"id": "v_" + secrets.token_hex(4), "name": name, "city": city,
                 "neighborhood": neighborhood.strip(), "lat": lat, "lng": lng,
                 "sports": sports, "added_by": "user", "added_at": int(time.time())}
        store.setdefault("custom", []).append(venue)
        self._save_json(self.venues_path, store)
        return {"ok": True, **venue}

    # ━━ Module-admin (sudo) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # The module owner holds a single off-chain secret that grants god-mode over
    # every game/player/venue. It is read from $OPENPLAY_ADMIN_KEY or generated
    # once into ~/.openplay/admin.json (chmod 600). Lives off-chain, never in
    # committed config. Passing it where a per-game admin_key is expected also
    # works, so the owner has organizer rights on every game.
    def _admin_secret(self) -> str:
        env = os.environ.get("OPENPLAY_ADMIN_KEY")
        if env:
            return env
        data = self._load_json(self.admin_path, {})
        if not data.get("secret"):
            data = {"secret": secrets.token_urlsafe(24), "created": int(time.time())}
            self._save_json(self.admin_path, data)
            try:
                os.chmod(self.admin_path, 0o600)
            except OSError:
                pass
        return data["secret"]

    def _is_sudo(self, token) -> bool:
        import hmac
        return bool(token) and hmac.compare_digest(str(token), self._admin_secret())

    def verify_sudo(self, admin_secret: str) -> Dict[str, Any]:
        """Check a module-admin secret (used by the UI to unlock owner mode)."""
        return {"sudo": self._is_sudo(admin_secret)}

    def _sudo_guard(self, admin_secret):
        if not self._is_sudo(admin_secret):
            return {"error": "module-admin secret required"}
        return None

    def admin_delete_game(self, admin_secret: str, game_id: str) -> Dict[str, Any]:
        """Hard-delete any game/series (owner only)."""
        err = self._sudo_guard(admin_secret)
        if err:
            return err
        games = self._load_games()
        if game_id not in games:
            return {"error": "game not found"}
        games.pop(game_id)
        self._save_games(games)
        return {"ok": True, "deleted_game": game_id}

    def admin_remove_player(self, admin_secret: str, handle: str,
                            game_id: str = None, occ: str = None) -> Dict[str, Any]:
        """Remove a player from one game, or purge a handle from EVERY game (owner only)."""
        err = self._sudo_guard(admin_secret)
        if err:
            return err
        if not handle:
            return {"error": "handle required"}
        games = self._load_games()
        touched = 0

        def scrub(bucket):
            n = 0
            for fld in ("players", "waitlist"):
                kept = [p for p in bucket.get(fld, []) if p.get("handle") != handle]
                n += len(bucket.get(fld, [])) - len(kept)
                bucket[fld] = kept
            for fld in ("invited", "declined"):
                bucket[fld] = [h for h in bucket.get(fld, []) if h != handle]
            return n

        targets = [game_id] if game_id else list(games.keys())
        for gid in targets:
            game = games.get(gid)
            if not game:
                continue
            game["roster"] = [h for h in game.get("roster", []) if h != handle]
            buckets = ([self._occ_mut(game, occ)] if (game_id and occ)
                       else list((game.get("rsvps") or {}).values()))
            for b in buckets:
                touched += scrub(b)
        self._save_games(games)
        return {"ok": True, "removed_handle": handle, "entries_removed": touched,
                "scope": "game" if game_id else "all_games"}

    def admin_delete_venue(self, admin_secret: str, venue_id: str = None,
                           name: str = None) -> Dict[str, Any]:
        """Delete a custom venue, or hide a built-in preset (owner only)."""
        err = self._sudo_guard(admin_secret)
        if err:
            return err
        store = self._venue_store()
        # custom venue by id
        if venue_id and not venue_id.startswith("preset:"):
            before = len(store.get("custom", []))
            store["custom"] = [v for v in store.get("custom", []) if v.get("id") != venue_id]
            if len(store["custom"]) < before:
                self._save_json(self.venues_path, store)
                return {"ok": True, "deleted_venue": venue_id}
        # built-in preset (or custom by name) → hide it
        target = name or (venue_id[len("preset:"):] if venue_id and venue_id.startswith("preset:") else None)
        if target:
            store.setdefault("hidden", [])
            if target not in store["hidden"]:
                store["hidden"].append(target)
            store["custom"] = [v for v in store.get("custom", []) if v.get("name") != target]
            self._save_json(self.venues_path, store)
            return {"ok": True, "hidden_venue": target}
        return {"error": "venue_id or name required"}

    # ━━ Accounts — claimed names backed by a key ━━━━━━━━━━━━━━━━━━
    # A player signs in with a name + password; the browser deterministically
    # derives an Ethereum keypair from them (never sent to us). The name is
    # claimed here by proving control of the key (an EIP-191 personal-sign over
    # a canonical message). One name → one address, so a handle can't be
    # impersonated. Rotating the key (new password or a fresh key) re-points the
    # name, authorised by a signature from the CURRENT key. Public reads expose
    # only name↔address; the private key stays in the browser.
    SIG_WINDOW = 900  # signed claims/rotations must be within 15 min (anti-replay)

    def _accounts(self) -> Dict[str, Any]:
        return self._load_json(self.accounts_path, {"names": {}})

    @staticmethod
    def _name_key(name: str) -> str:
        return " ".join((name or "").split()).lower()

    @staticmethod
    def _valid_name(name: str):
        disp = " ".join((name or "").split())
        if not (2 <= len(disp) <= 32):
            return None, "name must be 2–32 characters"
        if any(ord(c) < 32 for c in disp):
            return None, "name has invalid characters"
        return disp, None

    @staticmethod
    def _valid_addr(addr: str) -> bool:
        addr = (addr or "").strip()
        return addr.startswith("0x") and len(addr) == 42

    # Canonical signed messages — MUST byte-match the client (lib/account.ts).
    @staticmethod
    def _claim_message(name: str, address: str, ts: int) -> str:
        return f"OpenPlay — claim identity\nName: {name}\nKey: {address}\nTime: {ts}"

    @staticmethod
    def _rotate_message(name: str, new_address: str, ts: int) -> str:
        return f"OpenPlay — rotate key\nName: {name}\nNew key: {new_address}\nTime: {ts}"

    def _fresh(self, ts) -> bool:
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            return False
        return ts > 0 and abs(int(time.time()) - ts) <= self.SIG_WINDOW

    @staticmethod
    def _recover(message: str, signature: str) -> Optional[str]:
        """Recover the signer address from an EIP-191 personal_sign signature."""
        try:
            from eth_account import Account as _EA
            from eth_account.messages import encode_defunct
            return _EA.recover_message(encode_defunct(text=message), signature=signature)
        except Exception:
            return None

    def account(self, name: str) -> Dict[str, Any]:
        """Public: resolve a claimed name → its address (and whether it's free)."""
        key = self._name_key(name)
        if not key:
            return {"error": "name required"}
        rec = self._accounts().get("names", {}).get(key)
        if not rec:
            return {"name": " ".join((name or "").split()), "exists": False, "available": True}
        return {"name": rec.get("display", name), "exists": True, "available": False,
                "address": rec.get("address"), "created": rec.get("created"),
                "rotated": rec.get("rotated"), "rotations": rec.get("rotations", 0)}

    def register_account(self, name: str, address: str, signature: str,
                         ts: int = 0) -> Dict[str, Any]:
        """Claim a name for a key. Proven by signing `_claim_message` with that
        key. Idempotent if the name is already yours; rejected if it's someone
        else's."""
        disp, err = self._valid_name(name)
        if err:
            return {"error": err}
        addr = (address or "").strip()
        if not self._valid_addr(addr):
            return {"error": "a valid wallet address is required"}
        if not self._fresh(ts):
            return {"error": "this request expired — please try again"}
        signer = self._recover(self._claim_message(disp, addr, int(ts)), signature)
        if not signer or signer.lower() != addr.lower():
            return {"error": "signature does not match the key — sign-in failed"}
        data = self._accounts()
        names = data.setdefault("names", {})
        key = self._name_key(disp)
        existing = names.get(key)
        if existing and existing.get("address", "").lower() != addr.lower():
            return {"error": f"the name “{disp}” is already taken — pick another"}
        if existing:
            existing["display"] = disp  # refresh casing on re-login
            self._save_json(self.accounts_path, data)
            return {"ok": True, "name": disp, "address": addr,
                    "claimed": False, "already_yours": True}
        now = int(time.time())
        names[key] = {"display": disp, "address": addr,
                      "created": now, "rotated": now, "rotations": 0}
        self._save_json(self.accounts_path, data)
        return {"ok": True, "name": disp, "address": addr, "claimed": True}

    def rotate_account(self, name: str, new_address: str, signature: str,
                       ts: int = 0) -> Dict[str, Any]:
        """Re-point a name to a new key. Must be signed by the CURRENT key
        (`_rotate_message`). Lets a user change their password or swap to a fresh
        key while keeping their name."""
        disp, err = self._valid_name(name)
        if err:
            return {"error": err}
        new_addr = (new_address or "").strip()
        if not self._valid_addr(new_addr):
            return {"error": "a valid new wallet address is required"}
        if not self._fresh(ts):
            return {"error": "this request expired — please try again"}
        data = self._accounts()
        names = data.setdefault("names", {})
        key = self._name_key(disp)
        rec = names.get(key)
        if not rec:
            return {"error": "no account with that name yet — claim it first"}
        if rec.get("address", "").lower() == new_addr.lower():
            return {"ok": True, "name": disp, "address": new_addr,
                    "rotations": rec.get("rotations", 0), "unchanged": True}
        signer = self._recover(self._rotate_message(disp, new_addr, int(ts)), signature)
        if not signer or signer.lower() != rec.get("address", "").lower():
            return {"error": "a rotation must be signed by your current key"}
        rec["address"] = new_addr
        rec["rotated"] = int(time.time())
        rec["rotations"] = rec.get("rotations", 0) + 1
        self._save_json(self.accounts_path, data)
        return {"ok": True, "name": disp, "address": new_addr,
                "rotations": rec["rotations"]}

    # ━━ Recurrence ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    @staticmethod
    def _occ_key(ts: int) -> str:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")

    def _occurrences(self, game, horizon_days=70, max_occ=14, grace_hours=4) -> List[int]:
        """Expand a game into upcoming occurrence timestamps."""
        rec = game.get("recurrence") or {"freq": "once"}
        freq = rec.get("freq", "once")
        start = int(game["starts_at"])
        now = int(time.time())
        horizon = now + horizon_days * 86400
        grace = grace_hours * 3600

        candidates: List[int] = []
        if freq == "once":
            candidates = [start]
        else:
            interval = max(1, int(rec.get("interval", 1)))
            days = rec.get("days")  # weekly: list of weekday ints, 0=Mon
            until = int(rec["until"]) if rec.get("until") else None
            t = start
            guard = 0
            limit = horizon + 86400
            while t <= limit and guard < 1000:
                guard += 1
                if until and t > until:
                    break
                if freq == "weekly" and days:
                    wd = datetime.fromtimestamp(t, tz=timezone.utc).weekday()
                    if wd in days:
                        candidates.append(t)
                    t += 86400
                elif freq == "daily":
                    candidates.append(t)
                    t += 86400 * interval
                else:  # plain weekly (same weekday as start)
                    candidates.append(t)
                    t += 7 * 86400 * interval

        out: List[int] = []
        for t in candidates:
            if t < now - grace:
                continue
            if t > horizon:
                break
            if self._occ(game, self._occ_key(t)).get("cancelled"):
                continue
            out.append(t)
            if len(out) >= max_occ:
                break
        return out

    def _recurrence_label(self, game) -> str:
        rec = game.get("recurrence") or {"freq": "once"}
        freq = rec.get("freq", "once")
        if freq == "once":
            return "One-off"
        if freq == "daily":
            n = int(rec.get("interval", 1))
            return "Every day" if n == 1 else f"Every {n} days"
        if freq == "weekly":
            days = rec.get("days")
            if days:
                return "Weekly: " + ", ".join(_WEEKDAYS[d] for d in sorted(days))
            n = int(rec.get("interval", 1))
            wd = _WEEKDAYS[datetime.fromtimestamp(int(game["starts_at"]), tz=timezone.utc).weekday()]
            return f"Every {wd}" if n == 1 else f"Every {n} weeks ({wd})"
        return freq

    # ━━ Occurrence RSVP state ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _occ(self, game, occ_key) -> Dict[str, Any]:
        """Get (without mutating) the rsvp bucket for an occurrence."""
        return (game.get("rsvps") or {}).get(occ_key, {})

    def _occ_mut(self, game, occ_key) -> Dict[str, Any]:
        rsvps = game.setdefault("rsvps", {})
        return rsvps.setdefault(
            occ_key,
            {"players": [], "waitlist": [], "invited": [], "declined": [], "cancelled": False},
        )

    def _resolve_occ(self, game, occ: Optional[str]) -> Optional[str]:
        if occ:
            return occ
        ts = self._occurrences(game, max_occ=1)
        return self._occ_key(ts[0]) if ts else None

    # ━━ Public reads ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _summarize(self, game, ts) -> Dict[str, Any]:
        occ_key = self._occ_key(ts)
        bucket = self._occ(game, occ_key)
        players = bucket.get("players", [])
        cost = game.get("cost")
        free = not cost or float(cost.get("amount", 0)) <= 0
        cap = int(game.get("capacity", 0))
        return {
            "game_id": game["id"],
            "occ": occ_key,
            "occ_ts": int(ts),
            "sport": game["sport"],
            "title": game["title"],
            "city": game.get("city", self.default_city),
            "venue": game.get("venue", ""),
            "neighborhood": game.get("neighborhood", ""),
            "lat": game.get("lat"),
            "lng": game.get("lng"),
            "starts_at": int(ts),
            "duration_min": int(game.get("duration_min", 60)),
            "capacity": cap,
            "going": len(players),
            "spots_left": max(cap - len(players), 0) if cap else None,
            "waitlist": len(bucket.get("waitlist", [])),
            "recurring": (game.get("recurrence") or {}).get("freq", "once") != "once",
            "recurrence": self._recurrence_label(game),
            "free": free,
            "cost": None if free else {"amount": float(cost["amount"]), "currency": cost.get("currency", "USDC")},
            "admin": game.get("admin", ""),
            "notes": game.get("notes", ""),
            "status": game.get("status", "open"),
        }

    def games(self, sport: Optional[str] = None, city: Optional[str] = None) -> List[Dict[str, Any]]:
        """All upcoming occurrences, soonest first. Filter by sport and/or city."""
        out: List[Dict[str, Any]] = []
        for game in self._load_games().values():
            if game.get("status") == "cancelled":
                continue
            if sport and game.get("sport") != sport:
                continue
            if city and game.get("city", self.default_city) != city:
                continue
            for ts in self._occurrences(game):
                out.append(self._summarize(game, ts))
        out.sort(key=lambda o: o["occ_ts"])
        return out

    def my_games(self, handle: str = "", game_ids: List[str] = None) -> Dict[str, Any]:
        """Games that belong to you — `hosting` (you're the organizer, by admin_key
        or matching handle) and `playing` (you've RSVP'd). Searches every city.
        Cancelled games are dropped; games whose next date has passed are kept with
        `upcoming_none=True` so you can still find/cancel them."""
        handle = (handle or "").strip()
        owned_ids = set(game_ids or [])
        hosting: List[Dict[str, Any]] = []
        playing: List[Dict[str, Any]] = []
        for gid, game in self._load_games().items():
            if game.get("status") == "cancelled":
                continue
            is_owner = gid in owned_ids or (bool(handle) and game.get("admin", "") == handle)
            is_player = False
            if handle:
                if handle in game.get("roster", []):
                    is_player = True
                else:
                    for bucket in (game.get("rsvps") or {}).values():
                        if any(p.get("handle") == handle for p in bucket.get("players", [])):
                            is_player = True
                            break
            if not (is_owner or is_player):
                continue
            occs = self._occurrences(game, max_occ=1)
            summary = self._summarize(game, occs[0] if occs else int(game["starts_at"]))
            if not occs:
                summary["upcoming_none"] = True
            summary["role"] = "host" if is_owner else "player"
            (hosting if is_owner else playing).append(summary)
        hosting.sort(key=lambda o: o["occ_ts"])
        playing.sort(key=lambda o: o["occ_ts"])
        return {"hosting": hosting, "playing": playing}

    def game(self, game_id: str, occ: Optional[str] = None) -> Dict[str, Any]:
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        occ_key = self._resolve_occ(game, occ)
        if not occ_key:
            return {"error": "no upcoming occurrence"}
        bucket = self._occ(game, occ_key)
        roster = game.get("roster", [])
        joined = {p["handle"] for p in bucket.get("players", [])}
        wl = {p["handle"] for p in bucket.get("waitlist", [])}
        declined = set(bucket.get("declined", []))
        invited = [h for h in (roster + bucket.get("invited", []))
                   if h not in joined and h not in wl and h not in declined]
        ts = None
        for cand in self._occurrences(game, horizon_days=400, max_occ=400):
            if self._occ_key(cand) == occ_key:
                ts = cand
                break
        detail = self._summarize(game, ts if ts is not None else int(game["starts_at"]))
        detail.update({
            "players": bucket.get("players", []),
            "waitlist": bucket.get("waitlist", []),
            "invited": sorted(set(invited)),
            "upcoming": [self._occ_key(t) for t in self._occurrences(game)],
            "links": game.get("links", []),
            "chat_count": len(game.get("chat", [])),
        })
        return detail

    # ━━ Create / edit ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def create_game(self, sport: str, title: str, starts_at: int, admin: str,
                    city: str = None, venue: str = "", neighborhood: str = "",
                    lat: float = None, lng: float = None, duration_min: int = 60,
                    capacity: int = 10, recurrence: dict = None, cost: dict = None,
                    notes: str = "", admin_wallet: str = "", links: list = None) -> Dict[str, Any]:
        """Create a game (one-off or recurring). Returns the game + a one-time admin_key."""
        sport = (sport or "").lower()
        if sport not in SPORTS:
            return {"error": f"unknown sport '{sport}'", "sports": list(SPORTS)}
        if not title:
            return {"error": "title required"}
        if not admin:
            return {"error": "admin handle required"}
        try:
            starts_at = int(starts_at)
        except (TypeError, ValueError):
            return {"error": "starts_at must be a unix timestamp"}
        if starts_at <= 0:
            return {"error": "starts_at required"}

        cost = self._normalize_cost(cost, admin_wallet)
        if isinstance(cost, dict) and cost.get("error"):
            return cost

        rec = self._normalize_recurrence(recurrence)
        if rec.get("error"):
            return rec

        games = self._load_games()
        gid = secrets.token_hex(5)
        admin_key = secrets.token_urlsafe(16)
        games[gid] = {
            "id": gid,
            "sport": sport,
            "title": title.strip(),
            "city": (city or self.default_city),
            "venue": venue.strip(),
            "neighborhood": neighborhood.strip(),
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "starts_at": starts_at,
            "duration_min": int(duration_min),
            "capacity": int(capacity),
            "recurrence": rec,
            "cost": cost,
            "notes": (notes or "").strip(),
            "links": self._normalize_links(links),
            "admin": admin.strip(),
            "admin_wallet": (admin_wallet or "").strip(),
            "admin_key": admin_key,
            "status": "open",
            "created_at": int(time.time()),
            "roster": [],
            "rsvps": {},
            "chat": [],
        }
        self._save_games(games)
        detail = self.game(gid)
        detail["admin_key"] = admin_key  # surfaced once, to the creator only
        return detail

    def _normalize_links(self, links) -> List[Dict[str, str]]:
        """Group/chat links an organizer pins to a game (Telegram, WhatsApp, …)."""
        out: List[Dict[str, str]] = []
        for l in (links or []):
            if not isinstance(l, dict):
                continue
            url = (l.get("url") or "").strip()
            if not url or not url.lower().startswith(("http://", "https://")):
                continue
            label = (l.get("label") or "").strip()
            if not label:
                low = url.lower()
                if "t.me" in low or "telegram" in low:
                    label = "Telegram"
                elif "whatsapp" in low or "wa.me" in low:
                    label = "WhatsApp"
                elif "discord" in low:
                    label = "Discord"
                elif "signal" in low:
                    label = "Signal"
                else:
                    label = "Group chat"
            out.append({"label": label[:24], "url": url[:400]})
        return out[:6]

    def _normalize_recurrence(self, recurrence) -> Dict[str, Any]:
        if not recurrence:
            return {"freq": "once"}
        freq = recurrence.get("freq", "once")
        if freq not in ("once", "daily", "weekly"):
            return {"error": f"recurrence.freq must be once|daily|weekly, got '{freq}'"}
        rec = {"freq": freq, "interval": max(1, int(recurrence.get("interval", 1)))}
        if freq == "weekly" and recurrence.get("days"):
            try:
                days = sorted({int(d) for d in recurrence["days"] if 0 <= int(d) <= 6})
            except (TypeError, ValueError):
                return {"error": "recurrence.days must be weekday ints 0=Mon..6=Sun"}
            rec["days"] = days
        if recurrence.get("until"):
            rec["until"] = int(recurrence["until"])
        return rec

    def _normalize_cost(self, cost, admin_wallet) -> Optional[Dict[str, Any]]:
        if not cost:
            return None
        try:
            amount = float(cost.get("amount", 0))
        except (TypeError, ValueError):
            return {"error": "cost.amount must be a number"}
        if amount <= 0:
            return None  # free
        currency = (cost.get("currency") or "USDC").upper()
        chain = cost.get("chain", self.chain)
        tokens = self.payments.get("tokens", {}).get(chain, {})
        if currency not in tokens:
            return {"error": f"{currency} not supported on {chain}", "supported": list(tokens)}
        receiver = (cost.get("receiver") or admin_wallet or "").strip()
        if not receiver:
            return {"error": "a paid game needs a receiver wallet (the admin's Base address)"}
        return {"amount": amount, "currency": currency, "chain": chain, "receiver": receiver}

    def _auth(self, game, admin_key) -> Optional[Dict[str, str]]:
        # the per-game organizer key OR the module-admin secret (god-mode) passes
        if admin_key and (admin_key == game.get("admin_key") or self._is_sudo(admin_key)):
            return None
        return {"error": "admin_key required (only the game organizer can do that)"}

    def edit_game(self, game_id: str, admin_key: str, **fields) -> Dict[str, Any]:
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        err = self._auth(game, admin_key)
        if err:
            return err
        editable = {"title", "venue", "neighborhood", "lat", "lng", "starts_at",
                    "duration_min", "capacity", "notes"}
        for k, v in fields.items():
            if k in editable:
                game[k] = v
        if "links" in fields:
            game["links"] = self._normalize_links(fields["links"])
        if "recurrence" in fields:
            rec = self._normalize_recurrence(fields["recurrence"])
            if rec.get("error"):
                return rec
            game["recurrence"] = rec
        self._save_games(games)
        return self.game(game_id)

    def set_cost(self, game_id: str, admin_key: str, amount: float = 0,
                 currency: str = "USDC", receiver: str = "", chain: str = None) -> Dict[str, Any]:
        """Set (or clear, with amount=0) the per-seat fee. Free by default."""
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        err = self._auth(game, admin_key)
        if err:
            return err
        cost = self._normalize_cost(
            {"amount": amount, "currency": currency, "receiver": receiver,
             "chain": chain or self.chain},
            game.get("admin_wallet", ""),
        )
        if isinstance(cost, dict) and cost.get("error"):
            return cost
        game["cost"] = cost
        self._save_games(games)
        return self.game(game_id)

    def cancel_game(self, game_id: str, admin_key: str, occ: str = None) -> Dict[str, Any]:
        """Cancel the whole series, or just one occurrence (occ=YYYY-MM-DD)."""
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        err = self._auth(game, admin_key)
        if err:
            return err
        if occ:
            self._occ_mut(game, occ)["cancelled"] = True
            self._save_games(games)
            return {"ok": True, "cancelled_occurrence": occ, "game_id": game_id}
        game["status"] = "cancelled"
        self._save_games(games)
        return {"ok": True, "cancelled": game_id}

    # ━━ Join / leave / invites / kick ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def join(self, game_id: str, handle: str, wallet: str = "", occ: str = None,
             payment: str = None) -> Dict[str, Any]:
        """RSVP to a game. Free games join instantly; paid games require payment."""
        if not handle:
            return {"error": "handle required"}
        games = self._load_games()
        game = games.get(game_id)
        if not game or game.get("status") == "cancelled":
            return {"error": "game not found"}
        occ_key = self._resolve_occ(game, occ)
        if not occ_key:
            return {"error": "no upcoming occurrence to join"}
        bucket = self._occ_mut(game, occ_key)
        if bucket.get("cancelled"):
            return {"error": "that occurrence was cancelled"}

        if any(p["handle"] == handle for p in bucket["players"]):
            return {"ok": True, "rsvp": "already_in", **self.game(game_id, occ_key)}

        cost = game.get("cost")
        paid = True
        if cost and float(cost.get("amount", 0)) > 0:
            if not payment:
                return {
                    "payment_required": True,
                    "payment": self._payment_requirements(game),
                    "occ": occ_key,
                    "message": f"This game costs {cost['amount']} {cost['currency']} on {cost['chain']}.",
                }
            paid = payment  # tx hash / x402 receipt recorded as proof

        # remove from waitlist/invited/declined if present
        bucket["waitlist"] = [p for p in bucket["waitlist"] if p["handle"] != handle]
        bucket["declined"] = [h for h in bucket.get("declined", []) if h != handle]
        bucket["invited"] = [h for h in bucket.get("invited", []) if h != handle]

        entry = {"handle": handle, "wallet": (wallet or "").strip(),
                 "paid": paid, "at": int(time.time())}
        cap = int(game.get("capacity", 0))
        if cap and len(bucket["players"]) >= cap:
            bucket["waitlist"].append(entry)
            self._save_games(games)
            return {"ok": True, "rsvp": "waitlisted", **self.game(game_id, occ_key)}
        bucket["players"].append(entry)
        self._save_games(games)
        return {"ok": True, "rsvp": "going", **self.game(game_id, occ_key)}

    def leave(self, game_id: str, handle: str, occ: str = None) -> Dict[str, Any]:
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        occ_key = self._resolve_occ(game, occ)
        bucket = self._occ_mut(game, occ_key)
        before = len(bucket["players"]) + len(bucket["waitlist"])
        bucket["players"] = [p for p in bucket["players"] if p["handle"] != handle]
        bucket["waitlist"] = [p for p in bucket["waitlist"] if p["handle"] != handle]
        # promote first waitlister into the freed seat
        cap = int(game.get("capacity", 0))
        if bucket["waitlist"] and (not cap or len(bucket["players"]) < cap):
            bucket["players"].append(bucket["waitlist"].pop(0))
        self._save_games(games)
        after = len(bucket["players"]) + len(bucket["waitlist"])
        return {"ok": True, "left": before != after, **self.game(game_id, occ_key)}

    def invite(self, game_id: str, admin_key: str, handle: str, occ: str = None,
               series: bool = False) -> Dict[str, Any]:
        """Invite a player. series=True adds them as a regular on every occurrence."""
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        err = self._auth(game, admin_key)
        if err:
            return err
        if not handle:
            return {"error": "handle required"}
        if series:
            roster = game.setdefault("roster", [])
            if handle not in roster:
                roster.append(handle)
        else:
            occ_key = self._resolve_occ(game, occ)
            bucket = self._occ_mut(game, occ_key)
            if handle not in bucket["invited"]:
                bucket["invited"].append(handle)
            bucket["declined"] = [h for h in bucket.get("declined", []) if h != handle]
        self._save_games(games)
        return {"ok": True, "invited": handle, "series": series, **self.game(game_id, occ)}

    def accept_invite(self, game_id: str, handle: str, wallet: str = "",
                      occ: str = None, payment: str = None) -> Dict[str, Any]:
        """Accept an invite — same as join, but only valid if you were invited."""
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        occ_key = self._resolve_occ(game, occ)
        bucket = self._occ_mut(game, occ_key)
        invited = set(game.get("roster", [])) | set(bucket.get("invited", []))
        if handle not in invited:
            return {"error": "you were not invited to this game"}
        return self.join(game_id, handle, wallet=wallet, occ=occ_key, payment=payment)

    def decline_invite(self, game_id: str, handle: str, occ: str = None) -> Dict[str, Any]:
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        occ_key = self._resolve_occ(game, occ)
        bucket = self._occ_mut(game, occ_key)
        bucket["invited"] = [h for h in bucket["invited"] if h != handle]
        if handle not in bucket.setdefault("declined", []):
            bucket["declined"].append(handle)
        self._save_games(games)
        return {"ok": True, "declined": handle, **self.game(game_id, occ_key)}

    def kick(self, game_id: str, admin_key: str, handle: str, occ: str = None,
             series: bool = False) -> Dict[str, Any]:
        """Remove a player from an occurrence (or, with series=True, the roster too)."""
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        err = self._auth(game, admin_key)
        if err:
            return err
        occ_key = self._resolve_occ(game, occ)
        bucket = self._occ_mut(game, occ_key)
        bucket["players"] = [p for p in bucket["players"] if p["handle"] != handle]
        bucket["waitlist"] = [p for p in bucket["waitlist"] if p["handle"] != handle]
        bucket["invited"] = [h for h in bucket["invited"] if h != handle]
        if series:
            game["roster"] = [h for h in game.get("roster", []) if h != handle]
        # promote waitlist into freed seat
        cap = int(game.get("capacity", 0))
        if bucket["waitlist"] and (not cap or len(bucket["players"]) < cap):
            bucket["players"].append(bucket["waitlist"].pop(0))
        self._save_games(games)
        return {"ok": True, "kicked": handle, **self.game(game_id, occ_key)}

    # ━━ Game chat ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _is_member(self, game, handle) -> bool:
        """A game's chat is for its crew: the organizer, the standing roster, or
        anyone who has RSVP'd (going or waitlisted) to any occurrence."""
        h = (handle or "").strip()
        if not h:
            return False
        if h == game.get("admin", "") or h in game.get("roster", []):
            return True
        for bucket in (game.get("rsvps") or {}).values():
            if any(p.get("handle") == h for p in bucket.get("players", [])):
                return True
            if any(p.get("handle") == h for p in bucket.get("waitlist", [])):
                return True
        return False

    def messages(self, game_id: str, since: int = 0) -> Dict[str, Any]:
        """Read a game's chat. since=unix-ts returns only newer messages (polling)."""
        game = self._load_games().get(game_id)
        if not game:
            return {"error": "game not found"}
        chat = game.get("chat", [])
        try:
            since = int(since)
        except (TypeError, ValueError):
            since = 0
        if since:
            chat = [m for m in chat if int(m.get("ts", 0)) > since]
        return {"messages": chat, "count": len(game.get("chat", []))}

    def post_message(self, game_id: str, handle: str, text: str) -> Dict[str, Any]:
        """Post to a game's chat. Only the crew (see _is_member) can post."""
        handle = (handle or "").strip()
        text = (text or "").strip()
        if not handle:
            return {"error": "set your name first"}
        if not text:
            return {"error": "say something"}
        games = self._load_games()
        game = games.get(game_id)
        if not game or game.get("status") == "cancelled":
            return {"error": "game not found"}
        if not self._is_member(game, handle):
            return {"error": "join the game to chat with the crew"}
        chat = game.setdefault("chat", [])
        msg = {"id": secrets.token_hex(4), "handle": handle,
               "text": text[:1000], "ts": int(time.time())}
        chat.append(msg)
        if len(chat) > 200:
            del chat[:-200]
        self._save_games(games)
        return {"ok": True, "message": msg}

    # ━━ Payments ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _payment_requirements(self, game) -> Dict[str, Any]:
        cost = game.get("cost") or {}
        chain = cost.get("chain", self.chain)
        currency = cost.get("currency", "USDC")
        token = self.payments.get("tokens", {}).get(chain, {}).get(currency, {})
        decimals = int(token.get("decimals", 6))
        amount = float(cost.get("amount", 0))
        atomic = int(round(amount * (10 ** decimals)))
        return {
            "chain": chain,
            "currency": currency,
            "asset": token.get("address", ""),
            "decimals": decimals,
            "amount": amount,
            "atomic_amount": str(atomic),
            "receiver": cost.get("receiver", ""),
            "facilitator": self.payments.get("facilitator", ""),
            "scheme": "x402",
        }

    def payment_requirements(self, game_id: str) -> Dict[str, Any]:
        games = self._load_games()
        game = games.get(game_id)
        if not game:
            return {"error": "game not found"}
        cost = game.get("cost")
        if not cost or float(cost.get("amount", 0)) <= 0:
            return {"free": True}
        return self._payment_requirements(game)

    # ━━ Serve / register (mirrors openhouse) ━━━━━━━━━━━━━━━━━━━━━
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
        name = "openplay.api"
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
        ok = self._pm2_kill("openplay.api")
        return {"killed": ["openplay.api"] if ok else [], "success": ok}

    def serve_app(self, app_port=None, dev=True):
        app_port = int(app_port or self.app_port)
        results = {}
        self.kill_app()
        results.update(self.serve_api(port=self.port, reload=dev))
        app_dir = self.module_dir / "app"
        if (app_dir / "package.json").exists():
            name = "openplay.app"
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
            owner=os.environ.get("OPENPLAY_OWNER", ""),
        )
        return results

    def register(self, app_url=None, api_url=None, owner=None, gateway="https://modc2.com"):
        app_url = app_url or f"http://localhost:{self.app_port}"
        api_url = api_url or f"http://localhost:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("openplay", app_url)
            ns.reg_app("openplay", app_url, owner=owner or "", port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/openplay"
            print(f"openplay registered → {public}  (app: {app_url}, api: {api_url})")
            return {"ok": True, "gateway": public, "app": app_url, "api": api_url}
        except Exception as e:
            print(f"openplay: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": app_url, "api": api_url}

    def deregister(self):
        try:
            ns = m.mod("server.namespace")()
            ns.dereg_app("openplay")
            return {"ok": True, "deregistered": "openplay"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kill_app(self):
        killed = []
        if self._pm2_kill("openplay.api"):
            killed.append("openplay.api")
        if self._pm2_kill("openplay.app"):
            killed.append("openplay.app")
        return {"killed": killed}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def forward(self, action=None, **kwargs):
        """CLI entry: openplay <action> [args]"""
        actions = {
            "health": lambda: self.health(),
            "status": lambda: self.status(),
            "sports": lambda: self.sports(),
            "cities": lambda: self.cities(),
            "venues": lambda: self.venues(city=kwargs.get("city")),
            "add_venue": lambda: self.add_venue(
                kwargs.get("name", ""), kwargs.get("lat"), kwargs.get("lng"),
                city=kwargs.get("city"), neighborhood=kwargs.get("neighborhood", ""),
                sports=kwargs.get("sports")),
            "games": lambda: self.games(sport=kwargs.get("sport"), city=kwargs.get("city")),
            "my_games": lambda: self.my_games(
                handle=kwargs.get("handle", ""), game_ids=kwargs.get("game_ids")),
            "account": lambda: self.account(kwargs.get("name", "")),
            "register_account": lambda: self.register_account(
                kwargs.get("name", ""), kwargs.get("address", ""),
                kwargs.get("signature", ""), ts=kwargs.get("ts", 0)),
            "rotate_account": lambda: self.rotate_account(
                kwargs.get("name", ""), kwargs.get("new_address", ""),
                kwargs.get("signature", ""), ts=kwargs.get("ts", 0)),
            "verify_sudo": lambda: self.verify_sudo(kwargs.get("admin_secret", "")),
            "admin_delete_game": lambda: self.admin_delete_game(
                kwargs.get("admin_secret", ""), kwargs.get("game_id", "")),
            "admin_remove_player": lambda: self.admin_remove_player(
                kwargs.get("admin_secret", ""), kwargs.get("handle", ""),
                game_id=kwargs.get("game_id"), occ=kwargs.get("occ")),
            "admin_delete_venue": lambda: self.admin_delete_venue(
                kwargs.get("admin_secret", ""), venue_id=kwargs.get("venue_id"),
                name=kwargs.get("name")),
            "game": lambda: self.game(kwargs.get("game_id", ""), kwargs.get("occ")),
            "create_game": lambda: self.create_game(**kwargs),
            "edit_game": lambda: self.edit_game(**kwargs),
            "cancel_game": lambda: self.cancel_game(
                kwargs.get("game_id", ""), kwargs.get("admin_key", ""), kwargs.get("occ")),
            "set_cost": lambda: self.set_cost(**kwargs),
            "join": lambda: self.join(**kwargs),
            "leave": lambda: self.leave(**kwargs),
            "invite": lambda: self.invite(**kwargs),
            "accept_invite": lambda: self.accept_invite(**kwargs),
            "decline_invite": lambda: self.decline_invite(**kwargs),
            "kick": lambda: self.kick(**kwargs),
            "messages": lambda: self.messages(kwargs.get("game_id", ""), since=kwargs.get("since", 0)),
            "post_message": lambda: self.post_message(
                kwargs.get("game_id", ""), kwargs.get("handle", ""), kwargs.get("text", "")),
            "payment_requirements": lambda: self.payment_requirements(kwargs.get("game_id", "")),
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
                "module": "openplay",
                "description": self.description,
                "actions": list(actions.keys()),
                "status": self.status(),
            }
        return actions[action]()
