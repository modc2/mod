"""Read the vendored upstream (src/) for the TOOLKIT tab: packages, the
@agenti/mcp tool list, examples and READMEs. Parsed once per process."""
import functools
import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'src'
UPSTREAM = 'https://github.com/nirholas/agenti'
COMMIT = '72cfb67a18432a3d8773d200db1141b32289fb0a'

GROUPS = [
    ('Wallet & pay', ('create_wallet', 'get_balance', 'pay', 'create_invoice', 'check_payment', 'generate_mnemonic',
                      'derive_wallet', 'sign_message', 'usd_to_token_amount', 'get_token_price')),
    ('Market data', ('get_coin_price', 'get_trending_coins', 'get_protocol_tvl', 'get_top_protocols', 'get_crypto_news',
                     'get_ohlcv', 'search_coins', 'get_global_stats', 'detect_price_anomalies', 'get_market_volatility',
                     'get_crypto_news_feed', 'extract_trade_ideas', 'route_trade', 'calculate_pnl')),
    ('Solana', ('solana_', 'jupiter_', 'pump_', 'watch_pump', 'decode_pump', 'gmgn_', 'get_top_wallets',
                'get_wallet_trades', 'check_smart_wallet', 'get_vault_balances', 'get_payment_history',
                'verify_payment_receipt')),
    ('BNB Chain', ('bnb_',)),
    ('Shopping', ('bitrefill_',)),
]
SPENDS = re.compile(r'(swap|transfer|buy|sell|deploy|stake|copy_trade|^pay$)')


def _group(name):
    for g, keys in GROUPS:
        if any(name == k or (k.endswith('_') and name.startswith(k)) or name.startswith(k) for k in keys):
            return g
    return 'Other'


@functools.lru_cache(maxsize=1)
def packages() -> list:
    out = []
    for d in sorted((SRC / 'packages').iterdir()):
        pj = d / 'package.json'
        if pj.is_file():
            meta = json.loads(pj.read_text())
            out.append({'dir': d.name, 'name': meta.get('name'), 'version': meta.get('version'),
                        'description': meta.get('description', ''), 'readme': (d / 'README.md').is_file(),
                        'bin': list((meta.get('bin') or {}).keys()) if isinstance(meta.get('bin'), dict) else []})
        elif (d / 'pyproject.toml').is_file():
            out.append({'dir': d.name, 'name': 'agenti (python)', 'version': None,
                        'description': 'Python SDK', 'readme': False, 'bin': []})
        elif (d / 'openapi.yaml').is_file():
            out.append({'dir': d.name, 'name': 'openapi', 'version': None,
                        'description': 'OpenAPI spec for the hosted API', 'readme': False, 'bin': []})
    return out


@functools.lru_cache(maxsize=1)
def mcp_tools() -> list:
    text = (SRC / 'packages' / 'mcp' / 'src' / 'server.ts').read_text()
    rx = re.compile(r"server\.tool\(\s*'([a-z0-9_]+)',\s*((?:'(?:[^'\\]|\\.)*'\s*\+?\s*)+)", re.S)
    out = []
    for m in rx.finditer(text):
        name = m.group(1)
        desc = ''.join(re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(2))).replace("\\'", "'")
        out.append({'name': name, 'description': desc.strip(), 'group': _group(name),
                    'spends': bool(SPENDS.search(name)),
                    'needs_key': 'PRIVATE_KEY' in desc or 'private key' in desc.lower()})
    return out


@functools.lru_cache(maxsize=1)
def examples() -> list:
    out = []
    for p in sorted((SRC / 'examples').glob('*.ts')):
        lines = p.read_text().splitlines()
        head = next((l.strip(' /*') for l in lines[:12] if l.strip(' /*') and not l.startswith('import')), '')
        out.append({'file': p.name, 'title': re.sub(r'^\d+-', '', p.stem).replace('-', ' '),
                    'summary': head[:160], 'lines': len(lines)})
    return out


def example(file: str) -> dict:
    p = (SRC / 'examples' / Path(file).name)
    if p.suffix != '.ts' or not p.is_file():
        raise KeyError(file)
    return {'file': p.name, 'source': p.read_text()}


def readme(package: str = None) -> dict:
    if package:
        p = SRC / 'packages' / Path(package).name / 'README.md'
    else:
        p = SRC / 'README.md'
    if not p.is_file():
        raise KeyError(package or 'README')
    return {'package': package, 'markdown': p.read_text()}


def summary() -> dict:
    tools = mcp_tools()
    groups = {}
    for t in tools:
        groups[t['group']] = groups.get(t['group'], 0) + 1
    return {'upstream': UPSTREAM, 'commit': COMMIT, 'packages': len(packages()), 'mcp_tools': len(tools),
            'groups': groups, 'examples': len(examples()),
            'built': (SRC / 'packages' / 'mcp' / 'dist' / 'bin.js').is_file(),
            'installed': (SRC / 'node_modules').is_dir()}
