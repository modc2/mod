"""The corpora. Small on purpose, and written down here rather than downloaded.

Two of them:

`DOCS` — thirty-six short sentences across six topics, with the queries that
should find them. It is the search task, and the metric is whether the right
topic comes back first. Small enough to read in full, which means when a
compressed model gets a query wrong you can look at the document it preferred
and see why.

`sentiment()` — a few hundred labelled lines built from templates by a seeded
shuffle. It is synthetic and it is easy: a model that gets 95% here has not
learned much about language. It is here because compression needs a *task*
metric and not just a tensor-error metric, and because the number that matters
is never the accuracy itself but the gap between the float model's accuracy and
the compressed one's on the same held-out rows.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

DOCS: List[Tuple[str, str]] = [
    ('coffee', 'pour the water slowly over the ground coffee in a circle'),
    ('coffee', 'a burr grinder gives an even grind and a cleaner cup'),
    ('coffee', 'espresso needs pressure, filter coffee needs patience'),
    ('coffee', 'stale beans taste flat no matter how careful the brew'),
    ('coffee', 'weigh the beans and weigh the water, then adjust one of them'),
    ('coffee', 'the water should be just off the boil, never boiling'),

    ('sailing', 'trim the mainsail until the luff stops shaking'),
    ('sailing', 'a boat cannot sail directly into the wind, so it tacks'),
    ('sailing', 'the keel converts sideways push into forward motion'),
    ('sailing', 'reef early — it is easier than reefing in a gale'),
    ('sailing', 'read the water ahead for gusts darkening the surface'),
    ('sailing', 'the tide can beat the wind on a slow afternoon'),

    ('databases', 'an index makes reads fast and writes a little slower'),
    ('databases', 'a transaction either happens completely or not at all'),
    ('databases', 'normalise until it hurts, denormalise until it works'),
    ('databases', 'the query planner picks a plan from statistics, not from hope'),
    ('databases', 'a full table scan is fine when the table is small'),
    ('databases', 'replication buys availability and costs you consistency'),

    ('baking', 'let the dough rise until it doubles, then knock it back'),
    ('baking', 'too much flour makes a dry crumb and a heavy loaf'),
    ('baking', 'steam in the first ten minutes gives the crust its shine'),
    ('baking', 'a sourdough starter is flour, water and time'),
    ('baking', 'weigh the ingredients — cups lie about how much flour is in them'),
    ('baking', 'rest the loaf before cutting or the inside goes gummy'),

    ('astronomy', 'a light year is a distance, not a length of time'),
    ('astronomy', 'the moon shows the same face because its spin is locked'),
    ('astronomy', 'stars twinkle because the atmosphere moves, planets rarely do'),
    ('astronomy', 'a telescope collects light, magnification is secondary'),
    ('astronomy', 'red shift tells you a galaxy is moving away from us'),
    ('astronomy', 'the darkest sky you can reach beats the largest telescope you cannot'),

    ('cycling', 'keep the tyres hard on tarmac and soft on gravel'),
    ('cycling', 'spin a lighter gear on a long climb and save the legs'),
    ('cycling', 'a clean chain is quiet and lasts twice as long'),
    ('cycling', 'the saddle height sets your knee angle, get it right first'),
    ('cycling', 'brake before the corner, not in it'),
    ('cycling', 'a headwind out means a tailwind home, in theory'),
]

QUERIES: List[Tuple[str, str]] = [
    ('how finely should i grind the beans', 'coffee'),
    ('water temperature for brewing', 'coffee'),
    ('what does the keel do', 'sailing'),
    ('sailing upwind against the wind', 'sailing'),
    ('why add an index to a table', 'databases'),
    ('how does a transaction behave when it fails', 'databases'),
    ('how long should dough rise', 'baking'),
    ('why is my loaf heavy and dry', 'baking'),
    ('how far is a light year', 'astronomy'),
    ('why do stars twinkle', 'astronomy'),
    ('what gear for a long climb', 'cycling'),
    ('tyre pressure on gravel', 'cycling'),
]

TOPICS = sorted({topic for topic, _ in DOCS})

_POSITIVE = [
    'loved', 'excellent', 'delightful', 'superb', 'charming', 'gripping',
    'warm', 'clever', 'honest', 'beautiful', 'sharp', 'generous',
]
_NEGATIVE = [
    'hated', 'terrible', 'dull', 'clumsy', 'tedious', 'shallow',
    'cold', 'lazy', 'dishonest', 'ugly', 'blunt', 'mean',
]
_SUBJECTS = ['the film', 'the book', 'this record', 'the show', 'her second novel',
             'the sequel', 'the whole thing', 'the ending', 'the writing', 'the cast']
_FRAMES = [
    '{subject} was {word} from the first minute',
    'i found {subject} {word} and said so',
    'a {word} piece of work, {subject} at its most itself',
    'everyone i know thought {subject} was {word}',
    '{subject} is {word}, and that is the review',
    'after an hour {subject} still felt {word}',
]


_CONTRAST = '{subject} is {first} but ultimately {second}'


def sentiment(seed: int = 7, test_fraction: float = 0.25) -> Dict[str, object]:
    """Labelled lines, split into train and held-out test. 1 = positive.

    A quarter of the rows are `{subject} is X but ultimately Y`, labelled by Y.
    A bag of words cannot see word order, so those rows are decided by whatever
    weak asymmetry the training left behind and the logits there sit close to
    zero — which is exactly where a quantized model's rounding error changes an
    answer. Without them the task has no headroom and compression looks free.
    """
    rows: List[Tuple[str, int]] = []
    for label, vocabulary in ((1, _POSITIVE), (0, _NEGATIVE)):
        for word in vocabulary:
            for subject in _SUBJECTS:
                for frame in _FRAMES:
                    rows.append((frame.format(subject=subject, word=word), label))
    for positive, negative in zip(_POSITIVE, _NEGATIVE):
        for subject in _SUBJECTS:
            rows.append((_CONTRAST.format(subject=subject, first=positive,
                                          second=negative), 0))
            rows.append((_CONTRAST.format(subject=subject, first=negative,
                                          second=positive), 1))
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(rows))
    rows = [rows[i] for i in order]
    cut = int(len(rows) * (1 - test_fraction))
    return {
        'train': rows[:cut],
        'test': rows[cut:],
        'labels': ['negative', 'positive'],
        'note': 'synthetic and easy — read the gap between models, not the accuracy',
    }
