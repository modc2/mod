# docs (core module)

The documentation hub for the mod protocol. Serves the protocol doc pages under
`docs/` and aggregates every module's README/skill, plus the whitepaper.

- **CLI:** `m docs/overview`, `m docs/pages`, `m docs/page cli`, `m docs/modules`,
  `m docs/doc <module>`, `m docs/whitepaper`, `m docs/search <q>`
- **App:** a zero-dependency Node viewer (`app/server.js` + `app/index.html`) that
  renders the markdown at `/docs`. Run via `bash app/start.sh` (or
  `m pm/start docs target=app` to launch it inside the shared nix image).

Doc pages live in `docs/*.md` — edit those; the CLI and app pick them up live.

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
