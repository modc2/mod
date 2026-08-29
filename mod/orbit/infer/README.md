# infer

Inference optimization for any model architecture, on one standard binary.

```
m infer/examples                          # three models to work on
m infer/inspect mlp                       # ops, params, shapes, opset
m infer/plan cnn target=web               # what to try, and why
m infer/optimize mlp slim,extended        # do it — measured both ways
m infer/compare cnn                       # every pass, side by side
m infer/bench cnn runs=100 threads=1      # p50/p90/p99, warmed up
m infer/parity mlp mlp+slim+extended      # did the answers survive
m infer/portable cnn+slim+extended        # will it run in a browser
m infer/export torchvision:resnet18       # torch → the standard binary
m infer/serve
```

API `:50820` (`/api/infer`) · console `/infer` · MCP `POST /mcp` (14 tools)

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
POST /mcp             MCP JSON-RPC 2.0 (14 tools)
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
