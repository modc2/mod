"""
nyc.housing — the tenant's side of the housing data.

The module already answers what homes *cost* (``prices``, ``realestate``,
``rents``). This engine answers the questions a New Yorker actually asks
when looking for or living in a home:

  * "What affordable housing can I apply for RIGHT NOW?"
      ``lotteries()`` — Housing Connect lotteries, by lottery (``vy5i-a666``)
      and by building (``nibs-na6y``). Updated while lotteries are open, with
      deadlines, unit mixes and income bands.
  * "Is this building safe to rent in?"
      ``building()`` — one address, checked against HPD violations
      (``wvxf-dwi5``), HPD complaints/problems (``ygpa-z7cr``) and HPD
      litigation (``59kj-x8nc``).
  * "Where are conditions worst?"
      ``violations()`` — open housing-maintenance violations by class and
      borough, plus the buildings carrying the most open hazardous ones.
  * "What about public housing?"
      ``nycha()`` — the NYCHA Development Data Book (``evjd-dqpz``):
      developments, apartments, population, average rent.

Dataset quirks encoded here so callers never relearn them:

  * The violations file is ~11M rows — every question is answered with a
    server-side ``$group``; nothing downloads raw rows beyond small, bounded
    lists. (Same discipline as ``citydata``.)
  * Socrata JSON omits null columns entirely, so every field read is a
    ``.get`` and every count goes through ``_n``.
  * ``ygpa-z7cr`` is PROBLEM-level: one complaint can carry several problem
    rows. Counts here are labelled ``problems``, not complaints, and rows
    flagged as duplicates are excluded.
  * The old complaints file ``uwyv-629c`` now requires a login — don't go
    back to it.
  * Housing Connect ``lottery_status`` values are ``Active`` (open for
    applications), ``Closed``, ``Tenant Selection`` and ``All Units Filled``.
    Only ``Active`` is actionable; it is the default filter.
  * The NYCHA Data Book is a spreadsheet upload: numbers can arrive with
    thousands separators and the borough column is free-ish text. ``_num``
    strips commas; borough grouping is done locally on the ~300 rows.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import sources as S

HOUR = 3600
DAY = 24 * HOUR

VIOLATIONS = 'wvxf-dwi5'     # Housing Maintenance Code Violations (HPD)
COMPLAINTS = 'ygpa-z7cr'     # Housing Maintenance Code Complaints and Problems
LITIGATION = '59kj-x8nc'     # Housing Litigations (HPD)
LOTTERIES = 'vy5i-a666'      # Advertised Lotteries on Housing Connect, by lottery
LOTTERY_BLDGS = 'nibs-na6y'  # Advertised Lotteries on Housing Connect, by building
NYCHA = 'evjd-dqpz'          # NYCHA Development Data Book

APPLY_URL = 'https://housingconnect.nyc.gov'

# The lottery file stores boroughs as two-letter codes (plus 'Multiple').
LOTTERY_BORO_CODE = {'MANHATTAN': 'MN', 'BRONX': 'BX', 'BROOKLYN': 'BK',
                     'QUEENS': 'QN', 'STATEN ISLAND': 'SI'}
LOTTERY_BORO_NAME = {v: k.title() for k, v in LOTTERY_BORO_CODE.items()}

BOROUGHS = {
    'manhattan': 'MANHATTAN', 'mn': 'MANHATTAN', 'new york': 'MANHATTAN',
    'bronx': 'BRONX', 'the bronx': 'BRONX', 'bx': 'BRONX',
    'brooklyn': 'BROOKLYN', 'bk': 'BROOKLYN', 'kings': 'BROOKLYN',
    'queens': 'QUEENS', 'qn': 'QUEENS', 'qns': 'QUEENS',
    'staten island': 'STATEN ISLAND', 'si': 'STATEN ISLAND',
    'richmond': 'STATEN ISLAND',
}

# HPD violation classes, in rising order of severity.
VIOLATION_CLASSES = {
    'A': 'non-hazardous',
    'B': 'hazardous',
    'C': 'immediately hazardous',
    'I': 'information order',
}

# Income bands on the lottery file, lowest first. Values are unit counts.
AMI_BANDS = [
    ('applied_income_ami_ext_low', 'extremely low income (≤30% AMI)'),
    ('applied_income_ami_very_low', 'very low income (31-50% AMI)'),
    ('applied_income_ami_low', 'low income (51-80% AMI)'),
    ('applied_income_ami_moderate', 'moderate income (81-120% AMI)'),
    ('applied_income_ami_middle', 'middle income (121-165% AMI)'),
    ('applied_income_ami_above', 'above 165% AMI'),
]

BEDROOMS = [
    ('unit_distribution_studio', 'studio'),
    ('unit_distribution_1bed', '1br'),
    ('unit_distribution_2bed', '2br'),
    ('unit_distribution_3bed', '3br'),
    ('unit_distribution_4bed', '4br+'),
]


# ── small local helpers (engines here stay self-contained) ──────────────

def _n(v: Any) -> int:
    """'1,234' / '$513' / 12.0 / None → int. Bad input counts as 0."""
    if v is None:
        return 0
    try:
        s = str(v).replace(',', '').replace('$', '').strip()
        return int(float(s or 0))
    except (TypeError, ValueError):
        return 0


def _esc(s: str) -> str:
    """Escape a user string for a single-quoted SoQL literal."""
    return str(s).replace("'", "''")


def _boro(name: str) -> Optional[str]:
    key = str(name or '').strip().lower()
    if not key:
        return None
    if key not in BOROUGHS:
        raise ValueError(f'unknown borough {name!r}; try one of: '
                         'Manhattan, Bronx, Brooklyn, Queens, Staten Island')
    return BOROUGHS[key]


def _day(v: Any) -> str:
    """'2026-10-05T00:00:00.000' → '2026-10-05'; blanks stay blank."""
    return str(v or '')[:10]


def split_address(address: str) -> tuple:
    """
    '184 Eldert St' → ('184', 'ELDERT ST'). The house number is whatever
    comes before the first space — NYC house numbers can be '177-06' or
    '22A', so no digit check.
    """
    parts = str(address or '').strip().upper().split(None, 1)
    if len(parts) < 2 or not parts[0] or not parts[1].strip():
        raise ValueError('address must look like "184 Eldert Street" '
                         '(house number, then street)')
    return parts[0], parts[1].strip()


def _street_clause(col: str, street: str) -> str:
    """
    Street-name match that survives 'ST'/'STREET' style suffix drift: match
    on the name WITHOUT its last word when a known suffix is present, as a
    prefix. 'ELDERT ST' and 'ELDERT STREET' both hit 'ELDERT %'.
    """
    words = street.split()
    suffixes = {'ST', 'STREET', 'AVE', 'AVENUE', 'RD', 'ROAD', 'BLVD',
                'BOULEVARD', 'PL', 'PLACE', 'DR', 'DRIVE', 'LN', 'LANE',
                'CT', 'COURT', 'TER', 'TERRACE', 'PKWY', 'PARKWAY'}
    if len(words) > 1 and words[-1] in suffixes:
        stem = ' '.join(words[:-1])
        return f"upper({col}) like '{_esc(stem)} %'"
    return f"upper({col}) like '{_esc(street)}%'"


# ── lotteries: what you can apply for right now ──────────────────────────

def lotteries(borough: str = '', status: str = 'active',
              limit: int = 50) -> Dict[str, Any]:
    """
    Housing Connect lotteries — by default the ones open for applications,
    soonest deadline first, with unit mix, income bands and set-asides.
    """
    status_map = {'active': 'Active', 'open': 'Active', 'closed': 'Closed',
                  'tenant selection': 'Tenant Selection',
                  'filled': 'All Units Filled', 'all': ''}
    key = str(status or 'active').strip().lower()
    if key not in status_map:
        raise ValueError(f'status must be one of {sorted(status_map)}')
    want = status_map[key]
    b = _boro(borough)
    # Trap: the file holds rows still marked Active whose deadlines passed
    # years ago. "Open for applications" must also mean the deadline is
    # today or later (null = rolling, keep it).
    fresh = (f"(lottery_end_date >= '{date.today().isoformat()}' "
             'OR lottery_end_date IS NULL)') if want == 'Active' else None
    where = ' AND '.join(c for c in (
        f"lottery_status = '{want}'" if want else None,
        fresh,
        f"upper(borough) = '{LOTTERY_BORO_CODE[b]}'" if b else None) if c) \
        or None
    limit = max(1, min(int(limit or 50), 200))

    def fetch():
        rows = S.soql(S.NYC, LOTTERIES, where=where,
                      order='lottery_end_date ASC', limit=limit)
        out = []
        for r in rows:
            bands = [label for col, label in AMI_BANDS if _n(r.get(col))]
            units = {label: _n(r.get(col)) for col, label in BEDROOMS
                     if _n(r.get(col))}
            out.append({
                'lottery_id': r.get('lottery_id'),
                'name': r.get('lottery_name'),
                'status': r.get('lottery_status'),
                'borough': LOTTERY_BORO_NAME.get(
                    str(r.get('borough') or '').upper(),
                    (r.get('borough') or '').title()),
                'postcode': r.get('postcode'),
                'type': r.get('development_type'),
                'units': _n(r.get('unit_count')),
                'buildings': _n(r.get('building_count')),
                'unit_mix': units,
                'income_bands': bands,
                'opens': _day(r.get('lottery_start_date')),
                'deadline': _day(r.get('lottery_end_date')),
                'senior_62_plus_pct': _n(r.get('lottery_62_percent')) or None,
                'nycha_resident_pct': _n(r.get('lottery_nycha_percent')) or None,
                'mobility_pct': _n(r.get('lottery_mobility_percent')) or None,
                'community_board_pct':
                    _n(r.get('lottery_community_board_percent')) or None,
                'lat': r.get('latitude'), 'lng': r.get('longitude'),
            })
        deadlines = [x['deadline'] for x in out if x['deadline']]
        return {
            'lotteries': out,
            'count': len(out),
            'total_units': sum(x['units'] for x in out),
            'next_deadline': min(deadlines) if deadlines else None,
            'apply_at': APPLY_URL,
            'note': ('Apply free at Housing Connect; a lottery number is not '
                     'first-come-first-served, so applying any time before '
                     'the deadline counts the same.'),
            'source': ('NYC HPD/HDC, Advertised Lotteries on Housing Connect '
                       f'({LOTTERIES})'),
        }

    day = date.today().isoformat() if want == 'Active' else ''
    return S.cached(f'housing:lotteries:{want or "all"}:{b or "nyc"}:'
                    f'{limit}:{day}', 6 * HOUR, fetch)


def lottery_buildings(lottery_id: str) -> Dict[str, Any]:
    """The addresses behind one lottery (where the units physically are)."""
    lid = _esc(str(lottery_id or '').strip())
    if not lid:
        raise ValueError('lottery_id is required')

    def fetch():
        rows = S.soql(S.NYC, LOTTERY_BLDGS,
                      where=f"lottery_id = '{lid}'", limit=200)
        return {
            'lottery_id': lottery_id,
            'name': rows[0].get('lottery_name') if rows else None,
            'buildings': [{
                'address': ' '.join(w for w in (
                    r.get('house_number'), r.get('street_name')) if w),
                'borough': LOTTERY_BORO_NAME.get(
                    str(r.get('borough') or '').upper(),
                    (r.get('borough') or '').title()),
                'zip': r.get('address_zipcode'),
                'units': _n(r.get('unit_count')),
                'construction': r.get('reporting_construction_type'),
                'bbl': r.get('address_bbl'),
                'lat': r.get('address_latitude'),
                'lng': r.get('address_longitude'),
            } for r in rows],
            'source': ('Advertised Lotteries on Housing Connect by Building '
                       f'({LOTTERY_BLDGS})'),
        }

    return S.cached(f'housing:lotterybldgs:{lid}', 6 * HOUR, fetch)


# ── violations: where conditions are worst ───────────────────────────────

def violations(borough: str = '', limit: int = 15) -> Dict[str, Any]:
    """
    Open housing-maintenance violations: counts by class (A non-hazardous →
    C immediately hazardous), the borough breakdown, and the buildings with
    the most open class C violations.
    """
    b = _boro(borough)
    limit = max(1, min(int(limit or 15), 50))
    boro_and = f" AND upper(boro) = '{_esc(b)}'" if b else ''

    def fetch():
        by_class = S.soql(
            S.NYC, VIOLATIONS,
            select='class, count(1) as n',
            where="violationstatus = 'Open'" + boro_and,
            group='class', limit=50)
        by_boro = S.soql(
            S.NYC, VIOLATIONS,
            select='boro, class, count(1) as n',
            where="violationstatus = 'Open'",
            group='boro, class', limit=100)
        worst = S.soql(
            S.NYC, VIOLATIONS,
            select='housenumber, streetname, boro, zip, count(1) as n',
            where="violationstatus = 'Open' AND class = 'C'" + boro_and,
            group='housenumber, streetname, boro, zip',
            order='count(1) DESC', limit=limit)

        classes = {str(r.get('class') or '?'): _n(r.get('n'))
                   for r in by_class}
        boros: Dict[str, Dict[str, int]] = {}
        for r in by_boro:
            k = str(r.get('boro') or '(blank)').title()
            slot = boros.setdefault(k, {'open': 0, 'immediately_hazardous': 0})
            slot['open'] += _n(r.get('n'))
            if r.get('class') == 'C':
                slot['immediately_hazardous'] += _n(r.get('n'))
        return {
            'scope': (b or 'NYC').title(),
            'open_total': sum(classes.values()),
            'open_by_class': [
                {'class': c, 'meaning': VIOLATION_CLASSES.get(c, ''),
                 'open': classes[c]}
                for c in sorted(classes)],
            'by_borough': sorted(
                ({'borough': k, **v} for k, v in boros.items()),
                key=lambda r: -r['open']),
            'worst_buildings': [{
                'address': ' '.join(w for w in (
                    r.get('housenumber'), r.get('streetname')) if w),
                'borough': (r.get('boro') or '').title(),
                'zip': r.get('zip'),
                'open_class_c': _n(r.get('n')),
            } for r in worst],
            'source': ('NYC HPD, Housing Maintenance Code Violations '
                       f'({VIOLATIONS})'),
        }

    return S.cached(f'housing:violations:{b or "nyc"}:{limit}',
                    6 * HOUR, fetch)


# ── one building, checked before you sign ────────────────────────────────

def building(address: str, borough: str = '') -> Dict[str, Any]:
    """
    Due diligence on one address: open HPD violations by class with the
    latest few orders, tenant problems reported in the last two years, and
    any HPD litigation against the landlord.
    """
    house, street = split_address(address)
    b = _boro(borough)
    since = (date.today() - timedelta(days=730)).isoformat()

    def _where(house_col: str, street_col: str, boro_col: str,
               boro_val: Optional[str], extra: str = '') -> str:
        parts = [f"{house_col} = '{_esc(house)}'",
                 _street_clause(street_col, street)]
        if boro_val:
            parts.append(f"upper({boro_col}) = '{_esc(boro_val)}'")
        if extra:
            parts.append(extra)
        return ' AND '.join(parts)

    def fetch():
        open_by_class = S.soql(
            S.NYC, VIOLATIONS, select='class, count(1) as n',
            where=_where('housenumber', 'streetname', 'boro', b,
                         "violationstatus = 'Open'"),
            group='class', limit=20)
        recent = S.soql(
            S.NYC, VIOLATIONS,
            select='class, inspectiondate, novdescription, apartment',
            where=_where('housenumber', 'streetname', 'boro', b),
            order='inspectiondate DESC', limit=5)
        problems = S.soql(
            S.NYC, COMPLAINTS, select='major_category, count(1) as n',
            where=_where('house_number', 'street_name', 'borough', b,
                         f"received_date >= '{since}' AND "
                         "(problem_duplicate_flag IS NULL OR "
                         "problem_duplicate_flag != 'Y')"),
            group='major_category', order='count(1) DESC', limit=15)
        cases = S.soql(
            S.NYC, LITIGATION,
            select='casetype, caseopendate, casestatus, casejudgement',
            where=_where('housenumber', 'streetname', 'boro',
                         None,  # litigation boro is a digit code; skip it
                         None or ''),
            order='caseopendate DESC', limit=10)

        open_counts = {str(r.get('class') or '?'): _n(r.get('n'))
                       for r in open_by_class}
        return {
            'address': f'{house} {street}'.title(),
            'borough': (b or '').title() or None,
            'open_violations': {
                'total': sum(open_counts.values()),
                'by_class': [
                    {'class': c, 'meaning': VIOLATION_CLASSES.get(c, ''),
                     'open': open_counts[c]} for c in sorted(open_counts)],
            },
            'latest_violations': [{
                'class': r.get('class'),
                'inspected': _day(r.get('inspectiondate')),
                'apartment': r.get('apartment'),
                'order': (r.get('novdescription') or '')[:160],
            } for r in recent],
            'problems_reported_2y': [{
                'category': r.get('major_category'),
                'problems': _n(r.get('n')),
            } for r in problems],
            'hpd_litigation': [{
                'type': r.get('casetype'),
                'opened': _day(r.get('caseopendate')),
                'status': r.get('casestatus'),
                'judgement': r.get('casejudgement'),
            } for r in cases],
            'how_to_read': ('Class C violations are immediately hazardous '
                            '(no heat, lead paint, vermin); B are hazardous; '
                            'A are minor. An open heat-and-hot-water '
                            'litigation case is a strong warning sign. '
                            'No rows can also mean a small building that was '
                            'never inspected — absence is not a clean bill.'),
            'sources': [f'HPD violations ({VIOLATIONS})',
                        f'HPD complaints/problems ({COMPLAINTS})',
                        f'HPD litigation ({LITIGATION})'],
        }

    key = f'housing:building:{house}:{street}:{b or ""}'.lower()
    return S.cached(key, DAY, fetch)


# ── NYCHA: the public-housing stock ──────────────────────────────────────

def nycha(borough: str = '', limit: int = 15) -> Dict[str, Any]:
    """
    Public housing from the NYCHA Development Data Book: how many
    developments and apartments exist, where, who lives there, and the
    largest developments.
    """
    b = _boro(borough)
    limit = max(1, min(int(limit or 15), 100))

    def fetch():
        rows = S.soql(S.NYC, NYCHA,
                      select=('development, borough, program, '
                              'total_number_of_apartments, total_population, '
                              'avg_monthly_gross_rent'),
                      where='development IS NOT NULL', limit=5000)
        devs = []
        for r in rows:
            name = str(r.get('development') or '').strip()
            if not name or name.upper().startswith('TOTAL'):
                continue  # spreadsheet upload: ignore roll-up rows
            devs.append({
                'development': name.title(),
                'borough': str(r.get('borough') or '').strip().title(),
                'program': r.get('program'),
                'apartments': _n(r.get('total_number_of_apartments')),
                'population': _n(r.get('total_population')),
                'avg_monthly_rent': _n(r.get('avg_monthly_gross_rent')) or None,
            })
        if b:
            want = b.title()
            devs = [d for d in devs if d['borough'] == want]
        boros: Dict[str, Dict[str, int]] = {}
        for d in devs:
            slot = boros.setdefault(d['borough'] or '(blank)',
                                    {'developments': 0, 'apartments': 0,
                                     'population': 0})
            slot['developments'] += 1
            slot['apartments'] += d['apartments']
            slot['population'] += d['population']
        rents = [d['avg_monthly_rent'] for d in devs if d['avg_monthly_rent']]
        return {
            'scope': (b or 'NYC').title(),
            'developments': len(devs),
            'apartments': sum(d['apartments'] for d in devs),
            'population': sum(d['population'] for d in devs),
            'avg_monthly_rent': round(sum(rents) / len(rents)) if rents else None,
            'by_borough': sorted(
                ({'borough': k, **v} for k, v in boros.items()),
                key=lambda r: -r['apartments']),
            'largest': sorted(devs, key=lambda d: -d['apartments'])[:limit],
            'source': f'NYCHA Development Data Book ({NYCHA})',
        }

    return S.cached(f'housing:nycha:{b or "nyc"}:{limit}', 7 * DAY, fetch)
