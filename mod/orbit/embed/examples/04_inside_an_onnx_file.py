"""What is actually inside an .onnx file, byte by byte.

Run: python3 examples/04_inside_an_onnx_file.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from src import compress, onnxfile, runtime, text, zoo

print(__doc__.splitlines()[0])
print('=' * 72)

path = zoo.ensure('bow-64')
raw = path.read_bytes()

# ── 1. the bytes ─────────────────────────────────────────────────────
print(f'\n1. the first 24 bytes of {path.name}\n')
print('  ', ' '.join(f'{b:02x}' for b in raw[:24]))
print('''
   08 08          field 1 (ir_version), varint → 8
   12 09 ...      field 2 (producer_name), 9 bytes → "mod/embed"
   3a ...         field 7 (graph), a message, and the rest of the file

   That is protobuf: a tag byte holding (field_number << 3 | wire_type), then
   the payload. No magic number, no header, no version string a human can read.
   An .onnx file is a protobuf message whose field numbers are written down in
   onnx.proto — and src/onnxfile.py is those field numbers plus a hundred lines
   of parsing.''')

# ── 2. the graph ─────────────────────────────────────────────────────
model = onnxfile.load(path)
print('\n2. the graph it decodes to\n')
for value in model.graph.inputs:
    print(f'   input    {value.name:<12} {value.shape}')
for node in model.graph.nodes:
    print(f'   {node.op_type:<18}{node.inputs} → {node.outputs}')
for value in model.graph.outputs:
    print(f'   output   {value.name:<12} {value.shape}')
print('\n   weights:')
for tensor in model.graph.initializers:
    print(f'     {tensor.name:<12} {str(tensor.array.dtype):<9}'
          f'{str(tuple(tensor.array.shape)):<14}{tensor.nbytes:>10,} bytes')

# ── 3. running it is a for-loop ──────────────────────────────────────
ids = text.token_ids('the water should be just off the boil', drop_stopwords=True)
result = runtime.run(model, {'input_ids': ids}, trace=True)
print('\n3. running it — one dictionary, five steps\n')
for step in result['__trace__']:
    print(f'   {step["op"]:<16}→ {str(step["out_shape"]):<10}'
          f'{step["out_dtype"]:<10}{step["ms"]:>7.3f} ms')
print(f'\n   That is the whole of inference: each op reads names out of a dict\n'
      f'   and writes names back in. src/runtime.py implements '
      f'{len(runtime.implemented())} operators\n   in about 250 lines; a real '
      f'runtime implements roughly 190 and spends the\n   rest of its size on '
      f'making them fast.')

# ── 4. what compression changed ──────────────────────────────────────
target = path.with_name('bow-64.example.onnx')
report = compress.compress_file(path, target, 'int8-per-channel')
smaller = onnxfile.load(target)

print('\n4. the same graph after int8 compression\n')
for node in smaller.graph.nodes:
    marker = '  ← new' if node.op_type == 'DequantizeLinear' else ''
    print(f'   {node.op_type:<20}{node.inputs} → {node.outputs}{marker}')
print('\n   weights:')
for tensor in smaller.graph.initializers:
    print(f'     {tensor.name:<26} {str(tensor.array.dtype):<9}'
          f'{str(tuple(tensor.array.shape)):<14}{tensor.nbytes:>10,} bytes')

print(f"""
   The embedding table is int8 now, with 8,192 scales beside it, and one new
   node — DequantizeLinear, a standard ONNX operator — turns them back into
   floats before Gather sees them. Nothing downstream knows anything happened.

   {report['file_bytes_before']:,} bytes → {report['file_bytes_after']:,} bytes.

   This is what "weight-only quantization" means, and it is worth being precise
   about: the *file* is int8, the *arithmetic* is still float32. Making the
   matrix multiplies run in integers is a different and much larger change —
   ONNX spells that one MatMulInteger, and this module does not do it.""")

target.unlink(missing_ok=True)
print('\n   Next: 05_search_after_compression.py — where an answer finally moves.')
