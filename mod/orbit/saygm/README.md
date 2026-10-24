# saygm

[SayGM](https://saygm.com) as a mod. SayGM is an LLM gateway on Bittensor:
Claude, GPT, Gemini and open-weight models behind one key and one base URL,
priced at or below the maker's list price. Every request passes through an
Intel TDX enclave. The `-tee` models also run inference inside it, so the
prompt never leaves the enclave.

This module gives you:

- **Catalog**: the live `/v1/models` list, converted from nano-dollars to
  USD per million tokens, with each model's list price and discount.
- **Quotes**: what a call costs here compared with buying from the maker.
- **Chat**: one `chat` call for every model. SayGM speaks four wire shapes
  (Anthropic `messages`, OpenAI `chat.completions` / `responses`, Gemini
  `generateContent`). Each model lists the shapes it accepts, and `chat`
  picks the right one for you.
- **`/v1` passthrough**: point an OpenAI, Anthropic or Gemini SDK at this
  module and it behaves like the real gateway, streaming included.
- **Console** at `/saygm/`: browse and sort models, get quotes, and send a
  prompt.

Python stdlib only. You can read the catalog without a key; spending needs one.

## Use it

```bash
m saygm/serve                                    # console + API on :51270 → http://localhost:51270/saygm/

m saygm/models                                   # every available model
m saygm/models tier=confidential sort=input      # TEE models, cheapest first
m saygm/models sort=discount limit=10            # biggest discount vs list
m saygm/model id=claude-sonnet-5
m saygm/quote model=gpt-5.5 input_tokens=20000 output_tokens=2000

m saygm/set_key key=<gm-api-key>                 # mint one at saygm.com/dashboard
m saygm/chat model=glm-5.3-flash-tee prompt="say gm"
m saygm/chat model=claude-sonnet-5 system="be brief" prompt="what is a TEE?"
m saygm/credits
m saygm/test
```

## Use it from an SDK

```python
from openai import OpenAI
c = OpenAI(base_url="http://localhost:51270/v1", api_key="<gm-api-key>")
c.chat.completions.create(model="glm-5.3-flash", messages=[{"role": "user", "content": "gm"}])

import anthropic
a = anthropic.Anthropic(base_url="http://localhost:51270", api_key="<gm-api-key>")
a.messages.create(model="claude-sonnet-5", max_tokens=64, messages=[{"role": "user", "content": "gm"}])
```

On loopback you can leave the key empty and the stored house key is used.

## Keys and who pays

- The house key lives in `~/.mod/saygm/key.json` (mode 0600). It is never
  stored in `config.json`. `SAYGM_API_KEY` in the environment overrides it.
- The house key only pays for **loopback** callers. A request that comes in
  through the gateway (it carries `X-Forwarded-For`) has to bring its own key:
  `x-saygm-key` on `/chat` and `/credits`, or the usual
  `Authorization: Bearer` / `x-api-key` on `/v1/*`.
- `set_key` and `refresh` only work from loopback.
- Spend guard: if `max_tokens` × the output price could cost more than $0.50
  (`SAYGM_SPEND_USD`), `chat` returns `needs_confirm` instead of calling.
  Retry with `confirm=true`.

## API

| route | what |
|---|---|
| `GET /info` | null call: module info and key state (masked) |
| `GET /health` | liveness; never calls upstream |
| `GET /models` | `q, tier, lab, shape, confidential, available, sort=id\|input\|output\|discount, limit` |
| `GET /model?id=` | one model |
| `GET /labs` | labs with model counts |
| `GET /quote?model=&input_tokens=&output_tokens=` | USD here vs list |
| `GET /credits` | prepaid balance |
| `POST /chat` | `{model, prompt \| messages, system, max_tokens, temperature, confirm}` |
| `POST /set_key` | `{key}`, loopback only |
| `POST /refresh` | drop the 5-minute catalog cache, loopback only |
| `* /v1/*`, `/v1beta/*` | raw passthrough to `api.saygm.com` |

You can reach every route at `/{fn}`, `/saygm/api/{fn}` and `/api/saygm/{fn}`.
Errors come back as 4xx, never 5xx, because Cloudflare replaces 5xx bodies
with its own page.

## Notes

- Prices on the wire are integers in `ndollars_per_mtok` (1e-9 USD per
  million tokens). `discount` blends input and output 3:1 against the
  published retail ceiling.
- The catalog is cached for 5 minutes in `~/.mod/saygm/models.json`. If
  SayGM is unreachable, the module serves the stale copy rather than nothing.
- `flux.2-klein-4b` is an image model (`images.generations`), so `chat`
  refuses it. You can still call it through the `/v1` passthrough.
