---
name: spotify
description: Drive the user's Spotify account — search the catalog, play/pause/skip, move playback between devices, manage the queue, read recently-played and top tracks/artists, and build playlists. Use when asked to play music, find a song, see what's playing, control a speaker, or make a playlist.
type: orbit-module
---

# spotify

An adapter for the Spotify Web API with 22 MCP tools, a CLI and a REST mirror,
all on the caller's own account. Port **50610** — API, console (`/spotify`) and
MCP (`POST /mcp`) share it.

## Start with status

`spotify_status` (or `m spotify/status`) answers the three things every other call
depends on: is the module logged in, which device is active, what is playing.

- `logged_in: false` → the user must run `m spotify/login` and approve in a
  browser. **You cannot do the OAuth consent for them.** Say what to run; don't
  loop on failing calls.
- No device in the list → their Spotify app is closed everywhere. Playback
  commands will fail with `NO_ACTIVE_DEVICE` until one is open.

## Playing something

```
m spotify/play "boards of canada roygbiv"     # phrase → searched, top hit plays
m spotify/play uri=spotify:album:1DFixLWuPkv3
m spotify/queue "sade smooth operator"
m spotify/pause ; m spotify/next ; m spotify/volume 40
```

Any track/album/playlist argument accepts a `spotify:` URI, an
`open.spotify.com` link, a bare id, or free text. Free text is resolved by
search and **the top hit wins** — so phrase it `"artist - title"`, and when the
request is ambiguous (a cover, a remix, a common title), run `spotify_search`
first and confirm the hit before playing it.

`play` with no argument resumes. Album/playlist/artist URIs play as a context
(the whole thing); a track URI plays just that track.

## Devices

```
m spotify/devices                             # ● marks the active one
m spotify/transfer device="Kitchen speaker"   # name, prefix, or id
```

`NO_ACTIVE_DEVICE` is the most common failure. The fix is `spotify_devices`
then `spotify_transfer` — never a retry of the same call.

## Taste and history

`spotify_top` (`type=tracks|artists`, `time_range=short_term` 4 weeks /
`medium_term` ~6 months / `long_term` years) and `spotify_recent` are real
listening data. Ground any "make me a playlist like…" request in them rather
than guessing; Spotify's own `/recommendations` endpoint is deprecated for apps
created after Nov 2024, so build playlists from search + the user's history.

```
m spotify/playlist_create name="dinner" tracks='["khruangbin","sade"]'
m spotify/playlist_add id=<playlist> tracks='["spotify:track:…"]'
```

## Rules of the room

- **Writes are real and instant.** Playback changes what is coming out of the
  user's speakers; playlist edits change their library. Skipping, pausing or
  changing volume unasked is rude — do what was asked, not what would sound
  better.
- **Deleting is not undoable** — `spotify_playlist_edit remove=true` and
  `spotify_saved remove=true` take things out of their library. Confirm first
  unless they named exactly what to remove.
- **Premium gates the transport.** Free accounts get search, library and
  playlists but `PREMIUM_REQUIRED` on play/pause/volume/queue. Report that as
  an account fact, not a bug.
- **429 means stop.** The error carries `retry_after` seconds; wait it out
  instead of retrying in a loop.

## Escape hatch

`spotify_raw path=/browse/new-releases` calls any Web API endpoint with the
same token. Use it when no tool fits; prefer the tools when one does, because
they return flattened objects instead of Spotify's nested JSON.
