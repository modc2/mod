# embed

**Small models, made smaller, with the cost written down.**

Everyone shipping a model on a phone, in a browser, or on a box without a GPU
ends up at the same question: how much smaller can this get before it stops
being the model I tested? The answer is almost always given as a ratio — "4×
smaller with int8!" — and a ratio is only the half of the trade that flatters
whoever is quoting it.

This module is the other half. It builds two models small enough to hold in your
head, compresses them every way it knows, and then measures what each one did to
the *answers*. It has no dependency but numpy, and — deliberately — writes and
reads `.onnx` files itself, so nothing between you and the bytes is a black box.

```bash
m embed/build name=bow-64      # a 2 MB embedder, built from a seed in a second
m embed/sweep name=bow-64      # every compression method, scored on the task
m embed/examples run=03        # the same thing, narrated
m embed/serve                  # API :50620, console :50621/embed
```

## The table this module exists to produce

```
bow-64 — hashing bag-of-words → 64-d random projection, mean-pooled, normalised

   method                      file    gzipped   weights   retrieval top-1   agreement
   -----------------------------------------------------------------------------------
   float32               2,097,563B 1,949,462B    0.00%             0.833       1.000
   float16               1,049,063B   967,782B    0.02%             0.833       1.000
   int8                    524,910B   444,012B    1.15%             0.833       1.000
   int8-per-channel        565,887B   530,771B    0.60%             0.833       1.000
   int4-sim                524,910B   201,556B   20.82%             0.750       0.833

sent-mlp — 4096-bucket bag → 64 hidden units → 2 classes

   method                      file    gzipped   weights held-out accuracy   agreement
   -----------------------------------------------------------------------------------
   float32               1,049,814B   975,616B    0.00%             0.883       1.000
   float16                 525,584B   484,908B    0.02%             0.883       1.000
   int8                    263,547B   228,089B    1.04%             0.883       1.000
   int8-per-channel        263,877B   237,779B    0.87%             0.883       1.000
   int4-sim                263,547B   107,067B   18.88%             0.879       0.971
```

`agreement` is the share of answers identical to the float32 model's. It is the
strict metric and the one that moves first. Four things fall out of the table:

1. **float16 and int8 are free here.** Not nearly free — every one of the twelve
   retrieval questions and all 420 classification rows come back identical, at a
   quarter of the bytes.

2. **Accuracy hides swaps.** `sent-mlp` at 4 bits loses 0.4 points of accuracy
   and changes **twelve** answers; mistakes and corrections cancel. Ship on
   accuracy alone and you will call that lossless.

3. **Per-channel is not automatically better.** On `bow-64` it halves the weight
   error and produces a *larger* file than plain int8 — 8,192 extra scales
   against a 2 MB table — and buys nothing the task can see.

4. **gzip and quantization are not alternatives.** gzip saves ~7% on float32,
   because the low mantissa bits of a trained weight are noise and noise does not
   compress. Quantize first and gzip suddenly works.

## What breaks, and where

The interesting result is not that compression works. It is *which* answers go
first. From `examples/05`:

```
   "why add an index to a table"
      float32  +0.485  an index makes reads fast and writes a little slower
      int4     +0.479  a full table scan is fine when the table is small
      the float model only won that one by 0.010
```

Both of the answers 4-bit quantization changed were decided, in the float model,
by margins of 0.010 and 0.016. Everything the float model was sure about, the
4-bit model is still sure about. Compression error lands on the margin, not on
the mean — which is why the way to know whether a compressed model is safe for
your workload is to measure agreement on *your* inputs, not to read a ratio off
somebody's table. Including this one.

## The models

Both are built here, on this box, in a couple of seconds. Nothing is downloaded
unless you ask.

| | what it is | size |
|---|---|---|
| `bow-64` | words hashed into 8,192 buckets, a 64-d random projection, mean-pooled and normalised | 2.0 MB |
| `sent-mlp` | 4,096-bucket bag → 64 hidden units → 2 classes, trained here in numpy | 1.0 MB |
| `minilm` | `all-MiniLM-L6-v2`, a real 22M-parameter transformer — `m embed/pull` | ~90 MB |

Neither of the first two is good at its job the way a downloaded transformer is
good at its job, and neither pretends to be. `bow-64`'s "understanding" is word
overlap, survived through a random projection — sentences sharing words point
the same way, and that is the entire mechanism. They exist because compression
is far easier to understand on a model you watched get built: when int8 moves an
answer you can go back to the row of weights it happened in.

The honest limits are worth stating plainly:

- The retrieval corpus is 36 sentences and the classifier's data is synthetic.
  Absolute numbers here mean nothing. The *gap* between the float model and the
  compressed one on the same rows means something, and that is all this module
  claims to measure.
- Every method is post-training. No retraining, no calibration set, no
  quantization-aware training — so the ceiling is whatever the float model
  already knew.
- Compression is **weight-only**: the file is int8, the arithmetic is still
  float32. Making the matrix multiplies run in integers is a different and much
  larger change — ONNX spells that one `MatMulInteger` — and this module does not
  do it or claim to.

## No black boxes

The `onnx` package is not a dependency. Neither is `onnxruntime`, `torch`, or
`transformers`. What that buys is that every layer is inspectable:

| file | what it is | lines |
|---|---|---|
| `src/onnxfile.py` | an `.onnx` file read and written from raw protobuf | ~400 |
| `src/runtime.py` | those graphs run in numpy — 24 operators | ~250 |
| `src/quantize.py` | float32 → float16 / int8 / int4, and the error each costs | ~180 |
| `src/compress.py` | the same applied to a model file, still valid ONNX | ~200 |
| `src/text.py` | words → integers, by hashing, no vocabulary file | ~90 |
| `src/evaluate.py` | what compression did to the answers | ~170 |

A compressed model from here is an ordinary ONNX file. The weights are int8
initializers and a standard `DequantizeLinear` node — an operator from the spec,
not an invention of this module — turns them back into floats before the op that
consumes them. Any runtime reads it.

### Which is checked, not asserted

Home-made code that is wrong in a self-consistent way passes every test it
writes for itself. So when `onnxruntime` *is* installed, the module hands it the
same files and compares:

```bash
pip install onnxruntime          # optional
m embed/check all=true
```

```
bow-64    float32 / float16 / int8 / int8-per-channel / int4-sim   ok, worst Δ 3e-08
sent-mlp  float32 / float16 / int8 / int8-per-channel / int4-sim   ok, worst Δ 1e-07
```

Ten files, original and compressed, agreeing to ~1e-7 with an implementation
that shares no code with this one. That is what makes "it's a valid ONNX file"
a statement rather than a hope. Nothing else in the module imports onnxruntime;
a box without it loses this cross-check and nothing else.

## The lessons

Five scripts, in order, each one runnable on its own and each printing real
numbers computed as it goes:

```bash
python3 examples/01_what_an_embedding_is.py       # a sentence becomes 64 numbers
python3 examples/02_quantize_one_tensor.py        # scale, round, restore, by hand
python3 examples/03_compress_a_model.py           # the table above, produced live
python3 examples/04_inside_an_onnx_file.py        # the protobuf, the graph, the loop
python3 examples/05_search_after_compression.py   # where an answer finally moves
```

Some of what they show, which is the part not usually in the tutorial:

- **The noise floor is not zero.** Two coffee sentences with no shared word
  score *lower* than a coffee sentence and a sailing one. Below ~0.15 the
  ordering is meaningless, and a similarity score with no sense of its own noise
  floor is how a demo becomes a wrong answer. (`01`)
- **An outlier ruins the scale for everybody.** One weight thirty times the rest
  leaves the tensor-wide error at 0.5% and the other seven weights at 12%. An
  aggregate error is an average over things you may care about unequally. (`02`)
- **The tokenizer is doing as much work as the model.** Dropping stopwords
  before mean-pooling takes retrieval from 0.75 to 0.83 without touching a
  weight. At 8,192 buckets 4% of words share one; at 64 buckets, 98% do. (`01`)
- **A scaling choice three files away decides whether training works.**
  L1-normalised bags make the first layer's outputs so small that half the ReLUs
  never fire and the same loop converges to the class prior. (`src/text.py`)

## Layout

```
mod.py             the module surface — every fn the API also exposes
config.json        ports, endpoints, and what each method costs
src/onnxfile.py    protobuf in, protobuf out
src/runtime.py     the graph, run in numpy
src/quantize.py    the arithmetic, and the measurement
src/compress.py    a whole model, rewritten smaller
src/zoo.py         the models, built here
src/text.py        hashing tokenizer
src/data.py        the corpora — small enough to read in full
src/evaluate.py    task metrics, agreement, the sweep
src/check.py       the same files through onnxruntime
src/api.py         FastAPI, :50620
src/app.py         the console, stdlib only, :50621/embed
examples/          five lessons, in order
tests/             23 tests, ~8 seconds
```

State lives in `~/.mod/embed/models` — the built models and a ledger of what
each was built from. Nothing else is kept, and deleting it costs a rebuild of a
couple of seconds.

## CLI

```bash
m embed/info                             # the card
m embed/models                           # the zoo, and what is built
m embed/build name=sent-mlp              # trains it here, ~2s
m embed/search query="how fine a grind"  # rank the built-in corpus
m embed/classify text="the film was dull"
m embed/compare name=bow-64              # one tensor, every method
m embed/compress name=bow-64 method=int8 out=/tmp/small.onnx
m embed/sweep name=sent-mlp              # the table
m embed/inspect name=bow-64              # ops, shapes, weights, what runs here
m embed/collisions vocab=512             # what a smaller vocabulary costs
m embed/check name=bow-64 all=true       # against onnxruntime
m embed/examples                         # list the lessons
m embed/test
```

## Pointing it at a real model

```bash
m embed/pull name=minilm                              # ~90 MB from Hugging Face
m embed/inspect name=minilm                           # what it is made of
m embed/compress name=minilm method=int8-per-channel out=/tmp/minilm-int8.onnx
```

`all-MiniLM-L6-v2`, exported by somebody else's toolchain — 780 nodes, 23
operator types, opset 14:

```
90,405,214 bytes → 23,048,254 bytes          3.92×, in 9 seconds
worst tensor rel_rmse                        2.1%
cosine(float32, int8) on a sentence embedding   0.9994
```

Loaded and run by onnxruntime, not by anything here. The compressor works on any
ONNX file because rewriting initializers and inserting dequantize nodes needs no
understanding of what the ops do.

The numpy runtime is another matter: it implements 24 operators, this model uses
23 types including `Slice`, `Expand` and `Where` that it does not have, and
`m embed/inspect` names the missing ones rather than failing somewhere deep. So
for a pulled model you get real compression numbers and file sizes, and the task
metrics stop — score it with onnxruntime and your own data, which is what you
would want to do anyway.

Pointing it at a real model is also what found the one genuine bug in this
module's writer. `np.ascontiguousarray` quietly promotes a 0-d array to shape
`(1,)`, so every scalar `Constant` came back out as a rank-1 tensor. The two
models built here have no scalar constants, so every test passed and every file
this module wrote, it could also read — while no other runtime would load them.
A real 780-node graph from a different exporter failed on the first `Concat` that
consumed one. There is a test named after it now, and it is the argument for
this section existing at all.
