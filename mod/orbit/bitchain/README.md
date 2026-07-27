# bitchain

Mod protocol module.

- **API** — mod-protocol server on `:50260` (`m serve bitchain`), pm2 `bitchain`.
  Public at `modc2.com/api/bitchain` (prefix stripped): `POST /info`, `POST /readme`;
  `forward` requires auth.
- **App** — zero-dep console page (`app/server.py` + `app/index.html`) on `:50261`,
  pm2 `bitchain-app`. Public at `modc2.com/bitchain` (prefix kept — the page links
  and API calls are absolute `/bitchain` / `/api/bitchain` paths). Shows module
  identity, function list, and a live call console against the API.
- **Routing** — opted in via `"route": true` in `config.json`; regenerate with
  `m caddy/apply`.

The module logic itself (`mod.py`) is still a scaffold — `info` and `readme` only.
Add real functions to the `Mod` class and they appear in the console automatically
(the app reads `fns` from `POST /info`).
