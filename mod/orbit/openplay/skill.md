---
name: openplay
description: Pickup sports across the city — one board for every soccer/hockey/basketball game. Create games (one-off or recurring), invite/RSVP/kick players, optional USDC/USDT fee on Base. Free by default. No wallet needed to play.
type: orbit-module
---

# openplay

A single city-wide board for pickup games, built to replace a dozen scattered
WhatsApp groups. Anyone creates a game; players join with just a name. Admins
run recurring games, invite and kick players, and — only if they choose —
charge a per-seat fee in USDC/USDT on Base. **Every game is free by default.**

- **Ports**: API `50160`, app `50161`, gateway-routed at `modc2.com/openplay`.
- **Identity**: lightweight handle (display name). A wallet is only needed to
  pay for, or receive payment for, a paid game.
- **Admin auth**: each created game returns a one-time `admin_key` to its
  creator; that key gates invite/kick/cancel/edit/set_cost. Stored off-chain.

## Capabilities

- **City view, no wallet needed**: every upcoming game/occurrence on a Leaflet
  map (pins colored by sport) plus a chronological feed, filterable by sport.
- **Sports**: soccer, basketball, hockey, tennis, volleyball, football.
- **Recurring games**: one-off, daily, or weekly (incl. specific weekdays).
  Occurrences are computed on the fly over a ~70-day horizon — no materialized
  rows. Players RSVP per occurrence; admins keep a standing `roster` of regulars.
- **RSVP + capacity**: join/leave with auto-promoting waitlist when a game fills.
- **Invites**: admin invites by handle (per-occurrence or whole series);
  invitee can accept or decline. Kick removes a player (and optionally the roster).
- **Optional crypto fee**: admin sets a per-seat price (USDC or USDT) with their
  Base address as receiver. Joining a paid game returns x402 payment requirements
  (asset, atomic amount, receiver, facilitator); the player pays and re-joins with
  the tx hash as proof. Clearing the price (amount=0) makes it free again.
- **NYC venue presets**: curated real spots (West 4th "The Cage", Pier 40, Sky
  Rink, Rucker, McCarren…) with coordinates so games land on the map correctly.
- No seeded/fake games — the board shows real state (empty until games are made).

## Usage

### Python
```python
import mod as m
op = m.mod("openplay")()

op.serve()                                  # start api(50160)+app(50161), register gateway
op.games()                                  # all upcoming occurrences, soonest first (public)
op.venues(); op.sports()                    # presets + supported sports (public)

g = op.create_game(sport="soccer", title="Sunday Run",
                   starts_at=1750000000, admin="alex",
                   venue="Pier 40", lat=40.7297, lng=-74.0110,
                   capacity=10, recurrence={"freq": "weekly", "days": [6]})
gid, key = g["game_id"], g["admin_key"]     # admin_key shown ONCE

op.join(gid, "sam")                         # free game → instant
op.invite(gid, key, "jordan")               # admin invites
op.accept_invite(gid, "jordan")             # invitee accepts
op.kick(gid, key, "sam")                    # admin removes (needs admin_key)

# make it paid (USDC on Base), then a join returns payment_required
op.set_cost(gid, key, amount=5, currency="USDC", receiver="0x…")
```

### CLI
```bash
m openplay/serve
m openplay/games
m openplay/create_game sport=basketball title="Lunch Hoops" starts_at=1750000000 admin=jordan capacity=10
m openplay/join game_id=<gid> handle=sam
m openplay/invite game_id=<gid> admin_key=<key> handle=chris
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/games?sport=` | upcoming occurrences across the city (public) |
| GET | `/game/{id}?occ=YYYY-MM-DD` | one game/occurrence detail (players, invited, waitlist) |
| GET | `/sports`, `/venues`, `/status` | metadata + city stats (public) |
| GET | `/payment_requirements/{id}` | x402 reqs for a paid game (`{free:true}` if free) |
| POST | `/games` | create; returns `game_id` + one-time `admin_key` |
| POST | `/game/{id}/join` `/leave` | RSVP `{handle, wallet?, occ?, payment?}` |
| POST | `/game/{id}/accept` `/decline` | invitee responds |
| POST | `/game/{id}/invite` `/kick` | admin-only `{admin_key, handle, occ?, series?}` |
| POST | `/game/{id}/cost` `/cancel` `/edit` | admin-only `{admin_key, …}` |

Storage: `~/.openplay/games.json` (one record per game/series; rsvps keyed by
occurrence date). Registration mirrors openhouse/freewash via
`m.mod("server.namespace")().reg()/reg_app()`. App is `next dev` behind the
gateway (basePath `/openplay`, api proxied at `/openplay/api`).
