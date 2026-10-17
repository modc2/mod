"""Word error rate, so "did trimming hurt the transcript" has an answer.

Any claim that a shortcut is free needs a number attached, and for speech that
number is WER: the edit distance between what was said and what came back,
over the number of words that were said. The Harvard sentences in
`samples/harvard-8k.wav` are a standard, published list, so the truth for that
file is known and a comparison can be scored rather than eyeballed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# IEEE Recommended Practice for Speech Quality Measurements, list 1 — the text
# read in OSR_us_000_0010, which is what samples/harvard-8k.wav is.
HARVARD_LIST_1 = (
    'The birch canoe slid on the smooth planks. '
    'Glue the sheet to the dark blue background. '
    "It's easy to tell the depth of a well. "
    'These days a chicken leg is a rare dish. '
    'Rice is often served in round bowls. '
    'The juice of lemons makes fine punch. '
    'The box was thrown beside the parked truck. '
    'The hogs were fed chopped corn and garbage. '
    'Four hours of steady work faced us. '
    'A large size in stockings is hard to sell.'
)

TRUTH = {'harvard-8k.wav': HARVARD_LIST_1,
         'sparse-45pct-speech.wav': HARVARD_LIST_1}


def words(text: str) -> List[str]:
    """Case, punctuation and contractions removed — the usual scoring rules."""
    kept = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in text.lower())
    return kept.split()


def distance(reference: List[str], hypothesis: List[str]) -> Dict[str, int]:
    """Levenshtein over words, with the three error kinds kept apart."""
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    back = [[''] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0], back[i][0] = i, 'd'
    for j in range(cols):
        cost[0][j], back[0][j] = j, 'i'
    back[0][0] = ''
    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j], back[i][j] = cost[i - 1][j - 1], 'c'
                continue
            options = ((cost[i - 1][j - 1] + 1, 's'), (cost[i - 1][j] + 1, 'd'),
                       (cost[i][j - 1] + 1, 'i'))
            cost[i][j], back[i][j] = min(options)

    counts = {'substitutions': 0, 'deletions': 0, 'insertions': 0, 'correct': 0}
    i, j = len(reference), len(hypothesis)
    while i > 0 or j > 0:
        step = back[i][j]
        if step == 'c':
            counts['correct'] += 1
            i, j = i - 1, j - 1
        elif step == 's':
            counts['substitutions'] += 1
            i, j = i - 1, j - 1
        elif step == 'd':
            counts['deletions'] += 1
            i -= 1
        else:
            counts['insertions'] += 1
            j -= 1
    return counts


def wer(reference: str, hypothesis: str) -> Dict[str, Any]:
    """The score, and the arithmetic behind it."""
    ref, hyp = words(reference), words(hypothesis)
    counts = distance(ref, hyp)
    errors = counts['substitutions'] + counts['deletions'] + counts['insertions']
    return {'wer': round(errors / len(ref), 4) if ref else None,
            'errors': errors, 'reference_words': len(ref),
            'hypothesis_words': len(hyp), **counts}


def truth_for(source: Any) -> Optional[str]:
    """The known transcript for a bundled sample, if this is one."""
    name = str(source).split('/')[-1]
    return TRUTH.get(name)


def score(source: Any, hypothesis: str) -> Optional[Dict[str, Any]]:
    reference = truth_for(source)
    return wer(reference, hypothesis) if reference else None
