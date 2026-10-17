"""Reality: the plan measured against the city, through the nyc module.

housing.py says what Mamdani promised. This file says what the city looks
like right now, pillar by pillar, using nyc's open-data engine as the one
source of live numbers (see nycbridge.py for how it is reached and how it
degrades). Nothing here fetches on its own; every function is a pure
reshaping of nyc answers plus the plan's targets, so it can be tested
against the baked snapshot offline.

Reads used, and what each one is for:

    stats?geography=borough   ACS/DCP/DOF per borough + the city row: rents,
                              burden, vacancy, new/pipeline/permitted units
    stats?geography=nta       the same per neighborhood (who builds nothing)
    affordable                HPD affordable production since 2014, by band
    rents                     Local Law 44 rent roll: what affordable rents
    nycha                     developments, apartments, residents, rent
    violations                open HPD violations, class C = immediately hazardous
    lotteries                 Housing Connect lotteries open right now
    homes                     affordable listings under a rent cap
"""

import housing
import nycbridge as nb

BOROUGHS = ['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island']

# HPD 2026 Area Median Income, 100% AMI by household size (nyc.gov/hpd).
# 5+ persons follow HUD's family-size multipliers off the 4-person figure.
AMI_2026 = {1: 118_800, 2: 135_700, 3: 152_700, 4: 169_600}
AMI_MULT = {5: 1.08, 6: 1.16, 7: 1.24, 8: 1.32}
AMI_SOURCE = {'title': 'NYC HPD — Area Median Income (2026)',
              'url': 'https://www.nyc.gov/site/hpd/services-and-information/area-median-income.page'}

# Income bands as HPD names them; `key` matches nyc's affordable/rents data,
# `words` is how Housing Connect lotteries label them.
BANDS = [
    {'key': 'extremely_low', 'label': 'Extremely low income', 'lo': 0, 'hi': 30, 'words': 'extremely low'},
    {'key': 'very_low', 'label': 'Very low income', 'lo': 31, 'hi': 50, 'words': 'very low'},
    {'key': 'low', 'label': 'Low income', 'lo': 51, 'hi': 80, 'words': 'low income'},
    {'key': 'moderate', 'label': 'Moderate income', 'lo': 81, 'hi': 120, 'words': 'moderate'},
    {'key': 'middle', 'label': 'Middle income', 'lo': 121, 'hi': 165, 'words': 'middle'},
]

PLAN_YEARS = 10
PLAN_NEW = 200_000
PLAN_PRESERVE = 200_000
PLAN_START = 2026


def _r(fn, args=None):
    return nb.fetch(fn, args or {})


def _city(stats):
    return (stats or {}).get('city') or {}


def _src(*reads):
    """Collapse the provenance of several reads into one honest label."""
    srcs = [r['source'] for r in reads]
    if all(s in ('live', 'cache') for s in srcs):
        return 'live'
    if any(s == 'none' for s in srcs):
        return 'partial'
    return 'snapshot' if all(s == 'snapshot' for s in srcs) else 'stale'


def _meta(*reads):
    return {'source': _src(*reads),
            'fetched_at': max((r.get('fetched_at') or '' for r in reads), default=None) or None,
            'errors': [r['error'] for r in reads if r.get('error')][:3]}


# ── the crisis, live ─────────────────────────────────────────────

def live_crisis():
    """The crisis tiles with today's numbers where nyc has them.

    The HVS vacancy rate and the shelter census stay static (nyc has no
    feed for either); rent burden, median rent, income and vacancy by ACS
    come from nyc's stats row for the whole city."""
    st = _r('stats', {'geography': 'borough'})
    c = _city(st['data'])
    tiles = []
    if c:
        tiles += [
            {'id': 'rent_burden', 'label': 'Renters paying 30%+ of income',
             'value': c.get('rent_burden_pct'), 'unit': '%', 'bad': True,
             'note': f"{c.get('severe_burden_pct')}% pay half or more (ACS via nyc)"},
            {'id': 'median_rent', 'label': 'Median gross rent, city-wide',
             'value': c.get('median_rent'), 'unit': '$/mo', 'bad': True,
             'note': f"Median household income ${int(c.get('median_income') or 0):,} (ACS via nyc)"},
            {'id': 'renters', 'label': 'Renter households',
             'value': c.get('renter_households'), 'unit': 'homes', 'bad': False,
             'note': f"{c.get('renter_pct')}% of the city rents (ACS via nyc)"},
            {'id': 'pipeline', 'label': 'Homes in the pipeline',
             'value': c.get('pipeline_units'), 'unit': 'homes', 'bad': False,
             'note': f"{int(c.get('permitted_units') or 0):,} already permitted (DCP via nyc)"},
        ]
    static = {s['id']: s for s in housing.CRISIS['stats']}
    for sid in ('vacancy', 'shelter', 'stabilized'):
        s = static[sid]
        tiles.append({'id': sid, 'label': s['label'], 'value': s['value'],
                      'unit': s['unit'], 'note': s['note'] + f" ({s['source']})",
                      'bad': sid != 'stabilized', 'static': True})
    return {'tiles': tiles, **_meta(st)}


# ── the scorecard: promise → metric → now ────────────────────────

def scorecard():
    """Every pillar, with the number that will tell us whether it worked."""
    st = _r('stats', {'geography': 'borough'})
    af = _r('affordable')
    ny = _r('nycha')
    vi = _r('violations', {'limit': 5})
    lo = _r('lotteries', {'limit': 50})
    hm = _r('homes', {'max_rent': 1500, 'limit': 50})
    c = _city(st['data'])
    areas = (st['data'] or {}).get('areas') or {}
    a, n, v, l, h = af['data'] or {}, ny['data'] or {}, vi['data'] or {}, lo['data'] or {}, hm['data'] or {}

    renters = c.get('renter_households') or 0
    class_c = next((x['open'] for x in v.get('open_by_class', []) if x.get('class') == 'C'), None)
    per_1k = round(class_c / renters * 1000, 1) if class_c and renters else None
    since_2020_years = max(1, 2026 - 2020)

    rows = [
        {'id': 'freeze', 'promise': 'Freeze the rent on ~1,000,000 stabilized homes',
         'status': 'delivered',
         'metric': 'Renters paying 30%+ of income (should fall as wages catch up)',
         'now': c.get('rent_burden_pct'), 'unit': '%',
         'detail': {'median_rent': c.get('median_rent'), 'median_income': c.get('median_income'),
                    'severe_burden_pct': c.get('severe_burden_pct'),
                    'by_borough': [{'borough': b, 'rent_burden_pct': areas.get(b, {}).get('rent_burden_pct'),
                                    'median_rent': areas.get(b, {}).get('median_rent')} for b in BOROUGHS]},
         'source': 'ACS via nyc'},
        {'id': 'build', 'promise': f'{PLAN_NEW:,} new affordable homes in {PLAN_YEARS} years',
         'status': 'in_progress',
         'metric': 'All-housing completions per year since 2020 vs. the 20,000 affordable homes a year the plan needs',
         'target_per_year': PLAN_NEW // PLAN_YEARS,
         'now': round((c.get('new_units_since_2020') or 0) / since_2020_years), 'unit': 'homes / yr',
         'detail': {'new_units_since_2020': c.get('new_units_since_2020'),
                    'pipeline_units': c.get('pipeline_units'), 'permitted_units': c.get('permitted_units'),
                    'hpd_affordable_since_2014': a.get('units_total'),
                    'hpd_by_income_band': a.get('by_income_band'),
                    'by_borough': [{'borough': b, 'new_units_per_1k': areas.get(b, {}).get('new_units_per_1k'),
                                    'pipeline_per_1k': areas.get(b, {}).get('pipeline_per_1k')} for b in BOROUGHS]},
         'source': 'DCP Housing Database + HPD production via nyc'},
        {'id': 'preserve', 'promise': f'{PLAN_PRESERVE:,} existing affordable homes kept in the stock',
         'status': 'in_progress',
         'metric': 'Immediately-hazardous (class C) violations per 1,000 renter households — the decay gauge',
         'now': per_1k, 'unit': 'per 1k',
         'detail': {'open_class_c': class_c, 'open_total': v.get('open_total'),
                    'by_borough': v.get('by_borough'), 'worst_buildings': (v.get('worst_buildings') or [])[:5]},
         'source': 'HPD violations via nyc'},
        {'id': 'nycha', 'promise': 'Rebuild NYCHA: repairs, upgrades, NYCHA as a developer',
         'status': 'in_progress',
         'metric': 'Public-housing apartments and the people in them',
         'now': n.get('apartments'), 'unit': 'apartments',
         'detail': {'developments': n.get('developments'), 'population': n.get('population'),
                    'avg_monthly_rent': n.get('avg_monthly_rent'), 'by_borough': n.get('by_borough')},
         'source': 'NYCHA Development Data Book via nyc'},
        {'id': 'zoning', 'promise': 'Rezone so the wealthy neighborhoods that built nothing build',
         'status': 'in_progress',
         'metric': 'New homes per 1,000 residents since 2020, city-wide',
         'now': c.get('new_units_per_1k'), 'unit': 'per 1k',
         'detail': {'pipeline_per_1k': c.get('pipeline_per_1k'), 'units_growth_pct': c.get('units_growth_pct'),
                    'see': 'neighborhoods'},
         'source': 'DCP via nyc'},
        {'id': 'tenants', 'promise': 'Tenants who can find, apply for and keep an affordable home',
         'status': 'in_progress',
         'metric': 'Lotteries open for applications right now',
         'now': l.get('count'), 'unit': 'lotteries',
         'detail': {'lottery_units': l.get('total_units'), 'next_deadline': l.get('next_deadline'),
                    'homes_under_1500': h.get('matched'), 'apply_at': l.get('apply_at')},
         'source': 'Housing Connect + Local Law 44 via nyc'},
    ]
    return {'as_of': housing.CRISIS['as_of'], 'plan': housing.PLAN['name'],
            'rows': rows, **_meta(st, af, ny, vi, lo, hm)}


# ── neighborhoods: who builds nothing ────────────────────────────

def neighborhoods(min_income=120_000, min_population=15_000, limit=12):
    """Wealthy neighborhoods ranked by how little they have built since 2020 —
    the zoning pillar's target list, from nyc's neighborhood stats."""
    st = _r('stats', {'geography': 'nta', 'limit': 400, 'sort': 'median_income'})
    rows = (st['data'] or {}).get('areas') or []
    if isinstance(rows, dict):
        rows = [dict(v, key=k) for k, v in rows.items()]
    rich = [r for r in rows if (r.get('population') or 0) >= min_population
            and (r.get('median_income') or 0) >= min_income
            and r.get('new_units_per_1k') is not None]
    rich.sort(key=lambda r: (r['new_units_per_1k'], -(r.get('median_income') or 0)))
    city = _city(st['data'])
    return {'city_new_units_per_1k': city.get('new_units_per_1k'),
            'threshold': {'min_income': min_income, 'min_population': min_population},
            'count_wealthy': len(rich),
            'least_building': [{'name': r.get('name'), 'borough': r.get('borough'),
                                'median_income': r.get('median_income'),
                                'new_units_per_1k': r.get('new_units_per_1k'),
                                'pipeline_per_1k': r.get('pipeline_per_1k'),
                                'median_rent': r.get('median_rent'),
                                'population': r.get('population')} for r in rich[:limit]],
            **_meta(st)}


# ── a borough's share of the plan, grounded in its numbers ───────

def borough(name, **sim):
    """One borough: its live housing profile, and the simulator re-seeded
    with its share of the plan (by renter households) and its median rent."""
    canon = next((b for b in BOROUGHS if b.lower() == str(name).strip().lower()), None)
    if not canon:
        return {'error': f'unknown borough {name!r}', 'boroughs': BOROUGHS}
    st = _r('stats', {'geography': 'borough'})
    ny = _r('nycha', {'borough': canon})
    lo = _r('lotteries', {'borough': canon, 'limit': 50})
    d = (st['data'] or {}).get('areas', {}).get(canon) or {}
    c = _city(st['data'])
    share = (d.get('renter_households') or 0) / (c.get('renter_households') or 1)
    share = round(share, 4) if share else 0.2
    rent = d.get('median_rent') or 1500
    args = {
        'new_per_year': int(sim.get('new_per_year', PLAN_NEW // PLAN_YEARS * share)),
        'preserved_per_year': int(sim.get('preserved_per_year', PLAN_PRESERVE // PLAN_YEARS * share)),
        'freeze_years': int(sim.get('freeze_years', 2)),
        'rgb_hike': float(sim.get('rgb_hike', 3.0)),
        'attrition_per_year': int(sim.get('attrition_per_year', 10_000 * share)),
        'median_stabilized_rent': float(sim.get('median_stabilized_rent', rent)),
        'years': int(sim.get('years', 10)),
        'base_pace': int(sim.get('base_pace', 7_000 * share)),
        'stabilized': int(sim.get('stabilized', 1_000_000 * share)),
    }
    return {'borough': canon, 'share_of_renters': share,
            'profile': {k: d.get(k) for k in ('population', 'households', 'renter_households',
                                              'renter_pct', 'median_rent', 'median_income',
                                              'rent_burden_pct', 'severe_burden_pct', 'vacancy_pct',
                                              'new_units_since_2020', 'pipeline_units',
                                              'permitted_units', 'new_units_per_1k',
                                              'pipeline_per_1k', 'median_sale_price', 'poverty_pct')},
            'nycha': {k: (ny['data'] or {}).get(k) for k in ('developments', 'apartments',
                                                               'population', 'avg_monthly_rent')},
            'lotteries_open': (lo['data'] or {}).get('count'),
            'lottery_units': (lo['data'] or {}).get('total_units'),
            'simulation': housing.simulate(**args),
            'allocation': 'plan targets × this borough\'s share of renter households; '
                          'rent = ACS median gross rent here',
            **_meta(st, ny, lo)}


# ── the dream, for one household: find a home ────────────────────

def ami_for(household):
    h = max(1, min(int(household), 8))
    if h in AMI_2026:
        return AMI_2026[h]
    return round(AMI_2026[4] * AMI_MULT[h])


def band_for(pct):
    for b in BANDS:
        if pct <= b['hi']:
            return b
    return None


def find(income, household=1, bedrooms='', borough='', limit=12):
    """What the plan means for one household: the AMI band they qualify for,
    the rent they can afford at 30% of income, the lotteries open to that
    band right now, and affordable listings under their number."""
    try:
        income = max(0, float(income))
    except (TypeError, ValueError):
        return {'error': 'income must be a number'}
    household = max(1, min(int(household or 1), 8))
    ami = ami_for(household)
    pct = round(income / ami * 100, 1) if ami else None
    band = band_for(pct) if pct is not None else None
    max_rent = int(income * 0.30 / 12)

    lo = _r('lotteries', {'borough': borough, 'limit': 50})
    hm = _r('homes', {'max_rent': max_rent, 'bedrooms': bedrooms, 'borough': borough, 'limit': 200})

    lots = []
    for L in (lo['data'] or {}).get('lotteries', []):
        bands = ' '.join(L.get('income_bands') or []).lower()
        if not band or not bands or band['words'] in bands:
            lots.append({k: L.get(k) for k in ('lottery_id', 'name', 'borough', 'units',
                                                'unit_mix', 'income_bands', 'deadline', 'type')})
    # One row per development: the rent roll lists every bedroom/band
    # offering at every address, which repeated one project ten times.
    # Cheapest-first order means the first row kept is the cheapest.
    homes, seen = [], set()
    for x in (hm['data'] or {}).get('listings', []):
        k = (x.get('name') or '').strip().lower()
        if k in seen:
            continue
        seen.add(k)
        homes.append(x)
    return {
        'household': household, 'income': income, 'ami_100pct': ami, 'pct_ami': pct,
        'band': {'key': band['key'], 'label': band['label'], 'range': f"{band['lo']}–{band['hi']}% AMI"} if band else
                {'key': 'above', 'label': 'Above 165% AMI', 'range': '>165% AMI'},
        'affordable_rent': max_rent,
        'rule': '30% of income — the federal affordability line the plan is built around',
        'lotteries': {'open_for_your_band': len(lots), 'open_total': (lo['data'] or {}).get('count'),
                      'next_deadline': (lo['data'] or {}).get('next_deadline'),
                      'apply_at': (lo['data'] or {}).get('apply_at', 'https://housingconnect.nyc.gov'),
                      'items': lots[:limit]},
        'homes': {'matched': (hm['data'] or {}).get('matched'),
                  'buildings': len(homes),
                  'items': [{k: x.get(k) for k in ('name', 'address', 'borough', 'zip', 'bedrooms',
                                                   'units', 'rent', 'band', 'ami_range')}
                            for x in homes[:limit]],
                  'source': (hm['data'] or {}).get('source')},
        'ami_source': AMI_SOURCE,
        **_meta(lo, hm),
    }


def nyc_status():
    return nb.status()
