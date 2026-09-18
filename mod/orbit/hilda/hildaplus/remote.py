"""
Pull one year of HILDA+ out of a 4.5 GB ZIP without downloading the ZIP.

HILDA+ is published as ZIP archives — the GeoTIFF one is 4.5 GB and holds 244
members: 121 annual state rasters and 59 annual transition rasters, each a
global 36000x18000 uint8 GeoTIFF. Downloading the archive to read one year is
absurd, so we don't.

PANGAEA's file server answers HTTP range requests (206), which is all a ZIP
reader actually needs:

    1. GET the last 64 KB      -> end-of-central-directory (this archive is
                                  ZIP64, so the real offsets are in the ZIP64
                                  EOCD record, not the classic one)
    2. GET the central dir     -> every member's name, size and local offset
    3. GET one member's bytes  -> raw deflate, inflated locally

A single year costs ~19 MB over the wire and about two seconds. The member
index is cached, so step 1 and 2 happen once per machine.

Nothing here needs GDAL, rasterio or an HTTP library: stdlib zlib and urllib
do the whole job.
"""

import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import sources as S

UA = 'mod-hilda/1.0 (+https://modc2.com/hilda)'
TAIL = 65536            # enough for the EOCD, ZIP64 EOCD and locator
INDEX_FILE = 'zip_index.json'


class RemoteError(RuntimeError):
    pass


# ── raw HTTP ─────────────────────────────────────────────────────────────

def _get(url: str, start: Optional[int] = None, end: Optional[int] = None,
         timeout: int = 300, retries: int = 4) -> bytes:
    """GET, optionally a byte range. PANGAEA answers 503 when it is busy and
    asks us to come back — honour that instead of hammering it."""
    headers = {'User-Agent': UA}
    if start is not None:
        headers['Range'] = f'bytes={start}-{"" if end is None else end}'
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if start is not None and r.status != 206:
                    raise RemoteError(f'server ignored Range (status {r.status}); '
                                      'cannot read the archive piecewise')
                return r.read()
        except urllib.error.HTTPError as e:
            # HTTPError carries an open socket and cannot be pickled back to a
            # parent process, so it never leaves this function alive.
            last = f'HTTP {e.code}'
            if e.code in (429, 500, 502, 503, 504):
                wait = int(e.headers.get('retry-after') or 0) or (5 * (attempt + 1))
                time.sleep(min(wait, 30))
                continue
            raise RemoteError(f'{url}: HTTP {e.code} {e.reason}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(e)
            time.sleep(3 * (attempt + 1))
    raise RemoteError(f'{url}: {last}')


def archive_size(url: str = S.ARCHIVE_URL, retries: int = 4) -> int:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method='HEAD',
                                         headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return int(r.headers['content-length'])
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(int(e.headers.get('retry-after') or 0) or
                               5 * (attempt + 1), 30))
                continue
            raise RemoteError(f'{url}: HTTP {e.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(e)
            time.sleep(3 * (attempt + 1))
    raise RemoteError(f'{url}: {last}')


# ── ZIP central directory ────────────────────────────────────────────────

def _parse_central_directory(cd: bytes) -> Dict[str, dict]:
    """Members from a raw central directory, ZIP64 extras resolved."""
    out, p = {}, 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b'PK\x01\x02':
        method = struct.unpack('<H', cd[p + 10:p + 12])[0]
        csize, usize = struct.unpack('<II', cd[p + 20:p + 28])
        nlen, elen, clen = struct.unpack('<HHH', cd[p + 28:p + 34])
        local = struct.unpack('<I', cd[p + 42:p + 46])[0]
        name = cd[p + 46:p + 46 + nlen].decode('utf-8', 'replace')
        extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
        # ZIP64 extended information: fields appear only for the ones that
        # overflowed, in a fixed order.
        q = 0
        while q + 4 <= len(extra):
            hid, hsz = struct.unpack('<HH', extra[q:q + 4])
            if hid == 0x0001:
                f, r = extra[q + 4:q + 4 + hsz], 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack('<Q', f[r:r + 8])[0]; r += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack('<Q', f[r:r + 8])[0]; r += 8
                if local == 0xFFFFFFFF:
                    local = struct.unpack('<Q', f[r:r + 8])[0]; r += 8
            q += 4 + hsz
        if not name.endswith('/'):
            out[name] = {'method': method, 'csize': csize, 'usize': usize,
                         'local': local}
        p += 46 + nlen + elen + clen
    return out


def _locate_central_directory(tail: bytes, total: int) -> Tuple[int, int]:
    """(offset, size) of the central directory, preferring the ZIP64 record."""
    z = tail.rfind(b'PK\x06\x06')
    if z >= 0:
        size = struct.unpack('<Q', tail[z + 40:z + 48])[0]
        off = struct.unpack('<Q', tail[z + 48:z + 56])[0]
        return off, size
    e = tail.rfind(b'PK\x05\x06')
    if e < 0:
        raise RemoteError('no end-of-central-directory found in archive tail')
    size = struct.unpack('<I', tail[e + 12:e + 16])[0]
    off = struct.unpack('<I', tail[e + 16:e + 20])[0]
    return off, size


def index(url: str = S.ARCHIVE_URL, refresh: bool = False) -> Dict[str, dict]:
    """Every member of the archive. Cached on disk after the first call."""
    S.ensure_dirs()
    cache = S.CACHE / INDEX_FILE
    if cache.exists() and not refresh:
        try:
            doc = json.loads(cache.read_text())
            if doc.get('url') == url and doc.get('members'):
                return doc['members']
        except Exception:
            pass
    total = archive_size(url)
    tail = _get(url, max(0, total - TAIL), total - 1)
    base = total - len(tail)
    cd_off, cd_size = _locate_central_directory(tail, total)
    if cd_off >= base:                       # already inside the tail we have
        cd = tail[cd_off - base:cd_off - base + cd_size]
    else:
        cd = _get(url, cd_off, cd_off + cd_size - 1)
    members = _parse_central_directory(cd)
    if not members:
        raise RemoteError('central directory parsed to zero members')
    cache.write_text(json.dumps({'url': url, 'size': total,
                                 'members': members}, indent=1))
    return members


def member_name(year: int, kind: str = 'states') -> str:
    """The archive member holding one year.

    The templates in ``sources`` cover almost every year, but not quite: 2015
    is the base map the whole reconstruction is anchored on and is published
    as ``..._2015_states_GLOB-v1-0_base-map_wgs84-nn.tif``. Rather than special
    case that one file, fall back to matching the year inside the right
    subdirectory — which also absorbs whatever the next revision renames.
    """
    year = int(year)
    if kind not in ('states', 'transitions'):
        raise ValueError(f'kind must be states or transitions, got {kind!r}')
    valid = S.STATE_YEARS if kind == 'states' else S.TRANSITION_YEARS
    if year not in valid:
        raise ValueError(f'no {kind} layer for {year}; HILDA+ {kind} cover '
                         f'{valid.start}-{valid.stop - 1}')
    name = (S.MEMBER['states'].format(year=year) if kind == 'states'
            else S.MEMBER['transitions'].format(year=year, prev=year - 1))
    members = index()
    if name in members:
        return name
    folder = name.rsplit('/', 1)[0] + '/'
    stem = f'{year}_states' if kind == 'states' else f'{year}-{year - 1}_'
    hits = [k for k in members
            if k.startswith(folder) and stem in k and k.endswith('.tif')]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise RemoteError(f'no {kind} member for {year} in the archive')
    raise RemoteError(f'{len(hits)} candidate {kind} members for {year}: {hits}')


def member_bytes(name: str, url: str = S.ARCHIVE_URL) -> bytes:
    """One member, inflated. The local file header has to be read first: its
    name and extra-field lengths differ from the central directory's, so the
    data offset cannot be computed from the central directory alone."""
    m = index(url).get(name)
    if m is None:
        raise RemoteError(f'{name!r} is not in the archive')
    lh = _get(url, m['local'], m['local'] + 29)
    if lh[:4] != b'PK\x03\x04':
        raise RemoteError('bad local file header — stale index? try refresh=True')
    nlen, elen = struct.unpack('<HH', lh[26:30])
    start = m['local'] + 30 + nlen + elen
    raw = _get(url, start, start + m['csize'] - 1)
    if len(raw) != m['csize']:
        raise RemoteError(f'short read: {len(raw)} of {m["csize"]} bytes')
    if m['method'] == 0:
        return raw
    if m['method'] != 8:
        raise RemoteError(f'unsupported compression method {m["method"]}')
    import zlib
    data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw)
    if len(data) != m['usize']:
        raise RemoteError(f'inflated {len(data)} bytes, expected {m["usize"]}')
    return data


# ── the thing callers actually want ──────────────────────────────────────

def fetch_year(year: int, kind: str = 'states', force: bool = False) -> Path:
    """The GeoTIFF for one year, on local disk. ~19 MB and ~2 s if not cached."""
    S.ensure_dirs()
    path = S.tif_path(year, kind)
    if path.exists() and path.stat().st_size > 1_000_000 and not force:
        return path
    data = member_bytes(member_name(year, kind))
    tmp = path.with_suffix('.part')
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def have(year: int, kind: str = 'states') -> bool:
    p = S.tif_path(year, kind)
    return p.exists() and p.stat().st_size > 1_000_000


def cached() -> dict:
    """What we hold locally, and how much room it takes."""
    S.ensure_dirs()
    files = sorted(S.TIF_DIR.glob('*.tif'))
    by_kind: Dict[str, list] = {}
    for f in files:
        kind, _, year = f.stem.rpartition('_')
        by_kind.setdefault(kind, []).append(int(year))
    return {'dir': str(S.TIF_DIR),
            'years': {k: sorted(v) for k, v in by_kind.items()},
            'files': len(files),
            'bytes': sum(f.stat().st_size for f in files)}


def clear(kind: str = '', keep: int = 0) -> dict:
    """Drop cached rasters. ``kind`` scopes it; ``keep`` retains the newest N."""
    S.ensure_dirs()
    files = sorted(S.TIF_DIR.glob(f'{kind}*.tif' if kind else '*.tif'),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    dropped, freed = [], 0
    for f in files[keep:]:
        freed += f.stat().st_size
        f.unlink()
        dropped.append(f.name)
    return {'dropped': len(dropped), 'freed_bytes': freed, 'files': dropped[:20]}
