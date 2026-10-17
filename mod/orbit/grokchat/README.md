# grokchat

A Next.js chat app and a Python API that connect to `orbit/grokbot`.

grokbot (`:50890`) already owns the hard parts — the xAI upstream, wallet
identity, per-address keys, saved bots. grokchat is a dedicated chat surface
on top of it:

- **`api.py`** (`:50930`, python stdlib only) — proxies `/chat`, `/models`,
  `/me`, `/bots` to grokbot, forwards the caller's `Authorization` and
  `x-xai-key` headers verbatim, passes `stream:true` SSE through
  byte-for-byte, and knocks the activator (`:9000/api/grokbot`) once if
  grokbot is asleep. It stores nothing.
- **`app/`** (`:3930`, Next.js 14, basePath `/grokchat`) — the chat UI:
  model picker, live-search toggle, BYOK key field (kept in the browser's
  localStorage, sent per-request), streaming replies with citations.
  `/grokchat/_api/*` is rewritten to the python API, so the browser only
  ever talks same-origin.

## Run

```sh
cd app && npm install && npm run build   # once
m grokchat/serve                          # pm2: grokchat-api + grokchat-app
m grokchat/status                         # both halves + grokbot reachability
```

Open `http://localhost:3930/grokchat`.

## Keys

BYOK throughout, same as grokbot: paste an `xai-…` key in the app (sent as
`x-xai-key`, stored nowhere server-side), sign in with a wallet so grokbot
uses your saved key, or let grokbot fall back to the operator key. This
module never sees a key at rest.

## Env

| var | default | |
|---|---|---|
| `PORT` | `50930` | python API port |
| `GROKBOT_URL` | `http://localhost:50890` | where grokbot lives |
| `ACTIVATOR_URL` | `http://localhost:9000` | wake knock target |
| `GROKCHAT_API` | `http://localhost:50930` | what the Next rewrite targets |
| `GROKCHAT_TIMEOUT` | `300` | chat proxy timeout (s) |
