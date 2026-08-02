# docs (core module)

The documentation hub for the mod protocol. Serves the protocol doc pages under
`docs/` plus the whitepaper.

- **CLI:** `m docs/overview`, `m docs/pages`, `m docs/page cli`, `m docs/modules`,
  `m docs/doc <module>`, `m docs/whitepaper`, `m docs/search <q>`, `m docs/mcp`
- **App:** a zero-dependency Node viewer (`app/server.js` + `app/index.html`) that
  renders the markdown at `/docs`. Run via `bash app/start.sh` (or
  `m pm/start docs target=app` to launch it inside the shared nix image).
- **MCP:** the same functions as agent tools — see below.
- **Skill:** [`skill.md`](skill.md) is the one-page brief for agents.

Doc pages live in `docs/*.md` — edit those; the CLI, app and MCP tools pick them
up live.

## Dependency: hub

The per-module catalog ("what does each module do") is its own module,
[`hub`](../hub) — listed in `deps` and reached at runtime with `m.mod('hub')`.
`m docs/modules`, `m docs/doc <module>` and the module half of `m docs/search`
are thin re-exports of it, so the doc CLI stays one place while the catalog
logic lives in one module. For the catalog on its own, use `m hub/modules`.

## MCP server

`api/mcp.py` serves the module as MCP tools — `docs_overview`, `docs_pages`,
`docs_page`, `docs_search`, `docs_whitepaper`, `docs_modules`,
`docs_module_doc` — so an agent reads the protocol docs without scraping the
app. Each tool is a thin wrap of the `mod.py` function of the same name, so the
CLI, the app and the tools can't drift. Read-only, no auth.

- **Streamable HTTP:** `POST /docs/mcp` on the app port (the app proxies to the
  MCP server on `:50192`), so the public docs URL is the public MCP URL:
  `claude mcp add --transport http docs http://localhost:50191/docs/mcp`
- **stdio:** `python3 api/mcp.py` —
  `claude mcp add docs -- python3 ~/mod/mod/core/docs/api/mcp.py`
- **Run it:** `m pm/start docs target=api` (pm2 `docs.api`) or `bash api/start.sh`.
  Without it, `POST /docs/mcp` answers 503 with a JSON-RPC error saying so.
- **Tests:** `pytest core/docs/test` (protocol handshake, every tool, error shapes).

## Human / Engineer mode

Every page `docs/<name>.md` may have a plain-language twin `docs/simple/<name>.md`.
The app shows one site-wide HUMAN / ENGINEER switch (top of the sidebar); each
reader's choice is remembered in their own browser (localStorage `docs.mode`,
default HUMAN). Pages without a twin fall back to the technical version with a
subtle badge.

- **Serving:** `GET /_page/<name>?v=simple` returns the twin when it exists
  (`x-docs-variant` header says which flavor you got); `/_pages` returns
  `[{name, simple}]`.
- **CLI:** `m docs/page keys simple=true`, `m docs/simple_pages`,
  `m docs/whitepaper simple`.
- **Writing a twin:** same `# Title` as the technical page, plain English, no
  jargon, one analogy per big idea, ~40–80 lines, close with
  *Want the details? Flip to Engineer mode.*
