# musica

A DJ booth and a pattern studio in one browser tab — with a crate that digs
**Spotify, Bandcamp, SoundCloud, YouTube and the Internet Archive at once**,
and loads anything but Spotify straight onto a deck. The same crate is an
**MCP server**, so an agent can search, resolve and fetch audio with the calls
the console makes.

- **BOOTH** — two decks with waveforms and a detected beat grid, beatmatch
  (SYNC pitches and phase-aligns), three-band EQ, a bipolar filter, beat loops,
  four hot cues, a constant-power crossfader, a tempo-synced echo, a master
  limiter, and record-the-mix to `.webm`. Both decks show their live key on the
  Camelot wheel and the mixer says whether the two mix cleanly.
- **STUDIO** — a step sequencer (16/32/64 steps, swing, accents, eight
  patterns) and a piano roll in the FL Studio idiom, on the same clock as the
  decks. Every voice is synthesised; drop a file on a channel to sample it.
- **CRATE** — one search box for all five platforms, paste-a-link for any
  Spotify/Bandcamp/SoundCloud/YouTube/archive.org URL, the platform's own
  player for previews, a Bandcamp discover feed by tag, and a decoded-audio
  library. Everything you press + on lands in **MY SET** — the rail across the
  top of the crate, and across the top of the booth as UP NEXT: numbered,
  draggable, one click from either deck, each key lit against what is playing.
- **MCP** — sixteen `musica_*` tools at `POST /mcp` (and `python3 mcp.py` over
  stdio): the same search, the same streams, plus `musica_fetch` to put a
  track's audio on disk for an agent to work on.

All audio work is client-side. Files and platform streams are decoded and
mixed through Web Audio in the tab; tempo and key are detected locally. The
module's own jobs are to serve the console, answer the five platforms, proxy
the two streams that lack a CORS header, and speak MCP.

## Platforms

| | search | open albums / playlists / artists | preview | **load onto a deck** | keys needed |
|---|---|---|---|---|---|
| **Bandcamp** | yes | yes | Bandcamp embed | **yes** — the site player's own 128k MP3, proxied by the module | none |
| **SoundCloud** | yes | yes (sets, users) | SoundCloud widget | **yes** — progressive MP3, fetched by the browser (CORS-open) | none — the web player's client_id is scraped and cached |
| **YouTube** | yes (videos, playlists, channels) | yes (playlists, channels) | YouTube embed | **yes** — the best audio-only format (m4a first: every browser decodes AAC), proxied by the module | none — needs `yt-dlp` installed |
| **Internet Archive** | yes (items) | yes (an item's files) | archive.org player | **yes** — the original file, fetched by the browser (CORS-open) | none |
| **Spotify** | with keys | yes | Spotify embed (30s logged out, full logged in) | **no** — DRM | your app's client id + secret (client-credentials grant; no redirect URI) |

Spotify is the one that cannot stream: its audio is DRM-protected and cannot
be routed through Web Audio. So a Spotify find is for planning — preview it,
then **FIND ON BANDCAMP / SOUNDCLOUD / YOUTUBE** in the preview panel searches
the same track where it *can* be loaded.

### YouTube

`yt-dlp` does the extraction (`pip install yt-dlp`); nothing else in the module
depends on it, and `/platforms` says so plainly when it is missing. Search
covers videos, playlists and channels; a watch, shorts, `youtu.be`, playlist or
channel link all resolve. googlevideo sends **no** `Access-Control-Allow-Origin`
header, so audio comes through `/stream/youtube?id=…` with Range passthrough —
the same route Bandcamp uses. Signed URLs are cached until a minute before they
expire, so the second load of a track is instant. Live streams are refused
rather than half-decoded. If YouTube starts asking this IP to prove it is not a
bot, export browser cookies (Netscape format) to
`~/.mod/musica/youtube_cookies.txt` and they are picked up automatically.

### Internet Archive

No key, no challenge, `Access-Control-Allow-Origin: *`, and a lot of it is
public domain or Creative Commons — the freest audio here. Search covers
`mediatype:audio`; an item is an album and its files are the tracks, addressed
as `identifier/filename`. Where the same music sits in the item as MP3, Ogg and
FLAC, the friendliest format per track is what comes back. `archive_collection`
browses one corner of it: `etree` (the Live Music Archive — tens of thousands
of band-sanctioned concert recordings), `netlabels`, `78rpm`.

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
m musica/test             # files + JS syntax + link detection + MCP schema + engine harness
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
| `/search?q=&source=all\|spotify\|bandcamp\|soundcloud\|youtube\|archive&kind=track\|album\|artist\|playlist&limit=` | one platform or all five in parallel (results interleaved; each platform reports its own error). A pasted link resolves instead. |
| `/resolve?url=` | any Spotify / Bandcamp / SoundCloud / YouTube / archive.org link (or `spotify:` URI) → what it names, with tracks |
| `/stream?source=&id=[&track=]` | where a track's audio is: `direct:true` + a CORS-open URL for SoundCloud and archive.org; `direct:false` for Bandcamp and YouTube (use the proxy) |
| `/stream/<source>?id=…` | **the audio itself**, proxied with Range passthrough — the only non-JSON route |
| `/youtube_video?id=` · `/youtube_playlist?id=` · `/youtube_channel?id=` | one video's metadata · a playlist's videos · a channel's uploads |
| `/archive_item?id=` · `/archive_collection?id=&q=` | an item with its tracks · one collection, e.g. `etree` |
| `/discover?tag=&slice=top\|new\|rec&size=` | Bandcamp's discover feed |
| `/bandcamp_page?url=` | an album or track page, every track listed with `streamable` |
| `/soundcloud_playlist?id=` · `/soundcloud_user?id=` | a set/album with tracks hydrated · a user's uploads |
| `/track` · `/album` · `/artist` · `/playlist` | Spotify: one track · album tracks · artist top tracks · playlist tracks |
| `/my_playlists` | your Spotify playlists (needs orbit/spotify's login) |
| `/platforms` (alias `/keys`) | what each platform will and won't do here, keys masked |
| `/tools` · `/mcp` | the MCP tool list · the MCP server itself (`POST` JSON-RPC; `GET` lists tools) |
| `/decks` · `/kit` · `/info` · `/health` | the signal chain · the sequencer's voices · the null call · liveness |

Every row, whatever the source, is one shape:

```
{source, kind, id, name, artists, album, duration_ms, art, url, embed, streamable, …}
```

`streamable` is the honest bit — it is `false` for every Spotify row, and for
a YouTube live stream.

## MCP

```
POST http://localhost:50780/musica/mcp        # Streamable HTTP, JSON-RPC 2.0
python3 mcp.py                                # the same tools over stdio
m musica/tools                                # the list, from the CLI
m musica/mcp name=musica_search arguments='{"q":"four tet","source":"youtube"}'
```

| tool | what |
|---|---|
| `musica_search` | every platform at once, interleaved; `source=` narrows it |
| `musica_resolve` | any pasted link → what it names, with tracks |
| `musica_stream` | a track's audio URL, with `direct` and a `proxy_url` that always works |
| `musica_fetch` | download the audio to `~/.mod/musica/audio` and return the path |
| `musica_youtube_video` · `_playlist` · `_channel` | one video · a playlist's videos · a channel's uploads |
| `musica_bandcamp_page` · `_discover` | one album/track page · the discover feed for a tag |
| `musica_soundcloud_playlist` · `_user` | a set with tracks hydrated · a user's uploads |
| `musica_archive_item` · `_collection` | one item's tracks · one collection |
| `musica_spotify` | one Spotify track/album/artist/playlist (metadata only) |
| `musica_platforms` · `musica_console` | what each platform will do here · the booth and its signal chain |

No auth: every tool is a public read, and the one that writes (`musica_fetch`)
writes only into `~/.mod/musica/audio`.

## Console keys

`space` sequencer · `q`/`p` play deck A/B · `a`/`s`/`d` crossfader left/centre/right ·
`1`/`2`/`3` tabs · `/` jump to the crate · click a BPM readout to halve/double it ·
right-click a hot cue to clear it · shift-drag a knob for fine control · double-click a knob to reset.

## Files

```
mod.py          the anchor: every function the CLI, gateway and other modules see
serve.py        static console + API + the /stream proxy, one process for pm2
spotify.py      Spotify: client-credentials search, embeds, orbit/spotify's keys and login
platforms.py    Bandcamp, SoundCloud, YouTube and the Internet Archive, keyless:
                search, pages, discover, streams, link detection
mcp.py          the MCP server: sixteen musica_* tools over HTTP and stdio
web/            the console: engine.js (decks, mixer), analyze.js (tempo + key),
                synth.js (voices), sequencer.js, crate.js (API client, Camelot),
                ui.js (knobs, canvases), app.js (wiring)
tests/engine.mjs  the engine, the analysers and the crate helpers under node
```
