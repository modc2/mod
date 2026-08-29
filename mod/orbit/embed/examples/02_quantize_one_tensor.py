"""Quantization, on eight numbers you can check by hand.

Run: python3 examples/02_quantize_one_tensor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from src import quantize, zoo

print(__doc__.splitlines()[0])
print('=' * 72)
np.set_printoptions(suppress=True, linewidth=120)

# ── 1. eight weights ─────────────────────────────────────────────────
w = np.array([0.4213, -0.0072, 0.1885, -0.3301, 0.0004, 0.2560, -0.1113, 0.0921],
             dtype=np.float32)
scale = float(np.abs(w).max() / 127)

print('\n1. one scale, chosen so the largest weight lands on 127\n')
print(f'   weights   {np.round(w, 4)}')
print(f'   max|w|    {np.abs(w).max():.4f}')
print(f'   scale     max|w| / 127 = {scale:.8f}')

q = np.clip(np.rint(w / scale), -128, 127).astype(np.int8)
back = q.astype(np.float32) * scale

print(f'\n   q = round(w / scale)      {q.tolist()}')
print(f'   w\' = q * scale            {np.round(back, 4)}')
print(f'   error                     {np.round(w - back, 6)}')
print(f'\n   32 bytes became 8 bytes and 1 float. The largest number is exact by\n'
      f'   construction; the smallest, {w[4]:.4f}, rounds to {q[4]} and comes back\n'
      f'   as {back[4]:.4f}. Small weights are what quantization spends.')

# ── 2. where per-tensor goes wrong ───────────────────────────────────
print('\n2. one outlier ruins the scale for everybody\n')
spoiled = w.copy()
spoiled[0] = 12.0                      # one weight, thirty times the others
per_tensor = quantize.round_trip(spoiled, 'int8')
print(f'   same weights, but w[0] = 12.0 instead of {w[0]:.4f}')
print(f'   scale is now {np.abs(spoiled).max() / 127:.6f} — 28x coarser')
print(f'   restored     {np.round(per_tensor["restored"], 4)}')
print(f'\n   whole-tensor rel_rmse      {per_tensor["error"]["rel_rmse"]:>8.3%}   '
      '← looks fine')
rest = quantize.error(spoiled[1:], per_tensor['restored'][1:])
print(f'   rel_rmse of the other 7    {rest["rel_rmse"]:>8.3%}   ← is not fine')
print('\n   The outlier is stored exactly and it is huge, so it dominates the\n'
      '   average and the tensor-wide number stays small while the other seven\n'
      '   weights are wrecked — two of them rounded to zero. An aggregate error\n'
      '   is an average over things you may care about unequally.\n'
      '\n   This is why real quantizers work per channel: one bad column cannot\n'
      '   drag the rest of the tensor down with it.')

# ── 3. the whole tensor, every method ────────────────────────────────
model = zoo.load('bow-64')
weights = model.tensors()['embedding']

print(f'\n3. the real thing — bow-64\'s embedding table, {weights.shape}\n')
print(f'   {"method":<20}{"bytes":>12}{"ratio":>8}{"rel_rmse":>12}{"cosine":>12}')
print(f'   {"-" * 64}')
for row in quantize.compare(weights, axis=0)['methods']:
    label = row['method'] + (' (per-channel)' if row['per_channel'] else '')
    print(f'   {label:<20}{row["stored_bytes"]:>12,}{row["ratio"]:>7.1f}x'
          f'{row["error"]["rel_rmse"]:>11.3%}{row["error"]["cosine"]:>12.6f}')

print('\n   float16 is nearly free and halves the file. int8 costs about 1% of\n'
      '   the weights\' magnitude and quarters it. Per-channel scales cost 32KB\n'
      '   of extra floats here and halve the error again. int4 costs 20% — a\n'
      '   different order of thing entirely, and example 03 measures whether\n'
      '   any of that reaches the answers.')
