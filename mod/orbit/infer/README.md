# infer

Every inference router that settles in crypto and asks for no documents, merged
into one multimodal catalog — and the two older halves, a temperature-0 receipts
board and an ONNX optimizer, for when the cheapest router is no router at all.

```
m infer/routers                           # who trades here, and on what terms
m infer/market input=image                # every model that can see, cheapest first
m infer/market coin=XMR                   # what Monero can buy
m infer/market output=audio               # every voice in the registry
m infer/plan_route gpt-oss-120b           # who would serve it, and the saving
m infer/route gpt-oss-120b prompt="hi"    # call the cheapest funded one
m infer/spend                             # what each router cost, and billing drift
m infer/settle                            # the background payer, and its caps
m infer/sweep                             # read balances, propose top-ups
m infer/pay nanogpt usd=10 rail=eth       # dry run unless confirm=true
```

API `:50820` (`/api/infer`) · console `/infer` · MCP `POST /mcp` (40 tools)

## The router

Six catalogs, fetched at once and merged: **NanoGPT**, **Venice**, **PPQ**,
**Chutes**, **io.net** and **OpenRouter**. Around 1,350 offerings of ~1,120
distinct models — text, image, audio, video and embeddings.

| router | KYC | takes | note |
|---|---|---|---|
| nanogpt | none | XMR BTC ETH USDC LTC SOL XNO | no account at all; 601 models |
| venice | none | VVV USDC ETH BTC | quotes a DIEM column; 362 models incl. image, TTS, embeddings |
| ppq | none | BTC ETH USDC SOL | key bought with crypto; 369 models |
| chutes | none | TAO USDC | Bittensor-native, TEE-attested chutes |
| io.net | account | IO SOL USDC | Solana-native; publishes measured latency |
| openrouter | account | USDC on Base | 447 models — the yardstick |

`kyc` is a **ceiling**, not an equality test, and the default is `none`. A
router whose policy has not been read is `unknown` and is excluded — `kyc=any`
is the only way to reach one. That asymmetry is deliberate: being wrong in the
other direction hands somebody's passport to a provider they were avoiding.

These are dated declarations about **somebody else's onboarding policy**, not
guarantees. `m infer/routers` prints when each was last checked.

### Why aggregating pays, measured

217 models are served by more than one no-KYC router, and the spread between
the dearest and the cheapest reaches **5.6x for identical weights**:

```
gpt-oss-120b            ppq / nanogpt      5.63x
deepseek-v4-flash-0731  nanogpt/venice/ppq 4.46x
llama-3.2-3b-instruct   nanogpt / ppq      4.11x
```

## The unit trap

No two routers publish prices in the same unit, and **two of them use the same
key name for different units**:

| router | field | unit |
|---|---|---|
| openrouter | `pricing.prompt` | USD **per token**, as a string |
| chutes | `pricing.prompt` | USD **per million tokens** |
| nanogpt | `pricing.prompt` + `pricing.unit` | declares its own — the only one that does |
| ppq | `input_per_1M_tokens` | per million, self-naming |
| io.net | `input_token_price` | per token, as a float |
| venice | `pricing.input.usd` | per million, plus a DIEM column |

Read a chute the way OpenRouter is read and it looks **a million times** too
expensive, so a cheapest-first router silently never picks it. Units are
therefore **declared by each adapter and never inferred**; everything
downstream is USD per million tokens. `sniff()` exists for a provider that
declares nothing, and it returns its own confidence rather than guessing
quietly.

### A published zero is not a price

PPQ lists `input_per_1M_tokens: 0` for the Lyria music models because they bill
per clip; NanoGPT lists it for `auto-model` because the real price depends on
what it routes to. Read as free, those rows sort to the **top** of a
cheapest-first ranking and walk straight through the spend guard. So zero
across every field means *unknown* unless the router declares the model free
(`:free`), in which case it really is.

## Settlement

Crypto, in the background, and **disarmed by default**. The watcher reads
balances on a timer, prices any top-up a router needs, and files it as a
proposal. Nothing moves.

```
m infer/settle armed=true confirm=true rail=eth daily_cap_usd=25
```

Arming needs `confirm=true`, a rail and a daily cap — an unbounded autopayer is
not a feature. Once armed it works inside three guards: a per-top-up size cap
(4x the configured amount), a rolling 24h ceiling it will not cross for any
reason, and a deposit address that must already be on file. Nothing here
invents an address.

**Private keys never enter this module.** A transfer is an authenticated call
to the `eth` (:50730), `solana` (:50710) or `near` (:50910) module, each of
which holds its own key and gates its own writes. A rail that is not listening
is reported as down rather than retried into a timeout.

The same code path runs armed and disarmed — a proposal is an execution that
stopped before `rail.send` — so what you approve is what already got priced.

## Routing a call

`plan` and `chat` share one candidate function, so the dry run **is** the
decision rather than a description of it.

```
m infer/route gpt-oss-120b prompt="name three primes" require=image
```

Falls over to the next router when one refuses — a 429 is not an outage when
four others serve the same weights. Every answer carries a `routing` block:
who served it, what it **actually** cost computed from the usage they reported,
what the catalog estimated beforehand, and every router tried on the way. The
gap between billed and estimated accumulates in the ledger as `drift`, which is
the only way to notice a router whose bills and published prices disagree.

Above `INFER_SPEND_USD` (default $0.50) a call returns `needs_confirm` instead
of running, and a model with no usable price counts as **over** the limit.

## The standard binary

**ONNX.** Not because it is the newest format, but because it is the one two
runtimes execute directly with no conversion in between:

| where | runtime | how |
|---|---|---|
| this box | `onnxruntime` | `m infer/bench` — CPU, plus any execution provider the build ships |
| a browser tab | `onnxruntime-web` | the console — wasm + SIMD, threads when the page is cross-origin isolated, WebGPU where it exists |

That is the whole architectural bet. A model that has to be re-exported on the
way to where it runs is a model nobody measured, because the thing that was
benchmarked and the thing that shipped are two different files. Here the
console fetches `/blob/<id>` — the exact bytes the server just timed — and runs
them. Same file, two runtimes, two numbers you can put next to each other.

It is also what makes "any architecture" true rather than a slogan. A ResNet,
an LSTM, a transformer block and a gradient-boosted forest are all just graphs
by the time they are ONNX, and every pass in here reads the graph. Nothing in
the optimizer knows what framework anything was trained in.

## The passes

| pass | what it does | costs you |
|---|---|---|
| `slim` | strip doc strings, training info, initializers nothing reads | nothing |
| `shapes` | shape inference, so every tensor is annotated | nothing |
| `basic` | constant folding, dead nodes, redundant casts and identities | nothing |
| `extended` | `basic` + fusion: Conv+BatchNorm, MatMul+Add, GELU, attention | nothing |
| `all` | `extended` + layout transforms specific to this machine | **it can no longer leave this machine** |
| `fp16` | half-precision weights, fp32 kept at the boundary | ~1e-4 of accuracy |
| `int8` | dynamic weight quantization, no calibration set needed | real accuracy, and often speed |

The lossless ones are the floor — there is no argument for shipping a model
that still carries its training graph. The lossy ones are a trade, and this
module's job is to price it rather than to recommend it.

## What it will keep telling you

Two results show up constantly, both inconvenient, both real. They are in the
report rather than in a footnote because a tool that only reports wins is a
tool you cannot use to make a decision.

**Quantization often makes small models slower.** Measured here on a 302k-param
MLP, CPU:

```
extended   1.46× faster   same size    outputs identical
int8       1.22× SLOWER   3.86× smaller   max abs err 4.3e-02, argmax agrees 100%
fp16       2.51× SLOWER   2.00× smaller   max abs err 6.6e-05
```

int8 is a bandwidth optimization. When the weights already fit in cache there is
no bandwidth to save, and the dequantize nodes are pure added work. fp16 is
worse still on a CPU with no half-precision kernels — it pays for casts and gets
nothing. Both are excellent on a phone downloading the model over a cell
network, which is a different question, and `plan target=web` weighs it that way.

**One pass, and only one, breaks browser deployment.** `all` is the fastest
thing here and it rewrites the graph into layout operators for the CPU that ran
it. Loaded in a browser it dies immediately:

```
cnn + all   1.80× faster here   →   com.microsoft.nchwc.Conv(-1) is not a registered function/op
```

`optimize` re-checks portability afterwards and returns `portability_lost` with
the operators that did it, and `all` is in no default plan for either target.

The neighbouring claim — that *any* operator onnxruntime invented will break a
browser — is **false**, and this module asserted it until the console disproved
it. `extended` fuses into `com.microsoft.FusedConv` and `com.microsoft.BiasGelu`,
which look exactly as vendor-specific, and onnxruntime-web's wasm build
registers them:

```
cnn + slim,extended            FusedConv ×2   → ran in the browser, 1.6 ms p50
transformer + slim,extended    BiasGelu  ×1   → ran in the browser, 13.2 ms p50
```

So `portable` gives three answers rather than two: blocked, clean, or clean with
a `cautions` entry naming the contrib operators — because they do run in the
wasm backend, they are still not standard ONNX, and another runtime may not have
them. The static check is a prediction in every case. The console is the proof,
and it is the reason the prediction is now right.

So `slim,extended` is the honest default for both targets. Where local and web
really diverge is bytes: in a browser the download usually costs more than the
arithmetic, so `plan target=web` reaches for quantization at a much lower size
threshold than the local plan does.

## The report

`optimize` is the only call most work needs. It runs the passes, then checks
its own homework:

```
$ m infer/optimize mlp slim,extended,int8
verdict: 1.15× slower, 3.87× smaller (1.21 → 0.31 MB),
         outputs moved by at most 4.26e-02, argmax agrees 100% of the time,
         runs in the browser
```

Four facts, two of them unflattering, in one line.

and underneath that, per pass: which operators disappeared, bytes before and
after, and how long the pass itself took; then p50 latency for both models
measured in the same process on the same seeded inputs, `parity` (max absolute
error, max relative error, worst-case cosine, argmax agreement), and a fresh
portability check on the result.

Benchmarks disable onnxruntime's own graph optimization on purpose. Leaving it
on would silently re-apply the passes at load time and every measurement would
come out the same.

## Getting a model in

```
m infer/add path=~/models/detector.onnx
m infer/add url=https://example.com/model.onnx
m infer/export torchvision:resnet18 weights=DEFAULT
m infer/export mymodel.py                    # a file defining `model` (+ `example`)
m infer/export traced.pt shape=1,3,224,224
```

Or drop an `.onnx` on the console. Everything is stored under the SHA-256 of its
bytes, so the same model added twice is one entry and a report can always be
tied back to the exact bytes it was measured on. One consequence worth knowing:
running the same passes over the same source again produces the same bytes and
therefore the same entry — pass `name=` and you have renamed the existing model,
not made a second copy of it. `export` needs torch; nothing else here does.

Models that declare symbolic dimensions — `batch × sequence × 768` — cannot be
benchmarked until somebody decides what those are. The first symbolic dimension
takes `batch=`, the rest take 1, and `shapes={"input_ids":"1,128"}` overrides
any of it by name. Whatever was used is echoed back in the report, because a
latency number without a shape attached means nothing.

## The console

`/infer` — drop a model, see what it is, pick passes (or take the plan), run the
optimization, and then **run the result in the tab you are reading**. That last
part is the point: the browser number comes from a browser, on the same bytes,
with the same input shapes and graph optimization disabled on both sides. If a
pass fused the model into a private operator domain, this is where it fails to
load, which is the most useful failure the module can give you.

`onnxruntime-web` is loaded from a CDN, so the console needs network access in
the *browser*; the server side does not. Measured through it, on this box: a
transformer block that takes 1.1 ms on the server CPU takes 13.2 ms in a tab —
wasm+SIMD, single-threaded because the page is not cross-origin isolated. That
ratio is the number worth having before promising anybody browser inference,
and it is not one you can estimate from server timings.

## Endpoints

```
GET  /router                 every router and the terms it trades on
GET  /router/models          the market: one row per model, cheapest router first
                             ?q= &input= &output= &coin= &kyc= &max_usd=
                             &min_context= &multimodal= &free= &sort= &limit=
GET  /router/offerings       one row per (router, model) — same filters
GET  /router/modalities      what the registry can do, counted from live catalogs
GET  /router/refresh         re-fetch every catalog now
GET  /router/plan?model=     the ranking, spending nothing
POST /router/chat            {model, prompt|messages, require?, confirm?}
POST /router/key             {provider, key} — 0600, off the tree
GET  /router/spend           per-router cost, latency and billing drift
GET  /router/ledger          the append-only call log

GET  /settle                 watcher state, arming, caps, 24h spend
GET  /settle/balances        what each funded router has left
GET  /settle/rails           which chain modules are reachable
GET  /settle/policy          POST {armed, rail, coin, floor_usd, topup_usd,
                             daily_cap_usd, confirm} — arming needs confirm
POST /settle/sweep           one pass: read balances, propose or execute
GET  /settle/proposals       the queue    POST /settle/pay {..., confirm}
POST /settle/address         {provider, address, coin}
POST /settle/start           run the sweep on a timer   POST /settle/stop

GET  /health          runtime versions, execution providers, available passes
GET  /models          the store          POST /models {data|path|url}
GET  /blob/:id        raw .onnx bytes — what the browser fetches
GET  /inspect?model=  ops, params, inputs, outputs, opset, arch
GET  /plan?model=&target=local|web
GET  /passes          the catalog
POST /optimize        {model, passes?, batch?, runs?, samples?, tol?}
GET  /bench?model=&runs=&batch=&threads=&provider=
GET  /parity?a=&b=&samples=&tol=
GET  /portable?model=
POST /compare         {model, passes?} — every pass on its own, ranked
POST /export          {source, shape?, weights?}
POST /report          what a browser measured    GET /reports?model=
POST /mcp             MCP JSON-RPC 2.0 (40 tools)
```

## State and requirements

Models live in `~/.mod/infer/models/<sha256>.onnx` with `registry.json` beside
them (`INFER_DIR` moves it). Nothing here holds keys or money, so reads and
writes are both open.

Needs `onnx`, `onnxruntime` and `numpy`. `torch` is optional and only `export`
and `examples` touch it. `m infer/health` says which passes are actually
available on the box you are on — quantization and fp16 come from onnxruntime
itself, and a stripped build has neither.

## Tests

```
m infer/test
```

23 tests against a temporary store: content addressing, the pass mechanics
(`slim` really does drop a dangling initializer), lossless passes proven exact
and lossy ones proven to admit their cost, benchmark shape resolution, all three
portability verdicts — including the one this module originally got backwards —
and that `config.json`, the MCP tools and the REST routes still agree with each
other.
