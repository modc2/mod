# artlist

Search [Artlist.io](https://artlist.io)'s royalty-free catalog — music, sound effects, stock footage, templates — from the CLI, REST or MCP. Not to be confused with `orbit/artist` (the media studio).

- **Keyless for search & previews**: talks to Artlist's public search GraphQL (`search-api.artlist.io/v1/graphql`) with browser-shaped headers (required — their edge 403s bare clients). Python stdlib only. One SQLite TTL cache at `~/.mod/artlist/cache.db`.
- **Previews are real audio**: every song result carries a `preview_url` (AAC on `cms-public-artifacts.artlist.io`); `m artlist/preview <id>` downloads it. `fetch_preview` refuses non-artlist hosts (SSRF guard).
- **Licensed downloads are yours, not ours**: a paid Artlist account's bearer token goes in `~/.mod/artlist/token` (or `$ARTLIST_TOKEN`) — never in config.json or anything published. `gq(query, auth=True)` attaches it.
- **Schema by error-leakage**: Artlist disables GraphQL introspection, but Apollo's validation errors name missing arguments, their types, and "did you mean" field suggestions. Every wrapped query in `artlistapi/api.py` was verified live on 2026-10-05. To extend coverage, send a guess through `gq()` and read the error.

```
m artlist/music "epic cinematic"              # {total, results:[{id,name,artist,duration,preview_url,tags,page_url}]}
m artlist/music "lofi study" k=5 page=2
m artlist/song 89458                          # full record(s) by id
m artlist/preview 89458                       # AAC -> ~/.mod/artlist/previews/89458.aac
m artlist/gq '{ songList(page:1, songSortType:1, take:3, vocalMenuId:0, searchTerm:"jazz"){ totalResults } }'
m artlist/serve                               # REST + MCP on 127.0.0.1:51190 (pm2 `artlist`)
```

## Coverage

All verified against the live API (2026-10-05):

| fn | upstream query | returns |
|---|---|---|
| `music` | `songList` | songs + AAC `preview_url`, tags, totals |
| `sfx` | `sfxList` (sort: `NEWEST`/`TOP_DOWNLOADS`/`STAFF_PICKS`) | effects + AAC `preview_url`, categories |
| `footage` | `clipList` (Artgrid) | clips + thumbnail, duration, story, tags |
| `templates` | `templatesList` | AE/Premiere/FCP/Resolve templates + HLS preview |
| `voices` | `voices` (Cartesia) | AI voiceover voices; per-accent `preview_url` |
| `song` / `album` / `artist` / `clip` / `story` | by-id lookups | full records |

Not wrapped: `maFootage` (validates but its resolver 500s on every execution — upstream Motion Array source is dead; `clipList` covers footage instead), `samples` (valid but returns `[]`), `sfxs(ids)`, `collections`, `lutFilters` — all reachable via `gq()`.

## MCP

```
claude mcp add --transport http artlist http://localhost:51190/mcp     # after m artlist/serve
claude mcp add artlist -- python3 -m artlistapi.mcp_server             # stdio; run from orbit/artlist
```

Tools: `artlist_music`, `artlist_sfx`, `artlist_footage`, `artlist_templates`, `artlist_voices`, `artlist_song`, `artlist_gq`, `artlist_status`. Both transports share one dispatch (`artlistapi/mcp_server.py:handle_message`), so they can't drift apart.

## REST

`GET /music?q=&k=&page=` · `GET /sfx?q=` · `GET /footage?q=` · `GET /templates?q=` · `GET /voices` · `GET /song?ids=1,2` · `POST /gq {query,variables}` · `POST /mcp` — also served under `/artlist/api/…`. Errors come back as 4xx JSON, because Cloudflare strips the body of any 5xx.

## Layout

```
artlistapi/client.py   transport: headers, TTL cache, token, preview downloader
artlistapi/api.py      verified GraphQL documents + flat-dict normalization
artlistapi/mcp_server.py / server.py   transports
```

ToS note: this reads the same public API the artlist.io web app uses, at human rates (cached 15 min). Anything behind a license — full downloads, account data — requires your own paid account token and stays between you and Artlist.
