"""Tables, fitted cost models and figures for ztensor.tex, from
paper/data/snapshot.json only (offline; run collect.py to refresh).

Writes:
    paper/generated/macros.tex    every number the text cites, as a macro
    paper/generated/tab_*.tex     the tables
    paper/figures/*.pdf           the figures
    paper/generated/fits.json     least-squares cost models (for PAPER.md)
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = json.load(open(os.path.join(HERE, 'data', 'snapshot.json')))
GEN = os.path.join(HERE, 'generated')
FIG = os.path.join(HERE, 'figures')
os.makedirs(GEN, exist_ok=True)
os.makedirs(FIG, exist_ok=True)

# dataviz reference palette, categorical slots 1-5 in fixed order (validated
# light: CVD dE >= 9.1, normal >= 19.6; 3 slots < 3:1 contrast -> every
# series is direct-labelled and has its own marker; the tables carry values)
SERIES = {'eth': ('#2a78d6', 'o'), 'solana': ('#eb6834', 's'),
          'near': ('#1baf7a', '^'), 'substrate': ('#eda100', 'D'),
          'starknet': ('#e87ba4', 'v')}
NAMES = {'starknet': 'Starknet', 'near': 'NEAR', 'eth': 'Ethereum',
         'solana': 'Solana', 'substrate': 'Substrate', 'postquant': 'postquant'}
TEXT, MUTED, GRID = '#0b0b0b', '#52514e', '#e4e3df'

# per-transaction limits of the binding resource (what sets each max ring)
LIMITS = {'near': ('tgas', 300.0, '300 Tgas per call'),
          'eth': ('gas', float(1 << 24), '$2^{24}$ gas per tx (EIP-7825)'),
          'solana': ('cu_max', 1_400_000.0, '1.4\\,M CU per tx (per unit)'),
          'substrate': ('weight_ms', 1299.9, '1.3\\,s ref\\_time per extrinsic')}

eco = SNAP['economics']['chains']
costs = SNAP['costs']


def fit(xs, ys):
    """Ordinary least squares y = a + b x, with R^2."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    ss_res = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return a, b, 1 - ss_res / ss_tot if ss_tot else 1.0


def rows(chain, key):
    return [(r['ring'], r[key]) for r in (costs.get(chain) or {}).get('rows') or []
            if key in r and not r.get('error')]


FITS = {}
for chain, key in (('near', 'tgas'), ('eth', 'gas'), ('solana', 'cu'),
                   ('substrate', 'weight_ms'), ('substrate', 'proof_kb')):
    pts = rows(chain, key)
    a, b, r2 = fit([p[0] for p in pts], [p[1] for p in pts])
    FITS[f'{chain}.{key}'] = {'a': a, 'b': b, 'r2': r2, 'n': len(pts),
                              'rings': [p[0] for p in pts]}
json.dump(FITS, open(os.path.join(GEN, 'fits.json'), 'w'), indent=1)


def tex_num(v, digits=3):
    if v is None:
        return '--'
    if v == 0:
        return '0'
    if abs(v) >= 1e6:
        e = int(math.floor(math.log10(abs(v))))
        return f'{v / 10 ** e:.2f}\\times10^{{{e}}}'
    if abs(v) >= 100:
        return f'{v:,.0f}'.replace(',', '\\,')
    return f'{v:.{digits}g}'


def sci(v):
    """A probability for a table cell: 0.013, 3.9\\times10^{-5}."""
    if v >= 1e-3:
        return f'{v:.2g}'
    e = int(math.floor(math.log10(v)))
    return f'{v / 10 ** e:.1f}\\times10^{{{e}}}'


def usd(v):
    """A bare number: table columns are headed USD, text macros add it
    (a typeset \\$ pulls in a bitmap textcomp font under pdflatex)."""
    if v is None:
        return '--'
    if v >= 1:
        return f'{v:.2f}'
    if v >= 0.01:
        return f'{v:.3f}'
    return f'{v:.4f}'


# ---- macros ----------------------------------------------------------------

M = {}


def macro(name, value):
    M[name] = value


macro('snapdate', SNAP['date'])
for c in ('starknet', 'near', 'eth', 'solana', 'substrate'):
    e = eco[c]
    tag = {'starknet': 'Sn', 'near': 'Near', 'eth': 'Eth', 'solana': 'Sol',
           'substrate': 'Sub'}[c]
    macro(f'usd{tag}', 'USD~' + usd(e['actions']['score']['usd']))
    macro(f'price{tag}', f'USD~{e["usd_price"]:.4g}')
    macro(f'block{tag}', f'{e["block_seconds"]:.3g}')
    macro(f'tempo{tag}', f'{e["min_tempo"]:,}'.replace(',', '\\,'))
    macro(f'maxring{tag}', str(SNAP['chains'][c]['max_ring']))
    macro(f'sim{tag}', f'{SNAP["chains"][c]["sim"]["seconds"]:.3g}')
f = FITS['substrate.weight_ms']
macro('subSlope', f'{f["b"]:.2f}')
macro('subIntercept', f'{f["a"]:.1f}')
macro('subRtwo', f'{f["r2"]:.6f}')
macro('subPerBlock', f'{eco["substrate"]["actions_per_block"]:.0f}')
fp = FITS['substrate.proof_kb']
macro('subProofSlope', f'{fp["b"]:.2f}')
macro('subProofIntercept', f'{fp["a"]:.0f}')
f = FITS['near.tgas']
macro('nearSlope', f'{f["b"]:.2f}')
macro('nearIntercept', f'{f["a"]:.2f}')
macro('nearRtwo', f'{f["r2"]:.6f}')
f = FITS['eth.gas']
macro('ethSlope', f'{f["b"] / 1e3:.0f}')
macro('ethIntercept', f'{f["a"] / 1e3:.0f}')
macro('ethRtwo', f'{f["r2"]:.6f}')
f = FITS['solana.cu']
macro('solSlope', f'{f["b"] / 1e6:.2f}')
macro('solIntercept', f'{f["a"] / 1e6:.2f}')
macro('solRtwo', f'{f["r2"]:.6f}')
tests = SNAP['tests']
macro('testsNative', str(sum((t or {}).get('passed') or 0 for t in tests.values())))
for c, t in tests.items():
    macro(f'tests{c.capitalize()}', str((t or {}).get('passed')))
sn = SNAP['chains']['starknet']['sim']['actions']
macro('snSteps', f'{sn["score"]["steps"]:,.0f}'.replace(',', '\\,'))
macro('snEcop', f'{sn["score"]["ec_op"]:.0f}')
pq = SNAP['chains']['postquant']['sim']['actions']
macro('pqRunMs', f'{pq["score"]["run_ms"]:.1f}')
macro('pqGas', f'{pq["score"]["gas"]:,.0f}'.replace(',', '\\,'))

# ---- scaling: a Bittensor-sized subnet, both cost ladders (core/forecast) --

import collect  # noqa: E402  (loads the module's own adapters + forecast)

_m = collect.load_mod()
fc, NETS = _m.fc, _m.NETS
SCALE = {'M': 256, 'V': 64, 'ring': 16, 'hours': 1, 'K': 24, 'C': fc.COMMITTEE}
SCALE_ST = ('baseline', 'batch', 'lazy', 'proof', 'committee', 'private')
scale_rows, ratios = [], {}
for c in ('starknet', 'near', 'eth', 'solana', 'substrate'):
    row = eco[c]
    pc = fc.PROOF_COST.get(c)
    n = NETS.chain(c).fee(pc, {'gas': row['gas'], 'usd': row['usd_price']}) if pc else None
    u = fc.units(row, n * row['usd_price'] if n else None)
    day = {st: fc.epoch_cost(u, SCALE['M'], SCALE['V'], SCALE['ring'], st,
                             SCALE['K'], SCALE['C'])['total'] * 24 / SCALE['hours']
           for st in SCALE_ST}
    ratios[c] = day['baseline'] / day['private']
    SCALE_DAY = globals().setdefault('SCALE_DAY', {})
    SCALE_DAY[c] = day
    scale_rows.append(f'{NAMES[c]}{"$^*$" if not pc else ""} & ' +
                      ' & '.join(usd(day[st]) for st in SCALE_ST) +
                      f' & ${ratios[c]:.0f}\\times$ \\\\')
with open(os.path.join(GEN, 'tab_scale.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n' + '\n'.join(scale_rows) + '\n')
_t = {c: d['baseline'] for c, d in SCALE_DAY.items()}
macro('scaleChainSpread', f"{max(_t.values()) / min(_t.values()):,.0f}".replace(',', '\\,'))
macro('scaleMinRatio', f'{min(ratios.values()):.0f}')
macro('scaleMaxRatio', f'{max(ratios.values()):.0f}')
for k, v in SCALE.items():
    macro(f'scale{k}', str(v))

CAP_F = (0.10, 0.25, 1 / 3)


def prob(v):
    return '$0$' if v == 0 else '$<10^{-6}$' if v < 1e-6 else f'${sci(v)}$'


cap_rows = []
for V in (64, 256):
    for C in (16, 32, 48):
        cap_rows.append(f'{V} & {C} & ' + ' & '.join(prob(fc.capture(V, C, f)) for f in CAP_F)
                        + ' & ' + ' & '.join(prob(fc.starve(V, C, f)) for f in CAP_F) + ' \\\\')
with open(os.path.join(GEN, 'tab_capture.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n' + '\n'.join(cap_rows) + '\n')
macro('capDefault', sci(fc.capture(64, fc.COMMITTEE, 0.25)))
macro('starveDefault', sci(fc.starve(64, fc.COMMITTEE, 0.25)))
macro('capSixteen', sci(fc.capture(64, 16, 0.25)))
macro('starveSixteen', sci(fc.starve(64, 16, 0.25)))

with open(os.path.join(GEN, 'macros.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py from data/snapshot.json - do not edit\n')
    for k, v in M.items():
        fh.write(f'\\newcommand{{\\{k}}}{{{v}}}\n')

# ---- Table: cost per action (ring 4, last full epoch) ------------------------

UNITS = {'starknet': ('steps', 'Cairo steps'), 'near': ('tgas', 'Tgas'),
         'eth': ('gas', 'gas'), 'solana': ('cu', 'CU (all units)'),
         'substrate': ('weight_ms', 'ms ref\\_time'),
         'postquant': ('run_ms', 'ms wasm (off chain)')}
order = ['starknet', 'near', 'eth', 'solana', 'substrate', 'postquant']
lines = []
for c in order:
    acts = SNAP['chains'][c]['sim']['actions']
    key, unit = UNITS[c]
    cells = []
    for k in ('submit', 'score', 'settle', 'claim'):
        v = (acts.get(k) or {}).get(key)
        u = ((eco.get(c) or {}).get('actions') or {}).get(k, {}).get('usd')
        cells.append(f'${tex_num(v)}$' + (f' & {usd(u)}' if c in eco else ' & --'))
    lines.append(f'{NAMES[c]} & {unit} & ' + ' & '.join(cells) + ' \\\\')
with open(os.path.join(GEN, 'tab_actions.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n' + '\n'.join(lines) + '\n')

# ---- Table: verification cost model per chain -------------------------------

model = [
    ('Starknet', 'Cairo steps', f'${tex_num(sn["score"]["steps"])}$ (ring 4)', '--', '--', '64',
     'own choice'),
]
for c, key, unit, scale in (('near', 'tgas', 'Tgas', 1), ('eth', 'gas', 'kgas', 1e3),
                            ('solana', 'cu', 'MCU', 1e6),
                            ('substrate', 'weight_ms', 'ms', 1)):
    f = FITS[f'{c}.{key}']
    lim = LIMITS[c][2]
    model.append((NAMES[c], unit, f'${f["a"] / scale:.2f}$', f'${f["b"] / scale:.2f}$',
                  f'${f["r2"]:.6f}$', str(SNAP['chains'][c]['max_ring']), lim))
with open(os.path.join(GEN, 'tab_model.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n')
    for r in model:
        fh.write(' & '.join(r) + ' \\\\\n')

# ---- Table: mainnet economics -------------------------------------------------

with open(os.path.join(GEN, 'tab_econ.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n')
    for c in ('starknet', 'near', 'eth', 'solana', 'substrate'):
        e = eco[c]
        br = {r['ring']: r['usd'] for r in e['by_ring']}
        apb = e.get('actions_per_block')
        fh.write(f'{NAMES[c]} & {e["symbol"]} & ${e["block_seconds"]:.3g}$ & '
                 f'${e["min_tempo"]:,}$'.replace(',', '\\,') +
                 f' & {usd(br.get(4))} & {usd(br.get(32))} & {usd(br.get(64))} & '
                 f'{"$" + format(apb, ".0f") + "$" if apb else "--"} \\\\\n')

# ---- Table: test suites -------------------------------------------------------

suite = {'starknet': 'snforge (Cairo)', 'near': 'cargo test (near-sdk)',
         'eth': 'forge (Foundry)', 'solana': 'cargo test (sBPF)',
         'substrate': 'cargo test (ink! off-chain env)',
         'postquant': 'cargo test (pqc1)'}
with open(os.path.join(GEN, 'tab_tests.tex'), 'w') as fh:
    fh.write('% generated by paper/figures.py\n')
    for c in order:
        t = tests.get(c) or {}
        s = SNAP['chains'][c]['sim']
        fh.write(f'{NAMES[c]} & {suite[c]} & {t.get("passed")}/{(t.get("passed") or 0) + (t.get("failed") or 0)}'
                 f' & {"yes" if s.get("yuma_ok") else "no"} & '
                 f'{(s.get("double_claim") or "--").replace("rejected: ", "")} & ${s.get("seconds")}$ \\\\\n')

# ---- figures -----------------------------------------------------------------

import matplotlib  # noqa: E402

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update({
    'font.family': 'serif', 'font.size': 8.5, 'axes.edgecolor': MUTED,
    'axes.labelcolor': TEXT, 'xtick.color': MUTED, 'ytick.color': MUTED,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6,
    'legend.frameon': False, 'pdf.fonttype': 42})


def label_end(ax, x, y, text, color_dy=0):
    ax.annotate(text, (x, y), xytext=(5, color_dy), textcoords='offset points',
                va='center', ha='left', fontsize=7.5, color=TEXT)


# Figure 1: USD per score vs ring size
fig, ax = plt.subplots(figsize=(3.4, 2.5))
offs = {'eth': 0, 'solana': 3, 'near': -3, 'substrate': -2, 'starknet': 0}
for c in ('eth', 'solana', 'near', 'substrate', 'starknet'):
    col, mk = SERIES[c]
    br = sorted((r['ring'], r['usd'], r.get('est', False)) for r in eco[c]['by_ring'])
    xs, ys = [r[0] for r in br], [r[1] for r in br]
    est = any(r[2] for r in br)
    ax.plot(xs, ys, color=col, lw=1.6, ls='--' if est else '-', marker=mk,
            ms=4.5, mec='white', mew=0.8, label=NAMES[c] + (' (est.)' if est else ''))
    label_end(ax, xs[-1], ys[-1], NAMES[c], offs[c])
ax.set_xscale('log', base=2)
ax.set_yscale('log')
ax.set_xticks([4, 8, 16, 32, 64])
ax.set_xticklabels(['4', '8', '16', '32', '64'])
ax.set_xlim(3.5, 140)
ax.set_xlabel('ring size $n$ (anonymity set)')
ax.set_ylabel('USD per ring-signed action')
# the empty band between Ethereum and the rest: clear of every line
ax.legend(fontsize=6.5, loc='center left', bbox_to_anchor=(0.0, 0.6), ncol=2,
          handlelength=1.8, columnspacing=0.8)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, 'usd_by_ring.pdf'))
plt.close(fig)

# Figure 2: fraction of the per-transaction limit vs ring size
fig, ax = plt.subplots(figsize=(3.4, 2.4))
for c in ('eth', 'solana', 'near', 'substrate'):
    col, mk = SERIES[c]
    key, lim, _ = LIMITS[c]
    pts = rows(c, key)
    xs, ys = [p[0] for p in pts], [p[1] / lim for p in pts]
    ax.plot(xs, ys, color=col, lw=1.6, marker=mk, ms=4.5, mec='white', mew=0.8,
            label=NAMES[c])
    # Ethereum and NEAR land on the same share at n = 32: spread the labels
    label_end(ax, xs[-1], ys[-1], NAMES[c], {'eth': 8, 'near': -8, 'solana': 0,
                                             'substrate': 0}[c])
ax.axhline(1.0, color=MUTED, lw=0.9, ls=':')
ax.annotate('per-transaction limit', (4, 1.0), xytext=(0, 3),
            textcoords='offset points', fontsize=7, color=MUTED)
ax.set_xlabel('ring size $n$')
ax.set_ylabel('share of the per-tx limit')
ax.set_ylim(0, 1.12)
ax.set_xlim(0, 80)
ax.set_xticks([4, 16, 32, 48, 64])
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, 'limit_share.pdf'))
plt.close(fig)

print('ok', sorted(M)[:5], '...', len(M), 'macros')
