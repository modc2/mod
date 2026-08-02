---
name: docs
description: The mod protocol's documentation hub — read the protocol doc pages (technical or plain-language), the whitepaper, and the module catalog, from Python, the CLI, HTTP, the /docs web app, or as MCP tools any agent can call.
type: core-module
---

# docs

One place to answer "what is mod, and how do I use it". It serves the protocol
doc pages shipped under `docs/` plus the whitepaper; the per-module catalog
("what does each module do") comes from its dependency, [`hub`](../hub), and is
re-exported here so readers only need one door.

Every page has two flavors: the technical text (`docs/<name>.md`, ENGINEER) and
an optional plain-language twin (`docs/simple/<name>.md`, HUMAN). Readers pick
one with a site-wide switch; callers pick one per request.

Ports: app **:50191** (zero-dep node viewer at `/docs`) · MCP **:50192**
(also proxied at `/docs/mcp`, so the public docs URL is the public MCP URL).

## Capabilities

- **Protocol doc pages** — `README` (overview), `getting-started`, `protocol`,
  `cli`, `api`, `modules`, `orbit`, `servers`, `storage`, `keys`, `skills`,
  `contracts`, `frontend`, `utils`, `whitepaper`.
- **Two reading levels** — every page may ship a plain-language twin; requests
  report which variant they got (`tech` / `simple`) rather than silently
  substituting.
- **Whitepaper** in markdown, plain language, or LaTeX source.
- **Module catalog** — every module in `orbit/` + `core/` with its group,
  description and shipped docs; and any one module's README + skill (via `hub`).
- **Search** across page bodies and module descriptions in one call.
- **MCP server** — the same functions as tools for agents (stdio or HTTP).
- **Web app** — a no-build, no-deps viewer that renders the markdown live.

## Usage

### Python
```python
import mod as m
docs = m.mod('docs')()

docs.overview()                     # the front-door page
docs.pages()                        # ['README', 'api', 'cli', ...]
docs.simple_pages()                 # pages that have a plain-language twin
docs.page('cli')                    # technical text
docs.page('cli', simple=True)       # plain-language twin (falls back to tech)
docs.whitepaper('simple')           # 'md' (default) | 'simple' | 'tex'
docs.search('storage')              # {'pages': [...], 'modules': [...]}
docs.modules('core')                # catalog (via hub)
docs.doc('chain')                   # one module's description + README + skill
docs.mcp()                          # how to connect an agent to the tools
```

### CLI
```bash
m docs/overview
m docs/pages
m docs/page cli
m docs/page keys simple=true
m docs/whitepaper simple
m docs/search auth
m docs/modules group=orbit
m docs/doc claude
m docs/mcp
```

### HTTP (app, :50191)
```bash
curl localhost:50191/docs/_pages              # [{name, simple}]
curl localhost:50191/docs/_page/cli           # markdown
curl 'localhost:50191/docs/_page/cli?v=simple'  # twin; x-docs-variant says which
curl localhost:50191/docs/health
```

### MCP

Streamable HTTP (one JSON-RPC message per POST, no SSE), or stdio for clients
that spawn a process. Read-only and unauthenticated — the docs are public.

```bash
# Claude Code — HTTP (server already running) or stdio (self-contained)
claude mcp add --transport http docs http://localhost:50191/docs/mcp
claude mcp add docs -- python3 ~/mod/mod/core/docs/api/mcp.py

# by hand
curl -X POST localhost:50191/docs/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
python3 api/mcp.py                    # stdio
python3 api/mcp.py --http --port 50192  # HTTP directly, skipping the app proxy
m pm/start docs target=api            # run it under pm2 (docs.api)
```

| Tool | Arguments | Returns |
|---|---|---|
| `docs_overview` | — | the front-door page |
| `docs_pages` | — | `{pages: [{name, simple}]}` |
| `docs_page` | `name`*, `simple` | `{page, variant, text}` |
| `docs_search` | `query`* | `{pages, modules}` |
| `docs_whitepaper` | `fmt` (`md`\|`simple`\|`tex`) | `{fmt, text}` |
| `docs_modules` | `group` (`orbit`\|`core`\|`all`) | `{modules: [{name, group, description, readme, skill}]}` |
| `docs_module_doc` | `module`* | `{module, description, readme, skill}` |

\* required. Bad arguments and missing pages come back as MCP tool results with
`isError: true` and a message the calling model can act on — not as transport
errors.

## Layout

| Path | What |
|---|---|
| `docs/*.md` | the technical doc pages — edit these; CLI, app and MCP pick them up live |
| `docs/simple/*.md` | plain-language twins, same `# Title`, ~40–80 lines |
| `docs/whitepaper.md` · `.tex` | the whitepaper |
| `mod.py` | the module: pages, search, whitepaper, catalog re-exports, `mcp()` |
| `api/mcp.py` · `api/start.sh` | the MCP server (stdio + Streamable HTTP) |
| `app/server.js` · `app/index.html` | zero-dep viewer; proxies `POST /docs/mcp` |
| `test/test_mcp.py` | protocol + tool tests (`pytest core/docs/test`) |

## Writing docs

- A new page is a new `docs/<name>.md` — nothing to register.
- A twin is `docs/simple/<name>.md` with the same `# Title`: plain English, no
  jargon, one analogy per big idea, closing with *Want the details? Flip to
  Engineer mode.*
- Module-level docs (README.md / skill.md) belong to the module, not here —
  `docs_module_doc` reads them where they live.

## Environment

All optional: `PORT` / `APP_PORT` (app, default 50191), `BASE_PATH` (default
`/docs`), `MCP_PORT` (default 50192), `MOD_REPO` (repo root the catalog walks,
read by `hub`).
