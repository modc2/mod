# crates

A record crate and a DJ booth that remembers what you found. One search box
over **Spotify, Bandcamp, SoundCloud, YouTube and the Internet Archive**;
everything but Spotify loads onto a deck and mixes in the browser. What you pick
can be **kept as a playlist**, and any playlist can be **handed to someone as a
link** they can play from and copy.

Console `/crates` · API `:50790` (`/api/crates`) · MCP `POST /api/crates/mcp`
(19 tools, or stdio `python3 mcp.py`). A fork of `orbit/musica`: same booth and
same platform credentials (`~/.mod/musica/`), plus the playlist library
(`~/.mod/crates/`) and the MCP server.

## When to reach for it

- "find me X" across five music platforms in one call — `crates_search`
- a link (Spotify / Bandcamp / SoundCloud / YouTube / archive.org) → what it
  actually is, with its tracks — `crates_resolve`
- "where is this track's audio" — `crates_stream` (Spotify is DRM and says so)
- **"make me a playlist of…" / "add this to my Friday set" / "reorder it"** —
  the playlist tools below
- **"send this playlist to someone"** — `crates_playlist_share` mints a
  read-only link; `crates_playlist_feed` is what has been shared publicly here

Not for: playing audio (the console does that in a browser tab — an agent can
build the set, not hear it), and not for Spotify streams (DRM, always).

## Identity, before the playlist tools

Playlists are per-owner. Every playlist tool takes **`token`** (a mod-protocol
token — the fleet's shared `m.mod('auth')` identity) or **`guest`** (a key this
module mints), or reads `CRATES_TOKEN` / `CRATES_GUEST` from the server's
environment.

1. `crates_whoami` — is a credential in effect, and whose?
2. If anonymous and the user has no wallet: `crates_guest_key` mints one. **It
   is a password, not a username** — hand it to the user to keep, and pass it on
   every later call. Losing it loses the playlists.
3. Reads of shared links and the public directory need nothing.

## The order that matters

1. **`crates_playlists`** — what the user already keeps. Start here; do not
   create a second "Friday warmup" because you did not look.
2. **`crates_playlist`** — one in full, with the track keys you will need to
   remove or reorder. Also opens somebody else's by `share`.
3. **`crates_playlist_create`** — a name is enough. Adding comes next.
4. **`crates_playlist_add`** — takes `q`, a plain phrase: it searches the crate
   itself and appends the best hit that can actually be decoded onto a deck, so
   one call does what search-then-add does in three. `url` adds exactly what a
   link names; `track`/`tracks` add items you already have in hand. Duplicates
   are ignored, not appended twice.
5. **`crates_playlist_reorder`** — the whole order in one call, by track key.
   Keys the playlist does not have are ignored and tracks you leave out keep
   their place at the end, so this cannot lose a track. Prefer it to a series of
   `crates_playlist_move` calls.
6. **`crates_playlist_share`** — `listed:false` (the default) means the link is
   the only way in; `listed:true` also puts it in this deployment's directory.
   `on:false` revokes. The URL comes back in `url`.

`crates_playlist_copy` takes somebody's `share` id into the user's own library —
the copy is theirs, and editing it does not touch the original.

## Facts worth knowing

- **Spotify never streams.** Its audio is DRM-protected. A Spotify row is for
  planning; if the user wants to *play* it, search the same title with
  `source=bandcamp` or `soundcloud`. `crates_playlist_add?q=` already prefers a
  playable source for exactly this reason.
- **Search fans out and each platform reports its own error.** One source being
  down or unconfigured never empties the results — check `sources` in the reply
  before telling a user something does not exist.
- **Spotify search needs app keys** (`m crates/set_key client_id=… client_secret=…`,
  or `orbit/spotify`'s). The other four need none.
- **Bandcamp from a datacenter IP** hits a JavaScript challenge; the module
  clears it once in headless Chromium and caches the cookies. If Bandcamp
  errors, `m crates/warm` is the fix.
- **A playlist holds at most 500 tracks**, an owner at most 200 playlists.
- Track rows carry everything a deck needs (`source`, `id`, art, duration,
  `streamable`), so a playlist read is enough to load a set — no second call.

## CLI

```bash
m crates/serve                                   # pm2 crates.app :50790 + route
m crates/search q="four tet" source=bandcamp
m crates/guest_key                               # a key to own playlists with
m crates/playlist_new name="Friday warmup" guest=$KEY
m crates/playlist_add id=pl_… q="burial archangel" guest=$KEY
m crates/playlist_share id=pl_… listed=true guest=$KEY
m crates/mcp                                     # the tool list
m crates/test                                    # engine + playlist round-trip
```
