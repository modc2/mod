# model — unified BYOK gateway

Single app that fronts every provider under `model/` (openrouter, grok→chutes/targon/venice).
Bring-your-own-key — the gateway never reads stored keys; every request must carry one.

## Capabilities

- `serve()` boots a FastAPI gateway + single-page chat UI on port `50110`.
- `/providers`, `/models`, `/chat` (SSE streaming) — uniform OpenAI-compatible shape.
- Per-provider key isolation in the browser (`localStorage`, scoped per provider).
- CLI helpers: `m model/providers`, `m model/models provider=…`, `m model/forward …`.

## Usage

### Python

```python
import mod as m
gw = m.mod('model')()
gw.serve()                        # boots http://localhost:50110
gw.providers()                    # registry
gw.models(provider='openrouter', api_key='sk-or-…')

# One-shot completion (BYOK)
gw.forward('hello', provider='grok-venice', api_key='…')
gw.forward('hello', provider='grok-chutes', model='xai/grok-2-1212', api_key='…')
```

### CLI

```sh
m model/serve                              # background, logs to /tmp/model/api.log
m model/status
m model/kill
m model/providers
m model/models provider=grok-chutes api_key=$CHUTES_API_KEY
m model/forward 'hello' provider=openrouter api_key=$OPENROUTER_API_KEY
```

### Web UI

Open <http://localhost:50110>. Pick a provider, click **SET** to enter a key
(stored only in your browser's `localStorage`), select a model, chat. Keys
are sent only on requests that use them; they never touch the server's disk.

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Chat UI |
| GET | `/health` | `{ok, providers}` |
| GET | `/providers` | List of `{id,label,url,default_model,key_hint,docs}` |
| GET | `/models?provider=ID` | Requires `X-API-Key` |
| POST | `/chat` | SSE stream when `stream:true`. Requires `X-API-Key`. |

`X-API-Key` (or `Authorization: Bearer …`) carries the BYOK secret. The
server forwards it straight to the upstream provider for that one request.

## Structure

```
model/
├── mod.py            # anchor: serve/kill/status/providers/models/forward
├── config.json       # port (50110), urls, fns
├── skill.md          # this file
├── app/
│   ├── server.py     # FastAPI gateway (BYOK)
│   └── index.html    # single-page chat UI
├── openrouter/openrouter/model.py   # OpenRouter client
├── grok/grok/model.py               # Grok client (chutes/targon/venice)
└── hf/model.py                      # HuggingFace transformer wrapper
```

## Env vars (CLI-only fallback)

The web UI never reads env vars. CLI/Python `forward()` and `models()` will
fall back to these if `api_key=` isn't supplied:

| Provider | Env var |
|----------|---------|
| `openrouter` | `OPENROUTER_API_KEY` |
| `grok-chutes` | `CHUTES_API_KEY` |
| `grok-targon` | `TARGON_API_KEY` |
| `grok-venice` | `VENICE_API_KEY` |

## Mod protocol

- Logs: `/tmp/model/api.log`
- PID: `/tmp/model/api.pid`
- Port: `50110` (configurable via `MODEL_GATEWAY_PORT` env or `serve(port=…)`)
