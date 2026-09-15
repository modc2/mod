"""io.net Intelligence — per-token floats, plus the only measured latency here.

Its catalog publishes `avg_latency_ms_per_day` and `avg_throughput_per_day`
alongside price, which makes it the one provider whose speed claim comes from the
provider rather than from us timing it. That number feeds `sort=fast`; every
other row sorts on latency only after this module has measured it once.
"""

from .base import Offering, Provider, http, model_key
from .price import Price, to_mtok

MODS = {'text': 'text', 'image': 'image', 'audio': 'audio', 'video': 'video'}


class IoNet(Provider):
    name = 'ionet'
    base = 'https://api.intelligence.io.solutions/api/v1'
    caps = ('catalog', 'chat', 'embedding')
    price_unit = 'per_token'
    kyc = 'account'
    pay = ('IO', 'SOL', 'USDC')
    account = True
    pay_note = ('Solana-native account, funded with $IO or USDC; a free tier '
                'exists and no documents are requested')
    checked = '2026-09-14'

    @property
    def env(self):
        return ('IONET_API_KEY', 'IO_API_KEY')

    def catalog(self):
        d = http('GET', self.base + '/models', provider=self.name)
        out = []
        for m in d.get('data', []):
            price = Price(
                inp=to_mtok(m.get('input_token_price'), self.price_unit),
                out=to_mtok(m.get('output_token_price'), self.price_unit),
                cache_read=to_mtok(m.get('cache_read_token_price'), self.price_unit))
            tags = tuple(k for k, on in (
                ('attestation', m.get('supports_attestation')),
                ('reasoning', m.get('supports_reasoning')),
                ('cache', m.get('supports_prompt_cache'))) if on)
            extra = {}
            if m.get('avg_latency_ms_per_day'):
                extra['latency_ms'] = m['avg_latency_ms_per_day']
            if m.get('avg_throughput_per_day'):
                extra['throughput'] = m['avg_throughput_per_day']
            if m.get('min_access_tier'):
                extra['tier'] = m['min_access_tier']
            out.append(Offering(
                self.name, m['id'], model_key(m['id']), m.get('name'), price,
                tuple(MODS[x] for x in (m.get('input_modalities') or [])
                      if x in MODS) or ('text',),
                tuple(MODS[x] for x in (m.get('output_modalities') or [])
                      if x in MODS) or ('text',),
                m.get('context_window') or m.get('max_model_len'),
                m.get('max_tokens'), tags, extra))
        return out
