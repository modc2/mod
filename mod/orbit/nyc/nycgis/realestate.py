"""
nyc.realestate — the market as it is listed, not just as it closes.

The DOF deed file (``prices.py``) is the record of what *sold*. This module
adds the other half of the picture, from free public downloads:

  * StreetEasy Data Dashboard CSVs — median asking price, median asking
    rent and rental inventory, monthly since 2010, for NYC, each borough
    and ~176 neighborhoods. Published as zipped wide CSVs (one row per
    area, one column per month) on a public CDN, no key. Attribution
    required by their terms; it travels in every payload.
  * Zillow Research CSVs — ZHVI (home value index) and ZORI (observed rent
    index) for the New York metro, as a national-market yardstick.

Shape note: the StreetEasy CSV is *wide* (area × month). It is stored in the
cache transposed into per-area series keyed to one shared month list — the
month columns differ between files (sales start 2010-01, some series start
later), so each file keeps its own month list and nothing assumes alignment
across files.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import demographics as D
from . import sources as S

SE_CDN = 'https://cdn-charts.streeteasy.com'
SE_FILES = {
    'asking_price': f'{SE_CDN}/sales/All/medianAskingPrice_All.zip',
    'asking_rent': f'{SE_CDN}/rentals/All/medianAskingRent_All.zip',
    'inventory': f'{SE_CDN}/rentals/All/rentalInventory_All.zip',
    # The for-sale side of the dashboard. medianSalePrice 403s (recorded
    # sales are the deed file's job anyway); these four are open.
    'sale_inventory': f'{SE_CDN}/sales/All/totalInventory_All.zip',
    'days_on_market': f'{SE_CDN}/sales/All/daysOnMarket_All.zip',
    'price_cut_share': f'{SE_CDN}/sales/All/priceCutShare_All.zip',
}
ZILLOW = {
    'zhvi': ('https://files.zillowstatic.com/research/public_csvs/zhvi/'
             'Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv'),
    'zori': ('https://files.zillowstatic.com/research/public_csvs/zori/'
             'Metro_zori_uc_sfrcondomfr_sm_sa_month.csv'),
}

ATTRIBUTION = [
    {'name': 'StreetEasy Data Dashboard', 'url': 'https://streeteasy.com/blog/data-dashboard/'},
    {'name': 'Zillow Research', 'url': 'https://www.zillow.com/research/data/'},
]

WEEK = 7 * S.DAY


# ─────────────────────────────────────────────────────────────────────────────
# StreetEasy
# ─────────────────────────────────────────────────────────────────────────────

def _streeteasy(key: str) -> Dict[str, Any]:
    """One StreetEasy file as {'months': [...], 'areas': {name: {...}}}."""
    url = SE_FILES[key]

    def fetch():
        r = requests.get(url, timeout=60, headers={'User-Agent': S.USER_AGENT})
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        with z.open(z.namelist()[0]) as fh:
            rows = list(csv.reader(io.TextIOWrapper(fh, 'utf-8-sig')))
        header = rows[0]
        months = header[3:]
        areas = {}
        for row in rows[1:]:
            if len(row) < 4:
                continue
            name, borough, area_type = row[0], row[1], row[2]
            vals = [(float(v) if v not in ('', 'NA') else None) for v in row[3:]]
            areas[name] = {'borough': borough, 'type': area_type, 'values': vals}
        return {'months': months, 'areas': areas, 'url': url}

    return S.cached(f'market-se-{key}', WEEK, fetch)


def _latest(months: List[str], vals: List[Optional[float]]
            ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """(latest month, latest value, value 12 months earlier)."""
    for i in range(len(vals) - 1, -1, -1):
        if vals[i] is not None:
            prior = vals[i - 12] if i >= 12 else None
            return months[i], vals[i], prior
    return None, None, None


def _yoy(now: Optional[float], ago: Optional[float]) -> Optional[float]:
    if now is None or not ago:
        return None
    return round(100.0 * (now - ago) / ago, 1)


def _snap(key: str, area: str) -> Dict[str, Any]:
    t = _streeteasy(key)
    a = t['areas'].get(area)
    if not a:
        return {}
    m, v, ago = _latest(t['months'], a['values'])
    return {'month': m, 'value': v, 'year_ago': ago, 'yoy_pct': _yoy(v, ago)}


def _series(key: str, area: str, months_back: int = 120) -> List[dict]:
    t = _streeteasy(key)
    a = t['areas'].get(area)
    if not a:
        return []
    pairs = list(zip(t['months'], a['values']))[-months_back:]
    return [{'month': m, 'value': v} for m, v in pairs if v is not None]


def norm_name(name: str) -> str:
    """Area name → comparable form: lowercased, parens and punctuation out."""
    s = re.sub(r'\(.*?\)', ' ', str(name or '').lower())
    s = s.replace('&', ' and ')
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def match_nta(nta_name: str, nta_borough: str,
              se_areas: List[Tuple[str, str, str]]) -> Optional[str]:
    """
    The StreetEasy neighborhood an NTA belongs to, or None.

    NTA 2020 names are compounds ("Astoria (North)-Ditmars-Steinway");
    StreetEasy names are the colloquial neighborhood ("Astoria"). So the match
    runs the other way round: the StreetEasy name must appear whole-word inside
    the NTA name, in the same borough — and the longest such name wins, so
    "East Harlem" beats "Harlem" for East Harlem (North).
    """
    nname = norm_name(nta_name)
    best = None
    for name, norm, boro in se_areas:
        if boro != nta_borough or not norm:
            continue
        if re.search(rf'(?:^| ){re.escape(norm)}(?: |$)', nname):
            if best is None or len(norm) > len(best[1]):
                best = (name, norm)
    return best[0] if best else None


def forsale_choropleth() -> Dict[str, Any]:
    """
    The for-sale market as a neighborhood choropleth: median asking price
    (the fill), active listings, days on market and the share of listings cut,
    joined from StreetEasy's ~176 areas onto the 262 NTA polygons by name.
    ``breaks`` carries quantile classes for the asking price, so the frontend
    draws it with no extra parameterisation (same contract as the crime layer).
    """
    def fetch():
        from . import layers as L           # deferred: layers imports us
        # One read per table: S.cached re-parses its JSON from disk on every
        # call, so the ~1,000 lookups below must not each go through _snap.
        tables = {k: _streeteasy(k) for k in
                  ('asking_price', 'sale_inventory', 'days_on_market',
                   'price_cut_share')}
        se_areas = [(name, norm_name(name), a['borough'])
                    for name, a in tables['asking_price']['areas'].items()
                    if a['type'] == 'neighborhood']

        def snap(key: str, area: str):
            t = tables[key]
            a = t['areas'].get(area)
            if not a:
                return None, None, None
            return _latest(t['months'], a['values'])

        feats, values, matched = [], [], set()
        as_of = None
        for f in L.neighborhoods()['features']:
            p = f['properties']
            props: Dict[str, Any] = {
                'name': p.get('ntaname'), 'area': p.get('nta2020'),
                'borough': p.get('boroname'), 'se_area': None,
                'asking_price': None, 'asking_price_yoy': None,
                'inventory': None, 'days_on_market': None,
                'price_cut_pct': None, 'month': None,
            }
            se = match_nta(p.get('ntaname', ''), p.get('boroname', ''), se_areas)
            if se:
                m, price, ago = snap('asking_price', se)
                _, inv, _ = snap('sale_inventory', se)
                _, dom, _ = snap('days_on_market', se)
                _, cut, _ = snap('price_cut_share', se)
                props.update({
                    'se_area': se, 'month': m,
                    'asking_price': round(price) if price is not None else None,
                    'asking_price_yoy': _yoy(price, ago),
                    'inventory': int(inv) if inv is not None else None,
                    'days_on_market': int(dom) if dom is not None else None,
                    # The share file is a 0–1 fraction; shown as a percent.
                    'price_cut_pct': (round(cut * 100, 1) if cut is not None
                                      and cut <= 1 else cut),
                })
                if price is not None:
                    matched.add(se)
                    values.append(round(price))
                    as_of = as_of or m
            feats.append({'type': 'Feature', 'properties': props,
                          'geometry': f['geometry']})

        fc = {'type': 'FeatureCollection', 'features': feats}
        fc['breaks'] = D.quantile_breaks(values)
        fc['meta'] = {
            'metric': 'asking_price', 'label': 'Median asking price',
            'format': 'usd', 'as_of': as_of,
            'areas': len(feats), 'areas_with_data': len(values),
            'se_areas_matched': len(matched),
            'note': ('What sellers are ASKING, by active listing — not what '
                     'closes (that is the housing_prices / sales layers, from '
                     'recorded deeds). Grey areas are parks, industrial land '
                     'or neighborhoods StreetEasy does not track.'),
            'source': 'StreetEasy Data Dashboard (sales: asking price, '
                      'inventory, days on market, price cuts)',
        }
        fc['attribution'] = ATTRIBUTION
        return fc
    return S.cached('market-forsale-nta-v1', S.DAY, fetch)


def find_area(name: str) -> Optional[str]:
    """Resolve a user's neighborhood name against StreetEasy's area list."""
    want = str(name or '').strip().lower()
    if not want:
        return None
    areas = _streeteasy('asking_rent')['areas']
    for n in areas:
        if n.lower() == want:
            return n
    hits = [n for n in areas if want in n.lower()]
    return min(hits, key=len) if hits else None


# ─────────────────────────────────────────────────────────────────────────────
# Zillow
# ─────────────────────────────────────────────────────────────────────────────

def zillow_metro() -> Dict[str, Any]:
    """ZHVI + ZORI for the New York metro: level, YoY, and a yearly series."""
    def fetch():
        out = {}
        for key, url in ZILLOW.items():
            r = requests.get(url, timeout=120, headers={'User-Agent': S.USER_AGENT})
            r.raise_for_status()
            reader = csv.reader(io.StringIO(r.text))
            header = next(reader)
            first_month = next(i for i, h in enumerate(header)
                               if len(h) == 10 and h[4] == '-')
            months = header[first_month:]
            row = next(x for x in reader
                       if x[header.index('RegionName')] == 'New York, NY')
            vals = [(float(v) if v else None) for v in row[first_month:]]
            m, v, ago = _latest(months, vals)
            yearly = {}
            for mm, vv in zip(months, vals):
                if vv is not None:
                    yearly[mm[:4]] = vv       # December (or latest) per year
            out[key] = {'month': m, 'value': round(v) if v else None,
                        'yoy_pct': _yoy(v, ago),
                        'yearly': [{'year': y, 'value': round(val)}
                                   for y, val in sorted(yearly.items())][-12:]}
        out['region'] = 'New York, NY metro'
        return out
    return S.cached('market-zillow-ny', WEEK, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# the picture
# ─────────────────────────────────────────────────────────────────────────────

BOROUGHS = ['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island']


def market(area: Optional[str] = None) -> Dict[str, Any]:
    """
    The listing market right now: asking rent, asking price and rental
    inventory for the city and each borough (or one neighborhood), with
    year-over-year change, the biggest neighborhood rent moves, and the
    Zillow metro indices for context.
    """
    if area:
        resolved = find_area(area)
        if not resolved:
            return {'error': f'unknown area {area!r} — StreetEasy tracks NYC, '
                             'the five boroughs and ~176 neighborhoods'}
        out = {'area': resolved,
               'borough': _streeteasy('asking_rent')['areas']
                          .get(resolved, {}).get('borough'),
               'asking_rent': _snap('asking_rent', resolved),
               'asking_price': _snap('asking_price', resolved),
               'rental_inventory': _snap('inventory', resolved),
               'series': {'asking_rent': _series('asking_rent', resolved),
                          'asking_price': _series('asking_price', resolved)},
               'attribution': ATTRIBUTION}
        return out

    def fetch():
        places = {}
        for name in ['NYC'] + BOROUGHS:
            places[name] = {'asking_rent': _snap('asking_rent', name),
                            'asking_price': _snap('asking_price', name),
                            'rental_inventory': _snap('inventory', name)}

        # Neighborhood movers: asking rent YoY. A thin market whipsaws its
        # median (City Island: a handful of listings, "+100%"), so only
        # neighborhoods with 20+ active rental listings make the list.
        rent = _streeteasy('asking_rent')
        inv = _streeteasy('inventory')
        moves = []
        for name, a in rent['areas'].items():
            if a['type'] != 'neighborhood':
                continue
            m, v, ago = _latest(rent['months'], a['values'])
            change = _yoy(v, ago)
            if change is None or v is None or v < 500:
                continue
            _, listings, _ = _latest(inv['months'],
                                     inv['areas'].get(name, {}).get('values', []))
            if (listings or 0) < 20:
                continue
            moves.append({'area': name, 'borough': a['borough'],
                          'asking_rent': v, 'listings': int(listings),
                          'yoy_pct': change})
        moves.sort(key=lambda x: -x['yoy_pct'])

        try:
            zil = zillow_metro()
        except Exception as e:          # Zillow is context, not the spine
            zil = {'error': f'{type(e).__name__}: {e}'}

        # Join the two series by month — each drops its own null months, so
        # zipping them positionally would pair a rent with the wrong month.
        prices = {x['month']: x['value'] for x in _series('asking_price', 'NYC', 96)}
        city_series = [{'month': x['month'], 'asking_rent': x['value'],
                        'asking_price': prices.get(x['month'])}
                       for x in _series('asking_rent', 'NYC', 96)]

        return {
            'as_of': places['NYC']['asking_rent'].get('month'),
            'city': places['NYC'],
            'boroughs': {b: places[b] for b in BOROUGHS},
            'rent_rising_fastest': moves[:10],
            'rent_falling_fastest': sorted(moves[-10:], key=lambda x: x['yoy_pct']),
            'zillow_ny_metro': zil,
            'city_series': city_series,
            'notes': [
                'Asking figures are what is listed, not what closes; for '
                'recorded sale prices use the deeds data (nyc_prices).',
                'StreetEasy medians are of active listings in the month, so '
                'inventory mix shifts move them.',
            ],
            'attribution': ATTRIBUTION,
        }
    return S.cached('market-summary-v2', S.DAY, fetch)
