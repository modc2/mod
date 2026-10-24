"""Chutes (Bittensor SN64) — TAO-priced serverless inference, and the unit trap.

Its catalog ships `pricing.prompt`, the *same key name* OpenRouter uses for a
per-token price, holding a per-MILLION-token one. Read it as OpenRouter does and
every chute looks a million times too expensive to ever route to. That single
collision is why `price.py` refuses to infer units from field names.

Many chutes run in a TEE and say so; that tag survives into the catalog because
"confidential" is a reason to pick a provider that has nothing to do with price.
"""

from .base import Offering, Provider, http, model_key
from .price import Price, to_mtok

MODS = {'text': 'text', 'image': 'image', 'audio': 'audio', 'video': 'video'}


class Chutes(Provider):
    name = 'chutes'
    base = 'https://llm.chutes.ai/v1'
    caps = ('catalog', 'chat', 'image', 'balance')
    price_unit = 'per_million_tokens'      # NOT per-token, despite the key name
    kyc = 'none'
    pay = ('TAO', 'USDC')
    account = True
    pay_note = ('Bittensor-native; fund with TAO, no identity check, quotas are '
                'per-key and many chutes attest a TEE')
    checked = '2026-09-14'

    @property
    def env(self):
        return ('CHUTES_API_KEY',)

    def catalog(self):
        d = http('GET', self.base + '/models', provider=self.name)
        out = []
        for m in d.get('data', []):
            p = m.get('pricing') or {}
            coin = (m.get('price') or {})
            price = Price(
                inp=to_mtok(p.get('prompt'), self.price_unit),
                out=to_mtok(p.get('completion'), self.price_unit),
                cache_read=to_mtok(p.get('input_cache_read'), self.price_unit),
                coin='TAO',
                coin_in=to_mtok((coin.get('input') or {}).get('tao'), self.price_unit),
                coin_out=to_mtok((coin.get('output') or {}).get('tao'), self.price_unit))
            tags = tuple(m.get('supported_features') or ())
            if m.get('confidential_compute') or 'TEE' in str(m.get('id', '')):
                tags += ('tee',)
            if m.get('quantization'):
                tags += ('quant:' + str(m['quantization']),)
            out.append(Offering(
                self.name, m['id'], model_key(m['id']), m.get('id'), price,
                tuple(MODS[x] for x in (m.get('input_modalities') or [])
                      if x in MODS) or ('text',),
                tuple(MODS[x] for x in (m.get('output_modalities') or [])
                      if x in MODS) or ('text',),
                m.get('context_length'), m.get('max_output_length'), tags))
        return out

    def balance(self):
        d = http('GET', 'https://api.chutes.ai/users/me', headers=self.headers(),
                 provider=self.name)
        return {'provider': self.name, 'usd': None,
                'balance': d.get('balance'), 'raw': d}
