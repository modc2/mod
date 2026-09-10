"""Where a compressed model finally gives a different answer.

Run: python3 examples/05_search_after_compression.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from src import compress, data, evaluate, onnxfile, zoo

print(__doc__.splitlines()[0])
print('=' * 76)

source = zoo.ensure('bow-64')
full = onnxfile.load(source)
corpus = [d for _, d in data.DOCS]

# ── 1. search, at full precision ─────────────────────────────────────
query = 'how finely should i grind the beans'
print(f'\n1. float32 — "{query}"\n')
for hit in evaluate.search(full, query, top=3):
    print(f'   {hit["score"]:+.3f}  {hit["text"]}')

# ── 2. the same question of every compressed model ───────────────────
print('\n2. the same three answers, from every compressed model\n')
variants = {}
for method in ('float16', 'int8', 'int8-per-channel', 'int4-sim'):
    target = source.with_name(f'bow-64.demo-{method}.onnx')
    compress.compress_file(source, target, method)
    variants[method] = onnxfile.load(target)
    top = evaluate.search(variants[method], query, top=3)
    same = [h['index'] for h in top] == [h['index'] for h in
                                         evaluate.search(full, query, top=3)]
    print(f'   {method:<20}{"same ranking" if same else "DIFFERENT":<14}'
          f'top score {top[0]["score"]:+.4f}  "{top[0]["text"][:38]}"')
    target.unlink(missing_ok=True)

# ── 3. hunt for the questions that moved ─────────────────────────────
print('\n3. across all twelve built-in questions, what 4-bit changed\n')
reference = {q: evaluate.search(full, q, top=1)[0] for q, _ in data.QUERIES}
moved = 0
for question, expected in data.QUERIES:
    theirs = evaluate.search(variants['int4-sim'], question, top=1)[0]
    if theirs['index'] != reference[question]['index']:
        moved += 1
        was, now = reference[question], theirs
        print(f'   "{question}"   (topic: {expected})')
        print(f'      float32  {was["score"]:+.3f}  {was["text"]}')
        print(f'      int4     {now["score"]:+.3f}  {now["text"]}')
        margin = abs(was['score'] - evaluate.search(full, question, top=2)[1]['score'])
        print(f'      the float model only won that one by {margin:.3f}\n')

print(f'   {moved} of {len(data.QUERIES)} answers changed.')

print("""
The pattern is not "the model got worse at everything". It got worse at the
questions that were close calls to begin with — where the top two documents
were separated by less than the noise the quantizer added. Everything the float
model was sure about, the 4-bit model is still sure about.

That is the practical shape of the whole subject:

  · compression error lands on the margin, not on the mean
  · so a model with confident, well-separated answers compresses further than
    one that is always deciding between two near-identical options
  · and the way to know which you have is to measure agreement on your own
    inputs — not to read a ratio off someone else's table

The measurement is the deliverable. `m embed/sweep name=bow-64` runs it, and
src/evaluate.py is forty lines you can point at your own model and corpus.
""")
