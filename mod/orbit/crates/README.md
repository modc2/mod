# crates

A DJ booth and a pattern studio in one browser tab — with a crate that digs
**Spotify, Bandcamp, SoundCloud, YouTube and the Internet Archive at once**,
loads everything but Spotify straight onto a deck, and lets you **keep what you
find**: named playlists that save themselves, and a link that hands one to
anybody.

A fork of `orbit/musica`, on port **50790** at `/crates`. It shares musica's
platform credentials (`~/.mod/musica/`) and keeps its own playlists in
`~/.mod/crates/`.

- **BOOTH** — two decks with waveforms and a detected beat grid, beatmatch
  (SYNC pitches and phase-aligns), three-band EQ, a bipolar filter, beat loops,
  four hot cues, a constant-power crossfader, a tempo-synced echo, a master
  limiter, and record-the-mix to `.webm`. Both decks show their live key on the
  Camelot wheel and the mixer says whether the two mix cleanly.
- **STUDIO** — a step sequencer (16/32/64 steps, swing, accents, eight
  patterns) and a piano roll in the FL Studio idiom, on the same clock as the
  decks. Every voice is synthesised; drop a file on a channel to sample it.
- **CRATE** — one search box for five platforms, paste-a-link for any of their
  URLs, the platform's own player for previews, a Bandcamp discover feed by
  tag, and MY SET across the top: the tracks you picked, in order, keys lighting
  up against whatever is playing.
- **MY PLAYLISTS** — MY SET, kept. Name it and it auto-saves from then on;
  reopen it tomorrow, share it as a read-only link, or browse what other people
  have shared here and copy one into your own library.
- **MCP** — the same crate and the same playlists as 19 tools an agent can
  call, at `POST /api/crates/mcp` or over stdio.

All audio work is client-side. Files and platform streams are decoded and
mixed through Web Audio in the tab; tempo and key are detected locally. The
module's own jobs are to serve the console, answer the platforms, proxy the
streams that lack a CORS header, and keep your playlists.

## Playlists: whose they are, and how sharing works

A playlist is a JSON file under `~/.mod/crates/playlists/<owner>/`. Two ways to
be an owner, and the console never blocks on either:

| | what it is | where it works |
|---|---|---|
| **guest key** | 64 random hex characters the module mints and the console keeps in localStorage. The owner id is `guest:<sha256(key)[:24]>` — the key itself is never stored server-side. | this browser, until you copy the key to another one (the drawer has COPY MY KEY / USE ANOTHER KEY) |
| **wallet** | a mod-protocol token: `personal_sign` over `{data,time}`, the fleet's shared `m.mod('auth')` identity. CONNECT WALLET in the drawer. | anywhere you can sign — other browsers, the CLI, an agent |

Both ride on every request: `Authorization: Bearer <token>` and/or
`X-Crates-Guest: <key>`. Anonymous is a legitimate state — anyone can dig the
crate, open a share link and browse the public directory without a credential.

**Sharing** mints a second id, `sh_…`, and the link is
`https://modc2.com/crates?p=sh_…`. Whoever opens it sees the playlist read-only,
can send its tracks to a deck, and can copy it into their own library — the
copy is theirs, and editing it does not touch yours. `listed=true` also puts it
in this deployment's public directory (SHARED HERE in the console); without it,
the link is the only way in. Revoking is one call and the link dies.

```bash
m crates/playlist_new name="Friday warmup" guest=$KEY
m crates/playlist_add id=pl_… q="four tet baby" guest=$KEY   # searches, adds the playable hit
m crates/playlist_share id=pl_… listed=true guest=$KEY       # → the link
m crates/playlist_feed                                       # what is public here
```

## MCP

```bash
curl -sX POST localhost:50790/api/crates/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
claude mcp add crates -- python3 /root/mod/mod/orbit/crates/mcp.py   # stdio
```

19 tools. Digging is open (`crates_search`, `crates_resolve`, `crates_stream`,
`crates_discover`, `crates_platforms`); owning is attributed — every playlist
tool takes `token` or `guest`, or reads `CRATES_TOKEN` / `CRATES_GUEST` from the
server's environment, and `crates_whoami` says which is in effect.

The one to know is **`crates_playlist_add`**: it takes a phrase, not an id, so
"put Four Tet's Baby in my Friday set" is a single call — it searches the crate
itself and appends the best hit that can actually be decoded onto a deck.
`crates_playlist_reorder` takes the track keys in the order you want and cannot
add or lose a track, which is the safe way to let a model rearrange a set.

## Platforms

| | search | open albums / playlists / artists | preview | **load onto a deck** | keys needed |
|---|---|---|---|---|---|
| **Bandcamp** | yes | yes | Bandcamp embed | **yes** — the site player's own 128k MP3, proxied by the module | none |
| **SoundCloud** | yes | yes (sets, users) | SoundCloud widget | **yes** — progressive MP3, fetched by the browser (CORS-open) | none — the web player's client_id is scraped and cached |
| **YouTube** | yes | yes (playlists, channels) | YouTube embed | **yes** — the audio track yt-dlp resolves, proxied by the module | none — needs `yt-dlp` on the box |
| **Internet Archive** | yes | yes (items, collections) | archive.org player | **yes** — the item's own MP3/OGG, CORS-open | none |
| **Spotify** | with keys | yes | Spotify embed (30s logged out, full logged in) | **no** — DRM | your app's client id + secret (client-credentials grant; no redirect URI) |

Spotify is the one that cannot stream: its audio is DRM-protected and cannot
be routed through Web Audio. So a Spotify find is for planning — preview it,
then **FIND ON BANDCAMP / FIND ON SOUNDCLOUD** in the preview panel searches
the same track where it *can* be loaded.

### Keys

Nothing is committed. Platform credentials are **shared with `orbit/musica`**,
which this module is a fork of: they live in `~/.mod/musica/` (0600), so a
Bandcamp warm-up or a Spotify key set on either console serves both. Only the
playlists are this module's own, in `~/.mod/crates/`.

```
m crates/set_key client_id=… client_secret=…        # Spotify app keys
m crates/set_key soundcloud_client_id=…             # optional: pin a SoundCloud id
```

If **orbit/spotify** already has keys in `~/.mod/spotify/keys.json`, crates
uses them and none are needed here. If orbit/spotify is also *logged in*
(`m spotify/login`), crates can read your own playlists (`/my_playlists`) and
private ones — tokens are read from its `auth.json` and refreshed in memory,
never written back.

### Bandcamp and datacenter IPs

bandcamp.com fronts datacenter addresses with a JavaScript challenge; a plain
request gets a 3 KB "enable JavaScript" page. When that happens the module
clears it once in a headless Chromium (Playwright, if installed) and reuses the
cookies from `~/.mod/musica/bandcamp_cookies.json`. Without a browser the
error says so instead of pretending Bandcamp is empty. `m crates/warm` runs
the warm-up by hand.

## Run

```
m crates/serve            # pm2 `crates.app` on :50790, then register the route
m crates/play             # or: serve in this process and open a browser
m crates/url              # http://localhost:50790/crates
m crates/test             # files + JS syntax + links + a playlist round-trip + engine harness
m crates/kill
```

One process serves both halves of the protocol's URL rule:

- `/crates/*` — the console (prefix kept by the gateway)
- `/api/crates/*` — the API (prefix stripped by the gateway)
- `/crates/api/{fn}` — the API as the console calls it, one relative path

## API

Every function answers `GET` or `POST` with `{result: …}` or `{error: …}`.

| route | what |
|---|---|
| `/search?q=&source=all\|spotify\|bandcamp\|soundcloud\|youtube\|archive&kind=track\|album\|artist\|playlist&limit=` | one platform or all five in parallel (results interleaved; each platform reports its own error). A pasted link resolves instead. |
| `/playlists`, `/playlist_open`, `/playlist_new`, `/playlist_edit`, `/playlist_delete` | your library — credential in `Authorization: Bearer …` or `X-Crates-Guest: …` |
| `/playlist_add`, `/playlist_remove`, `/playlist_move`, `/playlist_set`, `/playlist_reorder` | the tracks in one of yours. `playlist_add?q=…` searches and appends the playable hit |
| `/playlist_share`, `/playlist_copy`, `/playlist_feed` | mint or revoke a link, copy somebody's, and what is public here |
| `/whoami`, `/guest_key` | who your credential makes you, and how to get one |
| `/mcp` (POST), `/tools` | the MCP server, and its tool list as plain JSON |
| `/resolve?url=` | any Spotify / Bandcamp / SoundCloud link (or `spotify:` URI) → what it names, with tracks |
| `/stream?source=&id=[&track=]` | where a track's audio is: `direct:true` + CDN URL for SoundCloud; `direct:false` for Bandcamp (use the proxy) |
| `/stream/bandcamp?id=<page url>[&track=<id>]` | **the MP3 itself**, proxied with Range passthrough — the one non-JSON route |
| `/discover?tag=&slice=top\|new\|rec&size=` | Bandcamp's discover feed |
| `/bandcamp_page?url=` | an album or track page, every track listed with `streamable` |
| `/soundcloud_playlist?id=` · `/soundcloud_user?id=` | a set/album with tracks hydrated · a user's uploads |
| `/track` · `/album` · `/artist` · `/playlist` | Spotify: one track · album tracks · artist top tracks · playlist tracks |
| `/my_playlists` | your Spotify playlists (needs orbit/spotify's login) |
| `/platforms` (alias `/keys`) | what each platform will and won't do here, keys masked |
| `/decks` · `/kit` · `/info` · `/health` | the signal chain · the sequencer's voices · the null call · liveness |

Every row, whatever the source, is one shape:

```
{source, kind, id, name, artists, album, duration_ms, art, url, embed, streamable, …}
```

`streamable` is the honest bit — it is `false` for every Spotify row.

## Console keys

`space` sequencer · `q`/`p` play deck A/B · `a`/`s`/`d` crossfader left/centre/right ·
`1`/`2`/`3` tabs · `/` jump to the crate · click a BPM readout to halve/double it ·
right-click a hot cue to clear it · shift-drag a knob for fine control · double-click a knob to reset.

## Files

```
mod.py          the anchor: every function the CLI, gateway and other modules see
serve.py        static console + API + the /stream proxy, one process for pm2
spotify.py      Spotify: client-credentials search, embeds, orbit/spotify's keys and login
platforms.py    Bandcamp + SoundCloud, keyless: search, pages, discover, streams, link detection
web/            the console: engine.js (decks, mixer), analyze.js (tempo + key),
                synth.js (voices), sequencer.js, crate.js (API client, Camelot),
                ui.js (knobs, canvases), app.js (wiring)
tests/engine.mjs  the engine, the analysers and the crate helpers under node
```
