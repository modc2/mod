"""Venice — crypto-native, no-KYC, and genuinely multimodal once you ask properly.

The trap worth knowing: `GET /models` returns only the 119 text models. The image
generators, the TTS voices, the embedders and the upscaler — 243 more — appear
only under `?type=all`, so a catalog built from the documented default call
silently drops two thirds of what the router serves, including every non-text
output modality it has.

Prices carry a DIEM column next to USD, which is the closest thing in this
registry to a router quoting its own settlement asset.
"""

from .base import Offering, Provider, http, model_key
from .price import Price, to_mtok


class Venice(Provider):
    name = 'venice'
    base = 'https://api.venice.ai/api/v1'
    caps = ('catalog', 'chat', 'image', 'audio', 'embedding', 'balance')
    price_unit = 'per_million_tokens'
    kyc = 'none'
    pay = ('VVV', 'USDC', 'ETH', 'BTC')
    account = False
    pay_note = ('privacy-first, no logs; pay with crypto or stake VVV for daily '
                'inference, DIEM is quoted next to USD in the catalog')
    checked = '2026-09-14'

    @property
    def env(self):
        return ('VENICE_API_KEY',)

    def catalog(self):
        # type=all, never the default — see the module docstring.
        d = http('GET', self.base + '/models', params={'type': 'all'},
                 provider=self.name)
        out = []
        for m in d.get('data', []):
            spec = m.get('model_spec') or {}
            caps = spec.get('capabilities') or {}
            kind = m.get('type') or 'text'
            price = self._price(spec.get('pricing') or {}, kind)
            inputs, outputs = self._modalities(kind, caps)
            tags = (kind,) + tuple(k for k, on in (
                ('tee', caps.get('supportsTeeAttestation')),
                ('e2ee', caps.get('supportsE2EE')),
                ('reasoning', caps.get('supportsReasoning'))) if on)
            out.append(Offering(
                self.name, m['id'], model_key(m['id']),
                spec.get('name') or m['id'], price, inputs, outputs,
                m.get('context_length') or spec.get('availableContextTokens')
                or spec.get('maxInputTokens'),
                spec.get('maxCompletionTokens'), tags))
        return out

    def _price(self, p, kind):
        u = self.price_unit
        gen = (p.get('generation') or {}).get('usd')
        if kind in ('image', 'upscale') or gen is not None:
            return Price(image=_f(gen), coin='DIEM',
                         coin_in=_f((p.get('generation') or {}).get('diem')))
        inp, out = p.get('input') or {}, p.get('output') or {}
        return Price(inp=to_mtok(inp.get('usd'), u), out=to_mtok(out.get('usd'), u),
                     cache_read=to_mtok((p.get('cache_input') or {}).get('usd'), u),
                     coin='DIEM', coin_in=to_mtok(inp.get('diem'), u),
                     coin_out=to_mtok(out.get('diem'), u))

    @staticmethod
    def _modalities(kind, caps):
        if kind == 'image':
            return ('text',), ('image',)
        if kind == 'upscale':
            return ('image',), ('image',)
        if kind == 'tts':
            return ('text',), ('audio',)
        if kind == 'embedding':
            return ('text',), ('embedding',)
        inputs = ('text',) + tuple(k for k, on in (
            ('image', caps.get('supportsVision')),
            ('audio', caps.get('supportsAudioInput')),
            ('video', caps.get('supportsVideoInput'))) if on)
        return inputs, ('text',)

    def balance(self):
        d = http('GET', self.base + '/api_keys/rate_limits', headers=self.headers(),
                 provider=self.name).get('data', {})
        bal = d.get('balances') or {}
        return {'provider': self.name, 'usd': _f(bal.get('USD')),
                'coins': {k: _f(v) for k, v in bal.items() if k != 'USD'}}


def _f(v):
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None
