"""
Where HILDA+ lives, what its pixel values mean, and where we keep our copy.

Everything in this module is a constant or a path helper. No network, no
numpy — importing it is cheap, so the CLI can answer ``m hilda`` without
touching the data.

HILDA+ (HIstoric Land Dynamics Assessment+) is published on PANGAEA under
CC-BY-4.0 as a handful of very large ZIP archives. We never download a whole
archive; see ``remote.py`` for how single years are pulled out of the 4.5 GB
GeoTIFF ZIP with HTTP range requests.
"""

import os
from pathlib import Path

# ── the dataset ──────────────────────────────────────────────────────────

DOI = 'https://doi.org/10.1594/PANGAEA.921846'
LANDING = 'https://doi.pangaea.de/10.1594/PANGAEA.921846'
CEOS = 'https://ceos.org/gst/HILDAplus.html'
PAPER = 'https://doi.org/10.1038/s41467-021-22702-2'
CITATION = (
    'Winkler, K; Fuchs, R; Rounsevell, M D A; Herold, M (2020): HILDA+ Global '
    'Land Use Change between 1960 and 2019 [dataset]. PANGAEA, '
    'https://doi.org/10.1594/PANGAEA.921846'
)
PAPER_CITATION = (
    'Winkler, K; Fuchs, R; Rounsevell, M D A; Herold, M (2021): Global land use '
    'changes are four times greater than previously estimated. '
    'Nature Communications 12, 2501.'
)
LICENSE = 'CC-BY-4.0'

PANGAEA_DATASET = 921846
FILE_BASE = f'https://download.pangaea.de/dataset/{PANGAEA_DATASET}/files'
ARCHIVE = 'hildap_vGLOB-1.0_geotiff.zip'          # 4.5 GB, 244 members
ARCHIVE_URL = f'{FILE_BASE}/{ARCHIVE}'

# Member name templates inside the archive. Verified against the archive's
# central directory — the transition files carry a different version stamp
# from the state files, which is a property of the publication, not a typo.
_ROOT = 'hildap_vGLOB-1.0_geotiff_wgs84'
MEMBER = {
    'states': (_ROOT + '/hildap_GLOB-v1.0_lulc-states/'
               'hilda_plus_{year}_states_GLOB-v1-0_wgs84-nn.tif'),
    'transitions': (_ROOT + '/hildap_GLOB-v1.0_lulc-transitions/'
                    'hildaplus_{year}-{prev}_transitions_GLOB-v20-11-29_wgs84-nn.tif'),
}

# States reach back to 1899; transitions start with the 1961-1960 pair. The
# pre-1960 states are a back-cast and the publication treats 1960 as the start
# of the record proper, so that is where our defaults begin.
STATE_YEARS = range(1899, 2020)
TRANSITION_YEARS = range(1961, 2020)
DEFAULT_YEARS = range(1960, 2020)

# 2015 is not a reconstructed annual state. The archive ships it as
# ``..._2015_states_GLOB-v1-0_base-map_wgs84-nn.tif`` — the base map the whole
# reconstruction is anchored on — and it does not use the no-data class at
# all: all 60.5 million pixels that every other year codes 99 (permanent ice)
# are coded 66 (sparse vegetation) instead. Included in the cube it adds
# 12.4 M km2 of phantom land in one year, which breaks every area series and
# hands the automaton a discontinuity to model.
#
# So it is excluded, and the series has a documented one-year hole rather than
# a silent 9% spike. Pass it explicitly to ``ingest`` if you want it anyway.
EXCLUDED_STATE_YEARS = {
    2015: 'base map, not an annual state: codes permanent ice as sparse '
          'vegetation and adds 12.4 M km2 of phantom land',
}

# ── the raster grid ──────────────────────────────────────────────────────

# EPSG:4326, 0.01 degree, origin at (-180, 90). Read off the GeoTIFF tags.
SRC_W, SRC_H = 36000, 18000
SRC_DEG = 0.01
ORIGIN = (-180.0, 90.0)

# The grid we aggregate onto. 0.5 degree divides the source exactly (50x50
# source pixels per cell) and gives a 720x360 map — which is, conveniently,
# also a comfortable size to draw as chunky pixels and to step a cellular
# automaton over.
DEFAULT_DEG = 0.5

EARTH_RADIUS_KM = 6371.0088

# ── land use / cover classes ─────────────────────────────────────────────

# Pixel codes as they appear in the state rasters. 0 is ocean (outside the
# land mask), 77 is inland water and 99 is "no data" — in practice permanent
# ice, mostly Antarctica and Greenland. Only codes 11-66 are land use classes
# that can convert into one another, and only those six are modelled.
CLASSES = [
    {'code': 11, 'key': 'urban',     'name': 'Urban areas',            'color': '#ff3b47', 'short': 'URB'},
    {'code': 22, 'key': 'cropland',  'name': 'Cropland',               'color': '#ffb02e', 'short': 'CRP'},
    {'code': 33, 'key': 'pasture',   'name': 'Pasture / rangeland',    'color': '#b5763a', 'short': 'PAS'},
    {'code': 44, 'key': 'forest',    'name': 'Forest',                 'color': '#1e9e57', 'short': 'FOR'},
    {'code': 55, 'key': 'grassland', 'name': 'Unmanaged grass/shrub',  'color': '#8fd14f', 'short': 'GRS'},
    {'code': 66, 'key': 'sparse',    'name': 'Sparse / no vegetation', 'color': '#9aa4b0', 'short': 'SPR'},
]
WATER = {'code': 77, 'key': 'water', 'name': 'Inland water', 'color': '#2f6bd8', 'short': 'WAT'}
ICE = {'code': 99, 'key': 'ice', 'name': 'No data (permanent ice)', 'color': '#e6ecf5', 'short': 'ICE'}
OCEAN = {'code': 0, 'key': 'ocean', 'name': 'Ocean / outside land mask', 'color': '#0a0e18', 'short': 'SEA'}

# The planes we store per year: the six land use classes, plus water and ice.
# Water is carried because shorelines and reservoirs move, and a cell whose
# water fraction grows has to lose land fraction somewhere. Ice is carried
# because without it Antarctica and Greenland have no fractions at all and
# render as open ocean — a world map missing two ice sheets.
#
# Neither is a land use class and neither is modelled; the automaton only ever
# moves the first N_CLASSES planes.
PLANES = [c['code'] for c in CLASSES] + [WATER['code'], ICE['code']]
PLANE_KEYS = [c['key'] for c in CLASSES] + [WATER['key'], ICE['key']]
N_CLASSES = len(CLASSES)          # 6 — the classes the automaton moves
N_PLANES = len(PLANES)            # 8 — what we store
WATER_PLANE = N_CLASSES           # 6
ICE_PLANE = N_CLASSES + 1         # 7

CODE_INDEX = {c['code']: i for i, c in enumerate(CLASSES)}
KEY_INDEX = {c['key']: i for i, c in enumerate(CLASSES)}
BY_KEY = {c['key']: c for c in CLASSES}
BY_CODE = {c['code']: c for c in CLASSES}

ALL_LEGEND = CLASSES + [WATER, ICE, OCEAN]


def resolve_class(name) -> int:
    """Class index (0-5) from a key, short code, pixel code or index."""
    if isinstance(name, int) or (isinstance(name, str) and str(name).isdigit()):
        n = int(name)
        if n in CODE_INDEX:
            return CODE_INDEX[n]
        if 0 <= n < N_CLASSES:
            return n
        raise ValueError(f'unknown class code {name!r}')
    k = str(name).strip().lower()
    if k in KEY_INDEX:
        return KEY_INDEX[k]
    for i, c in enumerate(CLASSES):
        if c['short'].lower() == k or c['name'].lower().startswith(k):
            return i
    raise ValueError(f'unknown class {name!r}; known: {list(KEY_INDEX)}')


# ── regions ──────────────────────────────────────────────────────────────

# Bounding boxes, west/south/east/north. A bbox is a blunt instrument for a
# continent, but it needs no boundary dataset and it is honest about what it
# is: everything in the box. Any endpoint that takes ``region`` also takes an
# arbitrary ``bbox``.
REGIONS = {
    'global':        {'name': 'Global',              'bbox': [-180.0, -90.0, 180.0, 90.0]},
    'africa':        {'name': 'Africa',              'bbox': [-19.0, -35.0, 52.0, 38.0]},
    'europe':        {'name': 'Europe',              'bbox': [-11.0, 35.0, 40.0, 71.0]},
    'north_america': {'name': 'North America',       'bbox': [-168.0, 15.0, -52.0, 72.0]},
    'south_america': {'name': 'South America',       'bbox': [-82.0, -56.0, -34.0, 13.0]},
    'asia':          {'name': 'Asia',                'bbox': [40.0, -11.0, 150.0, 78.0]},
    'oceania':       {'name': 'Oceania',             'bbox': [110.0, -48.0, 180.0, -10.0]},
    'amazon':        {'name': 'Amazon basin',        'bbox': [-79.0, -20.0, -44.0, 6.0]},
    'congo':         {'name': 'Congo basin',         'bbox': [8.0, -10.0, 32.0, 6.0]},
    'sea_asia':      {'name': 'Southeast Asia',      'bbox': [92.0, -11.0, 141.0, 24.0]},
    'india':         {'name': 'Indian subcontinent', 'bbox': [67.0, 6.0, 92.0, 36.0]},
    'china':         {'name': 'China',               'bbox': [73.0, 18.0, 135.0, 54.0]},
    'us_midwest':    {'name': 'US Midwest',          'bbox': [-104.0, 36.0, -80.0, 49.0]},
    'sahel':         {'name': 'Sahel',               'bbox': [-18.0, 10.0, 43.0, 20.0]},
    'boreal':        {'name': 'Boreal belt',         'bbox': [-180.0, 50.0, 180.0, 70.0]},
    'tropics':       {'name': 'Tropics',             'bbox': [-180.0, -23.5, 180.0, 23.5]},
}


def resolve_bbox(region=None, bbox=None) -> list:
    """A [w, s, e, n] box from a region name, an explicit bbox, or the globe."""
    if bbox is not None:
        if isinstance(bbox, str):
            bbox = [float(x) for x in bbox.replace(' ', '').split(',')]
        b = [float(x) for x in bbox]
        if len(b) != 4:
            raise ValueError('bbox must be w,s,e,n')
        return b
    if region:
        key = str(region).strip().lower().replace(' ', '_').replace('-', '_')
        if key not in REGIONS:
            raise ValueError(f'unknown region {region!r}; known: {list(REGIONS)}')
        return list(REGIONS[key]['bbox'])
    return list(REGIONS['global']['bbox'])


# ── local storage ────────────────────────────────────────────────────────

# Per the fleet convention, everything we cache is user state, not module
# source: it lives under ~/.mod/hilda and is never committed.
CACHE = Path(os.environ.get('HILDA_HOME') or (Path.home() / '.mod' / 'hilda'))
TIF_DIR = CACHE / 'tifs'
CUBE_DIR = CACHE / 'cubes'
RUN_DIR = CACHE / 'runs'


def ensure_dirs() -> None:
    for d in (CACHE, TIF_DIR, CUBE_DIR, RUN_DIR):
        d.mkdir(parents=True, exist_ok=True)


def tif_path(year: int, kind: str = 'states') -> Path:
    return TIF_DIR / f'{kind}_{int(year)}.tif'


def cube_path(kind: str = 'states', deg: float = DEFAULT_DEG) -> Path:
    return CUBE_DIR / f'cube_{kind}_{_degtag(deg)}.npz'


def _degtag(deg: float) -> str:
    return f'{float(deg):g}'.replace('.', 'p')


def attribution() -> dict:
    return {'dataset': 'HILDA+ v1.0 (vGLOB-1.0)', 'citation': CITATION,
            'paper': PAPER_CITATION, 'license': LICENSE, 'doi': DOI,
            'landing': LANDING, 'ceos': CEOS, 'source': 'PANGAEA'}
