# liquidai

Catalog and run every Liquid AI (LFM) model. Three runtimes behind one chat
call: **browser** (transformers.js/WebGPU, in the visitor's tab), **server**
(transformers on this box), **cloud** (`inference.liquid.ai`, caller's key).

All four model kinds have a surface: text and vision share the transcript,
embeddings get a cosine grid, speech gets an upload. Reads are open; spending
this box's compute needs a sign-in (browser key / MetaMask / Bittensor wallet).

API `:50460` (`/api/liquidai`) · console `:50461` (`/liquidai`)

## When to reach for it

- "what Liquid models are there / which ones run on a phone / in a browser"
- "run a small model locally without an API key"
- "compare the same prompt on-device vs server vs hosted"
- pulling LFM weights onto this box, or checking what's already pulled
- asking a VLM about an image, or scoring lines against each other with an
  encoder
- benchmarking small models against each other on a rule (ARENA)
- using LFMs as an OpenAI-compatible provider (`/v1/*`) from any client

Not for: general LLM routing (that's `dev`), Claude jobs (`claude`, `agent`),
or Liquid's own audio models — those need Liquid's `liquid-audio` runtime,
which stock transformers doesn't carry.

## Model families, in one breath

`LFM2` and `LFM2.5`, 230M → 24B-A2B (MoE). Text, VL (vision), Audio, and
embedding/encoder variants; role-specific fine-tunes (`-Thinking`, `-Extract`,
`-Math`, `-RAG`, `-Tool`, `-ENJP-MT`, `-PII`). Each model ships as some subset
of safetensors / GGUF / ONNX / MLX, and *that subset is what decides where it
runs* — the catalog derives runtimes from it rather than being told.

## CLI

```bash
m liquidai/models runtime=browser        # what a tab can run
m liquidai/models kind=vision            # VLMs
m liquidai/model LFM2.5-350M             # one model, every format + quant
m liquidai/runtimes                      # browser/server/cloud, with reasons
m liquidai/pull repo=LiquidAI/LFM2.5-350M
m liquidai/pulls                         # download progress
m liquidai/load repo=LiquidAI/LFM2.5-350M
m liquidai/chat prompt="..." model=LiquidAI/LFM2.5-350M runtime=server
m liquidai/embed texts="a cat|a kitten|a bus"   # vectors + pairwise matrix
m liquidai/games | play game=arithmetic models=... | board
m liquidai/auth                          # who owns this box, who signed in
m liquidai/disown                        # release the claim
m liquidai/set_key key=sk-...            # cloud BYOK → ~/.mod/liquidai/keys.json
m liquidai/serve | kill | status | logs | test
```

`chat` returns `{ok, text, ttft_sec, chunks_per_sec, elapsed_sec, prompt_tokens}`.

## HTTP

`GET /models?runtime=&kind=&family=&role=&q=&refresh=` · `GET /models/{id}` ·
`GET /runtimes` · `GET|POST /keys` · `GET /local/models` · `POST /local/pull` ·
`GET /local/pulls` · `POST /local/load` · `POST /local/unload` ·
`POST /chat` (SSE) · `GET /cloud/models` · `POST /auth/nonce` ·
`POST /auth/verify` · `GET /auth/me` · `GET /auth/owner` · `POST /embed` ·
`POST /transcribe` (multipart) · `GET|POST /arena/games` · `POST /arena/match` ·
`GET /arena/leaderboard` · `GET /v1/models` · `POST /v1/chat/completions` ·
`POST /v1/embeddings`

```bash
curl -N localhost:50460/chat -H 'content-type: application/json' -d '{
  "messages":[{"role":"user","content":"hi"}],
  "model":"LiquidAI/LFM2.5-350M","runtime":"server","max_tokens":64}'
```

## Traps

- **`runtime:"browser"` on `/chat` is a 400.** A browser run has no server leg;
  the tab loads the ONNX build itself. Nothing to proxy.
- **A dtype the runtime accepts ≠ a dtype the repo ships.** `LFM2.5-350M-ONNX`
  has `model_q4f16.onnx`; `LFM2.5-230M-ONNX` doesn't. The worker reads the
  repo's `onnx/` listing and picks from what's there — don't hardcode `q4f16`.
- **LFM2.5's chat template breaks @huggingface/jinja**: it wraps the assistant
  turn in `{% generation %}`, which python transformers understands and
  transformers.js does not ("Unknown statement type: generation"). The worker
  strips the two tags and re-renders — they only mark a span, so the prompt
  comes out identical.
- **One resident model.** `load` evicts the last one. On CPU, ≤1.2B is usable.
- **The AutoClass comes from the repo's own config**, not its name: vision →
  `AutoModelForImageTextToText` + a processor, embed → `AutoModel` + mean
  pooling, text → `AutoModelForCausalLM`. A vision repo needs `torchvision`
  installed or its image processor raises on import.
- **Liquid's audio models are not a transformers architecture.**
  `Lfm2AudioForConditionalGeneration` isn't in stock transformers and the repos
  ship no `auto_map`, so `/transcribe` serves speech-seq2seq repos and says so
  for the rest. Don't burn 3 GB finding out.
- **Sign-in is nonce → signature → token.** One nonce, one use, five minutes.
  Polkadot extensions wrap the payload in `<Bytes>…</Bytes>`; the verifier
  tries both forms. Sessions are HMAC over `~/.mod/liquidai/server.secret`
  (0600) — no session table, so `rm` it to sign everyone out.
- **First sign-in claims the box.** `LIQUIDAI_OWNER` pins an owner,
  `LIQUIDAI_OPEN=1` disables the gate, `m liquidai/disown` releases it. A shell
  here mints its own owner token from the secret.
- **Arena scoring is deterministic and blunt.** `number` passes if *any* number
  in the answer matches — a model that shows its working writes "1/2 × 156 =
  78" and first-number matching would score that wrong.
- **The catalog is one row per *model*, not per repo.** Ids are bare
  (`LFM2.5-350M`); the HF repo ids live in `torch_repo` / `onnx_repo` /
  `gguf_repo`. `/chat` wants a *repo*.
- **Cloud is BYOK only.** No house key, ever. Env `LIQUID_API_KEY` beats the
  0600 vault at `~/.mod/liquidai/keys.json`.
- **Catalog cache is 6h** at `~/.mod/liquidai/catalog.json`; pass `refresh=1`
  after Liquid ships something new.
