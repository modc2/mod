# github — semantic repo search, no API key

Ask GitHub a **question** instead of a keyword:

```bash
m github/search "a library that runs untrusted wasm in a sandbox"
m github/search "vector db written in rust" language=rust stars=100 n=10
m github/similar tokio-rs/tokio
```

No token, no OAuth, no account. Logging in is optional and only buys a higher
rate limit — see [Login](#login-optional-and-delegated).

## Why this isn't just GitHub search

GitHub's search matches words. If the repo you want calls itself a "capability
based module isolation runtime" and you asked for "sandbox untrusted code", it
never appears. This module puts two stages around that keyword search:

```
question ──▶ EXPAND ──▶ RETRIEVE ──▶ RANK ──▶ results
             lexicon    public API   local
             (+agent)   (keyless)    embeddings
```

1. **Expand** — the question is tokenized, stopwords dropped, and a small
   hand-checked lexicon turns concepts into extra queries and `topic:` filters.
   `m github/expand "<question>"` shows exactly what will be asked, so the
   retrieval stage is inspectable rather than magic. With the `agent` module up,
   `agent=1` lets it write the queries instead.
2. **Retrieve** — every expanded query hits `api.github.com/search/repositories`
   unauthenticated (10 req/min) and the hits are unioned into one candidate
   pool. A rate-limited query doesn't sink the search; it's reported in
   `errors` and the rest still rank.
3. **Rank** — candidates are scored against the *original* question by a local
   sentence-transformers model (`all-MiniLM-L6-v2`, CPU, weights downloaded
   once from Hugging Face — still no key). READMEs for the top candidates come
   from `raw.githubusercontent.com`, which is unauthenticated **and outside the
   API rate limiter**, so the ranker can afford real text.

```
score = 0.75·semantic + 0.15·topic-overlap + 0.10·popularity
```

The priors are deliberately small: they break ties between repos that are
equally on-topic. They must never float a famous irrelevant repo above an
obscure exact match — that is the failure mode being fixed. Archived repos are
scored ×0.85: still findable, just not preferred.

`explain=1` returns the breakdown per repo:

```bash
m github/search "p2p file sync" explain=1
```

### If there is no model

With no embedding model available (offline, no weights cached, `dense=0`) the
ranker falls back to TF-IDF cosine over the same corpus, with idf computed on
the candidate pool itself — small and topically tight, so rare discriminating
words score high exactly where they should. Expansion is what carries the
meaning in that mode, so results degrade rather than break. `ranker` in every
response says which one actually ran.

## Login (optional, and delegated)

Anonymous limits: **10 searches/min**, 60 API calls/hour. Connecting an account
raises that to 30/min and 5000/hour, and lets you search private repos.

This module does **not** implement a second GitHub login. The `git` module
already binds a GitHub account to a mod key, stored off-chain in `~/.mod/git`
(0600) — one identity per key, one place it lives:

```bash
m github/oauth                  # → m git/oauth (device flow)
m github/connect <pat>          # → m git/connect (personal access token)
m github/github                 # who is connected
m github/rate                   # what it bought you
```

Mod-protocol identity is the shared **auth** module. Reads (`search`, `similar`,
`repo`, `readme`, `trending`, `rate`) are open to everyone. Only cache and ACL
management are gated, by `m.mod('auth')` signed tokens — the same scheme, and
the same owner-of-record, as `git`:

```bash
m github/token                  # mint a Bearer token
m github/grant 0xADDR role=write
m github/access                 # who can do what
```

Send it as `Authorization: Bearer <token>`. A read that carries a token runs
against *that key's* GitHub connection; a read without one runs anonymously.
`GITHUB_ACCESS_OPEN=1` bypasses the ACL for local development.

## App + API

One zero-dependency server on **:50520** serves the console (`/`) and the JSON
API (`/api/*`), tolerating the gateway prefix either way, so caddy auto-routes
`modc2.com/github` (app) and `modc2.com/api/github` (API) from `config.json`
(`route: true`).

```bash
m github/serve      # background, :50520
m github/worker     # …under pm2 instead
m github/kill
```

```
GET  /api/search?query=…&n=20&language=rust&stars=100&explain=1
GET  /api/similar?repo=owner/name
GET  /api/expand?query=…
GET  /api/repo?repo=owner/name
GET  /api/readme?repo=owner/name&n=4000
GET  /api/trending?language=python&days=7
GET  /api/rate  /api/cache  /api/access  /api/whoami  /api/info
POST /api/clear_cache  /api/connect  /api/oauth        (auth: write)
POST /api/grant  /api/revoke                           (auth: admin)
```

## Cache

Searches are cached 15 minutes and READMEs 24 hours in `~/.mod/github/cache.json`,
bounded to 400 entries (oldest evicted). At 10 requests/minute anonymous, this
is what makes iterating on a query practical rather than a rate-limit game.

```bash
m github/cache                  # what's warm
m github/search "…" fresh=1     # skip it
m github/clear_cache            # (auth: write)
```

## Everything else

```bash
m github                                    # info
m github/repo huggingface/transformers      # one repo, keyless
m github/readme torvalds/linux n=2000
m github/trending language=python days=7
m github/candidates "…"                     # stage 2 only, unranked
```

## Related modules

| module | does |
| --- | --- |
| `git` | tracks repos, commits, pushes — and owns the GitHub login this module borrows |
| `agent` | optional query rewriter (`agent=1`) |
| `embedcode` | semantic search *inside* code you already have, rather than across GitHub |
