# musica

A DJ booth and a pattern studio in one browser tab — with a crate that digs
**Spotify, Bandcamp and SoundCloud at once**, and loads Bandcamp and SoundCloud
tracks straight onto a deck.

- **BOOTH** — two decks with waveforms and a detected beat grid, beatmatch
  (SYNC pitches and phase-aligns), three-band EQ, a bipolar filter, beat loops,
  four hot cues, a constant-power crossfader, a tempo-synced echo, a master
  limiter, and record-the-mix to `.webm`. Both decks show their live key on the
  Camelot wheel and the mixer says whether the two mix cleanly.
- **STUDIO** — a step sequencer (16/32/64 steps, swing, accents, eight
  patterns) and a piano roll in the FL Studio idiom, on the same clock as the
  decks. Every voice is synthesised; drop a file on a channel to sample it.
- **CRATE** — one search box for all three platforms, paste-a-link for any
  Spotify/Bandcamp/SoundCloud URL, the platform's own player for previews, a
  Bandcamp discover feed by tag, a decoded-audio library, and a set list whose
  keys light up against whatever is playing.

All audio work is client-side. Files and platform streams are decoded and
mixed through Web Audio in the tab; tempo and key are detected locally. The
module's own jobs are to serve the console, answer the three platforms, and
proxy the one stream that lacks a CORS header.

## Platforms

| | search | open albums / playlists / artists | preview | **load onto a deck** | keys needed |
|---|---|---|---|---|---|
| **Bandcamp** | yes | yes | Bandcamp embed | **yes** — the site player's own 128k MP3, proxied by the module | none |
| **SoundCloud** | yes | yes (sets, users) | SoundCloud widget | **yes** — progressive MP3, fetched by the browser (CORS-open) | none — the web player's client_id is scraped and cached |
| **Spotify** | with keys | yes | Spotify embed (30s logged out, full logged in) | **no** — DRM | your app's client id + secret (client-credentials grant; no redirect URI) |

Spotify is the one that cannot stream: its audio is DRM-protected and cannot
be routed through Web Audio. So a Spotify find is for planning — preview it,
then **FIND ON BANDCAMP / FIND ON SOUNDCLOUD** in the preview panel searches
the same track where it *can* be loaded.

### Keys

Nothing is committed. `~/.mod/musica/keys.json` (0600) holds what you set:

```
m musica/set_key client_id=… client_secret=…        # Spotify app keys
m musica/set_key soundcloud_client_id=…             # optional: pin a SoundCloud id
```

If **orbit/spotify** already has keys in `~/.mod/spotify/keys.json`, musica
uses them and none are needed here. If orbit/spotify is also *logged in*
(`m spotify/login`), musica can read your own playlists (`/my_playlists`) and
private ones — tokens are read from its `auth.json` and refreshed in memory,
never written back.

### Bandcamp and datacenter IPs

bandcamp.com fronts datacenter addresses with a JavaScript challenge; a plain
request gets a 3 KB "enable JavaScript" page. When that happens the module
clears it once in a headless Chromium (Playwright, if installed) and reuses the
cookies from `~/.mod/musica/bandcamp_cookies.json`. Without a browser the
error says so instead of pretending Bandcamp is empty. `m musica/warm` runs
the warm-up by hand.

## Run

```
m musica/serve            # pm2 `musica.app` on :50780, then register the route
m musica/play             # or: serve in this process and open a browser
m musica/url              # http://localhost:50780/musica
m musica/test             # files + JS syntax + link detection + engine harness
m musica/kill
```

One process serves both halves of the protocol's URL rule:

- `/musica/*` — the console (prefix kept by the gateway)
- `/api/musica/*` — the API (prefix stripped by the gateway)
- `/musica/api/{fn}` — the API as the console calls it, one relative path

## API

Every function answers `GET` or `POST` with `{result: …}` or `{error: …}`.

| route | what |
|---|---|
| `/search?q=&source=all\|spotify\|bandcamp\|soundcloud&kind=track\|album\|artist\|playlist&limit=` | one platform or all three in parallel (results interleaved; each platform reports its own error). A pasted link resolves instead. |
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
