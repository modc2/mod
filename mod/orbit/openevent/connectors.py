"""
OpenEvent — platform connectors.

Each connector speaks to one external events platform (Luma, Eventbrite,
Meetup, …) and exposes two capabilities:

  • discover(area, query, limit) → list[Event]   — read what's on, near a place
  • publish(event, creds)        → Syndication    — push an event onto it

Everything is normalized to one ``Event`` shape so the rest of the app never
has to care which platform a card came from. Discovery is real network I/O,
defensive by construction: a platform that 403s, rate-limits, or changes its
markup degrades to ``ok=False, count=0`` with the error surfaced — it never
crashes the aggregate feed.

No fabricated events: if a platform returns nothing, the feed shows nothing
from it (and says so).
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover - requests is in the runtime
    requests = None


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 18


# ── helpers ──────────────────────────────────────────────────────────
def _iso_to_unix(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        # epoch-ish?
        try:
            return int(float(s))
        except Exception:
            return None


def _clean(s: Optional[str], limit: int = 600) -> str:
    if not s:
        return ""
    s = _html.unescape(re.sub(r"<[^>]+>", " ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _dedup_key(title: str, starts_at: Optional[int]) -> str:
    t = re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:40]
    day = ""
    if starts_at:
        day = datetime.fromtimestamp(starts_at, tz=timezone.utc).strftime("%Y%m%d")
    return f"{t}@{day}"


class Connector:
    key = "base"
    label = "Base"
    color = "#888888"
    can_discover = False
    can_publish = False
    # publish requires the user to have connected an API key / token (BYOK)?
    publish_mode = "assisted"   # "api" (real create) | "assisted" (prefilled handoff)

    def meta(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "color": self.color,
            "can_discover": self.can_discover, "can_publish": self.can_publish,
            "publish_mode": self.publish_mode,
        }

    # subclasses override
    def discover(self, area: Dict[str, Any], query: str = "", limit: int = 40) -> List[Dict[str, Any]]:
        return []

    def publish(self, event: Dict[str, Any], creds: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {"platform": self.key, "status": "skipped", "message": "not supported"}

    # shared http
    def _get(self, url: str, **kw) -> "requests.Response":
        return requests.get(url, headers={"User-Agent": _UA, "Accept": "text/html,application/json"},
                            timeout=_TIMEOUT, **kw)

    def _tag(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp common fields + the platform pill onto a normalized event."""
        ev.setdefault("source", self.key)
        ev["uid"] = _dedup_key(ev.get("title", ""), ev.get("starts_at"))
        ev["platforms"] = [{
            "key": self.key, "label": self.label, "color": self.color,
            "url": ev.get("url"),
        }]
        ev["fetched_at"] = int(time.time())
        return ev


# ── Luma ─────────────────────────────────────────────────────────────
class LumaConnector(Connector):
    key = "luma"
    label = "Luma"
    color = "#f0529c"
    can_discover = True
    can_publish = True
    publish_mode = "assisted"   # Luma has no public create API → prefilled handoff

    def discover(self, area, query="", limit=40):
        slug = (area.get("luma") or "").strip()
        if not slug:
            return []
        url = f"https://lu.ma/{slug}"
        r = self._get(url)
        r.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
        if not m:
            return []
        data = json.loads(m.group(1))
        items = (((data.get("props") or {}).get("pageProps") or {})
                 .get("initialData", {}).get("data", {}).get("events") or [])
        ql = query.lower().strip()
        out: List[Dict[str, Any]] = []
        for it in items:
            ev = it.get("event") or {}
            name = ev.get("name") or it.get("name")
            if not name:
                continue
            if ql and ql not in name.lower():
                continue
            starts = _iso_to_unix(it.get("start_at") or ev.get("start_at"))
            slug_url = ev.get("url")
            link = f"https://lu.ma/{slug_url}" if slug_url else url
            geo = ev.get("geo_address_info") or {}
            ticket = it.get("ticket_info") or {}
            price = None
            if not ticket.get("is_free", True):
                cents = (ticket.get("price") or {}).get("cents")
                if cents:
                    price = {"amount": round(cents / 100, 2),
                             "currency": (ticket.get("price") or {}).get("currency", "USD").upper()}
            hosts = [h.get("name") for h in (it.get("hosts") or []) if h.get("name")]
            norm = {
                "source_id": it.get("api_id") or ev.get("api_id"),
                "title": name,
                "description": _clean((it.get("calendar") or {}).get("description_short")),
                "starts_at": starts,
                "ends_at": _iso_to_unix(ev.get("end_at")),
                "url": link,
                "cover_image": (it.get("cover_image") or {}).get("url") or ev.get("cover_url"),
                "venue": geo.get("address") or geo.get("city_state") or "",
                "address": geo.get("full_address") or geo.get("address") or "",
                "city": (it.get("featured_city") or {}).get("name") or area.get("label"),
                "lat": geo.get("latitude") or ev.get("geo_latitude"),
                "lng": geo.get("longitude") or ev.get("geo_longitude"),
                "online": (ev.get("location_type") == "online"),
                "free": price is None,
                "price": price,
                "organizer": ", ".join(hosts) if hosts else (it.get("calendar") or {}).get("name", ""),
            }
            out.append(self._tag(norm))
            if len(out) >= limit:
                break
        return out

    def publish(self, event, creds):
        # Luma's create flow is auth-gated with no documented public API. We hand
        # off a fully-composed draft + a one-click create link (assisted mode).
        return {
            "platform": self.key, "status": "assisted",
            "url": "https://lu.ma/create",
            "message": "Open Luma's composer with your event prefilled, then hit Create.",
            "draft": _share_draft(event),
        }


# ── Eventbrite ───────────────────────────────────────────────────────
class EventbriteConnector(Connector):
    key = "eventbrite"
    label = "Eventbrite"
    color = "#f05537"
    can_discover = True
    can_publish = True
    publish_mode = "api"   # real draft creation via the v3 API when a token is connected

    def discover(self, area, query="", limit=40):
        slug = (area.get("eventbrite") or "").strip()
        if not slug:
            return []
        q = f"?q={requests.utils.quote(query)}" if query else ""
        url = f"https://www.eventbrite.com/d/{slug}/all-events/{q}"
        r = self._get(url)
        r.raise_for_status()
        results = self._extract(r.text)
        out: List[Dict[str, Any]] = []
        for ev in results:
            name = (ev.get("name") or {})
            title = name.get("text") if isinstance(name, dict) else (ev.get("name") or "")
            if not title:
                continue
            start = ev.get("start") or {}
            venue = ev.get("primary_venue") or ev.get("venue") or {}
            addr = venue.get("address") or {}
            ta = ev.get("ticket_availability") or {}
            mp = (ta.get("minimum_ticket_price") or {}) if isinstance(ta, dict) else {}
            is_free = ev.get("is_free") if ev.get("is_free") is not None else ta.get("is_free")
            price = None
            if is_free is False and mp.get("major_value"):
                price = {"amount": float(mp["major_value"]), "currency": mp.get("currency", "USD")}
            lat, lng = addr.get("latitude"), addr.get("longitude")
            norm = {
                "source_id": str(ev.get("id") or ev.get("eid") or ""),
                "title": title,
                "description": _clean((ev.get("summary") or
                                       (ev.get("description") or {}).get("text") if isinstance(ev.get("description"), dict)
                                       else ev.get("description"))),
                "starts_at": _iso_to_unix(start.get("utc") or start.get("local")),
                "ends_at": _iso_to_unix((ev.get("end") or {}).get("utc")),
                "url": ev.get("url") or ev.get("vanity_url"),
                "cover_image": (ev.get("image") or {}).get("url") if isinstance(ev.get("image"), dict) else ev.get("image_url"),
                "venue": venue.get("name") or "",
                "address": addr.get("localized_address_display") or "",
                "city": addr.get("city") or area.get("label"),
                "lat": float(lat) if lat else None,
                "lng": float(lng) if lng else None,
                "online": (ev.get("is_online_event") or False),
                "free": price is None,
                "price": price,
                "organizer": (ev.get("primary_organizer") or ev.get("organizer") or {}).get("name", ""),
            }
            out.append(self._tag(norm))
            if len(out) >= limit:
                break
        return out

    def _extract(self, text: str) -> List[Dict[str, Any]]:
        # Eventbrite embeds a big JSON blob in window.__SERVER_DATA__
        m = re.search(r'window\.__SERVER_DATA__\s*=\s*(\{.*?\});\s*</script>', text, re.S)
        if m:
            try:
                sd = json.loads(m.group(1))
                events = (((sd.get("search_data") or {}).get("events") or {}).get("results")
                          or (sd.get("events") or {}).get("results") or [])
                if events:
                    return events
            except Exception:
                pass
        # fallback: JSON-LD ItemList of events
        out = []
        for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
            try:
                obj = json.loads(blk)
            except Exception:
                continue
            arr = obj if isinstance(obj, list) else [obj]
            for o in arr:
                if isinstance(o, dict) and o.get("@type") in ("Event", "SocialEvent", "BusinessEvent"):
                    loc = (o.get("location") or {})
                    geo = (loc.get("geo") or {}) if isinstance(loc, dict) else {}
                    out.append({
                        "name": o.get("name"), "url": o.get("url"),
                        "start": {"utc": o.get("startDate")}, "end": {"utc": o.get("endDate")},
                        "description": o.get("description"),
                        "image": {"url": o.get("image") if isinstance(o.get("image"), str) else None},
                        "primary_venue": {"name": loc.get("name") if isinstance(loc, dict) else "",
                                          "address": {"latitude": geo.get("latitude"),
                                                      "longitude": geo.get("longitude")}},
                    })
        return out

    def publish(self, event, creds):
        token = (creds or {}).get("token")
        org = (creds or {}).get("organization_id")
        if not token:
            return {"platform": self.key, "status": "needs_key",
                    "message": "Connect an Eventbrite personal OAuth token to publish here.",
                    "draft": _share_draft(event)}
        try:
            if not org:
                org = self._default_org(token)
            if not org:
                return {"platform": self.key, "status": "error",
                        "message": "No Eventbrite organization found for this token."}
            tz = event.get("timezone") or "UTC"
            body = {"event": {
                "name": {"html": event["title"]},
                "description": {"html": event.get("description") or event["title"]},
                "start": {"timezone": tz, "utc": _to_eb_utc(event.get("starts_at"))},
                "end": {"timezone": tz, "utc": _to_eb_utc(event.get("ends_at") or (event.get("starts_at", 0) + 7200))},
                "currency": (event.get("price") or {}).get("currency", "USD"),
                "online_event": bool(event.get("online")),
            }}
            r = requests.post(
                f"https://www.eventbriteapi.com/v3/organizations/{org}/events/",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                data=json.dumps(body), timeout=_TIMEOUT)
            if r.status_code >= 400:
                detail = (r.json().get("error_description") if r.headers.get("content-type", "").startswith("application/json") else r.text)
                return {"platform": self.key, "status": "error", "message": f"Eventbrite: {detail}"}
            created = r.json()
            return {"platform": self.key, "status": "draft",
                    "url": created.get("url"), "external_id": created.get("id"),
                    "message": "Draft created on Eventbrite — add tickets & publish from your dashboard."}
        except Exception as e:
            return {"platform": self.key, "status": "error", "message": f"Eventbrite: {e}"}

    def _default_org(self, token: str) -> Optional[str]:
        r = requests.get("https://www.eventbriteapi.com/v3/users/me/organizations/",
                         headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return None
        orgs = r.json().get("organizations") or []
        return orgs[0].get("id") if orgs else None


# ── Meetup ───────────────────────────────────────────────────────────
class MeetupConnector(Connector):
    key = "meetup"
    label = "Meetup"
    color = "#f6405f"
    can_discover = True
    can_publish = True
    publish_mode = "assisted"

    def discover(self, area, query="", limit=40):
        # Meetup's find endpoint returns JSON-LD on its event search pages.
        slug = (area.get("meetup") or area.get("eventbrite") or "").strip()
        if not slug:
            return []
        loc = slug.replace("--", "/")
        url = f"https://www.meetup.com/find/?location={requests.utils.quote(area.get('label',''))}&source=EVENTS"
        if query:
            url += f"&keywords={requests.utils.quote(query)}"
        try:
            r = self._get(url)
            r.raise_for_status()
        except Exception:
            return []
        out = []
        for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
            try:
                obj = json.loads(blk)
            except Exception:
                continue
            arr = obj if isinstance(obj, list) else [obj]
            for o in arr:
                if not (isinstance(o, dict) and o.get("@type") == "Event" and o.get("name")):
                    continue
                loc_o = o.get("location") or {}
                geo = (loc_o.get("geo") or {}) if isinstance(loc_o, dict) else {}
                offers = o.get("offers") or {}
                price = None
                try:
                    p = float(offers.get("price")) if offers.get("price") not in (None, "", "0", "0.0") else 0
                    if p > 0:
                        price = {"amount": p, "currency": offers.get("priceCurrency", "USD")}
                except Exception:
                    pass
                norm = {
                    "source_id": o.get("url"),
                    "title": o.get("name"),
                    "description": _clean(o.get("description")),
                    "starts_at": _iso_to_unix(o.get("startDate")),
                    "ends_at": _iso_to_unix(o.get("endDate")),
                    "url": o.get("url"),
                    "cover_image": o.get("image") if isinstance(o.get("image"), str) else None,
                    "venue": loc_o.get("name") if isinstance(loc_o, dict) else "",
                    "address": (loc_o.get("address") or {}).get("streetAddress") if isinstance(loc_o, dict) else "",
                    "city": area.get("label"),
                    "lat": geo.get("latitude"), "lng": geo.get("longitude"),
                    "online": False, "free": price is None, "price": price,
                    "organizer": (o.get("organizer") or {}).get("name", "") if isinstance(o.get("organizer"), dict) else "",
                }
                out.append(self._tag(norm))
                if len(out) >= limit:
                    return out
        return out

    def publish(self, event, creds):
        return {"platform": self.key, "status": "assisted",
                "url": "https://www.meetup.com/start/",
                "message": "Open Meetup's organizer with your event details prefilled.",
                "draft": _share_draft(event)}


# ── share draft (for assisted handoffs) ──────────────────────────────
def _share_draft(event: Dict[str, Any]) -> Dict[str, Any]:
    when = ""
    if event.get("starts_at"):
        when = datetime.fromtimestamp(event["starts_at"], tz=timezone.utc).strftime("%a %b %d, %Y · %H:%M UTC")
    lines = [event["title"]]
    if when:
        lines.append(when)
    if event.get("venue") or event.get("address"):
        lines.append(event.get("address") or event.get("venue"))
    if event.get("description"):
        lines.append("\n" + event["description"])
    return {"title": event["title"], "when": when,
            "where": event.get("address") or event.get("venue") or ("Online" if event.get("online") else ""),
            "text": "\n".join(lines)}


def _to_eb_utc(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── registry + aggregate discovery + dedup ───────────────────────────
class Registry:
    def __init__(self):
        self.connectors: Dict[str, Connector] = {}
        for c in (LumaConnector(), EventbriteConnector(), MeetupConnector()):
            self.connectors[c.key] = c

    def get(self, key: str) -> Optional[Connector]:
        return self.connectors.get(key)

    def all(self) -> List[Connector]:
        return list(self.connectors.values())

    def meta(self) -> List[Dict[str, Any]]:
        return [c.meta() for c in self.all()]

    def discover(self, area: Dict[str, Any], sources: List[str], query: str = "",
                 limit: int = 40) -> Dict[str, Any]:
        """Fan out across the requested platforms, merge + dedup the feed."""
        import concurrent.futures as cf
        sources = [s for s in sources if s in self.connectors] or list(self.connectors)
        reports: List[Dict[str, Any]] = []
        collected: List[Dict[str, Any]] = []

        def run(key):
            c = self.connectors[key]
            t0 = time.time()
            try:
                evs = c.discover(area, query=query, limit=limit)
                return key, evs, {"key": key, "label": c.label, "ok": True,
                                  "count": len(evs), "ms": int((time.time() - t0) * 1000)}
            except Exception as e:
                return key, [], {"key": key, "label": c.label, "ok": False,
                                 "count": 0, "error": str(e)[:160],
                                 "ms": int((time.time() - t0) * 1000)}

        with cf.ThreadPoolExecutor(max_workers=max(1, len(sources))) as ex:
            for key, evs, rep in ex.map(run, sources):
                collected.extend(evs)
                reports.append(rep)

        merged = self._merge(collected)
        merged.sort(key=lambda e: (e.get("starts_at") is None, e.get("starts_at") or 1 << 62))
        return {"events": merged, "sources": reports,
                "total": len(merged), "raw": len(collected)}

    def _merge(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for e in events:
            k = e.get("uid") or _dedup_key(e.get("title", ""), e.get("starts_at"))
            if k not in by:
                by[k] = e
                order.append(k)
            else:
                base = by[k]
                have = {p["key"] for p in base.get("platforms", [])}
                for p in e.get("platforms", []):
                    if p["key"] not in have:
                        base.setdefault("platforms", []).append(p)
                # fill gaps from the duplicate (geo, image, price, description)
                for fld in ("lat", "lng", "cover_image", "venue", "address",
                            "description", "price", "organizer"):
                    if not base.get(fld) and e.get(fld):
                        base[fld] = e[fld]
                if base.get("free") and e.get("price"):
                    base["free"] = False
                    base["price"] = e["price"]
        return [by[k] for k in order]
