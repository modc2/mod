"""OpenRouter — the widest catalog, and the benchmark the others are read against.

Kept in a no-KYC registry with its eyes open: OpenRouter needs an account, so it
is `kyc='account'`, not `none`. It earns its place because it takes USDC on Base
through Coinbase Commerce and never asks for a document, and because 447 models
is the yardstick that makes a cheaper router's claim legible. Filter it out with
`kyc=none` when the brief is strict.
"""

from .base import Offering, Provider, http, model_key
from .price import Price

MODS = {'text': 'text', 'image': 'image', 'audio': 'audio', 'video': 'video',
        'file': 'file'}


class OpenRouter(Provider):
    name = 'openrouter'
    base = 'https://openrouter.ai/api/v1'
    caps = ('catalog', 'chat', 'balance')
    price_unit = 'per_token'          # USD per token, as STRINGS — see price.py
    kyc = 'account'
    pay = ('USDC', 'ETH', 'BTC', 'SOL', 'USDT')
    account = True
    pay_note = ('account required, no documents; top up with USDC on Base via '
                'Coinbase Commerce, credits never expire')
    checked = '2026-09-14'

    @property
    def env(self):
        return ('OPENROUTER_API_KEY',)

    def _mods(self, arch, side):
        got = [MODS[m] for m in (arch.get(side) or []) if m in MODS]
        return tuple(got) or ('text',)

    def catalog(self):
        out = []
        for m in http('GET', self.base + '/models', provider=self.name).get('data', []):
            p = m.get('pricing') or {}
            arch = m.get('architecture') or {}
            price = Price(
                inp=self._n(p.get('prompt')), out=self._n(p.get('completion')),
                cache_read=self._n(p.get('input_cache_read')),
                request=self._f(p.get('request')), image=self._f(p.get('image')))
            top = m.get('top_provider') or {}
            out.append(Offering(
                self.name, m['id'], model_key(m['id']), m.get('name'), price,
                self._mods(arch, 'input_modalities'),
                self._mods(arch, 'output_modalities'),
                m.get('context_length'), top.get('max_completion_tokens')))
        return out

    def _n(self, v):
        from .price import to_mtok
        return to_mtok(v, self.price_unit)

    @staticmethod
    def _f(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    def balance(self):
        d = http('GET', self.base + '/credits', headers=self.headers(),
                 provider=self.name).get('data', {})
        used, total = d.get('total_usage') or 0, d.get('total_credits') or 0
        return {'provider': self.name, 'usd': round(total - used, 6),
                'granted': total, 'used': used}
