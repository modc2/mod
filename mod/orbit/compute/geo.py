"""Where a market actually is.

Every provider spells a location differently — clore says `RU`, vast says
`Washington, US`, lium says `Des Moines, United States`, shadeform says
`US, New York, NY`, and a cloud may just say `us-southeast-1`. This turns
any of those into one point on a map:

    >>> geo.place('US, Des Moines, IA')
    {'lat': 41.6, 'lon': -93.61, 'place': 'Des Moines', 'cc': 'US',
     'country': 'United States of America', 'precision': 'city'}

Three rules:

  1. **Offline.** The gazetteer is baked into geo/*.json by geo/build.py from
     GeoNames and Natural Earth. No network, no key, no rate limit, no
     dependency — the same rules the rest of the module lives by.
  2. **Precision is reported, never faked.** A country code places a dot on
     the country's centroid and says so (`precision: 'country'`); it does not
     pretend to know the building. A string that resolves to nothing returns
     None and the offer stays off the map instead of landing in the sea.
  3. **Parse, don't guess.** A token is a country, a state or a city because
     it is in a table, not because it looks like one.
"""

import json
import os
import re
import unicodedata

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'geo')

# A cloud names a region, not a place. These are the public anchor cities the
# big region codes actually sit in — coarse on purpose, and labelled as such.
CLOUD = {
    'us-east': (38.95, -77.45, 'US', 'US East'),
    'us-east-1': (38.95, -77.45, 'US', 'US East'),
    'us-east-2': (39.96, -83.0, 'US', 'US East'),
    'us-west': (45.84, -119.7, 'US', 'US West'),
    'us-west-1': (37.44, -122.14, 'US', 'US West'),
    'us-west-2': (45.84, -119.7, 'US', 'US West'),
    'us-central': (41.26, -95.86, 'US', 'US Central'),
    'us-south': (32.78, -96.8, 'US', 'US South'),
    'us-southeast': (33.75, -84.39, 'US', 'US Southeast'),
    'us-northeast': (40.71, -74.01, 'US', 'US Northeast'),
    'ca-central': (43.65, -79.38, 'CA', 'Canada Central'),
    'eu-west': (53.35, -6.26, 'IE', 'EU West'),
    'eu-central': (50.11, 8.68, 'DE', 'EU Central'),
    'eu-north': (59.33, 18.07, 'SE', 'EU North'),
    'eu-south': (45.46, 9.19, 'IT', 'EU South'),
    'ap-northeast': (35.69, 139.69, 'JP', 'Asia Northeast'),
    'ap-southeast': (1.35, 103.82, 'SG', 'Asia Southeast'),
    'ap-south': (19.08, 72.88, 'IN', 'Asia South'),
    'ap-east': (22.32, 114.17, 'HK', 'Asia East'),
    'sa-east': (-23.55, -46.63, 'BR', 'South America East'),
    'me-south': (26.07, 50.56, 'BH', 'Middle East'),
    'af-south': (-33.92, 18.42, 'ZA', 'Africa South'),
}

# Names a table does not carry, or carries under another spelling.
ALIAS = {
    'usa': 'US', 'u s a': 'US', 'united states': 'US', 'america': 'US',
    'uk': 'GB', 'united kingdom': 'GB', 'great britain': 'GB', 'england': 'GB',
    'russia': 'RU', 'south korea': 'KR', 'north korea': 'KP', 'uae': 'AE',
    'czechia': 'CZ', 'czech republic': 'CZ', 'holland': 'NL',
    'netherlands': 'NL', 'vietnam': 'VN', 'turkey': 'TR', 'turkiye': 'TR',
    'hong kong': 'HK', 'macau': 'MO', 'taiwan': 'TW', 'ivory coast': 'CI',
    'bosnia': 'BA', 'moldova': 'MD', 'laos': 'LA', 'syria': 'SY',
    'iran': 'IR', 'bolivia': 'BO', 'venezuela': 'VE', 'tanzania': 'TZ',
    'dr congo': 'CD', 'republic of the congo': 'CG', 'swaziland': 'SZ',
    'cape verde': 'CV', 'east timor': 'TL', 'burma': 'MM',
}

# Renames a gazetteer built this year no longer lists, but a market still prints.
CITY_ALIAS = {'kiev': 'kyiv', 'saigon': 'ho chi minh city', 'peking': 'beijing',
              'canton': 'guangzhou', 'rangoon': 'yangon', 'madras': 'chennai',
              'calcutta': 'kolkata', 'st petersburg': 'saint petersburg',
              'nyc': 'new york city', 'sf': 'san francisco'}

# Words that never help place anything and would otherwise be read as a city.
NOISE = {'', 'unknown', 'n a', 'na', 'none', 'null', 'any', 'global',
         'earth', 'world', 'anywhere', 'central', 'north', 'south', 'east',
         'west', 'unable to determine country', 'other'}

_CLOUD_RE = re.compile(r'^[a-z]{2}(-[a-z]+)+(-\d+)?$')
# "Chiyoda City" is Chiyoda, "Moscow Oblast" is Moscow — the administrative
# word is the provider's, not the gazetteer's.
_SUFFIX = re.compile(r'\s+(city|town|county|district|province|prefecture|'
                     r'region|oblast|krai|ku|shi|municipality|metropolitan|'
                     r'metro|area|dc)$')
_cache = {}
_data = None


def norm(s):
    """Fold to the gazetteer's key form: no accents, no case, no punctuation."""
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.lower().replace('-', ' ').replace('.', '').split())


def _load():
    """The gazetteer, read once. ~2 MB of JSON, built by geo/build.py."""
    global _data
    if _data is None:
        def read(n):
            with open(os.path.join(HERE, n + '.json')) as fh:
                return json.load(fh)
        countries = read('countries')
        _data = {
            'countries': countries,
            'states': read('states'),
            'cities': read('cities'),
            # every country name and alias, folded, pointing at its code
            'names': dict(ALIAS, **{norm(v[0]): k for k, v in countries.items()}),
        }
    return _data


def _hit(lat, lon, place, cc, precision):
    d = _load()
    return {'lat': lat, 'lon': lon, 'place': place, 'cc': cc,
            'country': (d['countries'].get(cc) or [cc])[0], 'precision': precision}


def place(region):
    """A location string from any provider → one point, or None.

    `precision` says how much the point is worth: 'city' is the city, 'state'
    is a state or province centroid, 'country' is a country centroid, 'region'
    is a cloud's own region anchor.
    """
    key = str(region or '').strip()
    if key not in _cache:
        if len(_cache) > 4000:
            _cache.clear()
        _cache[key] = _resolve(key)
    return _cache[key]


def _resolve(region):
    d = _load()
    raw = str(region or '').strip()
    if not raw or norm(raw) in NOISE:
        return None

    # a cloud region code is a whole string, not a comma list
    low = norm(raw).replace(' ', '-')
    if _CLOUD_RE.match(low):
        for k in (low, low.rsplit('-', 1)[0]):
            if k in CLOUD:
                lat, lon, cc, label = CLOUD[k]
                return _hit(lat, lon, label, cc, 'region')

    toks = [t.strip() for t in re.split(r'[,/|]', raw) if t.strip()]
    cc, rest = None, []
    for t in toks:
        n = norm(t)
        if n in NOISE:
            continue
        if cc is None:
            if len(t) == 2 and t.upper() in d['countries']:
                cc = t.upper()
                continue
            if n in d['names']:
                cc = d['names'][n]
                continue
        rest.append(t)

    # inside the US and Canada a bare token is more often the state than a
    # same-named town, so states are read first there
    if cc in ('US', 'CA'):
        for t in rest:
            for k in (f'{cc}:{t.strip().upper()}', f'{cc}:{norm(t)}'):
                if k in d['states']:
                    s = d['states'][k]
                    city = _city(cc, rest, skip=t)
                    return city or _hit(s[1], s[2], s[0], cc, 'state')

    hit = _city(cc, rest)
    if hit:
        return hit
    if cc and cc in d['countries']:
        c = d['countries'][cc]
        return _hit(c[1], c[2], c[0], cc, 'country')
    return None


def _city(cc, toks, skip=None):
    """First token that is a city — inside `cc` if known, else worldwide."""
    d = _load()
    for t in toks:
        if skip is not None and t == skip:
            continue
        n = norm(t)
        if n in NOISE:
            continue
        for cand in (n, CITY_ALIAS.get(n, n), _SUFFIX.sub('', n)):
            if cc:
                got = (d['cities'].get(cc) or {}).get(cand)
                if got:
                    return _hit(got[0], got[1], t.strip(), cc, 'city')
            else:
                got = d['cities'].get('_global', {}).get(cand)
                if got:
                    return _hit(got[0], got[1], t.strip(), got[2], 'city')
    return None


def attach(offers):
    """Put a `geo` on every offer that publishes a location it can place."""
    for o in offers:
        g = place(o.get('region'))
        if g:
            o['geo'] = g
    return offers


def rollup(offers, round_to=1):
    """Offers → the points a map draws: one per place, cheapest first.

    Nearby markets are merged onto a shared cell (`round_to` degrees) so a
    dot is a place a machine is, not a hundred dots fighting for a pixel.
    """
    cells, unplaced = {}, []
    for o in offers:
        g = o.get('geo') or place(o.get('region'))
        if not g:
            unplaced.append(o)
            continue
        k = (round(g['lat'] / round_to), round(g['lon'] / round_to))
        c = cells.setdefault(k, {'lat': 0.0, 'lon': 0.0, 'count': 0, 'labels': {},
                                 'providers': {}, 'prices': [], 'cc': g['cc'],
                                 'country': g['country'], 'precision': g['precision']})
        c['count'] += 1
        c['lat'] += g['lat']
        c['lon'] += g['lon']
        c['labels'][g['place']] = c['labels'].get(g['place'], 0) + 1
        c['providers'][o['provider']] = c['providers'].get(o['provider'], 0) + 1
        if o.get('usd_hr') is not None:
            c['prices'].append(o['usd_hr'])
        # the sharpest precision in the cell is what the cell is worth
        rank = ('city', 'state', 'region', 'country')
        if rank.index(g['precision']) < rank.index(c['precision']):
            c['precision'] = g['precision']

    points = []
    for (ky, kx), c in cells.items():
        p = sorted(c['prices'])
        points.append({
            'id': f'{ky}:{kx}',
            'place': max(c['labels'], key=c['labels'].get),
            'cc': c['cc'],
            'country': c['country'],
            'precision': c['precision'],
            'lat': round(c['lat'] / c['count'], 3),
            'lon': round(c['lon'] / c['count'], 3),
            'count': c['count'],
            'providers': dict(sorted(c['providers'].items(), key=lambda x: -x[1])),
            'min_usd_hr': p[0] if p else None,
            'median_usd_hr': p[len(p) // 2] if p else None,
        })
    points.sort(key=lambda x: -x['count'])
    return {'points': points, 'placed': sum(p['count'] for p in points),
            'unplaced': len(unplaced),
            'unplaced_providers': dict(sorted(
                _tally(o['provider'] for o in unplaced).items(), key=lambda x: -x[1])),
            'countries': len({p['cc'] for p in points})}


def _tally(it):
    out = {}
    for v in it:
        out[v] = out.get(v, 0) + 1
    return out
