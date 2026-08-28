"""
billing - what a run costs us at the provider, so a guest's deposit can pay for it.

A flat per-step price can't fund the key behind guest runs: one step on
Opus costs a hundred times one step on a small model, so a fixed price is
either a giveaway or a rip-off. This meter prices every model call from
the provider's own live catalog (OpenRouter /models, Venice /models), and
the credit ledger charges that cost plus the module's margin — the deposit
a guest makes is what tops the provider account back up.

Token counts are ESTIMATED from character counts: both providers stream
plain text back and neither surfaces the usage block through the model
module, so there is no exact count to read. The estimate uses the same
chars-per-token shape the provider modules use for context headroom, and
the treasury reconciles it against the provider's real meter — see
Credits.treasury(), which reports the drift.

Tallies are per-thread: one Mod instance serves every concurrent run in
the API's worker threads, so a shared accumulator would bill run A for
run B's tokens.
"""
import threading
from typing import Optional

# rough chars -> tokens for English text + JSON. Deliberately the same
# shape the provider modules use to size their context headroom.
CHARS_PER_TOKEN = 4.0


def _rates_from(info: dict) -> Optional[tuple]:
    """(prompt, completion) USD per token from a provider catalog entry.

    OpenRouter quotes per-token strings under `pricing`; Venice quotes USD
    per million tokens under `model_spec.pricing`. Unknown shape -> None,
    which means "unpriced" and sends the caller to its fallback.
    """
    pricing = info.get('pricing') or {}
    if 'prompt' in pricing or 'completion' in pricing:
        return (float(pricing.get('prompt') or 0), float(pricing.get('completion') or 0))
    spec = ((info.get('model_spec') or {}).get('pricing')) or {}
    if spec:
        inp = (spec.get('input') or {}).get('usd')
        out = (spec.get('output') or {}).get('usd')
        if inp is not None or out is not None:
            return (float(inp or 0) / 1e6, float(out or 0) / 1e6)
    return None


class Meter:
    """Prices a run's model calls from the live provider catalog."""

    def __init__(self, chars_per_token: float = CHARS_PER_TOKEN, multiplier: float = 1.0):
        self.chars_per_token = float(chars_per_token or CHARS_PER_TOKEN)
        # safety factor on the estimate — the owner tunes it from the drift
        # the treasury reports, so under-billing can be corrected
        self.multiplier = float(multiplier or 1.0)
        self._rates: dict = {}          # (provider, model) -> (prompt, completion) | None
        self._local = threading.local()

    # ── pricing ──────────────────────────────────────────────────────

    def rates(self, model_obj, provider: str, model: str) -> Optional[tuple]:
        """Per-token USD rates for a model, cached per (provider, model).

        The catalog itself is cached by the provider module (a store with a
        TTL), so this is a dict lookup after the first call per process.
        """
        key = (provider, model)
        if key in self._rates:
            return self._rates[key]
        rates = None
        try:
            catalog = model_obj.model2info()
            info = catalog.get(model)
            if info is None and hasattr(model_obj, 'resolve_model'):
                info = catalog.get(model_obj.resolve_model(model))
            if info:
                rates = _rates_from(info)
        except Exception as e:
            print(f"Pricing lookup failed for {provider}/{model}: {e}")
        self._rates[key] = rates
        return rates

    def price(self, model_obj, provider: str, model: str,
              prompt_tokens: float, completion_tokens: float) -> Optional[float]:
        """USD this call costs us at the provider, or None if unpriced."""
        rates = self.rates(model_obj, provider, model)
        if rates is None:
            return None
        cost = prompt_tokens * rates[0] + completion_tokens * rates[1]
        return round(cost * self.multiplier, 8)

    # ── per-run tally ────────────────────────────────────────────────

    def open(self, provider: str = None, model: str = None) -> dict:
        """Start — or continue — this thread's tally.

        A live tally is kept rather than reset: a chain runs its stages back
        to back on one thread and is billed once at the end, so every stage
        has to land in the same total.
        """
        tally = getattr(self._local, 'tally', None)
        if tally is None:
            tally = {'provider': provider, 'model': model, 'calls': 0,
                     'prompt_tokens': 0, 'completion_tokens': 0,
                     'cost': 0.0, 'priced': True}
            self._local.tally = tally
        else:
            tally['provider'], tally['model'] = provider, model
        return tally

    def peek(self) -> float:
        """Cost tallied on this thread so far — for mid-run budget checks."""
        tally = getattr(self._local, 'tally', None)
        return round(tally['cost'], 8) if tally else 0.0

    def take(self) -> dict:
        """Read and clear this thread's tally. Never raises — billing a run
        must not be able to break the run that just finished."""
        tally = getattr(self._local, 'tally', None)
        self._local.tally = None
        if not tally:
            return {'calls': 0, 'cost': 0.0, 'priced': False,
                    'prompt_tokens': 0, 'completion_tokens': 0}
        tally['cost'] = round(tally['cost'], 8)
        return tally

    def watch(self, output, *, model_obj, provider: str, model: str, prompt: str):
        """Tally one model call, counting the answer as it streams past.

        Returns the output untouched — a string stays a string, a stream
        stays a stream — so the loop that consumes it is unchanged.
        """
        sent = len(prompt or '') / self.chars_per_token
        if isinstance(output, str):
            self._record(model_obj, provider, model, sent, len(output) / self.chars_per_token)
            return output

        def counted():
            chars = 0
            try:
                for chunk in output:
                    chars += len(chunk or '')
                    yield chunk
            finally:   # a break or an error still burned the tokens it burned
                self._record(model_obj, provider, model, sent, chars / self.chars_per_token)
        return counted()

    def _record(self, model_obj, provider, model, prompt_tokens, completion_tokens):
        tally = getattr(self._local, 'tally', None)
        if tally is None:
            return
        tally['calls'] += 1
        tally['prompt_tokens'] += int(prompt_tokens)
        tally['completion_tokens'] += int(completion_tokens)
        tally['model'] = model or tally.get('model')
        tally['provider'] = provider or tally.get('provider')
        cost = self.price(model_obj, provider, model, prompt_tokens, completion_tokens)
        if cost is None:
            tally['priced'] = False   # unknown model — bill the fallback price
        else:
            tally['cost'] += cost
