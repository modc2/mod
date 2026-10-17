"""Compressing a whole model, and measuring what it cost the answers.

Run: python3 examples/03_compress_a_model.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import evaluate, zoo

print(__doc__.splitlines()[0])
print('=' * 78)

for name, metric, label in (('bow-64', 'top1_accuracy', 'retrieval top-1'),
                            ('sent-mlp', 'accuracy', 'held-out accuracy')):
    report = evaluate.sweep(name)
    print(f'\n{name} — {zoo.CATALOG[name]["about"]}')
    print(f'built from {report["source_bytes"]:,} bytes of float32\n')
    print(f'   {"method":<20}{"file":>12}{"gzipped":>11}{"weights":>10}'
          f'{label:>18}{"agreement":>12}')
    print(f'   {"-" * 83}')
    for row in report['results']:
        print(f'   {row["method"]:<20}{row["file_bytes"]:>11,}B'
              f'{row["gzip_bytes"]:>10,}B'
              f'{row["worst_tensor_rel_rmse"]:>9.2%}'
              f'{row.get(metric, 0):>18.3f}'
              f'{row.get("agreement_with_float", 1.0):>12.3f}')

print("""
Reading the table
-----------------
`weights` is the worst per-tensor error the compression introduced. `agreement`
is the fraction of answers identical to the float32 model's — the strict metric,
and the one that moves first.

`int4-sim`'s file is the same size as int8's, and that is not a mistake: ONNX
has no 4-bit tensor before opset 21, so the values use sixteen levels while
still occupying a byte each. It is there to measure the *accuracy* of 4 bits,
not the size. Its gzip column is the honest one — 201 KB against int8's 444 KB,
because four bits of entropy per byte is exactly what a compressor eats.

Three things worth taking away:

1. float16 and int8 are free here. Not "nearly free": every one of the twelve
   retrieval questions and all 420 classification rows come back identical, at
   a quarter of the bytes. For models this size, weight-only int8 is not a
   trade-off you need to think hard about.

2. int4 is not free, and the two models fail differently. Retrieval loses a
   question outright. The classifier's accuracy barely moves — and twelve of
   its answers still changed, cancelling out. If you had shipped it watching
   accuracy alone you would have called that lossless.

3. gzip and quantization are not alternatives. gzip alone saves ~7% on float32,
   because the low mantissa bits of a trained weight are noise and noise does
   not compress. Quantize first and gzip suddenly works — there is much less
   entropy left to store.

`int8-per-channel` is *larger* than plain `int8` on bow-64: 8,192 extra scales
against a 2 MB table. It halves the weight error and, here, buys nothing the
task can see. That is worth knowing before you reach for it by reflex.

Next: 04_inside_an_onnx_file.py — what actually changed inside the file.
""")
