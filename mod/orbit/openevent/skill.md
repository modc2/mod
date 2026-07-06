---
name: openevent
description: Cross-platform events aggregator — one live, de-duplicated feed of what's on near you pulled across Luma, Eventbrite and Meetup, plus a single composer that broadcasts your event to every platform at once (real Eventbrite drafts via BYOK token; one-click prefilled hand-offs for Luma & Meetup). No login to browse.
type: orbit-module
---

# openevent

The events world is splintered: Luma for tech mixers, Eventbrite for ticketed
shows, Meetup for communities. To **find** what's on you check five apps; to
**announce** something you re-type it into each one. OpenEvent collapses both
sides into one surface.

- **Ports**: API `50170`, app `50171`, gateway-routed at `modc2.com/openevent`.
- **Identity**: a lightweight handle. No login required to browse.
- **Secrets**: platform API tokens are **BYOK** and live **off-chain** in
  `~/.openevent/credentials.json` (chmod 600) — never in committed config.
- **Storage**: `~/.openevent/{events.json, credentials.json, cache.json, admin.json}`.

## Two sides

### Discover — one feed across platforms
A single, de-duplicated feed of upcoming events for a city. The same event
cross-listed on two platforms collapses into **one card with both platform
pills**, soonest first, on a Leaflet map + list.

| Platform | Discovery | How |
|---|---|---|
| **Luma** | ✅ live, key-free | parses `lu.ma/<city>` `__NEXT_DATA__` |
| **Eventbrite** | best-effort | scrapes public search; their edge bot-gates datacenter IPs, so it reports `blocked` honestly when refused |
| **Meetup** | best-effort | JSON-LD on find pages |

Every response carries a `sources` array (`{key, ok, count, error, ms}`) so the
UI shows exactly which platforms answered — **no fabricated events**. Results are
cached `cache_ttl` seconds (default 600) to stay clear of rate limits.

### Broadcast — compose once, publish everywhere
Compose an event once and fan it out to the selected platforms. Each target
returns a `status`:

- `draft` — **real** event created via API (Eventbrite, when a token is connected) with its live URL.
- `assisted` — a one-click, fully-composed hand-off (Luma, Meetup) — opens their composer + copy-paste draft.
- `needs_key` — an API platform with no token connected yet (falls back to a hand-off draft).
- `error` — surfaced verbatim.

Each created event keeps its per-platform `syndications` (status + URL) and can
be (re)broadcast to more platforms later with its one-time `edit_key`.

## Usage

### Python
```python
import mod as m
oe = m.mod("openevent")()

oe.serve()                                    # api(50170)+app(50171), register gateway
oe.discover(city="sf", limit=20)              # live de-duped feed + source report
oe.platforms(); oe.cities()                   # connector catalog + selectable cities

# bring your own Eventbrite token (stored off-chain), then broadcast
oe.connect("eventbrite", token="YOUR_PRIVATE_TOKEN")
ev = oe.create_event(
    title="Mod Orbit Launch Party",
    starts_at=1750000000, city="sf", venue="The Pad", organizer="agimarx",
    description="Aggregating every event platform into one.",
    targets=["luma", "eventbrite", "meetup"])
eid, key = ev["id"], ev["edit_key"]           # edit_key shown ONCE
oe.broadcast(eid, key, ["meetup"])            # push to more later
```

### CLI
```bash
m openevent/serve
m openevent/discover city=sf query=AI
m openevent/connect platform=eventbrite token=YOUR_TOKEN
m openevent/create_event title="Launch Party" starts_at=1750000000 city=sf targets='["luma","eventbrite"]'
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/discover?city=&query=&sources=&refresh=` | live de-duped feed + `sources` report (public) |
| GET | `/platforms`, `/cities`, `/status` | connector catalog + cities (public) |
| GET | `/events`, `/event/{id}` | your composed events |
| GET | `/draft/{id}/{platform}` | copy-paste draft for an assisted hand-off |
| POST | `/events` | compose + broadcast; returns `id` + one-time `edit_key` + `syndications` |
| POST | `/event/{id}/broadcast` | `{edit_key, targets[]}` — (re)syndicate |
| POST | `/event/{id}/delete` | `{edit_key}` |
| POST | `/connect` `/disconnect` | BYOK creds, off-chain `{platform, fields}` |
| POST | `/admin/verify` `/admin/delete_event` | module-admin (`OPENEVENT_ADMIN_KEY`) |

## Architecture

`connectors.py` holds a pluggable `Connector` base + `Luma`/`Eventbrite`/`Meetup`
implementations and a `Registry` that fans discovery out concurrently and merges
+ de-dupes (`title`+date key, platform pills combined, gaps filled). Adding a
platform = one subclass with `discover()` and/or `publish()`. `mod.py` wraps the
registry with city presets, a TTL cache, the off-chain credential vault, the
canonical event store, and broadcast/syndication tracking. Registration mirrors
openplay via `m.mod("server.namespace")().reg()/reg_app()`; the Next app runs
`next dev` behind the gateway (basePath `/openevent`, api proxied at `/openevent/api`).
