# infer

Inference optimization for any model architecture, on one standard binary:
**ONNX**, executed by `onnxruntime` on the server and `onnxruntime-web` in a
browser tab without being converted again. Fourteen MCP tools, a REST API and a
console on one port (`:50820`) — same code, so an agent, a shell and a human
never get different numbers.

API `:50820` (`/api/infer`) · console `/infer` · MCP `POST /mcp`

## When to reach for it

- "this model is too slow" / "too big to ship" — and you have (or can produce)
  an `.onnx`
- "can I run this in the browser" — and, much more often, "why did it stop
  working in the browser after I optimized it"
- "is int8 worth it here" — this is a measurement, never a rule of thumb
- "what did this optimization cost me in accuracy"
- "how fast is this model, honestly" — p50/p90/p99, warmed up, with the input
  shape it actually used stated
- reading an unfamiliar `.onnx`: what ops, how many parameters, which
  dimensions are symbolic, which opset

Not for: training or fine-tuning (`freetune`), serving an LLM (`dev`,
`openrouter`), GPU rental (`compute`, `targon`, `lium`), or running a model
somebody else hosts. This module makes a binary cheaper to execute; it does not
execute it for you in production.

## The order that matters

1. **`infer_add`** (or `infer_export` from torch, or `infer_examples` to get
   something to try) — a model has to be in the store before anything else
   works. Stored under the SHA-256 of its bytes.
2. **`infer_inspect`** — what it is. Read `inputs` first: any dimension that
   comes back as a *string* is symbolic and nothing can be timed until you
   decide what it should be.
3. **`infer_plan`** — what is worth trying on this graph and why. Pass
   `target=web` if it is going to a browser; the plan is genuinely different,
   not a formality.
4. **`infer_optimize`** — the one that does the work. It already benchmarks
   before and after, compares the outputs, and re-checks portability, so a
   separate bench/parity call afterwards is usually redundant.
5. Read **`verdict`** first, then **`portability_lost`** if present.

`infer_compare` short-circuits all of this when the plan is not obvious: it
applies each pass on its own to the same model and ranks them.

## The two things to actually watch for

**Quantization frequently makes small models slower.** int8 is a memory-
bandwidth optimization; if the weights already fit in cache there is no
bandwidth to save and the dequantize nodes are added work. On a 302k-parameter
MLP here: `int8` was 3.9× smaller and **1.2× slower**, `fp16` was 2× smaller and
**2.5× slower**. Both are still right for a phone downloading over a cell
network — different question, and `plan target=web` weighs it that way. Never
recommend quantization without running `infer_compare` or `infer_optimize` on
the actual model.

**Exactly one pass breaks browser deployment: `all`.** It rewrites the graph
into `com.microsoft.nchwc.*` layout operators for the CPU that produced them,
and onnxruntime-web refuses to create a session: *"com.microsoft.nchwc.Conv(-1)
is not a registered function/op"*. It is in no default plan for either target.

**`extended` does not.** This module claimed it did — fusion emits
`com.microsoft.FusedConv` and `com.microsoft.BiasGelu`, which look just as
vendor-specific — and the console disproved it: both loaded and ran in the wasm
backend, which registers the contrib domain. So do not steer anyone off
`extended` for portability reasons. `infer_portable` returns `portable: true`
with a `cautions` entry for contrib operators, and `portable: false` only for
the nchwc family and third-party custom domains.

`slim,extended` is therefore the right default for a browser build too. Where
web genuinely differs from local is **bytes**: the download usually costs more
than the arithmetic, so `plan target=web` reaches for quantization at a much
lower size threshold.

## Reading a report

`verdict` is one sentence and it is not padded — "1.15× slower, 3.87× smaller,
outputs moved by at most 4.26e-02, argmax agrees 100% of the time" is a normal
result and it is telling you to think.

- `passes[]` — per pass: nodes before/after, `removed` (which operators
  disappeared), bytes, and how long the pass took
- `speed` — p50 before and after, same process, same seeded inputs
- `parity` — `max_abs_err`, `max_rel_err`, `cosine`, `argmax_agreement`.
  Lossless passes must come back `identical` at exactly 0; if they do not,
  something is wrong with the model, not with the tolerance
- `portable` / `portability_lost` — `portable: true` with `cautions` is a pass,
  not a warning to act on; `portability_lost` means `all` was in the list
- `check_failed` — the passes ran but the measurement did not (a model that
  needs real inputs, usually). The optimized binary is still stored.

## Shapes

Symbolic dimensions have to be resolved before anything can run. Default: the
first symbolic dimension takes `batch` (default 1), the rest take 1. Override by
name — `shapes={"input_ids": "1,128"}` — for any model where sequence length is
what determines cost. Every benchmark echoes the shape it used; a latency number
without one is meaningless, so quote both.

Inputs are random: seeded gaussians for floats, 0/1 for integer inputs (which
are almost always embedding indices, and a random int64 is an out-of-range
lookup and a crash). That is fine for latency and for comparing two models
against each other. It is **not** an accuracy evaluation — `argmax_agreement`
on random inputs says the graph transform is faithful, not that the model is
good.

## The browser half

The console at `/infer` fetches `/blob/<id>` — the exact bytes the server
benchmarked — into onnxruntime-web and runs it in the tab, with the same input
shapes and graph optimization disabled on both sides, then posts the numbers
back to `/reports`. Point people at it rather than estimating browser
performance from server numbers: wasm has no AVX-512, and with no cross-origin
isolation it has no threads either. Measured here, a transformer block was
1.1 ms on the server CPU and 13.2 ms in the tab — the gap is large, and it is
model-specific enough that guessing it is not worth doing.

## Gotchas

- `export` and `examples` need torch; nothing else does. `infer_health` says
  what is available on the box.
- Benchmarks deliberately disable onnxruntime's own graph optimization. Turning
  it on would re-apply the passes at load time and every model would measure
  the same.
- `all` is not in any default plan. It is fast and it embeds decisions about
  the CPU that made it.
- Sizes are the file. A model that stores weights externally will look tiny and
  benchmark honestly.
