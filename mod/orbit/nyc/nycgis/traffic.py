"""
nyc.traffic — how the city's roads actually move, and when.

Two datasets, answering two different questions:

  * **Live speeds** (DOT ``i4gi-tjb9``) — where traffic is moving *right now*.
    Sensors on the highways and major arterials report a speed and a travel
    time every few minutes.

  * **Volume by hour** (DOT ``7ym2-wayt``) — how busy a street is at each hour
    of the day, averaged over every count DOT has taken there. This is the one
    you can actually plan around: it says which hour to leave.

Both are public, key-free NYC Open Data.

Two traps in these files are worth stating up front, because each one silently
produces a wrong number:

1. The speed feed is an **archive**, not a current-state table — 110M rows
   going back years. Querying it without an order returns arbitrary history.
   ``max(data_as_of)`` is not trustworthy either; it returned two different
   values seconds apart. The snapshot is taken by ordering newest-first and
   keeping the most recent reading per link.

2. Volume rows are **15-minute bin counts**, not hourly totals, and a handful
   of locations use 10-minute bins instead. Averaging ``vol`` per hour gives
   the average *bin*, which understates traffic by 4-6x. Summing the per-bin
   averages across the hour is exact and needs no assumption about bin width.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from . import sources as S

SPEEDS_DATASET = 'i4gi-tjb9'
VOLUME_DATASET = '7ym2-wayt'

# The live feed is refetched at most this often. Sensors report every 1-5
# minutes, so anything shorter is spending requests on the same numbers.
SPEED_TTL = 180

# Counts are historical averages that only move when DOT runs a new survey.
VOLUME_TTL = 30 * S.DAY

# How far back the hourly profile is built from. 2022 onward keeps the profile
# post-pandemic — commute peaks changed shape enough that mixing 2019 into the
# average would describe a city that no longer exists — while still covering
# 512 count locations. Widening it to 2019 adds locations but blends the two.
VOLUME_SINCE_YEAR = 2022

# Speed bands, in mph. Cut to match how the feed actually distributes (median
# ~33 mph, 5th percentile ~3): the point of the bands is to separate "stopped"
# from "slow", which is the distinction a driver is looking for.
SPEED_BANDS = [
    (0, 10, 'stopped', 'Stopped'),
    (10, 25, 'crawling', 'Crawling'),
    (25, 40, 'moving', 'Moving'),
    (40, 999, 'free', 'Free flow'),
]

_DIRECTIONS = {
    'NB': 'Northbound', 'SB': 'Southbound',
    'EB': 'Eastbound', 'WB': 'Westbound',
}


def _band(speed: float) -> str:
    for lo, hi, key, _ in SPEED_BANDS:
        if lo <= speed < hi:
            return key
    return 'free'


def _title_borough(raw: str) -> str:
    """The speed feed ships both 'Staten Island' and 'Staten island'."""
    return ' '.join(w.capitalize() for w in (raw or '').split())


def _parse_link_points(raw: str) -> List[List[float]]:
    """
    ``"40.78,-73.79 40.78,-73.78 ..."`` → GeoJSON ``[[lng, lat], ...]``.

    The feed truncates this column on some rows mid-coordinate, so pairs that
    do not parse are skipped rather than failing the whole link.
    """
    pts: List[List[float]] = []
    for chunk in (raw or '').replace('\n', ' ').split():
        parts = chunk.split(',')
        if len(parts) != 2:
            continue
        try:
            lat, lng = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if not (40.3 <= lat <= 41.1) or not (-74.4 <= lng <= -73.5):
            continue
        p = [round(lng, 5), round(lat, 5)]
        if not pts or p != pts[-1]:
            pts.append(p)
    return pts


def _link_direction(name: str) -> str:
    """Pull a standalone NB/SB/EB/WB out of DOT's link name, if it has one."""
    for token in re.split(r'[\s/]+', (name or '').upper()):
        if token in _DIRECTIONS:
            return _DIRECTIONS[token]
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# live speeds
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_speeds(scan: int = 20000) -> dict:
    # Newest-first is the only reliable way into this table (see module note).
    # 20k rows reaches roughly half a day back, which is far more than enough
    # to catch every link's latest reading even when some sensors are slow.
    return _snapshot_from_rows(
        S.soql(S.NYC, SPEEDS_DATASET, order='data_as_of DESC', limit=scan,
               timeout=120))


def _snapshot_from_rows(rows: List[dict]) -> dict:
    """Reduce a newest-first page of readings to one current line per link."""
    latest: Dict[str, dict] = {}
    for r in rows:
        link = r.get('link_id')
        if not link:
            continue
        prev = latest.get(link)
        if prev is None or (r.get('data_as_of') or '') > (prev.get('data_as_of') or ''):
            latest[link] = r

    feats, dark, newest = [], 0, ''
    for link, r in latest.items():
        # status -101 is DOT's "sensor is not reporting". Those rows still
        # carry a speed field, and it is always garbage — count them so the
        # map can say how much of the network is dark, but never draw them.
        if str(r.get('status', '')).strip() != '0':
            dark += 1
            continue
        try:
            speed = round(float(r.get('speed')), 1)
            travel = int(float(r.get('travel_time') or 0))
        except (TypeError, ValueError):
            dark += 1
            continue
        coords = _parse_link_points(r.get('link_points', ''))
        if len(coords) < 2:
            dark += 1
            continue

        as_of = str(r.get('data_as_of') or '')
        newest = max(newest, as_of)
        name = (r.get('link_name') or '').strip()
        feats.append({
            'type': 'Feature',
            'properties': {
                'link': link,
                'name': name,
                'direction': _link_direction(name),
                'speed': speed,
                'band': _band(speed),
                'travel_time': travel,
                'borough': _title_borough(r.get('borough', '')),
                'owner': (r.get('owner') or '').strip(),
                'as_of': as_of,
            },
            'geometry': {'type': 'LineString', 'coordinates': coords},
        })

    # Slowest on top: an overlapping tangle of links should show the jam.
    feats.sort(key=lambda f: -f['properties']['speed'])

    speeds = [f['properties']['speed'] for f in feats]
    by_band: Dict[str, int] = defaultdict(int)
    for f in feats:
        by_band[f['properties']['band']] += 1

    return {
        'type': 'FeatureCollection',
        'features': feats,
        'meta': {
            'as_of': newest,
            'links_reporting': len(feats),
            'links_dark': dark,
            'median_speed': round(sorted(speeds)[len(speeds) // 2], 1) if speeds else None,
            'bands': [{'key': k, 'label': lbl, 'min': lo, 'max': (None if hi > 200 else hi),
                       'links': by_band.get(k, 0)}
                      for lo, hi, k, lbl in SPEED_BANDS],
            'source': 'NYC DOT Real-Time Traffic Speeds (i4gi-tjb9)',
            'note': ('Sensor coverage is highways and major arterials only — '
                     'most local streets have no detector and are not shown.'),
        },
    }


SPEED_CACHE_KEY = 'traffic-speeds'


def speeds() -> dict:
    """
    The current speed picture, and never an older one than we already had.

    Socrata answers this dataset from replicas that are **not in sync**: the
    identical ordered query, run twice seconds apart, returns a snapshot from
    13:01 or one from 14:36 depending on which replica takes it. Cached
    naively, a refresh would visibly walk the map back in time an hour and a
    half. So a refetch is only allowed to replace what we hold if it is
    actually newer; a staler answer is discarded but still resets the clock, so
    a lagging replica cannot turn into a request loop either.

    Because only strictly-older snapshots are rejected, this still converges on
    the newest reading rather than pinning to the first one seen.
    """
    hit = S.cache_read(SPEED_CACHE_KEY, SPEED_TTL)
    if hit is not None:
        return _with_age(hit)

    try:
        fresh = _fetch_speeds()
    except Exception:
        stale = S.cache_read(SPEED_CACHE_KEY, None)
        if stale is not None:
            return _with_age(stale)
        raise

    prev = S.cache_read(SPEED_CACHE_KEY, None)
    if prev and (prev.get('meta') or {}).get('as_of', '') > fresh['meta'].get('as_of', ''):
        return _with_age(S.cache_write(SPEED_CACHE_KEY, prev))
    return _with_age(S.cache_write(SPEED_CACHE_KEY, fresh))


def _with_age(fc: dict) -> dict:
    """
    Stamp how old the reading is, in minutes.

    The feed publishes New York local time with no offset, so the comparison
    has to be made in that zone — doing it in UTC would report every reading as
    four or five hours stale depending on the season.
    """
    meta = fc.get('meta') or {}
    as_of = meta.get('as_of') or ''
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None)
        meta['age_minutes'] = max(0, int((now - datetime.fromisoformat(as_of)).total_seconds() // 60))
    except Exception:
        meta['age_minutes'] = None
    return fc


# ─────────────────────────────────────────────────────────────────────────────
# volume by hour of day
# ─────────────────────────────────────────────────────────────────────────────

_GROUP = 'segmentid,street,fromst,tost,direction,boro,wktgeom,hh,mm'


def _fetch_volume(since_year: int = VOLUME_SINCE_YEAR) -> dict:
    """
    One count location per (segment, direction), carrying a 24-hour profile of
    vehicles per hour.

    The profile is built by summing the mean of every time bin inside an hour.
    That is exact whether the location reports 15-minute or 10-minute bins, and
    it tolerates days with bins missing — unlike multiplying an average by an
    assumed four bins per hour, which is wrong for a handful of locations and
    silently understates them.
    """
    return _volume_from_rows(
        S.soql_all(S.NYC, VOLUME_DATASET, max_rows=200_000,
                   select=f'{_GROUP},avg(vol) as v,count(1) as n',
                   where=f'yr >= {int(since_year)}',
                   group=_GROUP),
        since_year)


def _volume_from_rows(rows: List[dict],
                      since_year: int = VOLUME_SINCE_YEAR) -> dict:
    # (segment, direction) → hour → summed bin means, plus the location's card
    profile: Dict[tuple, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    samples: Dict[tuple, int] = defaultdict(int)
    card: Dict[tuple, dict] = {}

    for r in rows:
        try:
            hour = int(r['hh'])
            vol = float(r['v'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= hour <= 23):
            continue
        key = (r.get('segmentid'), r.get('direction'), r.get('wktgeom'))
        profile[key][hour] += vol
        samples[key] += int(float(r.get('n') or 0))
        card.setdefault(key, r)

    feats = []
    partial = 0
    for key, hours in profile.items():
        # A "calmest hour" claim needs the whole day behind it; a location that
        # was only counted 09:00-17:00 would name 09:00 as its quietest hour
        # purely because the night is missing.
        if len(hours) < 24:
            partial += 1
            continue
        pos = S.wkt_point_to_lnglat(key[2] or '')
        if not pos:
            continue

        series = [round(hours[h], 1) for h in range(24)]
        peak_h = max(range(24), key=lambda h: series[h])
        calm_h = min(range(24), key=lambda h: series[h])
        peak = series[peak_h]
        r = card[key]
        direction = (r.get('direction') or '').strip().upper()

        feats.append({
            'type': 'Feature',
            'properties': {
                'segment': key[0],
                'street': (r.get('street') or '').strip(),
                'from': (r.get('fromst') or '').strip(),
                'to': (r.get('tost') or '').strip(),
                'direction': direction,
                'direction_label': _DIRECTIONS.get(direction, direction),
                'borough': (r.get('boro') or '').strip(),
                'profile': series,
                'daily': int(round(sum(series))),
                'peak_hour': peak_h,
                'peak_vph': int(round(peak)),
                'calm_hour': calm_h,
                'calm_vph': int(round(series[calm_h])),
                'am_peak_vph': int(round(max(series[6:11]))),
                'pm_peak_vph': int(round(max(series[15:20]))),
                # The hours a driver can use: at or under half the day's worst.
                'quiet_hours': [h for h in range(24) if series[h] <= peak * 0.5],
                'samples': samples[key],
            },
            'geometry': {'type': 'Point', 'coordinates': list(pos)},
        })

    feats.sort(key=lambda f: -f['properties']['daily'])
    citywide = _citywide_curve(feats)

    return {
        'type': 'FeatureCollection',
        'features': feats,
        'meta': {
            'locations': len(feats),
            'partial_locations': partial,
            'since_year': since_year,
            'citywide_profile': citywide,
            'citywide_peak_hour': max(range(24), key=lambda h: citywide[h]) if citywide else None,
            'citywide_calm_hour': min(range(24), key=lambda h: citywide[h]) if citywide else None,
            'source': 'NYC DOT Automated Traffic Volume Counts (7ym2-wayt)',
            'note': ('DOT counts a few hundred locations on a rotating survey, '
                     f'not every street. Profiles average every count since '
                     f'{since_year}; they are a typical day, not a forecast. '
                     f'{partial} location(s) with less than a full 24 hours of '
                     'coverage are excluded.'),
        },
    }


def _citywide_curve(feats: List[dict]) -> List[float]:
    """Mean share-of-peak across every location, so one huge road can't own it."""
    if not feats:
        return []
    totals = [0.0] * 24
    for f in feats:
        series = f['properties']['profile']
        peak = max(series) or 1
        for h in range(24):
            totals[h] += series[h] / peak
    return [round(t / len(feats), 4) for t in totals]


def volume(since_year: int = VOLUME_SINCE_YEAR) -> dict:
    return S.cached(f'traffic-volume-{since_year}', VOLUME_TTL,
                    lambda: _fetch_volume(since_year))


# ─────────────────────────────────────────────────────────────────────────────
# the planning answer
# ─────────────────────────────────────────────────────────────────────────────

def _hour_label(h: Optional[int]) -> str:
    if h is None:
        return ''
    ampm = 'AM' if h < 12 else 'PM'
    base = h % 12 or 12
    return f'{base}{ampm}'


def summary(street: str = '', borough: str = '', hour: Optional[int] = None,
            limit: int = 20) -> Dict[str, Any]:
    """
    When to drive, and what the roads are doing now.

    With no filter this is the city-wide read. Give it a ``street`` or
    ``borough`` and it narrows to the count locations that match, each with
    its own best and worst hours.
    """
    vol = volume()
    feats = vol['features']
    meta = vol['meta']

    q_street, q_boro = street.strip().lower(), borough.strip().lower()
    if q_street:
        feats = [f for f in feats
                 if q_street in f['properties']['street'].lower()
                 or q_street in f['properties']['from'].lower()
                 or q_street in f['properties']['to'].lower()]
    if q_boro:
        feats = [f for f in feats if q_boro in f['properties']['borough'].lower()]

    locations = []
    for f in feats[:limit]:
        p = f['properties']
        lng, lat = f['geometry']['coordinates']
        row = {
            'street': p['street'], 'from': p['from'], 'to': p['to'],
            'direction': p['direction_label'] or p['direction'],
            'borough': p['borough'],
            'daily_vehicles': p['daily'],
            'busiest_hour': _hour_label(p['peak_hour']),
            'busiest_vph': p['peak_vph'],
            'calmest_hour': _hour_label(p['calm_hour']),
            'calmest_vph': p['calm_vph'],
            'quiet_hours': [_hour_label(h) for h in p['quiet_hours']],
            'lat': lat, 'lng': lng,
        }
        if hour is not None and 0 <= hour <= 23:
            row['vph_at_hour'] = int(round(p['profile'][hour]))
            row['share_of_peak'] = (round(p['profile'][hour] / p['peak_vph'], 2)
                                    if p['peak_vph'] else None)
        locations.append(row)

    curve = meta.get('citywide_profile') or []
    out: Dict[str, Any] = {
        'query': {'street': street, 'borough': borough, 'hour': hour},
        'matched_locations': len(feats),
        'locations': locations,
        'citywide': {
            'busiest_hour': _hour_label(meta.get('citywide_peak_hour')),
            'calmest_hour': _hour_label(meta.get('citywide_calm_hour')),
            'hourly_share_of_peak': {_hour_label(h): curve[h] for h in range(len(curve))},
        },
        'coverage': {'locations': meta.get('locations'),
                     'since_year': meta.get('since_year'),
                     'note': meta.get('note')},
        'sources': [meta.get('source')],
    }

    # The live picture only makes sense city-wide or per borough — the speed
    # sensors are on a different network from the count locations and cannot be
    # matched to a named street reliably.
    try:
        sp = speeds()
        links = sp['features']
        if q_boro:
            links = [f for f in links if q_boro in f['properties']['borough'].lower()]
        vals = sorted(f['properties']['speed'] for f in links)
        slowest = sorted(links, key=lambda f: f['properties']['speed'])[:5]
        out['live'] = {
            'as_of': sp['meta'].get('as_of'),
            'links_reporting': len(links),
            'median_speed_mph': round(vals[len(vals) // 2], 1) if vals else None,
            'slowest_now': [{'road': f['properties']['name'],
                             'speed_mph': f['properties']['speed'],
                             'borough': f['properties']['borough']}
                            for f in slowest],
        }
        out['sources'].append(sp['meta'].get('source'))
    except Exception as e:
        # A dead live feed must not cost the caller the historical answer,
        # which is the part they can actually plan with.
        out['live'] = {'error': f'{type(e).__name__}: {e}'}

    return out
