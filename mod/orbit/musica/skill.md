# musica

Music, as five catalogues and one signal path. Sixteen MCP tools, a REST API
and a browser DJ booth on one port (`:50780`) — all the same code, so an agent,
a shell and a human get the same answers.

API `:50780` (`/api/musica`) · console `/musica` · MCP `POST /mcp` (stdio:
`python3 mcp.py`)

## When to reach for it

- "find me this track" / "what else did this artist put out"
- someone pasted a Spotify, Bandcamp, SoundCloud, YouTube or archive.org link
- you need actual audio bytes to work on — transcribe, fingerprint, analyse,
  sample (`musica_fetch`)
- a URL to hand a player, with the CORS question already answered
  (`musica_stream`)
- browsing for music nobody indexed: Bandcamp's discover feed, a label's
  YouTube channel, the Live Music Archive
- building a set list, or wanting the booth itself (`musica_console`)

Not for: generating music, stems or separation; buying anything; anything that
needs a Spotify *stream*.

## The order that matters

1. **`musica_search`** with `source=all` unless you already know where the
   thing lives. Five platforms run in parallel and the rows interleave; each
   platform reports its own error under `sources`, so an empty list from one is
   never an empty answer.
2. Pasted link → **`musica_resolve`**, not a search. Albums, playlists and
   channels come back with their tracks in the same call.
3. Row → **`musica_stream`** (a URL) or **`musica_fetch`** (bytes on disk).
   Both take the row's `source` + `id` pair, unchanged.
4. Nothing found → try the browse tools: `musica_bandcamp_discover` by tag,
   `musica_youtube_channel` for a label, `musica_archive_collection` for
   `etree` / `netlabels` / `78rpm`.

## Which sources actually play

| source | audio | how |
|---|---|---|
| bandcamp | yes | the site player's 128k MP3, **proxied** (no CORS upstream) |
| soundcloud | yes | progressive MP3, **direct** (CORS-open CDN) |
| youtube | yes | best audio-only format via yt-dlp, m4a first, **proxied** |
| archive | yes | the original file, **direct**, and often public domain / CC |
| spotify | **no** | DRM. Metadata and embeds only |

`direct: false` in a `musica_stream` answer means a browser cannot fetch that
URL — use `proxy_url` (or `proxy_path` joined to whatever host you reached).

## Things that will bite you

- **A Spotify row is a dead end for audio.** When it is the right track, search
  its `artists + name` on the other four; that is exactly what the console's
  FIND ON… buttons do.
- **Signed URLs expire.** YouTube's in hours, Bandcamp's likewise. Call
  `musica_stream` again rather than storing one.
- **YouTube needs yt-dlp installed** (`pip install yt-dlp`). Nothing else in
  the module does. `musica_platforms` says whether it is there, and its
  `last_error` is where a bot check would show up — the fix is browser cookies
  in `~/.mod/musica/youtube_cookies.txt`.
- **Bandcamp fronts datacenter IPs with a JavaScript challenge.** The module
  clears it once in headless Chromium and caches the cookies; if
  `musica_platforms` shows `browser: false` and a `last_error`, Bandcamp is
  blocked here rather than empty.
- **archive.org items are albums, not tracks.** Search returns items; a track
  id is `identifier/filename`, which comes from `musica_archive_item`.
- **Live streams do not load.** `streamable: false` on a YouTube row means it
  is live; the deck decodes whole files.
- **Spotify search is off without keys** — `m musica/set_key client_id=…
  client_secret=…`, or let it read orbit/spotify's. The other four never need
  any.

## Tempo and key

Detection happens in the browser tab, not on the server: load a track in the
console at `/musica` and each deck shows BPM, key and its Camelot code, with
the mixer saying whether the two mix cleanly. The API's `bpm` field is only
what a platform already knew.
