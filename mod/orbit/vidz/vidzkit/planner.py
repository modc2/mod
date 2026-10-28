"""
Script -> shots. Deterministic and offline: the film's length is split into
clips no longer than the provider's max, and each clip gets a prompt. Pass
shots=[...] to write the storyboard yourself; otherwise the one prompt is
carried through every shot with its place in the arc, so the model keeps
continuity (same subject, same style) across cuts.
"""
from typing import List, Optional

ARC = ['opening shot, establish the scene', 'build', 'rising action', 'turn',
       'climax', 'resolution', 'closing shot']


def lengths(total: int, max_clip: int) -> List[int]:
    """Split total seconds into near-equal clips, each <= max_clip."""
    total, max_clip = max(1, int(total)), max(1, int(max_clip))
    n = -(-total // max_clip)
    base, extra = divmod(total, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def beat(i: int, n: int) -> str:
    if n == 1:
        return ARC[0]
    return ARC[round(i * (len(ARC) - 1) / (n - 1))]


def plan(prompt: str, seconds: int, max_clip: int, shots: Optional[list] = None,
         style: str = '') -> List[dict]:
    if shots:
        n = len(shots)
        base, extra = divmod(max(n, int(seconds)), n)
        ls = [base + (1 if i < extra else 0) for i in range(n)]
        if max(ls) > max_clip:
            raise ValueError(f'{n} shots over {seconds}s means {max(ls)}s clips; this provider caps at {max_clip}s — add shots or shorten')
        texts = [str(s) for s in shots]
    else:
        ls = lengths(seconds, max_clip)
        n = len(ls)
        texts = [prompt if n == 1 else f'{prompt}. Shot {i + 1} of {n}: {beat(i, n)}.' for i in range(n)]
    tail = f' Style: {style}.' if style else ''
    return [{'i': i, 'seconds': s, 'prompt': t + tail} for i, (s, t) in enumerate(zip(ls, texts))]
