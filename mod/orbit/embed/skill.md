# embed

Small models and what it costs to shrink them. Builds two tiny ONNX models on
the spot, reads and writes `.onnx` from raw protobuf (no `onnx` package), runs
them in a numpy interpreter, quantizes to float16 / int8 / 4 bits, and measures
what each step did to the **answers** — not just to the file size.

Dependencies: numpy. `onnxruntime` is optional and used only to cross-check.

API `:50620` · console `:50621/embed` · state `~/.mod/embed/models`

## When to reach for it

- "explain quantization / int8 / what ONNX actually is" — with runnable numbers
- "how much smaller can this model get before it breaks"
- compressing an `.onnx` file to float16 or int8 without a toolchain
- reading an `.onnx` file's graph, ops, shapes and weights
- a worked example of embeddings, cosine similarity and hash-bucket tokenizers
- checking whether *your* compressed model still agrees with the float one

Not for: real embedding quality (that's `embedcode` or `liquidai`), running
transformers (`liquidai`, `dev`), or integer-arithmetic inference — this module
does weight-only compression and says so.

## The one idea

```
scale = max|w| / 127        q = round(w / scale)        w' = q * scale
```

Everything else is variations: a zero point (asymmetric), one scale per channel
instead of per tensor, fewer bits. Compressed files stay valid ONNX — int8
initializers plus a standard `DequantizeLinear` node.

## CLI

```bash
m embed/info                             # the card
m embed/models                           # the zoo: bow-64, sent-mlp, minilm
m embed/build name=bow-64                # ~1s, deterministic from a seed
m embed/sweep name=bow-64                # every method, scored on the task
m embed/compare name=bow-64              # one tensor, bytes and error only
m embed/compress name=bow-64 method=int8 out=/tmp/small.onnx
m embed/inspect name=bow-64              # ops, shapes, weights, what runs here
m embed/search query="how fine a grind"
m embed/classify text="the film was dull"
m embed/collisions vocab=512             # what a smaller vocabulary costs
m embed/check name=bow-64 all=true       # vs onnxruntime, if installed
m embed/examples run=03                  # five lessons, in order
m embed/serve                            # api + console
```

`method` is one of `float32`, `float16`, `int8`, `int8-per-channel`, `int4-sim`.

## Reading a sweep

| column | means |
|---|---|
| `file_bytes` / `gzip_bytes` | on disk, and over the wire |
| `worst_tensor_rel_rmse` | worst per-tensor weight error introduced |
| `top1_accuracy` / `accuracy` | the model's own task |
| `agreement_with_float` | share of answers identical to float32 — **the strict metric** |

Agreement is what moves first. On `sent-mlp`, 4-bit keeps accuracy within 0.4
points while changing twelve answers; a report that prints only accuracy calls
that lossless.

## Facts worth quoting

- float16 and int8 are **free** on both built-in models: 4× smaller, agreement
  1.000 across all queries and all 420 held-out rows.
- 4 bits costs a retrieval question and 12 classifier answers — and both flips
  were decided in the float model by margins under 0.02. Compression error lands
  on the margin, not on the mean.
- `int8-per-channel` produces a *larger* file than `int8` on `bow-64` (8,192
  extra scales) and buys nothing the task can see.
- gzip alone saves ~7% on float32 — trained-weight mantissa bits are noise.
  Quantize first and gzip works.
- Dropping stopwords before mean-pooling moves retrieval 0.75 → 0.83 without
  touching a weight.
- Everything the module writes is checked against onnxruntime when installed:
  10 files, original and compressed, agreeing to ~1e-7.
- It works on real models: `all-MiniLM-L6-v2` (780 nodes) compresses 90.4 MB →
  23.0 MB per-channel int8, runs in onnxruntime, cosine 0.9994 against float32.

## Honest limits

Post-training only, weight-only (the file is int8; the arithmetic is float32 —
`MatMulInteger` is not implemented). The corpora are tiny and the classifier's
data is synthetic, so absolute scores mean nothing; the gap between float and
compressed on the same rows is the measurement. The numpy runtime implements 24
operators — a pulled transformer compresses fine but will not run here, and
`m embed/inspect` names the missing ops rather than failing deep.
