"""Train the zoo's `digits` model and write src/zoo/digits.tcm1.

Run once, commit the blob. Pure python, no numpy, no dataset download: the
training set is generated from ten 8x8 glyphs by shifting and flipping
pixels, with a fixed seed, so this script is itself reproducible.

    python3 tools/train_digits.py

Float SGD trains it; the last step QUANTISES to the int8/int32 form
tensors.py can prove (per-tensor symmetric scales, a calibrated requantise
multiplier), and the script prints both accuracies so the cost of
quantisation is on the record rather than assumed away.
"""

import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))

import model as M                                              # noqa: E402

GLYPHS = {
    0: ('.####...', '#....#..', '#....#..', '#....#..',
        '#....#..', '#....#..', '#....#..', '.####...'),
    1: ('...##...', '..###...', '.#.##...', '...##...',
        '...##...', '...##...', '...##...', '.#####..'),
    2: ('.####...', '#....#..', '.....#..', '....#...',
        '...#....', '..#.....', '.#......', '######..'),
    3: ('.####...', '#....#..', '.....#..', '..###...',
        '.....#..', '.....#..', '#....#..', '.####...'),
    4: ('....#...', '...##...', '..#.#...', '.#..#...',
        '#...#...', '######..', '....#...', '....#...'),
    5: ('######..', '#.......', '#.......', '#####...',
        '.....#..', '.....#..', '#....#..', '.####...'),
    6: ('..###...', '.#......', '#.......', '#####...',
        '#....#..', '#....#..', '#....#..', '.####...'),
    7: ('######..', '#....#..', '.....#..', '....#...',
        '...#....', '...#....', '...#....', '...#....'),
    8: ('.####...', '#....#..', '#....#..', '.####...',
        '#....#..', '#....#..', '#....#..', '.####...'),
    9: ('.####...', '#....#..', '#....#..', '#....#..',
        '.#####..', '.....#..', '....#...', '.###....'),
}

W, H, IN, HID, OUT = 8, 8, 64, 24, 10


def glyph_px(d):
    return [1.0 if c == '#' else 0.0 for row in GLYPHS[d] for c in row]


def shift(px, dx, dy):
    out = [0.0] * IN
    for y in range(H):
        for x in range(W):
            sx, sy = x + dx, y + dy
            if 0 <= sx < W and 0 <= sy < H:
                out[sy * W + sx] = px[y * W + x]
    return out


def sample(rng, d, noise=0.06):
    px = shift(glyph_px(d), rng.choice((-1, 0, 0, 1)),
               rng.choice((-1, 0, 0, 1)))
    return [(1.0 - v if rng.random() < noise else v) for v in px]


def dataset(rng, per_class):
    data = []
    for d in range(10):
        for _ in range(per_class):
            data.append((sample(rng, d), d))
    rng.shuffle(data)
    return data


def forward(w1, b1, w2, b2, x):
    h = [max(0.0, sum(w1[j][i] * x[i] for i in range(IN)) + b1[j])
         for j in range(HID)]
    o = [sum(w2[k][j] * h[j] for j in range(HID)) + b2[k] for k in range(OUT)]
    return h, o


def softmax(o):
    m = max(o)
    e = [2.718281828459045 ** (v - m) for v in o]
    s = sum(e)
    return [v / s for v in e]


def train(rng, data, epochs=24, lr=0.06):
    w1 = [[rng.uniform(-0.3, 0.3) for _ in range(IN)] for _ in range(HID)]
    b1 = [0.0] * HID
    w2 = [[rng.uniform(-0.3, 0.3) for _ in range(HID)] for _ in range(OUT)]
    b2 = [0.0] * OUT
    for ep in range(epochs):
        loss = 0.0
        for x, y in data:
            h, o = forward(w1, b1, w2, b2, x)
            p = softmax(o)
            loss -= math.log(max(p[y], 1e-12))
            dz = [p[k] - (1.0 if k == y else 0.0) for k in range(OUT)]
            dh = [0.0] * HID
            for k in range(OUT):
                if dz[k] == 0.0:
                    continue
                row = w2[k]
                for j in range(HID):
                    dh[j] += dz[k] * row[j]
            for k in range(OUT):
                g = lr * dz[k]
                row = w2[k]
                for j in range(HID):
                    row[j] -= g * h[j]
                b2[k] -= g
            for j in range(HID):
                if h[j] <= 0.0 or dh[j] == 0.0:
                    continue
                g = lr * dh[j]
                row = w1[j]
                for i in range(IN):
                    if x[i]:
                        row[i] -= g * x[i]
                b1[j] -= g
        print(f'  epoch {ep + 1:2d}/{epochs}  loss {loss / len(data):.4f}')
    return w1, b1, w2, b2


def acc_float(w1, b1, w2, b2, data):
    ok = 0
    for x, y in data:
        _h, o = forward(w1, b1, w2, b2, x)
        ok += (max(range(OUT), key=lambda k: (o[k], -k)) == y)
    return ok / len(data)


def q(v, scale):
    n = int(round(v / scale)) if scale else 0
    return max(-127, min(127, n))


def quantise(w1, b1, w2, b2, data):
    """Per-tensor symmetric int8 + an integer requantise multiplier."""
    x_scale = 1.0 / 127.0
    w1_max = max(abs(v) for row in w1 for v in row)
    w2_max = max(abs(v) for row in w2 for v in row)
    w1_scale, w2_scale = w1_max / 127.0, w2_max / 127.0
    acc1_scale = x_scale * w1_scale
    w1q = [[q(v, w1_scale) for v in row] for row in w1]
    w2q = [[q(v, w2_scale) for v in row] for row in w2]
    b1q = [int(round(v / acc1_scale)) for v in b1]

    # calibrate the hidden range on real data, post-relu
    hmax = 1e-9
    for x, _y in data:
        h, _o = forward(w1, b1, w2, b2, x)
        hmax = max(hmax, max(h))
    h_scale = hmax / 127.0
    ratio = acc1_scale / h_scale
    shift = 24
    mult = int(round(ratio * (1 << shift)))
    while mult > (1 << 30) and shift > 0:
        shift -= 1
        mult = int(round(ratio * (1 << shift)))
    while mult < (1 << 20) and shift < 40:
        shift += 1
        mult = int(round(ratio * (1 << shift)))
    acc2_scale = h_scale * w2_scale
    b2q = [int(round(v / acc2_scale)) for v in b2]
    return {'w1': w1q, 'b1': b1q, 'w2': w2q, 'b2': b2q,
            'mult': mult, 'shift': shift,
            'scales': {'input': x_scale, 'w1': w1_scale, 'w2': w2_scale,
                       'hidden': h_scale, 'logit': acc2_scale}}


def main():
    rng = random.Random(20261023)
    train_set = dataset(rng, 90)
    test_set = dataset(random.Random(7), 40)
    print(f'training on {len(train_set)} samples, testing on '
          f'{len(test_set)}')
    w1, b1, w2, b2 = train(rng, train_set)
    fa = acc_float(w1, b1, w2, b2, test_set)
    qd = quantise(w1, b1, w2, b2, train_set)

    import tensors as T
    import runtime as R
    tensors = [
        {'name': 'fc1.w', 'dtype': 'i8', 'shape': [HID, IN],
         'data': T.to_i8([v for row in qd['w1'] for v in row])},
        {'name': 'fc1.b', 'dtype': 'i32', 'shape': [HID],
         'data': T.to_i32(qd['b1'])},
        {'name': 'fc2.w', 'dtype': 'i8', 'shape': [OUT, HID],
         'data': T.to_i8([v for row in qd['w2'] for v in row])},
        {'name': 'fc2.b', 'dtype': 'i32', 'shape': [OUT],
         'data': T.to_i32(qd['b2'])},
    ]
    graph = [
        {'op': 'matvec', 'w': 'fc1.w', 'x': 'in', 'out': 'a1'},
        {'op': 'bias', 'b': 'fc1.b', 'x': 'a1', 'out': 'a1b'},
        {'op': 'relu', 'x': 'a1b', 'out': 'a1r'},
        {'op': 'requant', 'x': 'a1r', 'out': 'h1',
         'mult': qd['mult'], 'shift': qd['shift']},
        {'op': 'matvec', 'w': 'fc2.w', 'x': 'h1', 'out': 'a2'},
        {'op': 'bias', 'b': 'fc2.b', 'x': 'a2', 'out': 'logits'},
        {'op': 'argmax', 'x': 'logits', 'out': 'cls'},
    ]
    io = {'input': {'buffer': 'in', 'dtype': 'i8', 'len': IN,
                    'what': '8x8 greyscale, row major, 0..127 per pixel'},
          'output': {'buffer': 'logits', 'dtype': 'i32'},
          'class': 'cls', 'labels': [str(d) for d in range(10)]}
    blob = M.build('digits', tensors, graph, io, meta={
        'what': 'handwriting-shaped 8x8 digit classifier, 64-24-10 MLP',
        'trained': 'pure-python float SGD on generated glyph variants '
                   '(tools/train_digits.py, seed 20261023), then quantised '
                   'to int8 per tensor',
        'params': HID * IN + HID + OUT * HID + OUT,
        'scales': qd['scales']})
    m = M.parse(blob)

    ok = 0
    for x, y in test_set:
        px = [max(0, min(127, int(round(v * 127)))) for v in x]
        res = R.run(m, R.parse_input(m, values=px), trace=False)
        ok += (res['class'] == y)
    qa = ok / len(test_set)
    print(f'\nfloat accuracy      {fa * 100:.1f}%')
    print(f'quantised accuracy  {qa * 100:.1f}%   (int8, what is provable)')
    print(f'model_root          {m["model_root"]}')
    print(f'blob                {len(blob)} bytes, {m["steps"] if "steps" in m else len(graph)} steps')

    out = os.path.join(os.path.dirname(HERE), 'src', 'zoo', 'digits.tcm1')
    with open(out, 'wb') as f:
        f.write(blob)
    with open(out + '.json', 'w') as f:
        json.dump({'model_root': m['model_root'], 'cid': m['cid'],
                   'bytes': len(blob), 'float_accuracy': round(fa, 4),
                   'quantised_accuracy': round(qa, 4),
                   'test_samples': len(test_set)}, f, indent=2)
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
