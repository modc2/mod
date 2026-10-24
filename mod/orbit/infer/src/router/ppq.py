"""PPQ — pay-per-prompt with crypto, and the catalog that names its own units.

OpenRouter-shaped everywhere except pricing, where the keys are spelled
`input_per_1M_tokens`. That self-naming is the safe case: no sniffing needed.
"""

from .base import Offering, Provider, http, model_key
from .price import Price, to_mtok

MODS = {'text': 'text', 'image': 'image', 'audio': 'audio', 'video': 'video',
        'file': 'file'}


class PPQ(Provider):
    name = 'ppq'
    base = 'https://api.ppq.ai'
    caps = ('catalog', 'chat')
    price_unit = 'per_million_tokens'
    kyc = 'none'
    pay = ('BTC', 'ETH', 'USDC', 'USDT', 'SOL')
    account = False
    pay_note = 'buy an API key with crypto, no signup or identity check'
    checked = '2026-09-14'

    @property
    def env(self):
        return ('PPQ_API_KEY',)

    def chat_url(self):
        return self.base + '/chat/completions'

    def catalog(self):
        d = http('GET', self.base + '/models', provider=self.name)
        out = []
        for m in d.get('data', []):
            arch = m.get('architecture') or {}
            p = m.get('pricing') or {}
            price = Price(
                inp=to_mtok(p.get('input_per_1M_tokens'), self.price_unit),
                out=to_mtok(p.get('output_per_1M_tokens'), self.price_unit))
            tags = tuple(t for t in (m.get('privacyLevel') and
                                     'privacy:' + m['privacyLevel'],) if t)
            out.append(Offering(
                self.name, m['id'], model_key(m['id']), m.get('name'), price,
                tuple(MODS[x] for x in (arch.get('input_modalities') or [])
                      if x in MODS) or ('text',),
                tuple(MODS[x] for x in (arch.get('output_modalities') or [])
                      if x in MODS) or ('text',),
                m.get('context_length'), None, tags))
        return out
