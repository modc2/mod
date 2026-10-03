"""Rebuild the gazetteer this module geocodes offers with.

Three public-domain sources, downloaded once and baked into JSON so the
module itself needs no network and no dependency to place a market:

    cities.json     GeoNames cities5000 (CC0)           — city → lat/lon, with
                    the ascii aliases big cities are also known by
    countries.json  Natural Earth 110m admin-0 (public) — country centroid,
                    filled in from GeoNames for countries too small to have one
    states.json     Natural Earth 110m admin-1 (public) — state / province

    python3 geo/build.py          # writes the three files next to this one

Coordinates are rounded to two decimals: a market is a building, not a
survey mark, and two decimals is about a kilometre.
"""

import csv
import io
import json
import os
import unicodedata
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CITIES = 'https://download.geonames.org/export/dump/cities5000.zip'
NE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/'
INFO = 'https://download.geonames.org/export/dump/countryInfo.txt'

# a city this big is worth carrying its other names — Bengaluru is Bangalore,
# Kyiv is Kiev, and a market will spell it either way
ALIAS_POP = 200000
ALIAS_CAP = 8


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.lower().replace('-', ' ').replace('.', '').split())


def fetch(url, path):
    if os.path.exists(path):
        return path
    print('fetch', url)
    urllib.request.urlretrieve(url, path)
    return path


def centroid(geom):
    """Area-weighted centroid of the largest ring — good enough to place a dot."""
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
    best, ba = None, -1
    for poly in polys:
        ring = poly[0]
        a = abs(sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
                    for i in range(len(ring) - 1))) / 2
        if a > ba:
            best, ba = ring, a
    xs = [p[0] for p in best]
    ys = [p[1] for p in best]
    return round(sum(ys) / len(ys), 2), round(sum(xs) / len(xs), 2)


def aliases(name, alts):
    """The other spellings worth carrying, nearest the primary name first.

    GeoNames lists every transliteration a city has in any language; only a
    handful are spellings a compute market would ever print. Names that share
    a word with the primary — Frankfurt for Frankfurt am Main — come first,
    then the short ones, and the tail is dropped.
    """
    base, seen, out = norm(name), {norm(name)}, []
    words = set(base.split())
    for a in alts.split(','):
        n = norm(a)
        if not (a.isascii() and 2 < len(a) < 30 and a[0].isalpha()) or n in seen:
            continue
        seen.add(n)
        near = 0 if (set(n.split()) & words) else 1
        out.append((near, len(n), a))
    out.sort()
    return [a for _, _, a in out[:ALIAS_CAP]]


def build_cities(tmp):
    z = zipfile.ZipFile(fetch(CITIES, os.path.join(tmp, 'cities5000.zip')))
    rows = z.read('cities5000.txt').decode('utf-8').splitlines()
    best = {}
    for r in csv.reader(rows, delimiter='\t', quoting=csv.QUOTE_NONE):
        name, alts, lat, lon, cc, pop = r[2], r[3], r[4], r[5], r[8], int(r[14] or 0)
        if not cc or not name:
            continue
        names = [name]
        if pop >= ALIAS_POP:
            names += aliases(name, alts)
        for nm in names:
            k = (cc, norm(nm))
            if k not in best or pop > best[k][2]:
                best[k] = (round(float(lat), 2), round(float(lon), 2), pop)
    out = {}
    for (cc, n), (lat, lon, _) in best.items():
        out.setdefault(cc, {})[n] = [lat, lon]
    # a bare city name with no country attached resolves against the biggest
    # city of that name on earth — Paris is France until someone says Texas
    top = {}
    for (cc, n), (lat, lon, pop) in best.items():
        if pop >= 200000 and (n not in top or pop > top[n][3]):
            top[n] = [lat, lon, cc, pop]
    out['_global'] = {n: v[:3] for n, v in top.items()}
    return out


def build_countries(tmp, cities):
    """Every ISO country, centroid where Natural Earth draws one.

    Natural Earth's 110m sheet drops the countries too small to survive
    generalisation — Seychelles, Malta, Singapore. GeoNames knows their names,
    and their own largest city is a better anchor than nothing at all.
    """
    g = json.load(open(fetch(NE + 'ne_110m_admin_0_countries.geojson',
                             os.path.join(tmp, 'ne_countries.json'))))
    out = {}
    for f in g['features']:
        p = f['properties']
        cc = (p.get('ISO_A2_EH') or p.get('ISO_A2') or '').upper()
        if not cc or cc == '-99':
            continue
        lat, lon = centroid(f['geometry'])
        out[cc] = [p.get('NAME') or cc, lat, lon]

    path = fetch(INFO, os.path.join(tmp, 'countryInfo.txt'))
    for line in open(path, encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        f = line.split('\t')
        cc, name = f[0].strip().upper(), f[4].strip()
        if not cc or not name:
            continue
        if cc in out:
            out[cc][0] = name          # GeoNames' plain name beats "Republic of"
        elif cc in cities and cities[cc]:
            lat, lon = _anchor(cities[cc])
            out[cc] = [name, lat, lon]
    return out


def _anchor(city_index):
    """A country with no drawn outline sits on the middle of its own cities."""
    pts = list(city_index.values())
    return (round(sum(p[0] for p in pts) / len(pts), 2),
            round(sum(p[1] for p in pts) / len(pts), 2))


def build_states(tmp):
    g = json.load(open(fetch(NE + 'ne_110m_admin_1_states_provinces.geojson',
                             os.path.join(tmp, 'ne_states.json'))))
    out = {}
    for f in g['features']:
        p = f['properties']
        cc = (p.get('iso_a2') or '').upper()
        code = (p.get('postal') or '').upper()
        name = p.get('name') or ''
        if not cc or cc == '-99' or not name:
            continue
        lat, lon = centroid(f['geometry'])
        rec = [name, lat, lon]
        if code:
            out[f'{cc}:{code}'] = rec
        out[f'{cc}:{norm(name)}'] = rec
    return out


def main():
    tmp = os.environ.get('GEO_TMP', '/tmp')
    cities = build_cities(tmp)
    for name, data in (('cities', cities),
                       ('countries', build_countries(tmp, cities)),
                       ('states', build_states(tmp))):
        path = os.path.join(HERE, name + '.json')
        with open(path, 'w') as fh:
            json.dump(data, fh, separators=(',', ':'), sort_keys=True)
        print(f'{name:10} {len(data):6} keys  {os.path.getsize(path) / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
