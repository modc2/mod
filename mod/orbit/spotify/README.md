# spotify

**Spotify as a mod.** One adapter for the Spotify Web API, exposed three ways —
CLI verbs, 22 MCP tools, and a REST mirror with a browser console — all running
the same code, all running on *your* account. Python stdlib only: no `spotipy`,
no `mcp` package, no framework.

```
m spotify/status                # who you are, what is playing, which devices are awake
m spotify/play "roygbiv"        # phrase, spotify: URI, or open.spotify.com link
m spotify/queue "aphex twin xtal"
m spotify/serve                 # REST + console + MCP on :50610
```

## Setup, once

1. Create an app at <https://developer.spotify.com/dashboard>. Any name.
2. Add a redirect URI to it — exactly one of:
   - `http://127.0.0.1:8899/callback` (the default, used by `m spotify/login`), or
   - `http://127.0.0.1:50610/callback` (if you'd rather the module's own server
     catch the redirect — that route is built in).
   Spotify requires a literal loopback IP; `localhost` is rejected.
3. Hand the module its credentials, then log in:

```bash
m spotify/set_key client_id=<from the dashboard> client_secret=<from the dashboard>
m spotify/login                  # prints a URL, waits for the redirect
```

`client_secret` is optional — the login flow is authorization code + **PKCE**.
Set it anyway if you want catalog search to work while logged out (that path
uses client credentials).

On a headless box, where the browser can't reach this process:

```bash
m spotify/authorize_url          # open the url wherever you have a browser
m spotify/exchange code=AQD…     # paste the ?code= from the redirect (or the whole URL)
```

Everything secret lives off-tree at `0600`:

| file | holds |
| --- | --- |
| `~/.mod/spotify/keys.json` | client_id, client_secret, redirect_uri |
| `~/.mod/spotify/auth.json` | access + refresh token, expiry, granted scopes |

Nothing lands in `config.json`, nothing is committed. Tokens refresh
automatically — a 401 mid-call triggers one refresh and one retry.

## The verbs

```bash
m spotify/status                              # auth + player state, no secrets
m spotify/search "boards of canada" type=album limit=5
m spotify/now                                 # track, progress, device, shuffle/repeat
m spotify/play "khruangbin white gloves"      # a phrase is searched; top hit plays
m spotify/play uri=spotify:playlist:37i9dQ…   # album/playlist/artist play as a context
m spotify/pause ; m spotify/next ; m spotify/previous
m spotify/seek position_ms=60000
m spotify/volume 40
m spotify/shuffle true ; m spotify/repeat off
m spotify/devices                             # everything Spotify can currently reach
m spotify/transfer device="Kitchen speaker"   # matched by name, prefix, or id
m spotify/queue "sade smooth operator"        # no argument → what's up next
m spotify/recent limit=10
m spotify/top type=artists time_range=short_term
m spotify/saved ; m spotify/save "aphex twin avril 14th"
m spotify/playlists
m spotify/playlist_create name=dinner tracks='["khruangbin","sade"]'
m spotify/playlist_add id=<playlist> tracks='["spotify:track:…"]'
m spotify/lookup https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT
m spotify/raw path=/browse/new-releases       # any endpoint, your token
```

Two conventions run through all of it:

- **Anything that takes a track takes a phrase.** URI, link, bare id or free
  text — free text is searched and the top hit wins, so `"artist - title"`
  aims better than `"title"`.
- **Devices are named, not hashed.** `device="Kitchen"` matches by exact name,
  then by prefix, then by id; a miss lists the devices that do exist.

## MCP

```bash
m spotify/mcp_config      # drop-in config for Claude Code / Desktop and friends
m spotify/tools           # the registry
m spotify/mcp_call spotify_now_playing
```

stdio: `python3 mcp.py`. HTTP: `POST /mcp` (Streamable HTTP, JSON-RPC 2.0, no
SSE) — served both by `mcp.py --http` and by the module's own API server, which
mounts the same `handle()`.

| tool | does |
| --- | --- |
| `spotify_status` | auth state + now playing + devices — the first call to make |
| `spotify_search` | catalog search, flattened, with URIs |
| `spotify_now_playing` | current track, progress, device, modes |
| `spotify_play` / `spotify_pause` / `spotify_skip` | transport |
| `spotify_seek` / `spotify_volume` / `spotify_mode` | position, volume, shuffle+repeat |
| `spotify_devices` / `spotify_transfer` | see and move playback |
| `spotify_queue` / `spotify_up_next` | add to and read the queue |
| `spotify_recent` / `spotify_top` | listening history, real taste data |
| `spotify_saved` | list Your Library, or save/unsave one track |
| `spotify_playlists` / `spotify_playlist` | read |
| `spotify_playlist_create` / `spotify_playlist_edit` | write |
| `spotify_lookup` | URI or link → the full object |
| `spotify_raw` | escape hatch to any Web API endpoint |

Failures come back as MCP `isError` results carrying `{error, status, hint}`,
so a model reads *"no active device — open Spotify somewhere, then transfer"*
and fixes itself instead of retrying blind.

## Server

```bash
m spotify/serve            # pm2 process spotify-api on :50610
m spotify/kill
```

- `GET  /` — the route list · `GET /health`
- `GET  /spotify` — the console (NOW · SEARCH · LIBRARY · AUTH · MCP)
- `GET  /login` → `GET /callback` — the whole OAuth dance in the browser
- `GET  /status /search /now /devices /queue /recent /top /saved /playlists /playlist /lookup`
- `POST /play /pause /next /previous /seek /volume /shuffle /repeat /transfer /queue /save /playlist /playlist/tracks /raw`
- `POST /mcp` — JSON-RPC 2.0

BYOK: send `authorization: Bearer <spotify access token>` and that token is
used for the request and never stored. Without one, the server falls back to
the operator's own tokens — which is why `route` is **false** in `config.json`.
Turn it on only behind an auth layer you trust; on a public gateway it would
hand strangers your account.

## Limits worth knowing

- **Playback control needs Spotify Premium.** Free accounts get search,
  library, playlists and read-only player state; `play`/`pause`/`volume`
  return `PREMIUM_REQUIRED`.
- **A device must be awake.** Spotify Connect only lists apps that are open.
  Nothing running → `NO_ACTIVE_DEVICE`; the error says so and names the fix.
- **Deprecated since Nov 2024, for apps created after that date:**
  `/recommendations`, `/audio-features`, `/audio-analysis`, related-artists,
  featured-playlists, and 30-second preview URLs. No tool here depends on them
  — a new app calling them via `spotify_raw` will get a 403/404, and that is
  Spotify's doing, not the module's.
- **Rate limits** are a rolling 30-second window. A 429 surfaces with
  `retry_after` in seconds; back off rather than hammering.

## Tests

```bash
m spotify/test        # or: python3 -m pytest -q test
```

39 offline tests — URI/link parsing, the normalizers, PKCE challenge
derivation, device resolution, playlist batching, the MCP wire format, and the
REST routing table. No network, no account, and they never read the operator's
real `~/.mod/spotify`.

## Layout

```
spotify/
├── mod.py        the protocol anchor — every verb above
├── spotify.py    the adapter: OAuth, HTTP, normalizers (the only file that knows Spotify)
├── mcp.py        22 MCP tools; stdio + Streamable HTTP; hand-rolled JSON-RPC
├── api.py        REST + console + /mcp + the OAuth redirect, on one port
├── console.html  zero-dependency browser console
└── test/         offline tests
```

`mod.py`, `api.py` and `mcp.py` all call the same `Spotify` class, so the CLI,
the console and an agent can never disagree about what "play" means.
