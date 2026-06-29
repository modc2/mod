# docs (core module)

The documentation hub for the mod protocol. Serves the protocol doc pages under
`docs/` and aggregates every module's README/skill, plus the whitepaper.

- **CLI:** `m docs/overview`, `m docs/pages`, `m docs/page cli`, `m docs/modules`,
  `m docs/doc <module>`, `m docs/whitepaper`, `m docs/search <q>`
- **App:** a zero-dependency Node viewer (`app/server.js` + `app/index.html`) that
  renders the markdown at `/docs`. Run via `bash app/start.sh` (or
  `m pm/start docs target=app` to launch it inside the shared nix image).

Doc pages live in `docs/*.md` — edit those; the CLI and app pick them up live.
