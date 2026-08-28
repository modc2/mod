"""
OpenBnB — an open short-stay marketplace you can reprogram while it runs.

Hosts list a place, guests book nights. Nothing about *how* the market behaves
is baked into this file: the module owner edits it at runtime.

Three layers of owner control, all live, no redeploy:

  1. POLICY   — a typed key/value document (currency, platform fee, night caps,
                cancellation windows, who may list, …). Every code path reads
                policy, never a constant. ``policy()`` / ``set_policy()``.
  2. RULES    — an ordered list of owner-authored ``when → then`` rules run on
                every quote/booking. ``when`` is a small, sandboxed expression
                over booking facts (no exec, no imports, whitelisted AST); the
                effects deny a booking, force host review, bend the price, or
                raise the night minimum. ``add_rule()`` / ``test_rule()``.
  3. HOOKS    — outbound webhooks per event, so the owner can wire OpenBnB into
                anything else. ``add_hook()``.

Everything the owner can do from the console is a plain function here, so the
same surface is available from Python, the CLI (``m openbnb/<fn>``) and HTTP.

Identity:  guests and hosts are handles. Each listing returns a one-time
           ``host_key`` to its creator, gating edits/approvals for that listing.
Owner:     one off-tree secret (``$OPENBNB_OWNER_KEY`` or
           ``~/.mod/openbnb/owner.json``, chmod 600) is god-mode. Never in
           committed config.
Storage:   ~/.mod/openbnb/{listings,bookings,policy,rules,hooks}.json
"""

import ast
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m


KINDS = {
    "entire_place": {"label": "Entire place", "emoji": "\U0001f3e0"},
    "private_room": {"label": "Private room", "emoji": "\U0001f6cf"},
    "shared_room": {"label": "Shared room", "emoji": "\U0001f465"},
    "hotel_room": {"label": "Hotel room", "emoji": "\U0001f3e8"},
}

# Suggested amenity vocabulary. The owner can replace this wholesale via
# policy.amenities — it is a suggestion list, not a validator.
AMENITIES = [
    "wifi", "kitchen", "washer", "dryer", "ac", "heating", "workspace", "tv",
    "parking", "elevator", "pool", "hot_tub", "gym", "pets_ok", "smoking_ok",
    "self_checkin", "balcony", "bbq", "crib", "step_free",
]

# ── The policy document ───────────────────────────────────────────────────
# Every knob the market runs on. Values here are the defaults a fresh install
# starts from; the live document lives in ~/.mod/openbnb/policy.json and is
# owner-editable at runtime. Adding a key here (with a schema entry below)
# makes it settable immediately — no other code changes.
DEFAULT_POLICY: Dict[str, Any] = {
    "market_name": "OpenBnB",
    "tagline": "Stay anywhere. Rules by the house.",
    "currency": "USDC",
    "chain": "base",
    "fee_bps": 0,                 # platform cut in basis points (0 = free market)
    "fee_wallet": "",             # where the cut is paid; required if fee_bps > 0
    "open_listings": True,        # False = only the owner can create listings
    "listing_cap_per_host": 0,    # 0 = unlimited
    "require_listing_review": False,   # new listings start "pending" not "live"
    "instant_book_default": True,
    "min_nights": 1,
    "max_nights": 90,
    "booking_horizon_days": 365,  # how far ahead a stay may start
    "max_guests": 16,
    "cities": [],                 # allowlist; empty = anywhere
    "kinds": list(KINDS),
    "amenities": AMENITIES,
    "cleaning_fee_cap": 0,        # 0 = uncapped
    "cancellation": {"full_refund_days": 7, "partial_refund_days": 2, "partial_pct": 50},
    "blocked_words": [],          # rejected in listing titles/notes
    "guest_review_required": False,   # force host approval on every booking
}

# name → (type, human note). set_policy() validates against this, so a typo
# fails loudly instead of silently creating a key nothing reads.
POLICY_SCHEMA: Dict[str, tuple] = {
    "market_name": ("str", "Name shown in the app header"),
    "tagline": ("str", "Subtitle shown in the app header"),
    "currency": ("str", "Settlement token symbol (must exist in config payments)"),
    "chain": ("str", "Settlement chain"),
    "fee_bps": ("int", "Platform cut in basis points, 0–2000"),
    "fee_wallet": ("str", "Address the platform cut is paid to"),
    "open_listings": ("bool", "Anyone may create a listing"),
    "listing_cap_per_host": ("int", "Max live listings per host, 0 = unlimited"),
    "require_listing_review": ("bool", "New listings start pending owner review"),
    "instant_book_default": ("bool", "Default instant-book flag for new listings"),
    "min_nights": ("int", "Floor on nights per stay"),
    "max_nights": ("int", "Ceiling on nights per stay"),
    "booking_horizon_days": ("int", "How far ahead a stay may start"),
    "max_guests": ("int", "Ceiling on guests per stay"),
    "cities": ("list", "Allowed cities, empty = anywhere"),
    "kinds": ("list", "Allowed listing kinds"),
    "amenities": ("list", "Amenity vocabulary offered in the UI"),
    "cleaning_fee_cap": ("num", "Max cleaning fee, 0 = uncapped"),
    "cancellation": ("dict", "full_refund_days / partial_refund_days / partial_pct"),
    "blocked_words": ("list", "Words rejected in listing text"),
    "guest_review_required": ("bool", "Force host approval on every booking"),
}

# Facts a rule's `when` expression may reference. Documented to the owner by
# fact_keys() and validated at rule-write time so a bad name can't land.
FACT_KEYS: Dict[str, str] = {
    "nights": "Number of nights in the stay",
    "guests": "Number of guests",
    "lead_days": "Days between now and check-in",
    "checkin_dow": "Check-in weekday, 0=Mon … 6=Sun",
    "checkout_dow": "Check-out weekday, 0=Mon … 6=Sun",
    "checkin_month": "Check-in month, 1–12",
    "weekend": "True if the stay covers a Fri or Sat night",
    "price": "Listing nightly price",
    "cleaning_fee": "Listing cleaning fee",
    "subtotal": "nights * price (before rules)",
    "city": "Listing city (lowercased)",
    "kind": "Listing kind, e.g. 'entire_place'",
    "amenities": "List of listing amenity keys",
    "instant_book": "Listing's instant-book flag",
    "listing_id": "Listing id",
    "host": "Host handle (lowercased)",
    "guest": "Guest handle (lowercased)",
    "guest_stays": "Confirmed stays this guest already has here",
    "listing_stays": "Confirmed stays this listing already has",
    "repeat_guest": "True if guest_stays > 0",
}

# Whitelisted AST for rule expressions. Anything outside this set is refused at
# write time — no attribute access, no calls except the helpers below, no names
# except the facts. This is the entire sandbox.
_RULE_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
    ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Mod, ast.Pow, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.In, ast.NotIn, ast.Name, ast.Load, ast.Constant, ast.List,
    ast.Tuple, ast.Set, ast.Call, ast.IfExp,
)
_RULE_FUNCS = {
    "len": len, "min": min, "max": max, "abs": abs, "round": round,
    "int": int, "float": float, "bool": bool, "any": any, "all": all,
}

RULE_EFFECTS = {
    "deny": "Refuse the booking (value = message shown to the guest)",
    "review": "Force host approval even if the listing is instant-book",
    "pct": "Adjust the stay subtotal by this percent (-10 = 10% off)",
    "flat": "Add this flat amount to the total (negative = discount)",
    "min_nights": "Raise the minimum nights for this booking",
    "tag": "Attach a label to the quote/booking",
}

HOOK_EVENTS = [
    "listing.created", "listing.updated", "listing.removed",
    "booking.quoted", "booking.created", "booking.confirmed",
    "booking.declined", "booking.cancelled",
    "policy.updated", "rule.updated",
]


class Mod:
    description = (
        "Open short-stay marketplace — hosts list places, guests book nights, and "
        "the owner programs policy, pricing/eligibility rules and webhooks at runtime."
    )

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser("~/.mod/openbnb"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.listings_path = self.store_dir / "listings.json"
        self.bookings_path = self.store_dir / "bookings.json"
        self.policy_path = self.store_dir / "policy.json"
        self.rules_path = self.store_dir / "rules.json"
        self.hooks_path = self.store_dir / "hooks.json"
        self.deliveries_path = self.store_dir / "deliveries.json"
        self.owner_path = self.store_dir / "owner.json"

        self.port = int(self.config.get("port", 50370))
        self.app_port = int(self.config.get("app_port", 50371))
        self.payments = self.config.get("payments", {})

    def _load_config(self):
        p = Path(__file__).parent / "config.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _load_json(self, path, default):
        p = Path(path)
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return default
        return default

    def _save_json(self, path, data):
        tmp = Path(path).with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    def _listings(self) -> Dict[str, Any]:
        return self._load_json(self.listings_path, {})

    def _bookings(self) -> Dict[str, Any]:
        return self._load_json(self.bookings_path, {})

    # ━━ Health ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def health(self):
        return {"status": "ok", "module": "openbnb",
                "listings": len(self._listings()), "bookings": len(self._bookings())}

    def status(self):
        pol = self.policy()
        listings = self._listings()
        bookings = self._bookings()
        by_status: Dict[str, int] = {}
        nights = 0
        volume = 0.0
        for b in bookings.values():
            by_status[b["status"]] = by_status.get(b["status"], 0) + 1
            if b["status"] == "confirmed":
                nights += int(b.get("nights", 0))
                volume += float(b.get("quote", {}).get("total", 0))
        return {
            "market_name": pol["market_name"],
            "tagline": pol["tagline"],
            "currency": pol["currency"],
            "chain": pol["chain"],
            "fee_bps": pol["fee_bps"],
            "listings": len(listings),
            "live_listings": sum(1 for l in listings.values() if l["status"] == "live"),
            "cities": sorted({l["city"] for l in listings.values() if l["status"] == "live"}),
            "bookings": len(bookings),
            "bookings_by_status": by_status,
            "nights_booked": nights,
            "volume": round(volume, 2),
            "rules": len(self.rules()),
            "active_rules": sum(1 for r in self.rules() if r.get("enabled", True)),
            "hooks": len(self._load_json(self.hooks_path, [])),
        }

    def kinds(self):
        allowed = set(self.policy()["kinds"])
        return [{"key": k, **v, "allowed": k in allowed} for k, v in KINDS.items()]

    def amenities(self):
        return self.policy()["amenities"]

    def cities(self):
        """Cities with live listings, plus the owner's allowlist if set."""
        counts: Dict[str, int] = {}
        for l in self._listings().values():
            if l["status"] == "live":
                counts[l["city"]] = counts.get(l["city"], 0) + 1
        allow = [c.lower() for c in self.policy()["cities"]]
        keys = sorted(set(counts) | set(allow))
        return [{"key": k, "label": k.title(), "listings": counts.get(k, 0),
                 "allowed": (not allow) or k in allow} for k in keys]

    # ━━ Owner (sudo) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # One secret, off-tree. $OPENBNB_OWNER_KEY wins so a deployment can inject
    # it; otherwise it is minted once into ~/.mod/openbnb/owner.json (0600).
    def _owner_secret(self) -> str:
        env = os.environ.get("OPENBNB_OWNER_KEY")
        if env:
            return env
        data = self._load_json(self.owner_path, {})
        if not data.get("secret"):
            data = {"secret": secrets.token_urlsafe(24), "created": int(time.time())}
            self._save_json(self.owner_path, data)
            try:
                os.chmod(self.owner_path, 0o600)
            except OSError:
                pass
        return data["secret"]

    def _is_owner(self, key) -> bool:
        import hmac
        return bool(key) and hmac.compare_digest(str(key), self._owner_secret())

    def _owner_guard(self, key):
        if not self._is_owner(key):
            return {"error": "owner key required"}
        return None

    def verify_owner(self, owner_key: str) -> Dict[str, Any]:
        """Used by the console to unlock owner mode."""
        return {"owner": self._is_owner(owner_key)}

    def owner_state(self, owner_key: str) -> Dict[str, Any]:
        """Everything the owner console needs, in one call."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        return {
            "status": self.status(),
            "policy": self.policy(),
            "policy_schema": self.policy_schema(),
            "rules": self.rules(),
            "facts": self.fact_keys(),
            "effects": RULE_EFFECTS,
            "hooks": self._load_json(self.hooks_path, []),
            "hook_events": HOOK_EVENTS,
            "deliveries": self._load_json(self.deliveries_path, [])[-25:],
            "listings": list(self._listings().values()),
            "bookings": sorted(self._bookings().values(),
                               key=lambda b: b["created_at"], reverse=True),
        }

    # ━━ Policy ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def policy(self) -> Dict[str, Any]:
        """The live policy document (defaults merged with owner overrides)."""
        saved = self._load_json(self.policy_path, {})
        pol = dict(DEFAULT_POLICY)
        pol.update({k: v for k, v in saved.items() if k in DEFAULT_POLICY})
        return pol

    def policy_schema(self) -> Dict[str, Any]:
        return {k: {"type": t, "note": note, "default": DEFAULT_POLICY[k]}
                for k, (t, note) in POLICY_SCHEMA.items()}

    @staticmethod
    def _coerce(key, value, typ):
        try:
            if typ == "int":
                return int(value), None
            if typ == "num":
                return float(value), None
            if typ == "bool":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on"), None
                return bool(value), None
            if typ == "list":
                if isinstance(value, str):
                    return [s.strip() for s in value.split(",") if s.strip()], None
                if isinstance(value, list):
                    return value, None
                return None, f"{key} must be a list"
            if typ == "dict":
                if isinstance(value, dict):
                    return value, None
                return None, f"{key} must be an object"
            return str(value), None
        except (TypeError, ValueError):
            return None, f"{key} must be a {typ}"

    def set_policy(self, owner_key: str, patch: dict = None, **kwargs) -> Dict[str, Any]:
        """Patch the policy document. Owner only. Unknown keys are refused so a
        typo can't create a knob nothing reads."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        patch = {**(patch or {}), **{k: v for k, v in kwargs.items() if k != "owner_key"}}
        if not patch:
            return {"error": "nothing to set"}
        unknown = [k for k in patch if k not in POLICY_SCHEMA]
        if unknown:
            return {"error": f"unknown policy keys: {', '.join(unknown)}",
                    "known": sorted(POLICY_SCHEMA)}
        saved = self._load_json(self.policy_path, {})
        changed = {}
        for k, raw in patch.items():
            val, cerr = self._coerce(k, raw, POLICY_SCHEMA[k][0])
            if cerr:
                return {"error": cerr}
            if k == "fee_bps" and not (0 <= val <= 2000):
                return {"error": "fee_bps must be between 0 and 2000 (max 20%)"}
            if k == "kinds":
                bad = [x for x in val if x not in KINDS]
                if bad:
                    return {"error": f"unknown kinds: {', '.join(bad)}", "known": list(KINDS)}
            if k == "cities":
                val = [str(c).strip().lower() for c in val if str(c).strip()]
            saved[k] = val
            changed[k] = val
        pol_after = {**DEFAULT_POLICY, **saved}
        if pol_after["fee_bps"] > 0 and not pol_after["fee_wallet"]:
            return {"error": "set fee_wallet before charging a platform fee"}
        if pol_after["min_nights"] > pol_after["max_nights"]:
            return {"error": "min_nights cannot exceed max_nights"}
        self._save_json(self.policy_path, saved)
        self._fire("policy.updated", {"changed": changed})
        return {"ok": True, "changed": changed, "policy": self.policy()}

    def reset_policy(self, owner_key: str, key: str = None) -> Dict[str, Any]:
        """Drop one override (or all of them) back to the shipped default."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        saved = self._load_json(self.policy_path, {})
        if key:
            if key not in POLICY_SCHEMA:
                return {"error": f"unknown policy key '{key}'"}
            saved.pop(key, None)
        else:
            saved = {}
        self._save_json(self.policy_path, saved)
        self._fire("policy.updated", {"reset": key or "all"})
        return {"ok": True, "reset": key or "all", "policy": self.policy()}

    # ━━ Rules ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def rules(self) -> List[Dict[str, Any]]:
        """The owner's rule list, in evaluation order."""
        return self._load_json(self.rules_path, [])

    def fact_keys(self) -> Dict[str, str]:
        """The facts a rule's `when` expression may reference."""
        return dict(FACT_KEYS)

    def sandbox(self) -> Dict[str, Any]:
        """Everything you need to write a rule: facts, effects, events, and a
        sample fact set to test against. Public — the language is not a secret."""
        return {"facts": dict(FACT_KEYS), "effects": dict(RULE_EFFECTS),
                "events": list(HOOK_EVENTS), "functions": sorted(_RULE_FUNCS),
                "sample": self._sample_facts()}

    def _compile_rule(self, expr: str):
        """Parse + whitelist a rule expression. Returns (code, error)."""
        expr = (expr or "").strip()
        if not expr:
            return None, "when-expression required"
        if len(expr) > 500:
            return None, "expression too long (500 chars max)"
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            return None, f"syntax error: {e.msg}"
        for node in ast.walk(tree):
            if not isinstance(node, _RULE_NODES):
                return None, f"{type(node).__name__} is not allowed in a rule"
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _RULE_FUNCS:
                    return None, f"only these calls are allowed: {', '.join(sorted(_RULE_FUNCS))}"
            if isinstance(node, ast.Name) and node.id not in FACT_KEYS and node.id not in _RULE_FUNCS:
                return None, (f"unknown fact '{node.id}' — available: "
                              f"{', '.join(sorted(FACT_KEYS))}")
        return compile(tree, "<rule>", "eval"), None

    @staticmethod
    def _normalize_then(then) -> Any:
        if not isinstance(then, dict) or not then:
            return {"error": 'then must be an object, e.g. {"pct": -10}'}
        unknown = [k for k in then if k not in RULE_EFFECTS]
        if unknown:
            return {"error": f"unknown effects: {', '.join(unknown)}",
                    "known": sorted(RULE_EFFECTS)}
        out: Dict[str, Any] = {}
        for k, v in then.items():
            if k == "deny":
                out["deny"] = "Booking not allowed" if isinstance(v, bool) else str(v)
            elif k == "review":
                out["review"] = bool(v)
            elif k in ("pct", "flat"):
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    return {"error": f"{k} must be a number"}
            elif k == "min_nights":
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    return {"error": "min_nights must be an integer"}
            elif k == "tag":
                out["tag"] = str(v)[:32]
        return out

    def add_rule(self, owner_key: str, name: str, when: str, then: dict,
                 enabled: bool = True) -> Dict[str, Any]:
        """Add a rule. `when` is a sandboxed expression over fact_keys(); `then`
        is any combination of RULE_EFFECTS. Rules run in list order."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        _, cerr = self._compile_rule(when)
        if cerr:
            return {"error": cerr}
        effects = self._normalize_then(then)
        if isinstance(effects, dict) and effects.get("error"):
            return effects
        rules = self.rules()
        rule = {"id": "r_" + secrets.token_hex(3), "name": (name or "rule").strip()[:60],
                "when": when.strip(), "then": effects, "enabled": bool(enabled),
                "created_at": int(time.time()), "hits": 0}
        rules.append(rule)
        self._save_json(self.rules_path, rules)
        self._fire("rule.updated", {"added": rule["id"]})
        return {"ok": True, "rule": rule, "rules": rules}

    def update_rule(self, owner_key: str, rule_id: str, name: str = None,
                    when: str = None, then: dict = None,
                    enabled: bool = None) -> Dict[str, Any]:
        err = self._owner_guard(owner_key)
        if err:
            return err
        rules = self.rules()
        rule = next((r for r in rules if r["id"] == rule_id), None)
        if not rule:
            return {"error": "rule not found"}
        if when is not None:
            _, cerr = self._compile_rule(when)
            if cerr:
                return {"error": cerr}
            rule["when"] = when.strip()
        if then is not None:
            effects = self._normalize_then(then)
            if isinstance(effects, dict) and effects.get("error"):
                return effects
            rule["then"] = effects
        if name is not None:
            rule["name"] = str(name).strip()[:60]
        if enabled is not None:
            rule["enabled"] = bool(enabled)
        self._save_json(self.rules_path, rules)
        self._fire("rule.updated", {"updated": rule_id})
        return {"ok": True, "rule": rule, "rules": rules}

    def delete_rule(self, owner_key: str, rule_id: str) -> Dict[str, Any]:
        err = self._owner_guard(owner_key)
        if err:
            return err
        rules = self.rules()
        kept = [r for r in rules if r["id"] != rule_id]
        if len(kept) == len(rules):
            return {"error": "rule not found"}
        self._save_json(self.rules_path, kept)
        self._fire("rule.updated", {"deleted": rule_id})
        return {"ok": True, "deleted": rule_id, "rules": kept}

    def move_rule(self, owner_key: str, rule_id: str, direction: str = "up") -> Dict[str, Any]:
        """Reorder — order is precedence: later rules stack on earlier ones."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        rules = self.rules()
        idx = next((i for i, r in enumerate(rules) if r["id"] == rule_id), None)
        if idx is None:
            return {"error": "rule not found"}
        swap = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap < len(rules):
            rules[idx], rules[swap] = rules[swap], rules[idx]
            self._save_json(self.rules_path, rules)
        return {"ok": True, "rules": rules}

    def test_rule(self, owner_key: str, when: str, facts: dict = None) -> Dict[str, Any]:
        """Dry-run an expression against facts before saving it. Missing facts
        fall back to the sample values, so the owner can test in one call."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        code, cerr = self._compile_rule(when)
        if cerr:
            return {"error": cerr}
        sample = self._sample_facts()
        sample.update(facts or {})
        try:
            result = eval(code, {"__builtins__": {}}, {**_RULE_FUNCS, **sample})
        except Exception as e:
            return {"error": f"evaluation failed: {e}", "facts": sample}
        return {"ok": True, "matches": bool(result), "value": result, "facts": sample}

    @staticmethod
    def _sample_facts() -> Dict[str, Any]:
        return {"nights": 3, "guests": 2, "lead_days": 14, "checkin_dow": 4,
                "checkout_dow": 0, "checkin_month": 7, "weekend": True,
                "price": 120.0, "cleaning_fee": 40.0, "subtotal": 360.0,
                "city": "toronto", "kind": "entire_place", "amenities": ["wifi", "kitchen"],
                "instant_book": True, "listing_id": "sample", "host": "sample_host",
                "guest": "sample_guest", "guest_stays": 0, "listing_stays": 0,
                "repeat_guest": False}

    def _apply_rules(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """Run the owner's rules over a set of facts. Returns the collected
        effects plus a per-rule trace, so every price line is explainable."""
        out = {"deny": None, "review": False, "pct": 0.0, "flat": 0.0,
               "min_nights": 0, "tags": [], "trace": [], "hits": []}
        for rule in self.rules():
            if not rule.get("enabled", True):
                continue
            code, cerr = self._compile_rule(rule.get("when", ""))
            if cerr:
                out["trace"].append({"rule": rule["name"], "id": rule["id"],
                                     "error": cerr, "matched": False})
                continue
            try:
                matched = bool(eval(code, {"__builtins__": {}}, {**_RULE_FUNCS, **facts}))
            except Exception as e:
                out["trace"].append({"rule": rule["name"], "id": rule["id"],
                                     "error": str(e), "matched": False})
                continue
            out["trace"].append({"rule": rule["name"], "id": rule["id"],
                                 "when": rule["when"], "matched": matched,
                                 "then": rule["then"] if matched else None})
            if not matched:
                continue
            out["hits"].append(rule["id"])
            then = rule["then"]
            if then.get("deny") and not out["deny"]:
                out["deny"] = then["deny"]
            if then.get("review"):
                out["review"] = True
            out["pct"] += float(then.get("pct", 0) or 0)
            out["flat"] += float(then.get("flat", 0) or 0)
            out["min_nights"] = max(out["min_nights"], int(then.get("min_nights", 0) or 0))
            if then.get("tag"):
                out["tags"].append(then["tag"])
        return out

    def _bump_hits(self, rule_ids: List[str]):
        if not rule_ids:
            return
        rules = self.rules()
        for r in rules:
            if r["id"] in rule_ids:
                r["hits"] = int(r.get("hits", 0)) + 1
        self._save_json(self.rules_path, rules)

    # ━━ Webhooks ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def hooks(self, owner_key: str) -> Any:
        err = self._owner_guard(owner_key)
        if err:
            return err
        return {"hooks": self._load_json(self.hooks_path, []), "events": HOOK_EVENTS}

    def add_hook(self, owner_key: str, url: str, events: list = None,
                 secret: str = "") -> Dict[str, Any]:
        """POST every matching event to `url`. events=None (or ["*"]) = all."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"error": "url must be http(s)"}
        events = [e for e in (events or ["*"]) if e == "*" or e in HOOK_EVENTS]
        if not events:
            return {"error": f"no valid events — known: {', '.join(HOOK_EVENTS)}"}
        hooks = self._load_json(self.hooks_path, [])
        hook = {"id": "h_" + secrets.token_hex(3), "url": url, "events": events,
                "secret": secret or "", "created_at": int(time.time())}
        hooks.append(hook)
        self._save_json(self.hooks_path, hooks)
        return {"ok": True, "hook": hook}

    def delete_hook(self, owner_key: str, hook_id: str) -> Dict[str, Any]:
        err = self._owner_guard(owner_key)
        if err:
            return err
        hooks = self._load_json(self.hooks_path, [])
        kept = [h for h in hooks if h["id"] != hook_id]
        if len(kept) == len(hooks):
            return {"error": "hook not found"}
        self._save_json(self.hooks_path, kept)
        return {"ok": True, "deleted": hook_id}

    def hook_deliveries(self, owner_key: str, limit: int = 25) -> Any:
        err = self._owner_guard(owner_key)
        if err:
            return err
        return {"deliveries": self._load_json(self.deliveries_path, [])[-int(limit):]}

    def _fire(self, event: str, payload: dict):
        """Best-effort webhook fan-out. Never blocks or breaks the caller."""
        hooks = [h for h in self._load_json(self.hooks_path, [])
                 if "*" in h["events"] or event in h["events"]]
        if not hooks:
            return
        import threading
        import urllib.request

        def deliver():
            log = self._load_json(self.deliveries_path, [])
            for h in hooks:
                body = json.dumps({"event": event, "ts": int(time.time()),
                                   "data": payload}, default=str).encode()
                headers = {"Content-Type": "application/json"}
                if h.get("secret"):
                    headers["X-OpenBnB-Secret"] = h["secret"]
                entry = {"hook": h["id"], "event": event, "ts": int(time.time())}
                try:
                    req = urllib.request.Request(h["url"], data=body, headers=headers)
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        entry["status"] = resp.status
                except Exception as e:
                    entry["status"] = 0
                    entry["error"] = str(e)[:200]
                log.append(entry)
            self._save_json(self.deliveries_path, log[-200:])

        threading.Thread(target=deliver, daemon=True).start()

    # ━━ Listings ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    @staticmethod
    def _public_listing(l: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in l.items() if k != "host_key"}

    def listings(self, city: str = None, kind: str = None, guests: int = 0,
                 host: str = None, include_all: bool = False) -> List[Dict[str, Any]]:
        """Live listings, newest first. include_all also returns paused/pending
        ones (used by the host's own view)."""
        out = []
        for l in self._listings().values():
            if not include_all and l["status"] != "live":
                continue
            if city and l["city"] != city.lower():
                continue
            if kind and l["kind"] != kind:
                continue
            if guests and int(l["guests"]) < int(guests):
                continue
            if host and l["host"] != host.strip().lower():
                continue
            out.append(self._public_listing(l))
        out.sort(key=lambda l: l["created_at"], reverse=True)
        return out

    def listing(self, listing_id: str) -> Dict[str, Any]:
        l = self._listings().get(listing_id)
        if not l:
            return {"error": "listing not found"}
        detail = self._public_listing(l)
        detail["booked"] = self._booked_nights(listing_id)
        detail["stays"] = sum(1 for b in self._bookings().values()
                              if b["listing_id"] == listing_id and b["status"] == "confirmed")
        return detail

    def create_listing(self, host: str, title: str, city: str, price: float,
                       kind: str = "entire_place", guests: int = 2, bedrooms: int = 1,
                       beds: int = 1, baths: float = 1, amenities: list = None,
                       notes: str = "", address: str = "", lat: float = None,
                       lng: float = None, photos: list = None, cleaning_fee: float = 0,
                       min_nights: int = 0, max_nights: int = 0,
                       instant_book: bool = None, host_wallet: str = "",
                       owner_key: str = "") -> Dict[str, Any]:
        """List a place. Returns a one-time `host_key` — the creator's key for
        editing this listing and approving its bookings."""
        pol = self.policy()
        if not pol["open_listings"] and not self._is_owner(owner_key):
            return {"error": "listings are closed — only the owner can list right now"}
        host = (host or "").strip().lower()
        title = (title or "").strip()
        city = (city or "").strip().lower()
        if not host:
            return {"error": "host handle required"}
        if not title:
            return {"error": "title required"}
        if not city:
            return {"error": "city required"}
        if pol["cities"] and city not in [c.lower() for c in pol["cities"]]:
            return {"error": f"'{city}' is not an open city here",
                    "open_cities": pol["cities"]}
        if kind not in pol["kinds"]:
            return {"error": f"kind must be one of: {', '.join(pol['kinds'])}"}
        try:
            price = float(price)
        except (TypeError, ValueError):
            return {"error": "price must be a number"}
        if price <= 0:
            return {"error": "price per night must be > 0"}
        try:
            cleaning_fee = float(cleaning_fee or 0)
        except (TypeError, ValueError):
            return {"error": "cleaning_fee must be a number"}
        if pol["cleaning_fee_cap"] and cleaning_fee > pol["cleaning_fee_cap"]:
            return {"error": f"cleaning fee is capped at {pol['cleaning_fee_cap']} here"}
        if int(guests) < 1 or int(guests) > pol["max_guests"]:
            return {"error": f"guests must be between 1 and {pol['max_guests']}"}
        blocked = [w for w in pol["blocked_words"]
                   if w and w.lower() in f"{title} {notes}".lower()]
        if blocked:
            return {"error": f"listing text contains blocked words: {', '.join(blocked)}"}
        if pol["listing_cap_per_host"]:
            mine = sum(1 for l in self._listings().values()
                       if l["host"] == host and l["status"] in ("live", "pending"))
            if mine >= pol["listing_cap_per_host"]:
                return {"error": f"you have reached the {pol['listing_cap_per_host']}-listing cap"}

        listings = self._listings()
        lid = "l_" + secrets.token_hex(4)
        host_key = secrets.token_urlsafe(16)
        known = set(pol["amenities"])
        listings[lid] = {
            "id": lid,
            "host": host,
            "host_wallet": (host_wallet or "").strip(),
            "host_key": host_key,
            "title": title[:120],
            "kind": kind,
            "city": city,
            "address": (address or "").strip()[:200],
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "guests": int(guests),
            "bedrooms": int(bedrooms),
            "beds": int(beds),
            "baths": float(baths),
            "amenities": [a for a in (amenities or []) if a in known],
            "photos": [p for p in (photos or []) if str(p).startswith("http")][:8],
            "notes": (notes or "").strip()[:2000],
            "price": price,
            "cleaning_fee": cleaning_fee,
            "min_nights": int(min_nights or 0),
            "max_nights": int(max_nights or 0),
            "instant_book": pol["instant_book_default"] if instant_book is None else bool(instant_book),
            "blocked": [],
            "status": "pending" if pol["require_listing_review"] else "live",
            "created_at": int(time.time()),
        }
        self._save_json(self.listings_path, listings)
        self._fire("listing.created", {"listing_id": lid, "host": host, "city": city})
        detail = self.listing(lid)
        detail["host_key"] = host_key   # shown once, to the creator
        return detail

    def _auth_listing(self, listing, key) -> Optional[Dict[str, str]]:
        if key and (key == listing.get("host_key") or self._is_owner(key)):
            return None
        return {"error": "host_key required (only this listing's host can do that)"}

    def edit_listing(self, listing_id: str, host_key: str, **fields) -> Dict[str, Any]:
        listings = self._listings()
        l = listings.get(listing_id)
        if not l:
            return {"error": "listing not found"}
        err = self._auth_listing(l, host_key)
        if err:
            return err
        pol = self.policy()
        editable = {"title", "notes", "address", "lat", "lng", "guests", "bedrooms",
                    "beds", "baths", "price", "cleaning_fee", "min_nights",
                    "max_nights", "instant_book", "amenities", "photos", "host_wallet"}
        for k, v in fields.items():
            if k not in editable:
                continue
            if k == "price":
                v = float(v)
                if v <= 0:
                    return {"error": "price per night must be > 0"}
            if k == "cleaning_fee":
                v = float(v)
                if pol["cleaning_fee_cap"] and v > pol["cleaning_fee_cap"]:
                    return {"error": f"cleaning fee is capped at {pol['cleaning_fee_cap']} here"}
            if k == "guests" and not (1 <= int(v) <= pol["max_guests"]):
                return {"error": f"guests must be between 1 and {pol['max_guests']}"}
            if k == "amenities":
                v = [a for a in v if a in set(pol["amenities"])]
            l[k] = v
        self._save_json(self.listings_path, listings)
        self._fire("listing.updated", {"listing_id": listing_id})
        return self.listing(listing_id)

    def set_status(self, listing_id: str, host_key: str, status: str) -> Dict[str, Any]:
        """live | paused | removed | pending. Owner review moves pending → live."""
        if status not in ("live", "paused", "removed", "pending"):
            return {"error": "status must be live|paused|removed|pending"}
        listings = self._listings()
        l = listings.get(listing_id)
        if not l:
            return {"error": "listing not found"}
        err = self._auth_listing(l, host_key)
        if err:
            return err
        if l["status"] == "pending" and status == "live" and not self._is_owner(host_key):
            return {"error": "this listing is awaiting owner review"}
        l["status"] = status
        self._save_json(self.listings_path, listings)
        self._fire("listing.removed" if status == "removed" else "listing.updated",
                   {"listing_id": listing_id, "status": status})
        return self.listing(listing_id)

    # ━━ Calendar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    @staticmethod
    def _dates(checkin: str, checkout: str) -> List[str]:
        """Nights in a stay — check-out day is not a night."""
        d0 = date.fromisoformat(checkin)
        d1 = date.fromisoformat(checkout)
        return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days)]

    def _booked_nights(self, listing_id: str) -> List[str]:
        """Nights taken by a live booking, plus host-blocked nights."""
        taken: List[str] = list(self._listings().get(listing_id, {}).get("blocked", []))
        for b in self._bookings().values():
            if b["listing_id"] != listing_id:
                continue
            if b["status"] not in ("confirmed", "pending"):
                continue
            taken.extend(self._dates(b["checkin"], b["checkout"]))
        return sorted(set(taken))

    def calendar(self, listing_id: str) -> Dict[str, Any]:
        l = self._listings().get(listing_id)
        if not l:
            return {"error": "listing not found"}
        return {"listing_id": listing_id, "booked": self._booked_nights(listing_id),
                "blocked": l.get("blocked", [])}

    def block_dates(self, listing_id: str, host_key: str, dates: list,
                    unblock: bool = False) -> Dict[str, Any]:
        """Host closes (or reopens) nights on their own calendar."""
        listings = self._listings()
        l = listings.get(listing_id)
        if not l:
            return {"error": "listing not found"}
        err = self._auth_listing(l, host_key)
        if err:
            return err
        try:
            nights = {date.fromisoformat(str(d)).isoformat() for d in (dates or [])}
        except ValueError:
            return {"error": "dates must be YYYY-MM-DD"}
        current = set(l.get("blocked", []))
        l["blocked"] = sorted(current - nights if unblock else current | nights)
        self._save_json(self.listings_path, listings)
        return {"ok": True, "blocked": l["blocked"]}

    # ━━ Quote — where policy + rules meet ━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _facts(self, listing: Dict[str, Any], checkin: str, checkout: str,
               guests: int, guest: str) -> Dict[str, Any]:
        nights = self._dates(checkin, checkout)
        d0 = date.fromisoformat(checkin)
        d1 = date.fromisoformat(checkout)
        today = datetime.now(timezone.utc).date()
        bookings = list(self._bookings().values())
        guest_l = (guest or "").strip().lower()
        return {
            "nights": len(nights),
            "guests": int(guests),
            "lead_days": (d0 - today).days,
            "checkin_dow": d0.weekday(),
            "checkout_dow": d1.weekday(),
            "checkin_month": d0.month,
            "weekend": any(date.fromisoformat(n).weekday() in (4, 5) for n in nights),
            "price": float(listing["price"]),
            "cleaning_fee": float(listing["cleaning_fee"]),
            "subtotal": round(len(nights) * float(listing["price"]), 2),
            "city": listing["city"],
            "kind": listing["kind"],
            "amenities": list(listing["amenities"]),
            "instant_book": bool(listing["instant_book"]),
            "listing_id": listing["id"],
            "host": listing["host"],
            "guest": guest_l,
            "guest_stays": sum(1 for b in bookings
                               if b["guest"] == guest_l and b["status"] == "confirmed"),
            "listing_stays": sum(1 for b in bookings
                                 if b["listing_id"] == listing["id"] and b["status"] == "confirmed"),
            "repeat_guest": any(b["guest"] == guest_l and b["status"] == "confirmed"
                                for b in bookings),
        }

    def quote(self, listing_id: str, checkin: str, checkout: str, guests: int = 1,
              guest: str = "", explain: bool = False) -> Dict[str, Any]:
        """Price a stay: policy checks, availability, then the owner's rules.
        Public — the app quotes before anyone commits. explain=True returns the
        full rule trace, which is what the owner console shows."""
        listing = self._listings().get(listing_id)
        if not listing:
            return {"error": "listing not found"}
        if listing["status"] != "live":
            return {"error": f"this listing is {listing['status']}"}
        pol = self.policy()
        try:
            nights = self._dates(checkin, checkout)
        except (ValueError, TypeError):
            return {"error": "checkin/checkout must be YYYY-MM-DD"}
        if not nights:
            return {"error": "check-out must be after check-in"}

        today = datetime.now(timezone.utc).date()
        d0 = date.fromisoformat(checkin)
        if d0 < today:
            return {"error": "check-in is in the past"}
        if (d0 - today).days > pol["booking_horizon_days"]:
            return {"error": f"stays can only be booked {pol['booking_horizon_days']} days ahead"}
        if int(guests) < 1 or int(guests) > int(listing["guests"]):
            return {"error": f"this place sleeps {listing['guests']}"}

        min_nights = max(pol["min_nights"], int(listing["min_nights"] or 0))
        max_nights = min(pol["max_nights"], int(listing["max_nights"] or 10 ** 6))
        if len(nights) < min_nights:
            return {"error": f"minimum stay is {min_nights} night(s)"}
        if len(nights) > max_nights:
            return {"error": f"maximum stay is {max_nights} night(s)"}

        clash = sorted(set(self._booked_nights(listing_id)) & set(nights))
        if clash:
            return {"error": "those nights are not available", "unavailable": clash}

        facts = self._facts(listing, checkin, checkout, guests, guest)
        eff = self._apply_rules(facts)
        if eff["min_nights"] and len(nights) < eff["min_nights"]:
            return {"error": f"a house rule sets a {eff['min_nights']}-night minimum for this stay",
                    "rule_min_nights": eff["min_nights"],
                    "trace": eff["trace"] if explain else None}
        if eff["deny"]:
            return {"error": eff["deny"], "denied_by_rule": True,
                    "trace": eff["trace"] if explain else None}

        subtotal = round(len(nights) * float(listing["price"]), 2)
        rule_adj = round(subtotal * (eff["pct"] / 100.0) + eff["flat"], 2)
        stay_total = max(round(subtotal + rule_adj, 2), 0.0)
        cleaning = float(listing["cleaning_fee"])
        fee = round((stay_total + cleaning) * pol["fee_bps"] / 10000.0, 2)
        total = round(stay_total + cleaning + fee, 2)

        lines = [{"label": f"{listing['price']:g} × {len(nights)} night(s)", "amount": subtotal}]
        if rule_adj:
            lines.append({"label": "House rules", "amount": rule_adj,
                          "rules": [t["rule"] for t in eff["trace"] if t["matched"]]})
        if cleaning:
            lines.append({"label": "Cleaning fee", "amount": cleaning})
        if fee:
            lines.append({"label": f"Service fee ({pol['fee_bps'] / 100:g}%)", "amount": fee})

        needs_approval = (not listing["instant_book"]) or eff["review"] or pol["guest_review_required"]
        quote = {
            "listing_id": listing_id,
            "checkin": checkin, "checkout": checkout,
            "nights": len(nights), "guests": int(guests),
            "currency": pol["currency"], "chain": pol["chain"],
            "subtotal": subtotal, "rule_adjustment": rule_adj,
            "cleaning_fee": cleaning, "service_fee": fee, "total": total,
            "host_payout": round(stay_total + cleaning, 2),
            "lines": lines,
            "tags": eff["tags"],
            "instant": not needs_approval,
            "needs_approval": needs_approval,
            "matched_rules": eff["hits"],
        }
        if explain:
            quote["trace"] = eff["trace"]
            quote["facts"] = facts
        self._fire("booking.quoted", {"listing_id": listing_id, "total": total})
        return quote

    # ━━ Bookings ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def book(self, listing_id: str, guest: str, checkin: str, checkout: str,
             guests: int = 1, guest_wallet: str = "", note: str = "",
             payment: str = "") -> Dict[str, Any]:
        """Request a stay. Instant-book listings confirm immediately (unless a
        rule or policy forces review); otherwise it lands as pending."""
        guest = (guest or "").strip().lower()
        if not guest:
            return {"error": "guest handle required"}
        q = self.quote(listing_id, checkin, checkout, guests=guests, guest=guest)
        if q.get("error"):
            return q
        listing = self._listings()[listing_id]
        if guest == listing["host"]:
            return {"error": "you can't book your own place"}

        bookings = self._bookings()
        bid = "b_" + secrets.token_hex(4)
        confirmed = not q["needs_approval"]
        booking = {
            "id": bid,
            "listing_id": listing_id,
            "listing_title": listing["title"],
            "city": listing["city"],
            "host": listing["host"],
            "guest": guest,
            "guest_wallet": (guest_wallet or "").strip(),
            "checkin": checkin, "checkout": checkout,
            "nights": q["nights"], "guests": int(guests),
            "note": (note or "").strip()[:500],
            "quote": q,
            "payment": (payment or "").strip(),
            "status": "confirmed" if confirmed else "pending",
            "created_at": int(time.time()),
            "history": [{"at": int(time.time()),
                         "event": "confirmed" if confirmed else "requested"}],
        }
        bookings[bid] = booking
        self._save_json(self.bookings_path, bookings)
        self._bump_hits(q.get("matched_rules") or [])
        self._fire("booking.created", {"booking_id": bid, "status": booking["status"],
                                       "listing_id": listing_id, "total": q["total"]})
        if confirmed:
            self._fire("booking.confirmed", {"booking_id": bid, "total": q["total"]})
        return booking

    def booking(self, booking_id: str) -> Dict[str, Any]:
        return self._bookings().get(booking_id) or {"error": "booking not found"}

    def bookings(self, guest: str = "", host: str = "", listing_id: str = "",
                 status: str = "") -> List[Dict[str, Any]]:
        """Filter the ledger. No filter = everything (the owner console view)."""
        out = []
        for b in self._bookings().values():
            if guest and b["guest"] != guest.strip().lower():
                continue
            if host and b["host"] != host.strip().lower():
                continue
            if listing_id and b["listing_id"] != listing_id:
                continue
            if status and b["status"] != status:
                continue
            out.append(b)
        out.sort(key=lambda b: b["created_at"], reverse=True)
        return out

    def _auth_booking_host(self, booking, key):
        listing = self._listings().get(booking["listing_id"], {})
        if key and (key == listing.get("host_key") or self._is_owner(key)):
            return None
        return {"error": "host_key required (only the host can do that)"}

    def approve_booking(self, booking_id: str, host_key: str) -> Dict[str, Any]:
        bookings = self._bookings()
        b = bookings.get(booking_id)
        if not b:
            return {"error": "booking not found"}
        err = self._auth_booking_host(b, host_key)
        if err:
            return err
        if b["status"] != "pending":
            return {"error": f"booking is already {b['status']}"}
        b["status"] = "confirmed"
        b["history"].append({"at": int(time.time()), "event": "confirmed"})
        self._save_json(self.bookings_path, bookings)
        self._fire("booking.confirmed", {"booking_id": booking_id})
        return b

    def decline_booking(self, booking_id: str, host_key: str, reason: str = "") -> Dict[str, Any]:
        bookings = self._bookings()
        b = bookings.get(booking_id)
        if not b:
            return {"error": "booking not found"}
        err = self._auth_booking_host(b, host_key)
        if err:
            return err
        if b["status"] not in ("pending", "confirmed"):
            return {"error": f"booking is already {b['status']}"}
        b["status"] = "declined"
        b["history"].append({"at": int(time.time()), "event": "declined",
                             "reason": (reason or "")[:200]})
        self._save_json(self.bookings_path, bookings)
        self._fire("booking.declined", {"booking_id": booking_id, "reason": reason})
        return b

    def cancel_booking(self, booking_id: str, guest: str = "",
                       host_key: str = "") -> Dict[str, Any]:
        """Guest cancels by handle; host/owner by key. Refund follows the
        owner's cancellation policy."""
        bookings = self._bookings()
        b = bookings.get(booking_id)
        if not b:
            return {"error": "booking not found"}
        by_guest = bool(guest) and b["guest"] == guest.strip().lower()
        if not by_guest and self._auth_booking_host(b, host_key):
            return {"error": "cancel needs your guest handle or the host key"}
        if b["status"] in ("cancelled", "declined"):
            return {"error": f"booking is already {b['status']}"}
        pol = self.policy()["cancellation"]
        days_out = (date.fromisoformat(b["checkin"]) - datetime.now(timezone.utc).date()).days
        total = float(b["quote"]["total"])
        if days_out >= int(pol.get("full_refund_days", 0)):
            refund, tier = total, "full"
        elif days_out >= int(pol.get("partial_refund_days", 0)):
            refund, tier = round(total * float(pol.get("partial_pct", 0)) / 100.0, 2), "partial"
        else:
            refund, tier = 0.0, "none"
        b["status"] = "cancelled"
        b["refund"] = {"amount": refund, "tier": tier, "days_out": days_out}
        b["history"].append({"at": int(time.time()), "event": "cancelled",
                             "by": "guest" if by_guest else "host", "refund": refund})
        self._save_json(self.bookings_path, bookings)
        self._fire("booking.cancelled", {"booking_id": booking_id, "refund": refund})
        return b

    # ━━ Payments (x402 requirements for a booking) ━━━━━━━━━━━━━━━━━
    def payment_requirements(self, booking_id: str) -> Dict[str, Any]:
        b = self._bookings().get(booking_id)
        if not b:
            return {"error": "booking not found"}
        pol = self.policy()
        chain, currency = pol["chain"], pol["currency"]
        token = self.payments.get("tokens", {}).get(chain, {}).get(currency, {})
        decimals = int(token.get("decimals", 6))
        total = float(b["quote"]["total"])
        listing = self._listings().get(b["listing_id"], {})
        return {
            "booking_id": booking_id,
            "chain": chain, "currency": currency,
            "asset": token.get("address", ""),
            "decimals": decimals,
            "amount": total,
            "atomic_amount": str(int(round(total * (10 ** decimals)))),
            "host_payout": b["quote"]["host_payout"],
            "receiver": listing.get("host_wallet", ""),
            "service_fee": b["quote"]["service_fee"],
            "fee_receiver": pol["fee_wallet"],
            "facilitator": self.payments.get("facilitator", ""),
            "scheme": "x402",
        }

    # ━━ Owner overrides on live data ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def owner_delete_listing(self, owner_key: str, listing_id: str) -> Dict[str, Any]:
        err = self._owner_guard(owner_key)
        if err:
            return err
        listings = self._listings()
        if listing_id not in listings:
            return {"error": "listing not found"}
        listings.pop(listing_id)
        self._save_json(self.listings_path, listings)
        self._fire("listing.removed", {"listing_id": listing_id, "by": "owner"})
        return {"ok": True, "deleted": listing_id}

    def owner_delete_booking(self, owner_key: str, booking_id: str) -> Dict[str, Any]:
        err = self._owner_guard(owner_key)
        if err:
            return err
        bookings = self._bookings()
        if booking_id not in bookings:
            return {"error": "booking not found"}
        bookings.pop(booking_id)
        self._save_json(self.bookings_path, bookings)
        return {"ok": True, "deleted": booking_id}

    def owner_set_booking_status(self, owner_key: str, booking_id: str,
                                 status: str) -> Dict[str, Any]:
        """Force any booking into any state — the owner's override of last resort."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        if status not in ("pending", "confirmed", "declined", "cancelled"):
            return {"error": "status must be pending|confirmed|declined|cancelled"}
        bookings = self._bookings()
        b = bookings.get(booking_id)
        if not b:
            return {"error": "booking not found"}
        b["status"] = status
        b["history"].append({"at": int(time.time()), "event": status, "by": "owner"})
        self._save_json(self.bookings_path, bookings)
        return b

    def export_state(self, owner_key: str) -> Dict[str, Any]:
        """Full snapshot — policy, rules, hooks, listings, bookings."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        return {"version": 1, "exported_at": int(time.time()),
                "policy": self._load_json(self.policy_path, {}),
                "rules": self.rules(),
                "hooks": self._load_json(self.hooks_path, []),
                "listings": self._listings(),
                "bookings": self._bookings()}

    def import_state(self, owner_key: str, state: dict, merge: bool = False) -> Dict[str, Any]:
        """Restore a snapshot. merge=True keeps existing listings/bookings."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        if not isinstance(state, dict):
            return {"error": "state must be an object from export_state"}
        if "policy" in state:
            self._save_json(self.policy_path, state["policy"] or {})
        if "rules" in state:
            self._save_json(self.rules_path, state["rules"] or [])
        if "hooks" in state:
            self._save_json(self.hooks_path, state["hooks"] or [])
        if "listings" in state:
            base = self._listings() if merge else {}
            base.update(state["listings"] or {})
            self._save_json(self.listings_path, base)
        if "bookings" in state:
            base = self._bookings() if merge else {}
            base.update(state["bookings"] or {})
            self._save_json(self.bookings_path, base)
        return {"ok": True, "merged": merge, "status": self.status()}

    # ━━ Demo data (opt-in, owner only, reversible) ━━━━━━━━━━━━━━━━━
    _DEMO = [
        {"host": "demo_maya", "title": "Loft over the bakery", "city": "toronto",
         "kind": "entire_place", "price": 145, "guests": 4, "bedrooms": 2, "beds": 3,
         "cleaning_fee": 45, "amenities": ["wifi", "kitchen", "washer", "workspace"],
         "notes": "Corner loft on Ossington. Coffee downstairs from 7am."},
        {"host": "demo_ivan", "title": "Sunny room, Kensington", "city": "toronto",
         "kind": "private_room", "price": 68, "guests": 2, "bedrooms": 1, "beds": 1,
         "cleaning_fee": 15, "amenities": ["wifi", "heating", "pets_ok"],
         "notes": "Small room, big window, one very polite cat."},
        {"host": "demo_lena", "title": "Brownstone garden floor", "city": "nyc",
         "kind": "entire_place", "price": 210, "guests": 3, "bedrooms": 1, "beds": 2,
         "cleaning_fee": 60, "amenities": ["wifi", "kitchen", "ac", "balcony", "self_checkin"],
         "notes": "Private entrance off the garden in Bed-Stuy."},
    ]

    def seed_demo(self, owner_key: str) -> Dict[str, Any]:
        """Insert three clearly-labelled demo listings (handles start `demo_`).
        Opt-in only — a fresh install ships empty. wipe_demo() undoes it."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        made = []
        for spec in self._DEMO:
            res = self.create_listing(owner_key=owner_key, **spec)
            if res.get("error"):
                return res
            made.append({"id": res["id"], "title": res["title"], "host_key": res["host_key"]})
        return {"ok": True, "created": made}

    def wipe_demo(self, owner_key: str) -> Dict[str, Any]:
        """Remove every demo_* listing and its bookings."""
        err = self._owner_guard(owner_key)
        if err:
            return err
        listings = self._listings()
        gone = [lid for lid, l in listings.items() if l["host"].startswith("demo_")]
        for lid in gone:
            listings.pop(lid)
        self._save_json(self.listings_path, listings)
        bookings = self._bookings()
        dropped = [bid for bid, b in bookings.items() if b["listing_id"] in gone]
        for bid in dropped:
            bookings.pop(bid)
        self._save_json(self.bookings_path, bookings)
        return {"ok": True, "listings_removed": len(gone), "bookings_removed": len(dropped)}

    # ━━ Serve / register (mirrors openplay/openhouse) ━━━━━━━━━━━━━━
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
        return subprocess.run(["pm2", "delete", name], capture_output=True,
                              text=True).returncode == 0

    def serve_api(self, port=None, reload=True):
        port = int(port or self.port)
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
        self._pm2_start("openbnb-api", cmd, env=env)
        return {"api": f"http://localhost:{port}", "pm2": "openbnb-api",
                "docs": f"http://localhost:{port}/docs"}

    def kill_api(self):
        ok = self._pm2_kill("openbnb-api")
        return {"killed": ["openbnb-api"] if ok else [], "success": ok}

    def serve_app(self, app_port=None, dev=True):
        app_port = int(app_port or self.app_port)
        results = {}
        self.kill_app()
        results.update(self.serve_api(port=self.port, reload=dev))
        app_dir = self.module_dir / "app"
        if (app_dir / "package.json").exists():
            env = {"NEXT_PUBLIC_API_URL": f"http://localhost:{self.port}",
                   "PORT": str(app_port)}
            cmd = ["npx", "next", "dev" if dev else "start", "-p", str(app_port)]
            self._pm2_start("openbnb-app", cmd, cwd=str(app_dir), env=env)
            results["app"] = f"http://localhost:{app_port}"
            results["pm2_app"] = "openbnb-app"
        else:
            results["app"] = None
        results["dev"] = dev
        results["registration"] = self.register(
            app_url=f"http://localhost:{app_port}",
            api_url=f"http://localhost:{self.port}",
            owner=os.environ.get("OPENBNB_OWNER", ""),
        )
        return results

    def kill_app(self):
        killed = [name for name in ("openbnb-api", "openbnb-app") if self._pm2_kill(name)]
        return {"killed": killed}

    def register(self, app_url=None, api_url=None, owner=None, gateway="https://modc2.com"):
        app_url = app_url or f"http://localhost:{self.app_port}"
        api_url = api_url or f"http://localhost:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("openbnb", app_url)
            ns.reg_app("openbnb", app_url, owner=owner or "", port=self.app_port,
                       api_url=api_url)
            public = f"{gateway.rstrip('/')}/openbnb"
            print(f"openbnb registered → {public}  (app: {app_url}, api: {api_url})")
            return {"ok": True, "gateway": public, "app": app_url, "api": api_url}
        except Exception as e:
            print(f"openbnb: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": app_url, "api": api_url}

    def deregister(self):
        try:
            m.mod("server.namespace")().dereg_app("openbnb")
            return {"ok": True, "deregistered": "openbnb"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def forward(self, action=None, **kwargs):
        """CLI entry: m openbnb/<action> key=value …"""
        def k(name, default=None):
            return kwargs.get(name, default)

        actions = {
            "health": lambda: self.health(),
            "status": lambda: self.status(),
            "kinds": lambda: self.kinds(),
            "amenities": lambda: self.amenities(),
            "cities": lambda: self.cities(),
            "listings": lambda: self.listings(city=k("city"), kind=k("kind"),
                                              guests=k("guests", 0), host=k("host"),
                                              include_all=k("include_all", False)),
            "listing": lambda: self.listing(k("listing_id", "")),
            "create_listing": lambda: self.create_listing(**kwargs),
            "edit_listing": lambda: self.edit_listing(**kwargs),
            "set_status": lambda: self.set_status(k("listing_id", ""), k("host_key", ""),
                                                  k("status", "live")),
            "block_dates": lambda: self.block_dates(k("listing_id", ""), k("host_key", ""),
                                                    k("dates", []), unblock=k("unblock", False)),
            "calendar": lambda: self.calendar(k("listing_id", "")),
            "quote": lambda: self.quote(k("listing_id", ""), k("checkin", ""), k("checkout", ""),
                                        guests=k("guests", 1), guest=k("guest", ""),
                                        explain=k("explain", False)),
            "book": lambda: self.book(**kwargs),
            "booking": lambda: self.booking(k("booking_id", "")),
            "bookings": lambda: self.bookings(guest=k("guest", ""), host=k("host", ""),
                                              listing_id=k("listing_id", ""),
                                              status=k("status", "")),
            "approve_booking": lambda: self.approve_booking(k("booking_id", ""), k("host_key", "")),
            "decline_booking": lambda: self.decline_booking(k("booking_id", ""), k("host_key", ""),
                                                            reason=k("reason", "")),
            "cancel_booking": lambda: self.cancel_booking(k("booking_id", ""), guest=k("guest", ""),
                                                          host_key=k("host_key", "")),
            "payment_requirements": lambda: self.payment_requirements(k("booking_id", "")),
            # owner surface
            "verify_owner": lambda: self.verify_owner(k("owner_key", "")),
            "owner_state": lambda: self.owner_state(k("owner_key", "")),
            "policy": lambda: self.policy(),
            "policy_schema": lambda: self.policy_schema(),
            "set_policy": lambda: self.set_policy(**kwargs),
            "reset_policy": lambda: self.reset_policy(k("owner_key", ""), key=k("key")),
            "rules": lambda: self.rules(),
            "fact_keys": lambda: self.fact_keys(),
            "sandbox": lambda: self.sandbox(),
            "add_rule": lambda: self.add_rule(k("owner_key", ""), k("name", "rule"),
                                              k("when", ""), k("then", {}),
                                              enabled=k("enabled", True)),
            "update_rule": lambda: self.update_rule(**kwargs),
            "delete_rule": lambda: self.delete_rule(k("owner_key", ""), k("rule_id", "")),
            "move_rule": lambda: self.move_rule(k("owner_key", ""), k("rule_id", ""),
                                                direction=k("direction", "up")),
            "test_rule": lambda: self.test_rule(k("owner_key", ""), k("when", ""),
                                                facts=k("facts")),
            "hooks": lambda: self.hooks(k("owner_key", "")),
            "add_hook": lambda: self.add_hook(k("owner_key", ""), k("url", ""),
                                              events=k("events"), secret=k("secret", "")),
            "delete_hook": lambda: self.delete_hook(k("owner_key", ""), k("hook_id", "")),
            "hook_deliveries": lambda: self.hook_deliveries(k("owner_key", ""),
                                                            limit=k("limit", 25)),
            "owner_delete_listing": lambda: self.owner_delete_listing(k("owner_key", ""),
                                                                      k("listing_id", "")),
            "owner_delete_booking": lambda: self.owner_delete_booking(k("owner_key", ""),
                                                                      k("booking_id", "")),
            "owner_set_booking_status": lambda: self.owner_set_booking_status(
                k("owner_key", ""), k("booking_id", ""), k("status", "")),
            "export_state": lambda: self.export_state(k("owner_key", "")),
            "import_state": lambda: self.import_state(k("owner_key", ""), k("state", {}),
                                                      merge=k("merge", False)),
            "seed_demo": lambda: self.seed_demo(k("owner_key", "")),
            "wipe_demo": lambda: self.wipe_demo(k("owner_key", "")),
            # lifecycle
            "serve": lambda: self.serve(app_port=k("app_port"), dev=k("dev", True)),
            "kill": lambda: self.kill(),
            "serve_api": lambda: self.serve_api(port=k("port"), reload=k("reload", True)),
            "kill_api": lambda: self.kill_api(),
            "serve_app": lambda: self.serve_app(app_port=k("app_port"), dev=k("dev", True)),
            "kill_app": lambda: self.kill_app(),
            "register": lambda: self.register(app_url=k("app_url"), api_url=k("api_url"),
                                              owner=k("owner"),
                                              gateway=k("gateway", "https://modc2.com")),
            "deregister": lambda: self.deregister(),
        }
        if not action or action not in actions:
            return {
                "module": "openbnb",
                "description": self.description,
                "actions": list(actions.keys()),
                "status": self.status(),
            }
        return actions[action]()
