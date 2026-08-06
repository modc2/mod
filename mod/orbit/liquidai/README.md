# liquidai

One interface to every Liquid AI (LFM) model, and to the three places one can run:

| runtime     | where it runs                    | who pays        | what it needs                    |
|-------------|----------------------------------|-----------------|----------------------------------|
| **BROWSER** | the visitor's tab, WebGPU        | nobody          | an ONNX build (transformers.js)  |
| **SERVER**  | the box this module runs on      | your CPU/GPU    | safetensors + torch/transformers |
| **CLOUD**   | `inference.liquid.ai`            | the caller's key| a Liquid Cloud key (BYOK)        |

The chat call is one endpoint with a `runtime` field, so moving a prompt from a
laptop tab to a datacentre is a one-word change and nothing else about the call
moves.

All four of Liquid's model kinds have a surface: **text** and **vision** share
the transcript (images ride in the turn), **embeddings** get a similarity grid,
**speech** gets an upload. Reading is open to anyone; spending this box's
compute needs a sign-in — a browser key, MetaMask, or a Bittensor wallet.

```
api   http://localhost:50460          gateway  /api/liquidai
app   http://localhost:50461/liquidai gateway  /liquidai
```

## The catalog

Liquid publishes one HuggingFace repo per *format* — `LFM2.5-350M` holds the
safetensors, `-GGUF` the quants, `-ONNX` the web build, `-MLX-4bit` the Apple
one. That's four rows for one model, which is the wrong unit for a console
whose whole question is "where can I run this". So the catalog folds the format
suffix off the repo name and keys on what's left: **one row per model**,
carrying its variants and — derived from which variants exist — its runtimes.

It is fetched live from the HF API (`author=LiquidAI`), never hardcoded: Liquid
ships models faster than a constant survives. Cached off-tree for 6h at
`~/.mod/liquidai/catalog.json`; a failed refresh serves the stale cache rather
than an error.

Today that's ~50 models across LFM2 and LFM2.5 — text, vision (VL), audio and
embedding (ColBERT / Encoder), from 230M up to a 24B MoE with 2B active.

## Signing in

Three doors, one flow: the API mints a nonce, your key signs the sentence, the
signature comes back and buys a 7-day session token.

| door          | key                                    | needs            |
|---------------|----------------------------------------|------------------|
| **BROWSER**   | a P-256 keypair the tab makes (WebCrypto) | nothing       |
| **METAMASK**  | EIP-191 `personal_sign`, address recovered from the signature | MetaMask |
| **BITTENSOR** | sr25519/ed25519 raw-bytes signature, SS58 checked | Talisman · SubWallet · Polkadot{.js} |

What each tier buys:

- **anyone** — the catalog, model detail, runtime status, and browser runs.
  Those cost the operator nothing and leak nothing, so they never ask.
- **signed in** — `/chat` on this box or the operator's cloud key, `/embed`,
  `/transcribe`, and arena entries.
- **owner** — the weights on this disk and the key vault. The first account to
  sign in claims the box (`~/.mod/liquidai/owner.json`); `m liquidai/disown`
  releases it, `LIQUIDAI_OWNER` pins it, `LIQUIDAI_OPEN=1` turns the gate off
  for local development.

Sessions are stateless HMAC tokens signed with `~/.mod/liquidai/server.secret`
(0600). Nothing about a session is stored, so restarting the API signs nobody
out and `rm server.secret` signs everybody out. A shell on this box mints its
own owner token from that secret — reading it already means being the operator.

## Running one

**In the browser.** `/run` with the BROWSER switch loads the model's ONNX build
into a module worker via transformers.js and generates on WebGPU (wasm where
WebGPU is missing). The weights come from HuggingFace to the tab and are cached
by the browser; the prompt and the tokens never reach this server. A 350M q4 is
a ~290 MB first load, then instant.

**On this box.** The SERVER switch streams SSE off the FastAPI backend, which
runs the safetensors through transformers. One model is resident at a time —
loading a second evicts the first, because a console that lets you click four
1.2B models into RAM is a console that OOMs the module. On a CPU box, ≤1.2B is
the usable range.

**Images, speech and vectors.** A vision turn carries its images as content
parts (`{"type":"image","image":"data:…"}`) on the same `/chat` call; the server
hands them to the model's processor and a text-only model gets them stripped
rather than a 500. `/embed` mean-pools an encoder's hidden states and returns
the cosine matrix between the lines — the matrix is the point, since a bare
vector says nothing on a screen. `/transcribe` takes a file upload.

Liquid's own audio models (`Lfm2AudioForConditionalGeneration`) need Liquid's
`liquid-audio` runtime: stock transformers has no such class and the repos ship
no remote code. The module says so up front rather than after a 3 GB download.

**In Liquid's cloud.** The CLOUD switch proxies `inference.liquid.ai` (an
OpenAI-compatible API) using the caller's own key. The key lives in
`~/.mod/liquidai/keys.json` at 0600 — never in `config.json`, never a house key
fronting someone else's usage. `LIQUID_API_KEY` in the environment wins over
the vault.

## CLI

```bash
m liquidai                                   # info
m liquidai/serve                             # api :50460 + console :50461
m liquidai/models runtime=browser            # what a tab can run
m liquidai/models kind=vision                # VLMs
m liquidai/model LFM2.5-350M                 # one model, every format
m liquidai/runtimes                          # what each runtime can do right now
m liquidai/pull repo=LiquidAI/LFM2.5-350M    # weights onto this disk (background)
m liquidai/pulls                             # download progress
m liquidai/load repo=LiquidAI/LFM2.5-350M    # make it resident
m liquidai/chat prompt="why are LFMs small?" # server-side, streamed, with stats
m liquidai/embed texts="a cat|a kitten|a bus" # vectors + the pairwise matrix
m liquidai/games                             # what the arena plays
m liquidai/play game=arithmetic models=LiquidAI/LFM2.5-350M
m liquidai/board                             # the leaderboard
m liquidai/auth                              # who owns this box, who signed in
m liquidai/disown                            # release the claim
m liquidai/set_key key=sk-...                # cloud BYOK
m liquidai/test generate=1                   # every read + one real completion
m liquidai/status                            # services + health
```

## HTTP

| method | path                | does                                                      |
|--------|---------------------|-----------------------------------------------------------|
| GET    | `/health`           | uptime, catalog freshness, server-runtime state, resident |
| GET    | `/models`           | catalog (`?runtime=&kind=&family=&role=&q=&refresh=`)     |
| GET    | `/models/{id}`      | one model: every format, quant, and what's on disk        |
| GET    | `/runtimes`         | browser / server / cloud availability, with reasons       |
| GET`|`POST | `/keys`        | BYOK vault status (masked) / store a key                  |
| GET    | `/local/models`     | LFM weights on this disk                                  |
| POST   | `/local/pull`       | download a `LiquidAI/*` repo in the background            |
| GET    | `/local/pulls`      | progress                                                  |
| POST   | `/local/load`       | make a repo resident (evicts the last)                    |
| POST   | `/local/unload`     | free it                                                   |
| POST   | `/chat`             | SSE `start` → `token`* → `done` \| `error`                |
| GET    | `/cloud/models`     | what your cloud key can reach                             |
| POST   | `/auth/nonce`       | the sentence to sign (`kind=browser\|evm\|bittensor`)     |
| POST   | `/auth/verify`      | signature → session token                                 |
| GET    | `/auth/me`          | who this bearer is                                        |
| GET    | `/auth/owner`       | who claimed this box                                      |
| POST   | `/embed`            | vectors + the cosine matrix between the lines             |
| POST   | `/transcribe`       | multipart audio → text                                    |
| GET`|`POST | `/arena/games` | every game / write one                                    |
| POST   | `/arena/match`      | up to 4 models through a game, scored per round           |
| GET    | `/arena/leaderboard`| best run per model per game                               |
| GET    | `/v1/models`        | OpenAI-shaped model list                                  |
| POST   | `/v1/chat/completions` | OpenAI chat completions                                |
| POST   | `/v1/embeddings`    | OpenAI embeddings                                         |

Writes (`/local/*`, `POST /keys`) want an owner token; `/chat`, `/embed`,
`/transcribe` and the arena want any session. Reads are open.

## As a provider

The `/v1/*` block is the same three runtimes wearing the interface everything
already speaks, so liquidai drops into anything that takes a `base_url` and a
key — no client has to learn what an LFM is. The key is a liquidai session
token, and the gate is the one the console goes through.

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:50460/v1", api_key=SESSION_TOKEN)
client.chat.completions.create(model="LiquidAI/LFM2.5-350M",
                               messages=[{"role": "user", "content": "hi"}])
```

Streaming lives on `/chat` in this module's own SSE shape and is deliberately
not duplicated here — one streaming encoder, no drift between two.

`runtime: "browser"` on `/chat` is a 400 on purpose — a browser run has no
server leg, so asking this endpoint for one is a bug in the caller, not a mode.

```bash
curl -N localhost:50460/chat -H 'content-type: application/json' -d '{
  "messages":[{"role":"user","content":"one sentence on why small models matter"}],
  "model":"LiquidAI/LFM2.5-350M","runtime":"server","max_tokens":80}'
```

## The console

Four boards, all wearing copytensor's 8-bit cabinet (ten skins, same tokens,
same rules: nothing is round, every edge is hard, pressing moves the pixel).

- **CATALOG** — every model, one line each, filtered by runtime / task /
  generation, sortable, with a stat strip that answers "what is everyone
  actually pulling" before you've read a row.
- **RUN** — the board changes with the model's task: a transcript for text and
  vision (attach or paste an image), a similarity grid for embeddings, an
  upload for speech. The rail collapses (▤), turns can be edited (⚒) or forked
  (⋔), and the stats strip says where it actually ran, time to first token and
  chunks/sec.
- **ARENA** — models play scored games. A game is rounds, and a round is a
  prompt plus a check (`contains`, `equals`, `number`, `regex`, `lines`,
  `absent`) — no judge model, no rubric, which is what makes two runs
  comparable. Four ship; write your own with ✚ NEW GAME, or fork a built-in.
- **LOCAL** — every server-runnable model with its disk state, PULL/LOAD/FREE
  inline; the cloud key; what this box is.
- **MODEL** — one model's formats, plus the commands to run it under
  transformers, transformers.js and llama.cpp, because this board is a front
  door, not a lock-in.

## Layout

```
src/
  mod.py            mod-protocol surface + lifecycle (pm2, else Popen)
  api/
    app.py          FastAPI: catalog, keys, local weights, /chat routing
    catalog.py      HF → one row per model, runtimes derived from formats
    server_rt.py    resident model, pulls, streamed CPU/GPU generation
    cloud.py        inference.liquid.ai proxy on the caller's key
    keys.py         ~/.mod/liquidai/keys.json (0600)
    auth.py         nonce → signature → session; three key kinds, one flow
    arena.py        games, deterministic per-round scoring, the leaderboard
  app/              Next.js console
    public/lfm-worker.js   the browser runtime — deliberately unbundled
```

`lfm-worker.js` is a plain module worker that imports transformers.js from a
CDN rather than being bundled. Bundling it would quietly put the app server
back in the middle of an inference path whose entire point is that it isn't
there.

## State

Verified on this box, both through the console and the CLI:

- **SIGN IN** — all three doors verified against the live API. The browser door
  was driven end to end in a real headless Chromium (WebCrypto keygen → nonce →
  ECDSA signature → token in localStorage → owner star in the rail); MetaMask
  and Bittensor were verified server-side with real `eth_account` and `sr25519`
  signatures, including the `<Bytes>…</Bytes>` wrapper extensions add. A
  tokenless write comes back 403 and `m liquidai/test` asserts it.
- **SERVER** — LFM2.5-350M on CPU: ~12s cold load, 0.34s to first token,
  ~6 chunks/s. `m liquidai/test` → 13/13.
- **VISION** — LFM2.5-VL-450M read a generated image (red rectangle, green
  circle) correctly in 3.3s on CPU. Needs `torchvision`; without it the VL
  image processor raises on import, so it's in requirements.
- **EMBED** — LFM2.5-Encoder-230M, 1024 dims, 3 lines in 4.7s. Note the raw
  encoders are *not* tuned for bare cosine similarity — on the sample lines the
  paraphrase pair scored 0.60 against 0.63 for an unrelated pair. The mechanism
  is right; `LFM2.5-Embedding-350M` is the model to reach for.
- **ARENA** — played through the console on the server runtime. LFM2.5-230M
  scored 4/4 on EXTRACT (1.2s/round) and 3/4 on MATH DASH; the -Base variant
  took 4/4 and 0/4 — exactly the instruction-tuning difference the board exists
  to show.
- **BROWSER** — LFM2.5-230M ran in a headless Chromium tab on WebGPU and
  answered: 33s to first token on a software adapter (a real GPU is the point
  of the runtime; this was the slowest possible way to prove it works).
- **CLOUD** — BYOK and *unverified*: there is no Liquid key on this box. The
  endpoint is reachable and rejects an invalid key with a 401, which is as far
  as testing goes without one.

Both services run under pm2 (`liquidai-api`, `liquidai-app`) and the module is
registered with the gateway, so `/liquidai` and `/api/liquidai` both answer.

Not built: browser-side arena entries (`POST /arena/result` exists and is
scored, but the RUN board doesn't drive a game through the tab yet), and
speech through any Liquid audio model — that one is blocked upstream on
`liquid-audio`, not on this module.
