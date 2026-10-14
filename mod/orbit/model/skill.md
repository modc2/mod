# model — every model, one place

One BYOK gateway that aggregates **every provider** and **every type of model**.
16 providers are registered in `providers.json` (routers: openrouter, huggingface,
together, fireworks, deepinfra, groq, cerebras; labs: anthropic, openai, gemini,
xai, mistral, deepseek; networks: chutes, targon, venice), and users can add any
OpenAI-compatible https endpoint of their own. Every model is addressable as
`<provider>:<model>` and typed by `kind`: chat, image, video, embedding, speech,
transcription, rerank, moderation. The gateway never reads stored keys; every
request carries its own.

## Capabilities

- `serve()` boots the FastAPI gateway + single-page app on port `50110`.
- `/catalog`: every provider's models merged and typed (parallel fetch, per-provider
  errors, `kind`, `price_in`/`price_out` USD per 1M, `price_image`, context, vision).
  OpenRouter is listed with `output_modalities=all` and Venice with `type=all`, so
  non-text models are included.
- Run endpoints per kind: `/chat` (SSE), `/images`, `/embeddings` (+cosine matrix),
  `/speech`, `/transcribe`, `/rerank`. Video and moderation models are catalog-only.
- UI: **sidebar** with a MODELS tab (search `/`, kind chips, provider filter, sort,
  ready/free toggles, favorites, `+` to compare up to 4 chat models side by side) and
  a PROVIDERS tab (connect/test/remove keys, show/hide a provider, add a custom endpoint).
  The main pane switches workspace by kind (chat, image gallery, embedding heatmap,
  TTS player, mic/file transcription, rerank bars) and has a `</> API` curl snippet.
- Custom endpoints travel per request in `X-Custom-Providers`; they must be public
  https hosts (private/loopback addresses are refused, since this server fetches them).
- CLI helpers: `m model/providers`, `m model/models provider=…`, `m model/forward …`.

## Usage

### Python

```python
import mod as m
gw = m.mod('model')()
gw.serve()                        # boots http://localhost:50110
gw.providers()                    # registry
gw.models(provider='openrouter', api_key='sk-or-…')
gw.models()                       # ALL providers combined (env-var keys) -> '<provider>:<model>' refs
gw.models(api_key={'chutes': 'cpk_…', 'venice': '…'}, search='deepseek')

# One-shot completion (BYOK) — a combined ref picks the provider
gw.forward('hello', model='venice:llama-3.3-70b', api_key='…')
gw.forward('hello', provider='anthropic', api_key='sk-ant-…')

# Full per-provider clients (key store, history, pricing, local HF)
gw.client('openrouter').free_models()
gw.client('grok', provider='targon')
```

### CLI

```sh
m model/serve                              # background, logs to /tmp/model/api.log
m model/status
m model/kill
m model/providers
m model/models provider=chutes api_key=$CHUTES_API_KEY
m model/models search=claude                # every provider combined
m model/forward 'hello' provider=openrouter api_key=$OPENROUTER_API_KEY
```

### Web UI

Open <http://localhost:50110> (or `/model` behind the router; API at `/model/api`).
Pick any model in the sidebar; if its provider has no key, the main pane asks for
one inline. Keys live only in the browser's `localStorage` (`modelgw_key_<id>`),
are sent only with requests to their own provider, and never touch the server's disk.

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Chat UI |
| GET | `/health` | `{ok, providers}` |
| GET | `/providers` | List of `{id,label,url,default_model,key_hint,docs}` |
| GET | `/models?provider=ID` | Requires `X-API-Key` (openrouter's catalog is public) |
| GET | `/catalog?search=&kind=` | All providers combined, typed. Keys via `X-API-Keys: {"chutes":"…",…}` |
| GET | `/check?provider=ID` | Validate a key (fresh listing) -> `{ok, count}` |
| POST | `/chat` | `{model:"<provider>:<model>", messages}` or `{provider, model, messages}`. SSE when `stream:true`. |
| POST | `/images` | `{model, prompt, size, n}` -> `{images}` (OpenRouter via chat `modalities`) |
| POST | `/embeddings` | `{model, input:[…]}` -> `{dims, vectors, similarity}` |
| POST | `/speech` | `{model, input, voice, format}` -> audio |
| POST | `/transcribe` | multipart `model`, `file` -> `{text}` |
| POST | `/rerank` | `{model, query, documents}` -> ranked `{results}` |

Upstream 5xx errors are returned as 424 so Cloudflare keeps the error body.
Add `X-Custom-Providers: {"c-mine": {"url": "https://…/v1", "label": "Mine"}}` to
any call to use your own endpoint as provider `c-mine`.

`X-API-Key` (or `Authorization: Bearer …`) carries one BYOK secret;
`X-API-Keys` carries a JSON map of several. The server forwards the matching
key straight to the upstream provider for that one request. Model lists are
cached in memory for 5 min, keyed by a hash of the key, never the key itself.

## Structure

```
model/
├── mod.py            # anchor: serve/kill/status/providers/models/forward/client
├── providers.json    # THE provider registry (python + rust read it)
├── config.json       # port (50110), urls, fns
├── skill.md          # this file
├── app/
│   ├── catalog.py    # registry + custom endpoints + typed /models fetch + combined catalog
│   ├── server.py     # FastAPI gateway (BYOK)
│   └── index.html    # single-page app: sidebar manager + per-kind workspaces
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
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `chutes` | `CHUTES_API_KEY` |
| `targon` | `TARGON_API_KEY` |
| `venice` | `VENICE_API_KEY` |
| `huggingface` | `HF_TOKEN` |
| `together` / `fireworks` / `deepinfra` / `groq` / `cerebras` | `<NAME>_API_KEY` |
| `gemini` / `mistral` / `deepseek` | `<NAME>_API_KEY` |

Add a provider: append it to `providers.json` (any OpenAI-compatible
`/chat/completions`). It shows up in the server, CLI, UI and Rust build.

## Mod protocol

- Logs: `/tmp/model/api.log`
- PID: `/tmp/model/api.pid`
- Port: `50110` (configurable via `MODEL_GATEWAY_PORT` env or `serve(port=…)`)
