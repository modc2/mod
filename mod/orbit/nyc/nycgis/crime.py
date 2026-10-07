"""
nyc.crime — public safety from the NYPD's own open-data files.

Three sources, all key-free Socrata datasets:

  * Complaints, current year   5uac-w243   (refreshed quarterly)
  * Complaints, historic       qgea-i56i   (through the last full year)
  * Shootings, 2006–present    5ucz-vwe8

Traps this module exists to hold, each of which silently skews a number:

  * The current-year file carries typo dates (``1016-04-30``) — every query
    is bounded to the year it is supposed to cover.
  * "Year to date" means *through the last published quarter*, not today.
    ``data_through()`` reads the real coverage end out of the file, and every
    prior-year comparison uses the same Jan-1-to-that-date window — comparing
    a half year against a full one reads as a 50% crime drop.
  * Aggregation happens server-side with ``$group`` (the complaint file is
    ~9M rows); nothing here downloads a row set.
  * The city publishes no census population per precinct, so precinct
    figures stay raw counts with a note saying so; per-1,000 rates are
    computed only at borough level, where census population is solid.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import sources as S

YTD = '5uac-w243'          # complaints, current year (quarterly)
HISTORIC = 'qgea-i56i'     # complaints, 2006 → last full year
SHOOTINGS = '5ucz-vwe8'    # shooting incidents, 2006 → current quarter
PRECINCTS_GEO = 'y76i-bdw7'

LEVELS = {'FELONY': 'felony', 'MISDEMEANOR': 'misdemeanor', 'VIOLATION': 'violation'}

# 2020 census population by borough, for borough-level rates.
BORO_POP = {'MANHATTAN': 1_694_251, 'BROOKLYN': 2_736_074, 'QUEENS': 2_405_464,
            'BRONX': 1_472_654, 'STATEN ISLAND': 495_747}


def _precinct_borough(p: int) -> str:
    if p < 35:
        return 'Manhattan'
    if p < 53:
        return 'Bronx'
    if p < 95:
        return 'Brooklyn'
    if p < 116:
        return 'Queens'
    return 'Staten Island'


def _iso(d: date) -> str:
    return d.isoformat()


def data_through() -> str:
    """
    The real coverage end of the current-year complaint file (YYYY-MM-DD).

    The file is refreshed quarterly, so "this year" usually ends a few months
    ago — every comparison window must be cut there, on both sides.
    """
    def fetch():
        year = date.today().year
        rows = S.soql(S.NYC, YTD, select='max(cmplnt_fr_dt) as d',
                      where=(f'cmplnt_fr_dt >= "{year}-01-01T00:00:00.000" and '
                             f'cmplnt_fr_dt <= "{year}-12-31T23:59:59.000"'))
        d = str((rows or [{}])[0].get('d') or '')[:10]
        if not d:
            raise ValueError('current-year complaint file looks empty')
        return {'through': d, 'year': year}
    return S.cached('crime-through', S.DAY, fetch)['through']


def _windows() -> Dict[str, str]:
    """This year's covered window and the same window one year earlier."""
    through = data_through()
    year = int(through[:4])
    return {
        'since': f'{year}-01-01', 'until': through,
        'prior_since': f'{year - 1}-01-01',
        'prior_until': f'{year - 1}{through[4:]}',
        'year': str(year),
    }


def _date_clause(since: str, until: str) -> str:
    return (f'cmplnt_fr_dt >= "{since}T00:00:00.000" and '
            f'cmplnt_fr_dt <= "{until}T23:59:59.000"')


def _grouped(dataset: str, since: str, until: str, by: str,
             extra_where: str = '') -> List[dict]:
    where = _date_clause(since, until)
    if extra_where:
        where += f' and {extra_where}'
    return S.soql(S.NYC, dataset,
                  select=f'{by},law_cat_cd,count(1) as n',
                  where=where, group=f'{by},law_cat_cd', limit=5000)


def _tally(rows: List[dict], by: str) -> Dict[str, Dict[str, int]]:
    """{key: {felony, misdemeanor, violation, total}} out of grouped rows."""
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        key = str(r.get(by) or '').strip()
        if not key:
            continue
        level = LEVELS.get(str(r.get('law_cat_cd') or '').strip().upper())
        n = int(float(r.get('n') or 0))
        slot = out.setdefault(key, {'felony': 0, 'misdemeanor': 0,
                                    'violation': 0, 'total': 0})
        if level:
            slot[level] += n
        slot['total'] += n
    return out


def _change(now: Optional[int], before: Optional[int]) -> Optional[float]:
    if not before:
        return None
    return round(100.0 * ((now or 0) - before) / before, 1)


# ─────────────────────────────────────────────────────────────────────────────
# precinct choropleth
# ─────────────────────────────────────────────────────────────────────────────

def precinct_geo() -> dict:
    return S.cached('geo-precincts', 30 * S.DAY, lambda: S.simplify_geojson(
        S.socrata_geojson(PRECINCTS_GEO), tol=0.00012, keep=['precinct']))


def by_precinct() -> dict:
    """
    Complaints this year per precinct as a choropleth FeatureCollection:
    felony / misdemeanor / violation / total, shootings, and the change
    against the same window last year. ``breaks`` carries quantile classes
    for the total, so the frontend can draw it with no extra request.
    """
    def fetch():
        w = _windows()
        now = _tally(_grouped(YTD, w['since'], w['until'], 'addr_pct_cd'),
                     'addr_pct_cd')
        prior = _tally(_grouped(HISTORIC, w['prior_since'], w['prior_until'],
                                'addr_pct_cd'), 'addr_pct_cd')
        shots = {str(r.get('precinct') or '').strip(): int(float(r.get('n') or 0))
                 for r in S.soql(S.NYC, SHOOTINGS,
                                 select='precinct,count(1) as n',
                                 where=(f'occur_date >= "{w["since"]}T00:00:00.000" and '
                                        f'occur_date <= "{w["until"]}T23:59:59.000"'),
                                 group='precinct', limit=500)}

        fc = {'type': 'FeatureCollection', 'features': []}
        for f in precinct_geo().get('features', []):
            pct = str(f['properties'].get('precinct') or '').strip()
            # Socrata writes precinct as "1"/"014" depending on the file.
            key = pct.lstrip('0') or pct
            t = now.get(key, {'felony': 0, 'misdemeanor': 0, 'violation': 0, 'total': 0})
            p = prior.get(key, {})
            props = {
                'precinct': int(key) if key.isdigit() else key,
                'name': f'Precinct {key}',
                'borough': _precinct_borough(int(key)) if key.isdigit() else '',
                **t,
                'prior_total': p.get('total'),
                'change_pct': _change(t['total'], p.get('total')),
                'shootings': shots.get(key, 0),
            }
            fc['features'].append({'type': 'Feature', 'properties': props,
                                   'geometry': f['geometry']})

        from . import demographics as D
        vals = [f['properties']['total'] for f in fc['features']
                if f['properties']['total']]
        fc['breaks'] = D.quantile_breaks(vals)
        fc['meta'] = {
            'metric': 'total', 'label': f'Complaints, {w["year"]} through {w["until"]}',
            'format': 'int',
            'window': {'since': w['since'], 'until': w['until']},
            'prior_window': {'since': w['prior_since'], 'until': w['prior_until']},
            'note': ('Raw complaint counts per precinct; precincts differ widely '
                     'in residential and daytime population. NYPD refreshes the '
                     'current-year file quarterly.'),
            'source': 'NYPD Complaint Data Current (5uac-w243) + Historic (qgea-i56i)',
        }
        return fc
    return S.cached('crime-precincts-v1', S.DAY, fetch)


def shooting_points(years: int = 3) -> dict:
    """
    Individual shooting incidents for the map, recent years only.

    The source file's ``latitude``/``longitude`` columns are SWAPPED on all
    but a few hundred rows (latitude = -73.9…), so each row is checked and
    un-swapped individually — trusting either orientation wholesale draws
    most of the city in the Southern Ocean. It also carries no murder flag
    (the historic file does; this one doesn't), so points are incidents only.
    """
    def fetch():
        since = _iso(date.today() - timedelta(days=365 * years))
        rows = S.soql_all(
            S.NYC, SHOOTINGS, max_rows=20000,
            select='occur_date,occur_time,boro,precinct,latitude,longitude',
            where=(f'occur_date >= "{since}T00:00:00.000" and latitude IS NOT NULL'),
            order='occur_date DESC')
        for r in rows:
            r['date'] = str(r.pop('occur_date', ''))[:10]
            # occur_time is "19:36:00" on most rows but a full 1899-epoch
            # datetime on some; keep whatever follows the date part.
            r['time'] = str(r.pop('occur_time', '')).split('T')[-1][:5]
            r['borough'] = (r.pop('boro', '') or '').title()
            try:
                a, b = float(r.get('latitude')), float(r.get('longitude'))
            except (TypeError, ValueError):
                continue
            if a < 0 < b:                      # the swapped majority
                r['latitude'], r['longitude'] = b, a
        fc = S.points_from_rows(rows, 'latitude', 'longitude',
                                props=['date', 'time', 'borough', 'precinct'])
        fc['meta'] = {'since': since, 'incidents': len(fc['features']),
                      'source': 'NYPD Shooting Incident Data (5ucz-vwe8)'}
        return fc
    return S.cached(f'crime-shootings-{years}y-v2', S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# trends and tables
# ─────────────────────────────────────────────────────────────────────────────

def monthly_trend(months: int = 36) -> List[dict]:
    """
    Complaints per month by severity, stitched across the historic and
    current-year files (they never overlap: historic ends Dec 31, the
    current file holds only this year).
    """
    def fetch():
        w = _windows()
        start_year = int(w['year']) - max(1, (months + 11) // 12)
        since = f'{start_year}-01-01'
        out: Dict[str, Dict[str, int]] = {}
        for dataset, until in ((HISTORIC, f'{int(w["year"]) - 1}-12-31'),
                               (YTD, w['until'])):
            rows = S.soql(S.NYC, dataset,
                          select=('date_trunc_ym(cmplnt_fr_dt) as m,'
                                  'law_cat_cd,count(1) as n'),
                          where=_date_clause(since, until),
                          group='date_trunc_ym(cmplnt_fr_dt),law_cat_cd',
                          limit=5000)
            for r in rows:
                mth = str(r.get('m') or '')[:7]
                if not mth:
                    continue
                level = LEVELS.get(str(r.get('law_cat_cd') or '').upper())
                n = int(float(r.get('n') or 0))
                slot = out.setdefault(mth, {'felony': 0, 'misdemeanor': 0,
                                            'violation': 0, 'total': 0})
                if level:
                    slot[level] += n
                slot['total'] += n
        series = [{'month': m, **v} for m, v in sorted(out.items())]
        return series[-months:]
    return S.cached(f'crime-trend-{months}', S.DAY, fetch)


def by_borough() -> List[dict]:
    """This year's complaints per borough, with rates and last-year change."""
    def fetch():
        w = _windows()
        now = _tally(_grouped(YTD, w['since'], w['until'], 'boro_nm'), 'boro_nm')
        prior = _tally(_grouped(HISTORIC, w['prior_since'], w['prior_until'],
                                'boro_nm'), 'boro_nm')
        out = []
        for boro, pop in BORO_POP.items():
            t = now.get(boro, {'felony': 0, 'misdemeanor': 0, 'violation': 0, 'total': 0})
            p = prior.get(boro, {})
            out.append({
                'borough': boro.title(), 'population': pop, **t,
                'per_1k': round(1000.0 * t['total'] / pop, 1),
                'felony_per_1k': round(1000.0 * t['felony'] / pop, 1),
                'prior_total': p.get('total'),
                'change_pct': _change(t['total'], p.get('total')),
            })
        out.sort(key=lambda r: -r['total'])
        return out
    return S.cached('crime-boroughs-v1', S.DAY, fetch)


def top_offenses(limit: int = 15) -> List[dict]:
    """The most-reported offense types this year, with last-year change."""
    def fetch():
        w = _windows()

        def counts(dataset, since, until):
            rows = S.soql(S.NYC, dataset,
                          select='ofns_desc,law_cat_cd,count(1) as n',
                          where=_date_clause(since, until),
                          group='ofns_desc,law_cat_cd',
                          order='count(1) DESC', limit=400)
            out: Dict[str, dict] = {}
            for r in rows:
                name = str(r.get('ofns_desc') or '').strip()
                if not name or name == '(null)':
                    continue
                o = out.setdefault(name, {'n': 0, 'level': ''})
                o['n'] += int(float(r.get('n') or 0))
                o['level'] = o['level'] or str(r.get('law_cat_cd') or '').title()
            return out

        now = counts(YTD, w['since'], w['until'])
        prior = counts(HISTORIC, w['prior_since'], w['prior_until'])
        rows = [{'offense': k.title(), 'level': v['level'], 'count': v['n'],
                 'prior': prior.get(k, {}).get('n'),
                 'change_pct': _change(v['n'], prior.get(k, {}).get('n'))}
                for k, v in now.items()]
        rows.sort(key=lambda r: -r['count'])
        return rows

    # Cache the full ranked list once; `limit` only slices the cached copy.
    return S.cached('crime-offenses-v1', S.DAY, fetch)[:max(1, int(limit))]


def _shootings_pair() -> Dict[str, Any]:
    w = _windows()

    def count(since, until):
        # This vintage of the file carries no murder flag, so incidents only.
        rows = S.soql(S.NYC, SHOOTINGS, select='count(1) as n',
                      where=(f'occur_date >= "{since}T00:00:00.000" and '
                             f'occur_date <= "{until}T23:59:59.000"'))
        return {'incidents': int(float((rows or [{}])[0].get('n') or 0))}

    now, prior = count(w['since'], w['until']), count(w['prior_since'], w['prior_until'])
    return {'this_year': now, 'last_year_same_window': prior,
            'change_pct': _change(now['incidents'], prior['incidents'])}


def summary() -> Dict[str, Any]:
    """The whole safety picture in one call — for the tool, report and app."""
    def fetch():
        w = _windows()
        boros = by_borough()
        total = sum(b['total'] for b in boros)
        prior = sum(b['prior_total'] or 0 for b in boros)
        felony = sum(b['felony'] for b in boros)
        trend = monthly_trend(36)
        return {
            'window': {'since': w['since'], 'until': w['until'],
                       'compared_to': {'since': w['prior_since'],
                                       'until': w['prior_until']}},
            'complaints': {
                'total': total, 'felony': felony,
                'misdemeanor': sum(b['misdemeanor'] for b in boros),
                'violation': sum(b['violation'] for b in boros),
                'prior_total': prior,
                'change_pct': _change(total, prior),
                'per_1k_residents': round(1000.0 * total / sum(BORO_POP.values()), 1),
            },
            'shootings': _shootings_pair(),
            'by_borough': boros,
            'top_offenses': top_offenses(15),
            'monthly_trend': trend,
            'notes': [
                'Complaints are crimes reported to the NYPD, not convictions; '
                'reporting rates differ by offense and neighborhood.',
                'The current-year file is refreshed quarterly, so "this year" '
                f'runs through {w["until"]}. Every last-year comparison uses '
                'the same January-to-that-date window.',
                'Precinct counts are raw totals; precincts differ widely in '
                'population, so compare a precinct with itself over time.',
            ],
            'sources': [
                {'name': 'NYPD Complaint Data Current (Year To Date)',
                 'url': f'https://data.cityofnewyork.us/d/{YTD}'},
                {'name': 'NYPD Complaint Data Historic',
                 'url': f'https://data.cityofnewyork.us/d/{HISTORIC}'},
                {'name': 'NYPD Shooting Incident Data',
                 'url': f'https://data.cityofnewyork.us/d/{SHOOTINGS}'},
            ],
        }
    return S.cached('crime-summary-v1', S.DAY, fetch)
