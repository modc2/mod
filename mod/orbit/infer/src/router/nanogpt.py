"""NanoGPT — the reason this module has a `kyc=none` default.

601 models behind one OpenAI-shaped endpoint, paid for with a deposit in any of
a long list of coins including Monero, and no account needed to start. The plain
`/v1/models` is a bare id list; `?detailed=true` is the one worth calling, and it
is the only router here that publishes its own price unit rather than leaving it
to be inferred.
"""

from .base import Offering, Provider, http, model_key
from .price import Price, to_mtok

MODS = {'text': 'text', 'image': 'image', 'audio': 'audio', 'video': 'video',
        'file': 'file'}


class NanoGPT(Provider):
    name = 'nanogpt'
    base = 'https://nano-gpt.com/api/v1'
    caps = ('catalog', 'chat', 'image', 'balance')
    price_unit = 'per_million_tokens'
    kyc = 'none'
    pay = ('XMR', 'BTC', 'ETH', 'USDC', 'USDT', 'LTC', 'SOL', 'XNO')
    account = False
    pay_note = ('no signup required; deposit crypto to a generated address and '
                'spend down the balance, Monero accepted')
    checked = '2026-09-14'

    @property
    def env(self):
        return ('NANOGPT_API_KEY',)

    def catalog(self):
        d = http('GET', self.base + '/models', params={'detailed': 'true'},
                 provider=self.name)
        out = []
        for m in d.get('data', []):
            arch = m.get('architecture') or {}
            caps = m.get('capabilities') or {}
            p = m.get('pricing') or {}
            # The one router that declares its unit. Trust the declaration over
            # our default when it disagrees.
            unit = p.get('unit') or self.price_unit
            price = Price(inp=to_mtok(p.get('prompt'), unit),
                          out=to_mtok(p.get('completion'), unit))
            inputs = tuple(MODS[x] for x in (arch.get('input_modalities') or [])
                           if x in MODS)
            if not inputs:                      # fall back to the capability flags
                inputs = ('text',) + tuple(
                    k for k, on in (('image', caps.get('vision')),
                                    ('video', caps.get('video_input')),
                                    ('audio', caps.get('audio_input'))) if on)
            tags = tuple(t for t in (m.get('category'),) if t) + \
                tuple(k for k in ('reasoning', 'tool_calling') if caps.get(k))
            out.append(Offering(
                self.name, m['id'], model_key(m['id']), m.get('name'), price,
                inputs,
                tuple(MODS[x] for x in (arch.get('output_modalities') or [])
                      if x in MODS) or ('text',),
                m.get('context_length'), m.get('max_output_tokens'), tags))
        return out

    def balance(self):
        d = http('GET', 'https://nano-gpt.com/api/check-balance',
                 headers=self.headers(), provider=self.name)
        bal = d.get('balance') if isinstance(d, dict) else None
        return {'provider': self.name, 'usd': _num(bal), 'raw': d}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
