"""
nyc.report — the population & housing brief, as one self-contained file.

Everything a reader needs travels in a single HTML document: the maps are
inline SVG drawn here from the tract polygons (no tile server, no JavaScript,
no CDN), the tables are plain HTML, and the sources are cited at the bottom.
It can be emailed, printed, or opened offline years from now and still read
the same. ``csv()`` is the same numbers for a spreadsheet.

The findings section states only what the data says. Ranking and filtering
are mechanical and documented next to each list, so a reader can disagree
with a threshold and still trust the arithmetic.
"""

from __future__ import annotations

import csv as _csv
import html
import io
import math
from datetime import date
from typing import Any, Dict, List, Optional

from . import demographics as D
from . import sources as S

# Sequential ramp for a LIGHT (print) surface, light → dark. Validated with
# the dataviz palette checker against #fcfcfb: monotone lightness, every
# adjacent ΔL ≥ 0.06, light end 2.06:1 against the page, single hue. The
# on-screen map's dark-surface ramp lives in app/src/lib/palette.ts.
RAMP = ['#86b6ef', '#5598e7', '#256abf', '#184f95', '#0d366b', '#061f40']
NO_DATA = '#d4d3cd'
SURFACE = '#fcfcfb'


# ─────────────────────────────────────────────────────────────────────────────
# formatting
# ─────────────────────────────────────────────────────────────────────────────

def fmt(v: Any, kind: str) -> str:
    if v is None:
        return '–'
    if kind == 'usd':
        return f'${v:,.0f}'
    if kind == 'pct':
        return f'{v:.1f}%'
    if kind == 'int':
        return f'{v:,.0f}'
    if kind == 'x':
        return f'{v:.1f}×'
    return f'{v:,.1f}'


def _short(v: float, kind: str) -> str:
    """Compact legend labels: 18.5k, $1.2M, 42%."""
    if kind == 'pct':
        return f'{v:.0f}%'
    pre = '$' if kind == 'usd' else ''
    for div, suf in ((1e6, 'M'), (1e3, 'k')):
        if abs(v) >= div:
            return f'{pre}{v / div:.1f}{suf}'.replace('.0' + suf, suf)
    return f'{pre}{v:,.0f}' if kind != 'num' else f'{v:.1f}'


# ─────────────────────────────────────────────────────────────────────────────
# SVG maps
# ─────────────────────────────────────────────────────────────────────────────

_COS = math.cos(math.radians(40.7))     # equirectangular, true at NYC's latitude


def _rings(geom: dict) -> List[List[list]]:
    t, c = geom.get('type'), geom.get('coordinates') or []
    if t == 'Polygon':
        return c
    if t == 'MultiPolygon':
        return [r for poly in c for r in poly]
    return []


def map_svg(metric: str = 'density', geography: str = 'tract',
            width: int = 640, title: str = '') -> str:
    """One choropleth as an inline SVG, with its legend, quantile classes."""
    fc = D.choropleth(metric, geography)
    meta = fc['meta']
    kind = meta['format']
    feats = fc['features']

    # Re-quantile into len(RAMP) classes (the API layer uses 7 for screen).
    vals = [f['properties'].get(metric) for f in feats]
    vals = [v for v in vals if v is not None]
    if metric in ('density', 'population'):
        vals = [v for v in vals if v]
    stops = D.quantile_breaks(vals, len(RAMP))['stops']

    def cls(v):
        if v is None or (metric in ('density', 'population') and not v):
            return None
        i = 0
        while i < len(stops) and v >= stops[i]:
            i += 1
        return i

    tol = 0.00025 if geography == 'tract' else 0.0003
    xs, ys, shapes = [], [], []
    for f in feats:
        g = S.simplify_geometry(f['geometry'], tol=tol, precision=5)
        if not g:
            continue
        rings = [[((x * _COS), -y) for x, y in r] for r in _rings(g)]
        for r in rings:
            for x, y in r:
                xs.append(x)
                ys.append(y)
        shapes.append((rings, f['properties']))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    k = width / (x1 - x0)
    height = round((y1 - y0) * k)

    paths = []
    for rings, p in shapes:
        d = ''.join('M' + 'L'.join(f'{(x - x0) * k:.1f} {(y - y0) * k:.1f}'
                                   for x, y in r) + 'Z' for r in rings)
        c = cls(p.get(metric))
        fill = NO_DATA if c is None else RAMP[min(c, len(RAMP) - 1)]
        tip = f"{p.get('name') or ''}"
        if p.get('nta') and geography == 'tract':
            tip += f" ({p['nta']})"
        tip += f": {fmt(p.get(metric), kind)}"
        paths.append(f'<path d="{d}" fill="{fill}"><title>{html.escape(tip)}</title></path>')

    # Legend: one swatch per class. The bottom class reads "under" its upper
    # bound — its true minimum is often a lone outlier (a near-empty tract).
    labels = ([f'under {_short(stops[0], kind)}'] if stops else []) + [
        f'{_short(v, kind)}+' if i == len(stops) - 1 else f'{_short(v, kind)}'
        for i, v in enumerate(stops)]
    sw = ''.join(
        f'<span class="sw"><i style="background:{RAMP[i]}"></i>{lbl}</span>'
        for i, lbl in enumerate(labels[:len(RAMP)]))
    sw += f'<span class="sw"><i style="background:{NO_DATA}"></i>no data / no residents</span>'
    head = html.escape(title or meta['label'])
    return (f'<figure class="map"><figcaption>{head}'
            f'<span class="sub"> by {D.GEOS[geography]["label"].lower()}</span></figcaption>'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{head} map of NYC" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<g stroke="{SURFACE}" stroke-width="{0.25 if geography == "tract" else 0.8}" '
            f'stroke-linejoin="round">{"".join(paths)}</g></svg>'
            f'<div class="legend">{sw}</div></figure>')


# ─────────────────────────────────────────────────────────────────────────────
# tables and findings
# ─────────────────────────────────────────────────────────────────────────────

# (key, header, format) — the columns of every area table and the CSV.
COLUMNS = [
    ('population', 'Population', 'int'),
    ('density', 'People / sq mi', 'int'),
    ('median_income', 'Median household income', 'usd'),
    ('median_rent', 'Median gross rent', 'usd'),
    ('rent_burden_pct', 'Renters 30%+ burdened', 'pct'),
    ('severe_burden_pct', 'Renters 50%+ burdened', 'pct'),
    ('renter_pct', 'Renter households', 'pct'),
    ('median_sale_price', 'Median home sale', 'usd'),
    ('price_to_income', 'Sale price / income', 'x'),
    ('median_value', 'Median owner value (ACS)', 'usd'),
    ('housing_units', 'Homes', 'int'),
    ('vacancy_pct', 'Vacant', 'pct'),
    ('new_units_since_2020', 'New homes since 2020', 'int'),
    ('new_units_per_1k', 'New homes / 1k people', 'num'),
    ('pipeline_units', 'Homes in pipeline', 'int'),
    ('poverty_pct', 'Below poverty', 'pct'),
]


def _table(rows: List[Dict[str, Any]], cols=COLUMNS, name_col: str = 'Area',
           extra: Optional[List[tuple]] = None) -> str:
    cols = list(cols) + list(extra or [])
    th = f'<th>{name_col}</th>' + ''.join(f'<th>{html.escape(h)}</th>' for _, h, _ in cols)
    body = []
    for r in rows:
        nm = html.escape(str(r.get('name') or ''))
        if r.get('borough') and r.get('borough') != r.get('name'):
            nm += f' <span class="muted">{html.escape(r["borough"])}</span>'
        body.append(f'<tr><td>{nm}</td>' + ''.join(
            f'<td>{fmt(r.get(k), f)}</td>' for k, _, f in cols) + '</tr>')
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


MIN_POP = 2000      # an NTA smaller than this is a park, cemetery or airport


def findings(city: Dict[str, Any], areas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ranked neighbourhood lists, each with the rule that produced it."""
    res = [a for a in areas if (a.get('population') or 0) >= MIN_POP
           and a.get('residential', True)]

    def top(key, n=12, rev=True, where=lambda a: True):
        xs = [a for a in res if a.get(key) is not None and where(a)]
        return sorted(xs, key=lambda a: a[key], reverse=rev)[:n]

    c = city
    lag = top('density', 15, where=lambda a: (
        a['density'] > c['density'] and (a.get('rent_burden_pct') or 0) > c['rent_burden_pct']
        and (a.get('new_units_per_1k') or 0) < c['new_units_per_1k']))
    return [
        {'title': 'Densest neighborhoods',
         'rule': 'Residents per square mile of land.',
         'rows': top('density'), 'cols': [('density', 'People / sq mi', 'int')]},
        {'title': 'Dense, rent-burdened, and under-built',
         'rule': (f'Denser than the city ({fmt(c["density"], "int")}/sq mi), a larger share '
                  f'of rent-burdened renters than the city ({fmt(c["rent_burden_pct"], "pct")}), '
                  f'and fewer new homes per 1,000 residents since 2020 than the city '
                  f'({fmt(c["new_units_per_1k"], "num")}). Sorted by density.'),
         'rows': lag, 'cols': [('density', 'People / sq mi', 'int'),
                               ('rent_burden_pct', '30%+ burdened', 'pct'),
                               ('new_units_per_1k', 'New homes / 1k', 'num'),
                               ('pipeline_per_1k', 'Pipeline / 1k', 'num')]},
        {'title': 'Highest rent burden',
         'rule': 'Share of renter households paying 30% or more of income on rent.',
         'rows': top('rent_burden_pct'),
         'cols': [('rent_burden_pct', '30%+ burdened', 'pct'),
                  ('severe_burden_pct', '50%+ burdened', 'pct'),
                  ('median_income', 'Median income', 'usd'),
                  ('median_rent', 'Median rent', 'usd')]},
        {'title': 'Least affordable to buy, relative to local income',
         'rule': ('Median home sale price divided by median household income, '
                  'neighborhoods with 20+ sales. This prices what sold, not what residents '
                  'occupy: where nearly everyone rents, the few sales are mostly 2-3 family '
                  'houses, so the ratio reads as how far ownership is out of local reach.'),
         'rows': top('price_to_income', where=lambda a: (a.get('sales') or 0) >= 20),
         'cols': [('price_to_income', 'Price / income', 'x'),
                  ('median_sale_price', 'Median sale', 'usd'),
                  ('median_income', 'Median income', 'usd'),
                  ('sales', 'Sales', 'int')]},
        {'title': 'Most new homes since 2020',
         'rule': 'Net units completed 2020 to date per 1,000 residents.',
         'rows': top('new_units_per_1k'),
         'cols': [('new_units_per_1k', 'New homes / 1k', 'num'),
                  ('new_units_since_2020', 'New homes', 'int'),
                  ('pipeline_units', 'Pipeline', 'int')]},
        {'title': 'Fewest new homes since 2020',
         'rule': 'Net units completed 2020 to date per 1,000 residents (lowest first).',
         'rows': top('new_units_per_1k', rev=False),
         'cols': [('new_units_per_1k', 'New homes / 1k', 'num'),
                  ('density', 'People / sq mi', 'int'),
                  ('rent_burden_pct', '30%+ burdened', 'pct')]},
    ]


def _tile(label: str, value: str, sub: str = '') -> str:
    return (f'<div class="tile"><div class="tl">{html.escape(label)}</div>'
            f'<div class="tv">{value}</div><div class="ts">{html.escape(sub)}</div></div>')


CSS = """
:root{--ink:#1d1d1b;--ink2:#55544f;--mute:#8a8983;--line:#e4e3de;--surf:#fcfcfb;--acc:#184f95}
*{box-sizing:border-box}body{margin:0;background:var(--surf);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:40px 28px 80px}
h1{font-size:30px;line-height:1.15;margin:0 0 6px}h2{font-size:19px;margin:44px 0 6px;
padding-top:16px;border-top:1px solid var(--line)}h3{font-size:15px;margin:26px 0 2px}
.lede{color:var(--ink2);font-size:15px;max-width:760px}.muted,.sub{color:var(--mute);font-weight:400}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:22px 0}
.tile{border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fff}
.tl{font-size:12px;color:var(--ink2)}.tv{font-size:24px;font-weight:600;margin:2px 0;
font-variant-numeric:tabular-nums}.ts{font-size:11.5px;color:var(--mute)}
.maps{display:grid;grid-template-columns:1fr 1fr;gap:22px}.maps .wide{grid-column:1/-1}
figure.map{margin:0}figcaption{font-weight:600;margin-bottom:6px}svg{width:100%;height:auto;display:block}
svg path:hover{stroke:var(--ink);stroke-width:1.2}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink2);margin-top:6px}
.sw{display:inline-flex;align-items:center;gap:5px}.sw i{width:14px;height:10px;border-radius:2px;display:inline-block}
.rule{color:var(--ink2);font-size:12.5px;margin:0 0 6px;max-width:820px}
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:#fff}
table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--ink2);background:#f6f6f3;position:sticky;top:0}
th:first-child,td:first-child{text-align:left}tbody tr:hover{background:#f3f6fb}
.grid2{display:grid;grid-template-columns:1fr;gap:6px}
details summary{cursor:pointer;color:var(--acc);margin:10px 0}ul.notes{color:var(--ink2);font-size:12.5px}
a{color:var(--acc)}
@media (max-width:760px){.maps,.grid2{grid-template-columns:1fr}}
@media print{main{padding:0}h2{break-before:auto}.tbl{border:0}figure,table{break-inside:avoid}
details{display:block}details>*{display:block}}
"""


def html_report(since: str = '2025-01-01') -> str:
    """The whole brief as one self-contained HTML document."""
    nta = D.stats('nta', since)
    boro = D.stats('borough', since)
    c = nta['city']
    areas = sorted(nta['areas'].values(), key=lambda a: (a.get('borough') or '', a.get('name') or ''))
    boroughs = sorted(boro['areas'].values(), key=lambda a: -(a.get('population') or 0))
    today = date.today().isoformat()

    tiles = ''.join([
        _tile('Residents', fmt(c['population'], 'int'), f'ACS {nta["acs_vintage"]}'),
        _tile('Density', fmt(c['density'], 'int'), 'people per sq mi of land'),
        _tile('Median household income', fmt(c['median_income'], 'usd'), 'Census, citywide'),
        _tile('Median gross rent', fmt(c['median_rent'], 'usd'), 'rent + utilities, monthly'),
        _tile('Renters paying 30%+', fmt(c['rent_burden_pct'], 'pct'),
              f'{fmt(c["severe_burden_pct"], "pct")} pay 50%+'),
        _tile('Households that rent', fmt(c['renter_pct'], 'pct'),
              f'{fmt(c["renter_households"], "int")} renter households'),
        _tile('Median home sale', fmt(c.get('median_sale_price'), 'usd'),
              f'{fmt(c.get("sales"), "int")} sales since {since}'),
        _tile('Sale price / income', fmt(c.get('price_to_income'), 'x'),
              'years of median income'),
        _tile('New homes since 2020', fmt(c['new_units_since_2020'], 'int'),
              f'+{fmt(c.get("units_growth_pct"), "pct")} on the 2020 stock'),
        _tile('Homes in the pipeline', fmt(c['pipeline_units'], 'int'),
              f'{fmt(c["permitted_units"], "int")} already permitted'),
        _tile('Vacant homes', fmt(c['vacancy_pct'], 'pct'),
              'includes seasonal + for-sale/rent'),
        _tile('Below poverty line', fmt(c['poverty_pct'], 'pct'), 'of residents'),
    ])

    maps = ''.join([
        f'<div class="wide">{map_svg("density", "tract", 1000, "Population density, people per square mile")}</div>',
        map_svg('rent_burden_pct', 'nta', 520, 'Renters paying 30%+ of income on rent'),
        map_svg('median_income', 'nta', 520, 'Median household income'),
        map_svg('new_units_per_1k', 'nta', 520, 'New homes since 2020 per 1,000 residents'),
        map_svg('price_to_income', 'nta', 520, 'Median home sale price / median income'),
    ])

    finds = ''
    for f in findings(c, list(nta['areas'].values())):
        finds += (f'<section><h3>{html.escape(f["title"])}</h3>'
                  f'<p class="rule">{html.escape(f["rule"])}</p>'
                  + (_table(f['rows'], cols=[], name_col='Neighborhood', extra=f['cols'])
                     if f['rows'] else '<p class="muted">None.</p>') + '</section>')

    src = ''.join(f'<li><a href="{s["url"]}">{html.escape(s["name"])}</a></li>'
                  for s in nta['sources'])
    notes = ''.join(f'<li>{html.escape(n)}</li>' for n in nta['notes'])
    city_row = dict(c, name='New York City', borough=None)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>New York City: population, density and housing — {today}</title>
<style>{CSS}</style></head><body><main>
<h1>New York City: who lives where, and what housing costs them</h1>
<p class="lede">Population, density, income, rent, rent burden, home prices and new
construction for all five boroughs and {len(areas)} neighborhoods, from public
records only. Census figures are the American Community Survey {nta['acs_vintage']}
5-year estimates; sales are recorded deeds since {since}; construction is the City
Planning Housing Database. Compiled {today}. Hover any area on a map for its value.</p>
<div class="tiles">{tiles}</div>

<h2>Maps</h2><div class="maps">{maps}</div>

<h2>By borough</h2>{_table(boroughs + [city_row], name_col='Borough')}

<h2>What stands out</h2>
<p class="rule">Lists below rank residential neighborhoods (not parks, airports,
cemeteries or Rikers Island) with at least {MIN_POP:,} residents.
Each rule is stated in full; the complete table follows.</p>
<div class="grid2">{finds}</div>

<h2>Every neighborhood</h2>
<details><summary>Show all {len(areas)} neighborhoods</summary>
{_table(areas, name_col='Neighborhood')}</details>
<p class="rule">The same table, plus every census tract, is available as CSV from
<code>/nyc/api/report.csv?geography=nta</code> and <code>?geography=tract</code>.</p>

<h2>Sources and method</h2><ul class="notes">{src}</ul><ul class="notes">{notes}</ul>
<p class="rule">Generated by the open-source <code>nyc</code> module of the mod protocol.
No data here is paid, licensed, or private.</p>
</main></body></html>"""


def csv(geography: str = 'nta', since: str = '2025-01-01') -> str:
    st = D.stats(geography, since)
    keys = ['key', 'name', 'borough'] + [k for k, _, _ in COLUMNS] + [
        'households', 'land_sqmi', 'median_value', 'value_to_income', 'household_size',
        'rent_pct_income', 'pipeline_per_1k', 'units_growth_pct', 'sales']
    keys = list(dict.fromkeys(keys))
    out = io.StringIO()
    w = _csv.writer(out)
    w.writerow(keys)
    for k, a in sorted(st['areas'].items()):
        w.writerow([k] + [a.get(c) for c in keys[1:]])
    w.writerow(['NYC'] + [st['city'].get(c) for c in keys[1:]])
    return out.getvalue()
