# aipg

A handle on [AI Power Grid](https://aipowergrid.io) — a network of
community-run workers serving open-weight models for **text, image, video and
audio** behind an OpenAI-shaped API, paid in USD-denominated credits.

Nothing is vendored and nothing is installed. The module is stdlib `urllib`
against `https://api.aipowergrid.io/v1`, so it runs anywhere Python does.

```
m aipg/grid                          the whole grid on one screen
m aipg/models                        every model — typed, online-or-not, priced
m aipg/models type=image online=true only the image models anyone is serving
m aipg/workers                       who is actually up, and what they hold
m aipg/pricing model=gpt-oss-120b    the rate card for one model
m aipg/estimate gpt-oss-120b 1000 500   what that call would cost
m aipg/set_key <key>                 ~/.mod/aipg/key.json, 0600
m aipg/credits                       what is left on it
m aipg/ask "what is a GPU"           one turn, just the text
m aipg/image "a tin robot"           one image → ~/.mod/aipg/out
```

## The two facts this module exists for

Both were measured against the live API, not read off a page.

**1. `/v1/models` is the text surface only.** It returned 6 ids while
`/v1/status/models` returned 12 — every image, video and audio model was
missing:

| type | in `/v1/status/models` | in `/v1/models` |
|---|---|---|
| text | `gpt-oss-120b`, `gpt-oss-20b`, `qwen3-27b`, `deepseek-v4-flash-nvfp4`, `Smollm-135m` | yes |
| image | `z-image-turbo`, `FLUX.2 Klein 4B FP8`, `Krea 2 Turbo` | **no** |
| video | `LTX-2.3`, `LTX-2.3 Audio`, `LTX Director 2.0` | **no** |
| audio | `ace-step-v1.5-xl-turbo` | **no** |

So point a plain OpenAI client at the grid and half of it is invisible. The
roster here comes from `/v1/status/models`, and `/v1/models` is reduced to one
boolean per row — `openai_visible` — which is the honest thing it actually
tells you. `m aipg/grid` reports the hidden set directly.

**2. A price is not availability.** The upstream price book says so in its own
payload: *"Configured rates do not assert that a model is online."* Online means
a worker is currently holding the weights, which only the roster and
`/v1/workers` know. Three reads, three partial answers — so `models()` joins
them into one row that answers the two questions a caller actually has: *can I
use this, and what will it cost.*

The join resolves a name the way the price book itself does, and every row
says which rule priced it (`price_source`):

1. **`book`** — the name, **case-insensitively**: the grid writes
   `FLUX.2 Klein 4B FP8` in the roster and `flux.2 klein 4b fp8` in the book.
2. **`alias:<target>`** — the book's `aliases` map roster names to billed names
   (`deepseek-v4-flash-nvfp4` → `deepseek-v4-flash`, `LTX Director 2.0` and
   `LTX-2.3 Audio` → `ltx-2.3`).
3. **`roster`** — the rate `/v1/status/models` carries inline on some rows.
4. **`default`** — text only: the book's `default_text`, what the grid bills an
   unlisted text model at. Media never inherits it.

Anything else comes back unpriced (`price: {}`, `price_source: null`) rather
than guessed at. `m aipg/pricing` returns the whole book — schema, version,
aliases, `default_text`, the availability note and `comparison_evidence` —
and `m aipg/price_book` returns the upstream payload verbatim.

## Design decisions, and why

**Its own protocol server on :50147.** The generic core gate only knows one
fleet-wide public fn list, which refuses every read here — so the module owns
its `serve()` (stdlib `http.server`; core/server hands off to it, so the
existing `m.serve('aipg')` pm2 entry runs it unchanged). Same wire as
core/server: `POST /{fn}` with a JSON object of params → `{"result": ...}`,
errors as 4xx `{"error": ...}`. `GET /{fn}?k=v` works too. The fn may sit
behind any prefix the fleet uses: `/`, `/mod/aipg/`, `/aipg/api/`,
`/api/aipg/`.

- `public_fns` in config.json are open to anyone who can reach the port.
- Everything else — `credits`, `chat`, `ask`, media, `set_key` — answers only a
  **direct loopback caller with no proxy headers**: the operator on this box.
  A request relayed by caddy is the internet, and is refused with 403.
- `key` and `serve` are never callable over HTTP.

```
curl localhost:50147/pricing?model=gpt-oss-120b
curl -XPOST localhost:50147/estimate -d '{"model":"ltx-2.3","seconds":10}'
```

`"route": true` is set, but the module is marked private in build, and private
modules are not routed — `{host}/aipg/api` goes live when it is made public.

**The key lives in `~/.mod/aipg/key.json`, mode `0600`.** It is a bearer
credential that spends real money, so it is written with
`os.open(..., 0o600)` rather than a plain write; the default umask is not a
permission model. `AIPG_API_KEY` overrides it. `key_status()` reports presence
and origin and never the secret — the repo never sees either.

**Never `import mod`.** Every mod in the fleet ships a `mod.py`, so inside this
file `import mod` usually resolves to *this file*, and the failure is quiet.
Everything here is stdlib, which removes the question.

**One flat `mod.py`, no `src/` package.** A directory named `src` collides on
`sys.modules['src']` with every other module that has one. At this size there is
nothing to gain by paying that.

**Media defaults to the first online model of the right type**, not a pinned
name. The media roster turns over with whoever is running a worker, so a
hardcoded default is a default that breaks.

**`chat(stream=True)` prints as it lands and returns the whole text.** A
generator is the prettier answer, but the caller is usually a CLI that prints
return values.

## Reads, and what they cost you

| function | needs a key | upstream |
|---|---|---|
| `grid` `models` `roster` `workers` `pricing` `price_book` `openai_models` `estimate` `test` | no | `/v1/status/models`, `/v1/workers`, `/v1/pricing`, `/v1/models` |
| `ask` `chat` `image` `video` `audio` `credits` | yes | `/v1/chat/completions`, `/v1/{images,videos,audio}/generations`, `/v1/account/credits` |

Keys come from [console.aipowergrid.io](https://console.aipowergrid.io).

## Verification status

Be precise about this, because it matters when something misbehaves:

- **Verified live** — every unkeyed path. `/v1/status/models`, `/v1/workers`,
  `/v1/pricing` and `/v1/models` were all called while writing this, and the
  numbers and field names above are what came back.
- **Written to the documented shape, not yet executed** — the keyed paths.
  There was no API key on this machine, so `chat`, `ask`, `image`, `video`,
  `audio` and `credits` follow the upstream docs and the OpenAI conventions
  (`choices[].message.content`, `data[].b64_json` or `data[].url`). `_save()`
  deliberately accepts both media shapes rather than betting on one. First real
  key is the moment to confirm them — start with `m aipg/credits`.
- **Retired upstream** — the old AI-Horde-shaped `/api/v2/*` answers
  `{"status":"gone"}`. Anything written against Horde kudos, `find_user` or
  `generate/async` no longer applies here.

## State

```
~/.mod/aipg/
  key.json        0600, {"key": ..., "set_at": ...}
  out/            image-<ts>-<n>.png, video-…mp4, audio-…mp3
```

## Tests

```
python3 -m pytest orbit/aipg/tests -q          # offline, no key, no network
python3 -m pytest orbit/aipg/tests -q -m live  # hits the real grid, unkeyed
```
