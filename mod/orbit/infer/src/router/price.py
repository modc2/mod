"""Price normalization — the one place a per-token number meets a per-million one.

Every router in this registry publishes token prices, and no two agree on the
unit. Worse, two of them disagree *while using the same key name*:

    openrouter   {"prompt": "0.00000025"}            USD per token, a string
    chutes       {"prompt": 0.12}                    USD per MILLION tokens
    nanogpt      {"prompt": 0.4, "unit": "per_million_tokens"}   per million
    ppq          {"input_per_1M_tokens": 0.611}      per million, self-naming
    io.net       {"input_token_price": 3.15e-07}     USD per token, a float
    venice       {"input": {"usd": 0.9375}}          per million, plus a DIEM column

So a normalizer that sniffs field names ranks chutes a million times more
expensive than openrouter and silently never routes to it. An aggregator whose
whole job is "pick the cheapest" cannot afford that class of bug, so units are
never inferred here: an adapter *declares* what unit its numbers are in, and
this module converts. Guessing is available, but only as `sniff()`, only for
providers that declare nothing, and it reports that it guessed.

Canonical unit everywhere downstream: **USD per million tokens**, as a float.
One unit, named in the field (`usd_per_mtok`), so nothing further down has to
ask again.
"""

# What an adapter may declare. Value is the multiplier onto USD/million-tokens.
UNITS = {
    'per_token': 1_000_000.0,
    'per_million_tokens': 1.0,
    'per_thousand_tokens': 1_000.0,
}

# Above this, a "per token" price would mean >$1 per token; below it, a "per
# million" price would mean <$0.001 per million. Both are absurd, and the gap
# between them is six orders of magnitude wide, which is why sniffing works at
# all — but it is still a guess and is labelled as one.
_SNIFF_CEILING = 0.001


def to_mtok(value, unit):
    """One published number → USD per million tokens. `unit` is not optional."""
    if value is None or value == '':
        return None
    try:
        v = float(value)                       # openrouter ships these as strings
    except (TypeError, ValueError):
        return None
    if v < 0:                                  # openrouter's -1 "variable price"
        return None                            # sentinel — see the router memory
    mult = UNITS.get(unit)
    if mult is None:
        raise ValueError(f'unknown price unit {unit!r} — declare one of {list(UNITS)}')
    return v * mult


def sniff(value):
    """A guess for a provider that declares no unit, with its own confidence.

    Returns (usd_per_mtok, confident). Only ever used when an adapter cannot
    declare, and the `confident` flag rides along so the catalog can mark the
    row rather than quietly present a guess as a measurement.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, False
    if v < 0:
        return None, False
    if v == 0:
        return 0.0, True                       # free is free in every unit
    if v < _SNIFF_CEILING:
        return v * 1_000_000.0, True           # far too small to be per-million
    return v, False                            # plausibly per-million, not certain


class Price:
    """What a model costs, in one unit, with the crypto column kept alongside.

    `token` prices are USD per million tokens. `request` and `image` are USD per
    unit of the thing, because that is how they are published and converting
    them to a token rate would be inventing a token count.
    """

    __slots__ = ('inp', 'out', 'cache_read', 'request', 'image', 'coin',
                 'coin_in', 'coin_out', 'guessed')

    def __init__(self, inp=None, out=None, cache_read=None, request=None,
                 image=None, coin=None, coin_in=None, coin_out=None,
                 guessed=False):
        self.inp, self.out, self.cache_read = inp, out, cache_read
        self.request, self.image = request, image
        self.coin, self.coin_in, self.coin_out = coin, coin_in, coin_out
        self.guessed = guessed

    @property
    def known(self):
        return self.inp is not None or self.out is not None \
            or self.request is not None or self.image is not None

    def blended(self, in_tok=1000, out_tok=500):
        """USD for one representative call — what ranking actually sorts on.

        Ranking needs a single number, and input-only or output-only comparisons
        both mis-rank: a cheap-prompt/expensive-completion model wins on one and
        loses on the other. The default mix is stated, not hidden, so a caller
        who knows their own shape can pass it.
        """
        if self.request is not None and not (self.inp or self.out):
            return self.request
        if self.inp is None and self.out is None:
            return None
        total = ((self.inp or 0.0) * in_tok + (self.out or 0.0) * out_tok) / 1e6
        if self.request:
            total += self.request
        return total

    def dict(self):
        d = {'usd_per_mtok_in': self.inp, 'usd_per_mtok_out': self.out}
        if self.cache_read is not None:
            d['usd_per_mtok_cache_read'] = self.cache_read
        if self.request is not None:
            d['usd_per_request'] = self.request
        if self.image is not None:
            d['usd_per_image'] = self.image
        if self.coin:
            d['coin'] = self.coin
            d['coin_per_mtok_in'] = self.coin_in
            d['coin_per_mtok_out'] = self.coin_out
        if self.guessed:
            d['unit_guessed'] = True
        return d
