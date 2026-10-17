"""The arithmetic Letterboxd doesn't do: one diary judged, two diaries compared.

Nothing here touches the network. Both functions take entries that
``letterboxd.py`` has already parsed, which is what makes them testable
offline and cheap to call twice.

One caveat runs through every number below, and each answer repeats it in its
own ``coverage`` field rather than leaving it to the reader: the RSS feed is a
window of roughly the last 50 logged films, not a lifetime. A mean rating over
50 films that gets read as a mean over 4,000 is a lie of framing, so the window
size travels with the statistic.
"""

import letterboxd as lb


def _key(entry):
    """What makes two diary entries the same film.

    Slug first — it is Letterboxd's own identity and already disambiguates
    remakes by year, where a bare title would collide. TMDB id, then
    title+year, back it up for anything the slug is missing from.
    """
    return (entry.get('slug') or entry.get('tmdb')
            or f"{(entry.get('film') or '').lower()}:{entry.get('year')}")


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else round((s[mid - 1] + s[mid]) / 2, 2)


def profile(entries):
    """How a member rates, off the films in their feed window.

    Takes parsed diary entries (lists already dropped, or they are ignored).
    """
    films = [e for e in entries or [] if e.get('film')]
    rated = [e for e in films if e.get('rating') is not None]
    scores = [e['rating'] for e in rated]

    hist = {}
    for s in scores:
        hist[s] = hist.get(s, 0) + 1

    decades = {}
    for e in films:
        if e.get('year'):
            d = f"{(e['year'] // 10) * 10}s"
            decades[d] = decades.get(d, 0) + 1

    dates = sorted(e['watched'] for e in films if e.get('watched'))
    mean = round(sum(scores) / len(scores), 2) if scores else None

    return {
        'films': len(films),
        'rated': len(rated),
        'reviews': sum(1 for e in films if e.get('review')),
        'mean': mean,
        'mean_stars': lb.stars(round(mean * 2) / 2) if mean is not None else None,
        'median': _median(scores),
        'histogram': {str(k): hist[k] for k in sorted(hist)},
        'liked': sum(1 for e in films if e.get('liked')),
        'like_rate': round(sum(1 for e in films if e.get('liked')) / len(films), 2)
                     if films else None,
        'rewatches': sum(1 for e in films if e.get('rewatch')),
        'rewatch_rate': round(sum(1 for e in films if e.get('rewatch')) / len(films), 2)
                        if films else None,
        'decades': dict(sorted(decades.items(), reverse=True)),
        'watched_from': dates[0] if dates else None,
        'watched_to': dates[-1] if dates else None,
        # Poster and url travel with the film: every caller that shows `top`
        # wants to render it as a card, and re-fetching the film page for
        # artwork the diary already handed us would cost a throttled request
        # each.
        'top': [{'film': e['film'], 'year': e.get('year'), 'rating': e['rating'],
                 'slug': e.get('slug'), 'poster': e.get('poster'),
                 'url': e.get('url'), 'liked': e.get('liked')}
                for e in sorted(rated, key=lambda e: -e['rating'])[:5]],
        'coverage': f'the {len(films)} films in the feed window, not all time',
    }


def overlap(a_entries, b_entries, a='a', b='b'):
    """Two members' feed windows held against each other.

    The overlap is small by construction — two windows of ~50 films each — so
    ``seen_by_both`` is reported next to both window sizes rather than alone,
    and an empty overlap is an honest answer rather than an error.
    """
    ma = {_key(e): e for e in a_entries or [] if e.get('film')}
    mb = {_key(e): e for e in b_entries or [] if e.get('film')}

    shared = []
    for k in ma.keys() & mb.keys():
        x, y = ma[k], mb[k]
        gap = (None if x.get('rating') is None or y.get('rating') is None
               else round(x['rating'] - y['rating'], 2))
        shared.append({
            'film': x.get('film'), 'year': x.get('year'), 'slug': x.get('slug'),
            'url': x.get('url'), 'poster': x.get('poster'),
            a: x.get('rating'), b: y.get('rating'), 'gap': gap,
        })

    scored = [r for r in shared if r['gap'] is not None]
    scored.sort(key=lambda r: abs(r['gap']), reverse=True)
    agreed = [r for r in scored if abs(r['gap']) <= 0.5]

    def recommend(mine, theirs, owner):
        """Their best that the other one has not logged — the point of asking."""
        loved = [e for e in theirs.values()
                 if e.get('rating') is not None and e['rating'] >= 4
                 and _key(e) not in mine]
        loved.sort(key=lambda e: -e['rating'])
        return [{'film': e['film'], 'year': e.get('year'), 'slug': e.get('slug'),
                 'rating': e['rating'], 'from': owner, 'poster': e.get('poster')}
                for e in loved[:8]]

    return {
        'members': [a, b],
        'windows': {a: len(ma), b: len(mb)},
        'seen_by_both': len(shared),
        'rated_by_both': len(scored),
        'agreement': round(len(agreed) / len(scored), 2) if scored else None,
        'mean_gap': round(sum(r['gap'] for r in scored) / len(scored), 2)
                    if scored else None,
        # Only films they actually scored differently. A gap of 0 is the
        # opposite of a disagreement, and listing it under that name — which
        # the console prints as "hardest splits" — reads as a bug.
        'disagreements': [r for r in scored if r['gap']][:10],
        'shared': shared,
        f'for_{a}': recommend(ma, mb, b),
        f'for_{b}': recommend(mb, ma, a),
        'coverage': ('both feed windows only — two members who have not '
                     'overlapped lately will show little overlap here, even '
                     'if their all-time libraries agree closely'),
    }
