# artist

A drag-and-drop studio that compiles clips and music into one film —
local-first, no cloud, no accounts.

## How it works

1. **Drop files in.** Video, music, voice, images — drag them onto the
   LIBRARY rail (or click to pick). Every asset is stored content-hashed in
   `~/.mod/artist/assets/`, so the same file never lands twice.
2. **Cut on the timeline.** Two lanes: VIDEO (clips and stills, played in
   order) and AUDIO (music and voice, played in order underneath). Drag from
   the library onto a lane, drag within a lane to reorder, click the `s`
   badge on a still to set how long it holds.
3. **Compile.** EXPORT plays the cut once and records it **in the browser
   itself** — canvas + WebAudio + MediaRecorder → one `.webm` download. No
   server-side codecs required, nothing leaves the machine. If ffmpeg is
   installed on the host, a RENDER button appears and compiles the saved
   project server-side into an mp4 (`POST /render`).
4. **Share.** `GET /export_pack?id=` hands the whole project out as plain
   JSON — timeline plus assets inlined — and `POST /import_pack` merges it
   into another library. Content-hash ids mean no duplicate bytes, ever.

## Where AI fits

The Veo / Lyria / ElevenLabs / Kling class of engines are *sources*, not
dependencies: anything — a cloud model, a local model, a phone camera — that
produces a media file drops into the library like any other clip. The
`vidz` module (AI video via x402) and `voice` (local ASR) on this fleet pair
naturally: generate there, compile here.

## Surfaces

```
m artist/serve                         # studio + API on :51140
http://localhost:51140/artist/         # the console
```

| route | what |
| --- | --- |
| `GET /assets` | the library |
| `POST /upload?name=f.mp4` | raw bytes in → content-hashed asset |
| `GET /artist/media/{id}` | asset bytes, Range-aware (players can seek) |
| `GET/POST /projects` `save_project` `project` `remove_project` | cuts |
| `POST /render` | server-side mp4 compile (needs ffmpeg) |
| `GET /export_pack` / `POST /import_pack` | whole-project sharing |

State lives in `~/.mod/artist/` (override with `ARTIST_DIR`). Deletes
(`remove_asset`) answer loopback callers only: anyone you share the studio
with can cut and save; only this box can destroy.

## Design notes

- **Local-first on purpose.** The browser is the render farm. ffmpeg is an
  upgrade, not a requirement — the module detects it at read time
  (`health.ffmpeg`) and the console shows RENDER only when it is true.
- **Timelines are data.** `{video: [{asset, dur?}], audio: [{asset}]}` —
  plain JSON, so other modules (or an agent) can compose cuts
  programmatically and POST them to `save_project`.
- Stdlib only. One SQLite file, one assets directory, no deps.

## Tests

```
m artist/test        # pytest, offline, in a throwaway studio
```
