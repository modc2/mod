"""
nyc.citydata — curated answers over the big everyday-city datasets:
311 complaints, crime, vehicle collisions, restaurant inspections, street
trees, evictions, building permits and air quality.

Every function here follows the same discipline as the rest of the module:

  * **Aggregate server-side** with SoQL ``$select``/``$group`` — the 311 file
    alone is tens of millions of rows and the collision file 2.3M; nothing
    here ever downloads a raw row set (the one bounded exception is the
    restaurant search, which fetches ≤1,000 *filtered* inspection rows so it
    can keep only the latest inspection per restaurant).
  * **Cache through ``sources.cached``** — 1 hour for the feeds where recency
    matters (311, collisions), a day or more for the rest; a stale answer
    beats an error.
  * **Cite the source** — every result carries the dataset id and a human
    name, so an answer can link the city's own file.

Dataset-specific traps held here so callers don't relearn them:

  * Borough columns differ per dataset (``borough`` / ``boro`` / ``boro_nm``
    / ``boroname``) and casing differs per file — every filter goes through
    ``upper(col) = 'BROOKLYN'`` and user input through the alias table.
  * Some files carry garbage boroughs ('Unspecified', '0'); grouped rows
    keep them visible (blank keys become ``(blank)``) rather than crashing
    or silently merging them.
  * NYPD complaints live in TWO files that never overlap (historic through
    last Dec 31, a quarterly-refreshed current-year file); ``crime()``
    stitches a date window across both and reports the real coverage end.
  * DOB has two permit systems. **DOB NOW (``rbx6-tga4``) is used here**: it
    is where new permits actually land (166k issued in 2024 vs ~15k in the
    legacy BIS file) and its date columns are real ``calendar_date``s. The
    legacy BIS file (``ipu4-2q9a``) stores dates as MM/DD/YYYY *text*, so it
    cannot even be grouped by year server-side without casts; it remains
    reachable via ``nyc_query`` for pre-2016 history.
  * The 2015 Street Tree Census is the last census with species common
    names, health and borough on every row; the Parks "Forestry Tree Points"
    living inventory (``hn5i-inap``) is current (updated daily) but carries
    only genus/species and a condition rating, with no borough column — so
    the census is the default and the living inventory an explicit option.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import sources as S

HOUR = 3600

D311 = 'erm2-nwe9'          # 311 Service Requests from 2020 to Present
CRIME_YTD = '5uac-w243'     # NYPD Complaint Data Current (Year To Date)
CRIME_HIST = 'qgea-i56i'    # NYPD Complaint Data Historic (2006 → last Dec 31)
COLLISIONS = 'h9gi-nx95'    # Motor Vehicle Collisions - Crashes
RESTAURANTS = '43nn-pn8j'   # DOHMH Restaurant Inspection Results
TREES_2015 = 'uvpi-gqnh'    # 2015 Street Tree Census - Tree Data
TREES_LIVE = 'hn5i-inap'    # Forestry Tree Points (living inventory)
EVICTIONS = '6z8x-wfk4'     # Marshal evictions, 2017 → present
PERMITS = 'rbx6-tga4'       # DOB NOW: Build – Approved Permits
AIR = 'c3uy-2p5r'           # Air Quality and Health Impacts (DOHMH)

NAMES = {
    D311: '311 Service Requests (2020–present)',
    CRIME_YTD: 'NYPD Complaint Data Current (Year To Date)',
    CRIME_HIST: 'NYPD Complaint Data Historic',
    COLLISIONS: 'Motor Vehicle Collisions – Crashes',
    RESTAURANTS: 'DOHMH Restaurant Inspection Results',
    TREES_2015: '2015 Street Tree Census',
    TREES_LIVE: 'NYC Parks Forestry Tree Points (living inventory)',
    EVICTIONS: 'Marshal Evictions',
    PERMITS: 'DOB NOW: Build – Approved Permits',
    AIR: 'DOHMH Air Quality and Health Impacts',
}


def _src(*datasets: str) -> List[dict]:
    return [{'name': NAMES.get(d, d), 'dataset': d,
             'url': f'https://data.cityofnewyork.us/d/{d}'} for d in datasets]


# ─────────────────────────────────────────────────────────────────────────────
# input normalisation — these values go into SoQL strings
# ─────────────────────────────────────────────────────────────────────────────

BOROUGHS = {
    'manhattan': 'MANHATTAN', 'mn': 'MANHATTAN', 'new york': 'MANHATTAN',
    'bronx': 'BRONX', 'the bronx': 'BRONX', 'bx': 'BRONX',
    'brooklyn': 'BROOKLYN', 'bk': 'BROOKLYN', 'kings': 'BROOKLYN',
    'queens': 'QUEENS', 'qn': 'QUEENS', 'qns': 'QUEENS',
    'staten island': 'STATEN ISLAND', 'si': 'STATEN ISLAND',
    'richmond': 'STATEN ISLAND',
}


def borough_norm(name: str) -> Optional[str]:
    """'brooklyn' / 'BK' / 'Kings' → 'BROOKLYN'. None for blank input."""
    key = str(name or '').strip().lower()
    if not key:
        return None
    if key not in BOROUGHS:
        raise ValueError(f'unknown borough {name!r}; try one of: '
                         'Manhattan, Bronx, Brooklyn, Queens, Staten Island')
    return BOROUGHS[key]


def _esc(s: str) -> str:
    """Escape a user string for a single-quoted SoQL literal."""
    return str(s).replace("'", "''")


def _boro_clause(col: str, name: str) -> Optional[str]:
    b = borough_norm(name)
    return f"upper({col}) = '{b}'" if b else None


def _like(col: str, needle: str) -> Optional[str]:
    needle = str(needle or '').strip()
    if not needle:
        return None
    return f"upper({col}) like '%{_esc(needle).upper()}%'"


def _day(d: str, what: str) -> str:
    """Validate a date argument (also the injection guard for date params)."""
    s = str(d).strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError(f'{what} must be a date like 2026-01-01, got {d!r}')


def _dates(col: str, since: str, until: str) -> List[str]:
    out = []
    if since:
        out.append(f"{col} >= '{_day(since, 'since')}T00:00:00'")
    if until:
        out.append(f"{col} <= '{_day(until, 'until')}T23:59:59'")
    return out


def _where(*clauses: Optional[str]) -> Optional[str]:
    parts = [c for c in clauses if c]
    return ' and '.join(parts) or None


def _group_col(group_by: str, groups: Dict[str, str], what: str) -> str:
    key = str(group_by or '').strip().lower()
    if key not in groups:
        raise ValueError(f'unknown {what} group_by {group_by!r}; '
                         f'one of: {", ".join(groups)}')
    return groups[key]


def _n(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _label(v: Any) -> str:
    """Grouped keys come back blank/absent on garbage rows — keep them honest."""
    s = str(v or '').strip()
    return s if s else '(blank)'


def _ckey(prefix: str, *parts: Any) -> str:
    return prefix + '-' + '-'.join(str(p).lower() for p in parts)


# ─────────────────────────────────────────────────────────────────────────────
# 311
# ─────────────────────────────────────────────────────────────────────────────

_311_GROUPS = {'type': 'complaint_type', 'borough': 'borough',
               'zip': 'incident_zip', 'agency': 'agency', 'status': 'status'}


def complaints_311(complaint: str = '', borough: str = '', since: str = '',
                   until: str = '', group_by: str = 'type', limit: int = 25,
                   recent: bool = False) -> dict:
    """
    311 complaint counts over a date window, grouped — or, with
    ``recent=True``, the newest individual matching complaints (≤50).

    The window defaults to the last 30 days; citywide that is already over a
    quarter-million complaints, all counted server-side.
    """
    if not since:
        since = (date.today() - timedelta(days=30)).isoformat()
    where = _where(*_dates('created_date', since, until),
                   _like('complaint_type', complaint),
                   _boro_clause('borough', borough))
    window = {'since': since, 'until': until or 'today'}
    filters = {'complaint': complaint or None, 'borough': borough or None}

    if recent:
        limit = max(1, min(int(limit), 50))

        def fetch_recent():
            rows = S.soql(S.NYC, D311,
                          select=('created_date,complaint_type,descriptor,'
                                  'borough,incident_zip,agency,status'),
                          where=where, order='created_date DESC', limit=limit)
            for r in rows:
                r['created_date'] = str(r.get('created_date') or '')[:16]
            return {'window': window, 'filters': filters, 'mode': 'recent',
                    'returned': len(rows), 'complaints': rows,
                    'sources': _src(D311)}
        return S.cached(_ckey('city311-recent', complaint, borough, since,
                              until, limit), HOUR, fetch_recent)

    col = _group_col(group_by, _311_GROUPS, '311')
    limit = max(1, min(int(limit), 1000))

    def fetch():
        rows = S.soql(S.NYC, D311, select=f'{col}, count(1) as n',
                      where=where, group=col, order='n DESC', limit=limit)
        out = [{group_by: _label(r.get(col)), 'count': _n(r.get('n'))}
               for r in rows]
        return {'window': window, 'filters': filters, 'group_by': group_by,
                'groups': len(out), 'total': sum(r['count'] for r in out),
                'rows': out, 'sources': _src(D311),
                'note': ('Total is the sum over the returned groups only '
                         'if the group count hit the limit.')}
    return S.cached(_ckey('city311', group_by, complaint, borough, since,
                          until, limit), HOUR, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# crime — stitched across the historic + current-year NYPD files
# ─────────────────────────────────────────────────────────────────────────────

_CRIME_GROUPS = {'offense': 'ofns_desc', 'borough': 'boro_nm',
                 'severity': 'law_cat_cd', 'precinct': 'addr_pct_cd'}
_SEVERITIES = {'felony': 'FELONY', 'misdemeanor': 'MISDEMEANOR',
               'violation': 'VIOLATION'}


def crime(offense: str = '', borough: str = '', severity: str = '',
          since: str = '', until: str = '', group_by: str = 'offense',
          limit: int = 25) -> dict:
    """
    NYPD complaint counts over a date window, stitched across the historic
    file (through last Dec 31) and the quarterly current-year file.

    The default window is the current year through the file's real coverage
    end (NOT today — the current-year file is published quarterly, and
    ``nycgis.crime.data_through()`` reads the true end out of the data).
    """
    from . import crime as C
    col = _group_col(group_by, _CRIME_GROUPS, 'crime')
    sev = str(severity or '').strip().lower()
    if sev and sev not in _SEVERITIES:
        raise ValueError(f'severity must be one of {", ".join(_SEVERITIES)}')
    through = C.data_through()
    cur_year = int(through[:4])
    if not since:
        since = f'{cur_year}-01-01'
    if not until:
        until = through
    since, until = _day(since, 'since'), _day(until, 'until')
    if since > until:
        raise ValueError(f'since {since} is after until {until}')
    limit = max(1, min(int(limit), 1000))

    extra = _where(_like('ofns_desc', offense),
                   _boro_clause('boro_nm', borough),
                   f"law_cat_cd = '{_SEVERITIES[sev]}'" if sev else None)

    # Which file covers which slice of the window. They never overlap.
    spans = []
    hist_end = f'{cur_year - 1}-12-31'
    if since <= hist_end:
        spans.append((CRIME_HIST, since, min(until, hist_end)))
    if until >= f'{cur_year}-01-01':
        spans.append((CRIME_YTD, max(since, f'{cur_year}-01-01'),
                      min(until, through)))

    def fetch():
        tallies: Dict[str, int] = {}
        for dataset, lo, hi in spans:
            rows = S.soql(S.NYC, dataset, select=f'{col}, count(1) as n',
                          where=_where(*_dates('cmplnt_fr_dt', lo, hi), extra),
                          group=col, limit=5000)
            for r in rows:
                k = _label(r.get(col))
                tallies[k] = tallies.get(k, 0) + _n(r.get('n'))
        out = sorted(({group_by: k, 'count': n} for k, n in tallies.items()),
                     key=lambda r: -r['count'])[:limit]
        return {
            'window': {'since': since, 'until': until},
            'data_through': through,
            'filters': {'offense': offense or None, 'borough': borough or None,
                        'severity': sev or None},
            'group_by': group_by, 'groups': len(tallies),
            'total': sum(tallies.values()), 'rows': out,
            'sources': _src(*dict.fromkeys(d for d, _, _ in spans)),
            'notes': [
                'Complaints reported to the NYPD, not convictions.',
                f'The current-year file is refreshed quarterly and runs '
                f'through {through}; the window is clipped there.',
            ],
        }
    return S.cached(_ckey('citycrime', group_by, offense, borough, sev,
                          since, until, limit), S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# collisions
# ─────────────────────────────────────────────────────────────────────────────

_COLL_GROUPS = {'borough': 'borough', 'street': 'on_street_name',
                'year': 'date_extract_y(crash_date)'}


def collisions(borough: str = '', street: str = '', since: str = '',
               until: str = '', group_by: str = 'borough',
               limit: int = 25) -> dict:
    """
    Crashes, injuries and deaths from the NYPD collision file (~2.3M crashes
    since 2012). ``group_by='street'`` is the worst-streets ranking, ordered
    by people injured.
    """
    col = _group_col(group_by, _COLL_GROUPS, 'collisions')
    if not since:
        since = (date.today() - timedelta(days=365)).isoformat()
    where = _where(*_dates('crash_date', since, until),
                   _boro_clause('borough', borough),
                   _like('on_street_name', street),
                   # A street ranking led by "(blank)" answers nothing; the
                   # un-geocoded crashes still show in `totals`.
                   'on_street_name IS NOT NULL' if group_by == 'street' else None)
    order = 'injured DESC' if group_by == 'street' else 'crashes DESC'
    limit = max(1, min(int(limit), 1000))

    def fetch():
        sel = (f'{col} as k, count(1) as crashes, '
               'sum(number_of_persons_injured) as injured, '
               'sum(number_of_persons_killed) as killed')
        rows = S.soql(S.NYC, COLLISIONS, select=sel, where=where,
                      group=col, order=order, limit=limit)
        out = [{group_by: _label(r.get('k')).strip(),
                'crashes': _n(r.get('crashes')),
                'injured': _n(r.get('injured')),
                'killed': _n(r.get('killed'))} for r in rows]
        tot = S.soql(S.NYC, COLLISIONS,
                     select=('count(1) as crashes, '
                             'sum(number_of_persons_injured) as injured, '
                             'sum(number_of_persons_killed) as killed'),
                     where=where)[0]
        return {
            'window': {'since': since, 'until': until or 'today'},
            'filters': {'borough': borough or None, 'street': street or None},
            'group_by': group_by,
            'totals': {'crashes': _n(tot.get('crashes')),
                       'injured': _n(tot.get('injured')),
                       'killed': _n(tot.get('killed'))},
            'rows': out, 'sources': _src(COLLISIONS),
            'notes': ['A third of crash rows have no borough geocoded — '
                      'the (blank) bucket is real crashes, not an error.',
                      'Crashes reported by police only (generally those with '
                      'injury, death or $1,000+ damage).'],
        }
    return S.cached(_ckey('citycoll', group_by, borough, street, since,
                          until, limit), HOUR, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# restaurants
# ─────────────────────────────────────────────────────────────────────────────

GRADE_MEANINGS = {'A': 'A (0-13 points)', 'B': 'B (14-27 points)',
                  'C': 'C (28+ points)', 'N': 'Not yet graded',
                  'Z': 'Grade pending', 'P': 'Grade pending (re-opening)'}


def restaurants(name: str = '', cuisine: str = '', borough: str = '',
                grade: str = '', mode: str = 'search',
                limit: int = 20) -> dict:
    """
    DOHMH restaurant inspections. ``mode='search'`` returns one row per
    restaurant (the LATEST inspection only — the file holds every historic
    inspection, so without the dedupe one diner appears a dozen times).
    ``mode='grades'`` is the grade distribution (distinct restaurants).
    """
    g = str(grade or '').strip().upper()
    if g and g not in GRADE_MEANINGS:
        raise ValueError(f'grade must be one of {", ".join(GRADE_MEANINGS)}')
    base = _where(_like('dba', name), _like('cuisine_description', cuisine),
                  _boro_clause('boro', borough))
    filters = {'name': name or None, 'cuisine': cuisine or None,
               'borough': borough or None, 'grade': g or None}

    if mode == 'grades':
        def fetch_grades():
            rows = S.soql(S.NYC, RESTAURANTS,
                          select='grade, count(distinct camis) as restaurants',
                          where=_where(base, 'grade IS NOT NULL'),
                          group='grade', order='restaurants DESC', limit=20)
            out = [{'grade': _label(r.get('grade')),
                    'meaning': GRADE_MEANINGS.get(str(r.get('grade') or '').strip(), ''),
                    'restaurants': _n(r.get('restaurants'))} for r in rows]
            return {'mode': 'grades', 'filters': filters, 'rows': out,
                    'total_restaurants': sum(r['restaurants'] for r in out),
                    'sources': _src(RESTAURANTS),
                    'note': ('Distinct restaurants that ever held each grade '
                             'within the filter; lower score is better.')}
        return S.cached(_ckey('cityrest-grades', name, cuisine, borough),
                        S.DAY, fetch_grades)

    if mode != 'search':
        raise ValueError("mode must be 'search' or 'grades'")
    limit = max(1, min(int(limit), 100))

    def fetch():
        # Bounded raw fetch: ≤1000 filtered inspection rows, newest first,
        # then keep only the newest row per restaurant (camis).
        rows = S.soql(S.NYC, RESTAURANTS,
                      select=('camis,dba,boro,cuisine_description,grade,'
                              'score,inspection_date'),
                      where=_where(base, 'inspection_date IS NOT NULL'),
                      order='inspection_date DESC', limit=1000)
        seen, out = set(), []
        for r in rows:
            c = r.get('camis')
            if not c or c in seen:
                continue
            seen.add(c)
            gr = str(r.get('grade') or '').strip()
            out.append({
                'name': (r.get('dba') or '').strip(),
                'borough': (r.get('boro') or '').strip().title(),
                'cuisine': r.get('cuisine_description') or '',
                'grade': gr or None,
                'grade_meaning': GRADE_MEANINGS.get(gr, ''),
                'score': _n(r.get('score')) if r.get('score') is not None else None,
                'last_inspection': str(r.get('inspection_date') or '')[:10],
            })
        if g:
            out = [r for r in out if (r['grade'] or '') == g]
        return {'mode': 'search', 'filters': filters,
                'restaurants_matched': len(out), 'returned': min(len(out), limit),
                'rows': out[:limit], 'sources': _src(RESTAURANTS),
                'notes': ['One row per restaurant: its latest inspection only.',
                          'Score is violation points — LOWER is better; '
                          '0-13 = A, 14-27 = B, 28+ = C.']}
    return S.cached(_ckey('cityrest', name, cuisine, borough, g, limit),
                    S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# street trees
# ─────────────────────────────────────────────────────────────────────────────

_TREE_GROUPS = {'species': 'spc_common', 'health': 'health',
                'borough': 'boroname', 'status': 'status'}
_TREE_LIVE_GROUPS = {'species': 'genusspecies', 'health': 'tpcondition'}


def trees(species: str = '', borough: str = '', group_by: str = 'species',
          limit: int = 25, source: str = 'census') -> dict:
    """
    Street trees. ``source='census'`` (default) is the 2015 Street Tree
    Census — the last file with common names, health and borough on every
    row (683,788 trees). ``source='living'`` is the Parks Forestry Points
    living inventory (1.1M points, updated daily) which carries only
    Latin genus/species and a condition rating, and no borough.
    """
    limit = max(1, min(int(limit), 1000))
    if source == 'living':
        if borough:
            raise ValueError('the living inventory (hn5i-inap) has no borough '
                             "column — use source='census' for boroughs")
        col = _group_col(group_by, _TREE_LIVE_GROUPS, 'living-inventory trees')
        where = _like('genusspecies', species)

        def fetch_live():
            rows = S.soql(S.NYC, TREES_LIVE, select=f'{col}, count(1) as n',
                          where=where, group=col, order='n DESC', limit=limit)
            out = [{group_by: _label(r.get(col)), 'count': _n(r.get('n'))}
                   for r in rows]
            return {'source_file': 'living inventory', 'group_by': group_by,
                    'filters': {'species': species or None},
                    'total': sum(r['count'] for r in out), 'rows': out,
                    'sources': _src(TREES_LIVE),
                    'note': 'Includes dead trees and stumps (condition Dead).'}
        return S.cached(_ckey('citytrees-live', group_by, species, limit),
                        7 * S.DAY, fetch_live)

    if source != 'census':
        raise ValueError("source must be 'census' or 'living'")
    col = _group_col(group_by, _TREE_GROUPS, 'trees')
    # Health is only recorded for living trees; counting stumps as
    # health-unknown would be noise, so species/health counts are of
    # living trees. status grouping sees everything.
    alive = "status = 'Alive'" if group_by in ('species', 'health') else None
    where = _where(_like('spc_common', species),
                   _boro_clause('boroname', borough), alive)

    def fetch():
        rows = S.soql(S.NYC, TREES_2015, select=f'{col}, count(1) as n',
                      where=where, group=col, order='n DESC', limit=limit)
        out = [{group_by: _label(r.get(col)), 'count': _n(r.get('n'))}
               for r in rows]
        return {'source_file': '2015 census', 'group_by': group_by,
                'filters': {'species': species or None,
                            'borough': borough or None},
                'total': sum(r['count'] for r in out), 'rows': out,
                'sources': _src(TREES_2015),
                'notes': ['Total is the sum over the returned groups only — '
                          'raise limit for a complete census.',
                          '2015 census — the last with species common names, '
                          'health and borough per tree. For current (but '
                          "coarser) data pass source='living'.",
                          'Species and health counts are living trees only.']}
    return S.cached(_ckey('citytrees', group_by, species, borough, limit),
                    7 * S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# evictions
# ─────────────────────────────────────────────────────────────────────────────

_EVICT_GROUPS = {'year': 'date_extract_y(executed_date)', 'borough': 'borough'}


def evictions(borough: str = '', since: str = '', until: str = '',
              group_by: str = 'year', kind: str = '') -> dict:
    """
    Marshal-executed evictions (2017–present), by year or borough, split
    residential vs commercial.
    """
    col = _group_col(group_by, _EVICT_GROUPS, 'evictions')
    k = str(kind or '').strip().lower()
    if k and k not in ('residential', 'commercial'):
        raise ValueError("kind must be 'residential' or 'commercial'")
    where = _where(*_dates('executed_date', since, until),
                   _boro_clause('borough', borough),
                   f"upper(residential_commercial_ind) like '{k[:3].upper()}%'"
                   if k else None,
                   'executed_date IS NOT NULL')

    def fetch():
        rows = S.soql(S.NYC, EVICTIONS,
                      select=f'{col} as k, residential_commercial_ind as t, '
                             'count(1) as n',
                      where=where, group=f'{col},residential_commercial_ind',
                      limit=2000)
        slots: Dict[str, Dict[str, int]] = {}
        for r in rows:
            key = _label(r.get('k'))
            t = str(r.get('t') or '').strip().lower()
            slot = slots.setdefault(key, {'residential': 0, 'commercial': 0,
                                          'total': 0})
            n = _n(r.get('n'))
            if t.startswith('res'):
                slot['residential'] += n
            elif t.startswith('com'):
                slot['commercial'] += n
            slot['total'] += n
        out = [{group_by: key, **v} for key, v in slots.items()]
        out.sort(key=lambda r: str(r[group_by]) if group_by == 'year'
                 else -r['total'])
        return {'window': {'since': since or '2017 (start of file)',
                           'until': until or 'today'},
                'filters': {'borough': borough or None, 'kind': k or None},
                'group_by': group_by,
                'total': sum(r['total'] for r in out), 'rows': out,
                'sources': _src(EVICTIONS),
                'note': ('Evictions actually executed by city marshals — '
                         'filings are far more numerous. File starts 2017.')}
    return S.cached(_ckey('cityevict', group_by, borough, since, until, k),
                    S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# building permits
# ─────────────────────────────────────────────────────────────────────────────

_PERMIT_GROUPS = {'year': 'date_extract_y(issued_date)', 'borough': 'borough',
                  'type': 'work_type', 'street': 'street_name'}


def permits(borough: str = '', work_type: str = '', since: str = '',
            until: str = '', group_by: str = 'year',
            limit: int = 25) -> dict:
    """
    Construction permits from DOB NOW (the live permitting system — the
    legacy BIS file ipu4-2q9a dwindled to ~1/10 the volume after 2020 and
    stores its dates as text). Coverage ~2016 → present.
    """
    col = _group_col(group_by, _PERMIT_GROUPS, 'permits')
    where = _where(*_dates('issued_date', since, until),
                   _boro_clause('borough', borough),
                   _like('work_type', work_type),
                   'issued_date IS NOT NULL')
    limit = max(1, min(int(limit), 1000))
    order = ('k ASC' if group_by == 'year' else 'n DESC')

    def fetch():
        rows = S.soql(S.NYC, PERMITS, select=f'{col} as k, count(1) as n',
                      where=where, group=col, order=order, limit=limit)
        out = [{group_by: _label(r.get('k')).strip() or '(blank)',
                'permits': _n(r.get('n'))} for r in rows]
        return {'window': {'since': since or '2016 (start of DOB NOW)',
                           'until': until or 'today'},
                'filters': {'borough': borough or None,
                            'work_type': work_type or None},
                'group_by': group_by,
                'total': sum(r['permits'] for r in out), 'rows': out,
                'sources': _src(PERMITS),
                'notes': ['DOB NOW: Build approved permits — the current '
                          'permitting system. Pre-2016 history lives in the '
                          'legacy BIS file (ipu4-2q9a), reachable with '
                          'nyc_query (note: its dates are MM/DD/YYYY text).',
                          'Initial permits and renewals both count as rows.']}
    return S.cached(_ckey('citypermits', group_by, borough, work_type, since,
                          until, limit), S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# air quality
# ─────────────────────────────────────────────────────────────────────────────

def air(measure: str = '', place: str = '', limit: int = 50) -> dict:
    """
    DOHMH neighborhood air-quality indicators. With no ``measure`` this
    lists every available indicator; with one it returns the LATEST
    surveillance period's value for every place that reports it.
    """
    limit = max(1, min(int(limit), 500))
    if not str(measure or '').strip():
        def fetch_measures():
            rows = S.soql(S.NYC, AIR,
                          select=('name, measure, measure_info, count(1) as n,'
                                  ' max(start_date) as latest'),
                          group='name,measure,measure_info',
                          order='name', limit=200)
            out = [{'measure': r.get('name'), 'statistic': r.get('measure'),
                    'unit': r.get('measure_info') or '',
                    'observations': _n(r.get('n')),
                    'latest_period_start': str(r.get('latest') or '')[:10]}
                   for r in rows]
            return {'measures': out, 'count': len(out), 'sources': _src(AIR),
                    'note': ('Pass one of these as `measure` (substring is '
                             'fine, e.g. "PM 2.5") for values by place.')}
        return S.cached('cityair-measures', S.DAY, fetch_measures)

    def fetch():
        rows = S.soql(S.NYC, AIR,
                      select=('name,measure,measure_info,geo_type_name,'
                              'geo_place_name,time_period,start_date,'
                              'data_value'),
                      where=_where(_like('name', measure),
                                   _like('geo_place_name', place)),
                      order='start_date DESC', limit=2000)
        if not rows:
            return {'measure': measure, 'rows': [], 'sources': _src(AIR),
                    'note': 'No indicator matched; call with no measure '
                            'to list them.'}
        newest = max(str(r.get('start_date') or '') for r in rows)
        latest = [r for r in rows if str(r.get('start_date') or '') == newest]
        latest.sort(key=lambda r: -(float(r.get('data_value') or 0)))
        out = [{'place': r.get('geo_place_name'),
                'geography': r.get('geo_type_name'),
                'value': float(r['data_value']) if r.get('data_value') else None,
                'period': r.get('time_period')} for r in latest[:limit]]
        head = latest[0]
        return {'measure': head.get('name'),
                'statistic': head.get('measure'),
                'unit': head.get('measure_info') or '',
                'period': head.get('time_period'),
                'places': len(latest), 'rows': out, 'sources': _src(AIR),
                'note': ('Latest surveillance period only. Geography types '
                         'mix boroughs, community districts and UHF '
                         'neighborhoods — compare like with like.')}
    return S.cached(_ckey('cityair', measure, place, limit), S.DAY, fetch)
