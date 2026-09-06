"""What an embedding is, in about forty lines and no libraries.

Run: python3 examples/01_what_an_embedding_is.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from src import data, evaluate, text, zoo

print(__doc__.splitlines()[0])
print('=' * 72)

# ── 1. a word becomes a number ───────────────────────────────────────
print('\n1. words become integers by hashing — no vocabulary file exists\n')
for word in ('coffee', 'espresso', 'sailing', 'coffee'):
    print(f'   {word:<10} fnv1a → {text.fnv1a(word):>12}  % 8192 → '
          f'{text.fnv1a(word) % 8192:>5}')
print('\n   "coffee" hashes to the same bucket both times. That is the only '
      'promise\n   a hashing tokenizer makes, and it is enough to run a model.')

# ── 2. the numbers become a vector ───────────────────────────────────
model = zoo.load('bow-64')
sentence = 'pour the water slowly over the ground coffee'
ids = text.token_ids(sentence, drop_stopwords=True)
vector = evaluate.embed(model, sentence)

print(f'\n2. a sentence becomes 64 numbers\n')
print(f'   "{sentence}"')
print(f'   ids      {ids.tolist()}   (stopwords dropped first)')
print(f'   vector   {np.round(vector[:6], 3)} ... ({vector.size} of them)')
print(f'   length   {np.linalg.norm(vector):.4f}   (normalised, so only the '
      'direction carries meaning)')

# ── 3. direction is similarity ───────────────────────────────────────
print('\n3. two sentences are similar when their vectors point the same way\n')
pairs = [
    ('a burr grinder gives an even grind', 'espresso needs pressure'),
    ('a burr grinder gives an even grind', 'the keel converts sideways push'),
    ('weigh the beans and weigh the water', 'weigh the ingredients'),
]
for left, right in pairs:
    score = text.cosine(evaluate.embed(model, left), evaluate.embed(model, right))
    bar = '#' * max(0, int(score * 40))
    print(f'   {score:+.3f} |{bar:<40}| {left[:32]:<34} ~ {right[:32]}')

print('\n   Nothing here understands coffee. The third pair scores 0.6 because it\n'
      '   shares the word "weigh" — that is the whole mechanism.\n'
      '\n   Read the first two rows carefully: two coffee sentences with no word\n'
      '   in common score *lower* than a coffee sentence and a sailing one. Both\n'
      '   are noise. Sixty-four random directions do not separate cleanly at\n'
      '   small angles, so anything below about 0.15 here means "unrelated" and\n'
      '   the ordering inside that band is meaningless. A similarity score with\n'
      '   no sense of its own noise floor is how a demo becomes a wrong answer.')

# ── 4. what the hashing costs ────────────────────────────────────────
corpus = [d for _, d in data.DOCS] + [q for q, _ in data.QUERIES]
print('\n4. the price of having no vocabulary file: collisions\n')
for vocab in (8192, 512, 64):
    stats = text.collisions(corpus, vocab)
    print(f'   vocab {vocab:>5}: {stats["collision_rate"]:>6.1%} of words share a '
          f'bucket with another word')
print('\n   Two words in one bucket are the same word as far as the model is\n'
      '   concerned. At 8192 buckets that is rare; at 64 the model is mostly\n'
      '   reading noise. Vocabulary size is the first thing you shrink and the\n'
      '   first thing that bites.')

print(f'\n   Next: 02_quantize_one_tensor.py — making those numbers smaller.')
