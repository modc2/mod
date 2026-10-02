# news

Ask for a topic, get the news about it — from many outlets, de-duplicated and ranked on your own machine. Also an MCP server, so any agent can do the same.

- **Keyless, local-first**: Python stdlib only. One SQLite cache (`~/.mod/news/cache.db`) and one feed list (`~/.mod/news/feeds.json`). No accounts, no API keys, no model calls.
- **Open sources by default**: [GDELT](https://www.gdeltproject.org/) (global news index, all languages), Hacker News, and your own RSS/Atom feeds (seeded with BBC, Guardian, Al Jazeera, NPR, Ars, The Register). `reddit` and `google` exist but are opt-in.
- **Merged**: the same story from five outlets becomes one result. Its `also` field lists the other outlets, and coverage raises its rank.
- **Explainable ranking**: `0.50·relevance + 0.30·freshness (24h half-life) + 0.15·coverage + 0.05·popularity`. Every result has a `why` line.
- **Degrades, never dies**: each source fails on its own (`sources: {gdelt: {ok:false, error}}`), and an expired cache entry is served when the upstream is down.

```
m news/search "bittensor"                       # works in-process, no server needed
m news/search "EU AI act" k=5 hours=24 sources=gdelt,hn
m news/headlines "openai"                       # one line per story
m news/read https://...                         # readable article text
m news/add_feed https://example.org/rss name=Example
m news/serve                                    # REST + MCP on 127.0.0.1:51120 (pm2 `news`)
```

## MCP

```
claude mcp add --transport http news http://localhost:51120/mcp       # after m news/serve
claude mcp add news -- python3 -m newsagg.mcp_server                  # stdio; run from orbit/news
```

Tools: `news_search`, `news_read`, `news_sources`, `news_feeds`, `news_add_feed`, `news_remove_feed`, `news_status`. The stdio and HTTP transports share one dispatch function (`newsagg/mcp_server.py:handle_message`), so they can't drift apart.

## REST

`GET /search?q=&k=&hours=&sources=` · `GET /read?url=` · `GET /sources` · `GET|POST|DELETE /feeds` · `POST /mcp` — also served under `/news/api/…`. Errors come back as 4xx JSON, because Cloudflare strips the body of any 5xx.

## Layout

```
newsagg/sources.py   adapters: (query, limit) -> [item]; add a source = add a function + a SOURCES row
newsagg/engine.py    fan-out -> time window -> cluster -> score; read() article text
newsagg/cache.py     url -> raw body, TTL, stale fallback
newsagg/store.py     feed list
newsagg/mcp_server.py / server.py   transports
```

Any URL a caller supplies (`read`, `add_feed`) is resolved first and refused if it isn't a public http(s) address. This is the SSRF guard.
