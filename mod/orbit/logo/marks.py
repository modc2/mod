"""
The marks themselves: what is stored, what is served, and what is mirrored.

A mark is one of four things —

    cube     nothing set. The mod protocol's own cube is the answer, and it is
             what a module should look like until its owner says otherwise.
    glyph    one to four characters. The cheapest possible logo.
    url      an image somebody else hosts.
    image    bytes uploaded here, served back from here, so the mark survives
             whatever host it came from.

Where it lives: `~/.mod/logo/marks/{group}/{name}.json`, with an upload's bytes
beside it as `{name}.{ext}`. Not in the module's config.json — config is a
manifest, checked in, read by the registry and rewritten by other processes,
and a 300KB data URI has no business in it. A SHORT mark (a glyph, or a URL)
is additionally *mirrored* into the target module's config.json `logo` field
so the fleet's catalogs can put the same mark on their module cards; an upload
mirrors as the path that serves it.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import identity

STATE = identity.STATE
MARKS = STATE / 'marks'

# Images we will store and serve back. SVG is allowed but served under a
# `default-src 'none'` CSP, so a scripted SVG cannot run against this origin
# even if someone opens the file's URL directly.
ALLOWED_MIME = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/svg+xml': 'svg',
}

# 512KB of decoded bytes. A header mark that needs more than that is a mark
# that should be hosted, not pasted.
MAX_IMAGE_BYTES = int(os.environ.get('LOGO_MAX_IMAGE_BYTES', 512 * 1024))
MAX_GLYPH_CHARS = 4

# The origin-relative prefix an uploaded mark is served from. An absolute path
# rather than a baked-in host, so one stored value is correct on modc2.com, on
# a bare port and behind anybody else's gateway.
PUBLIC_BASE = os.environ.get('LOGO_PUBLIC_BASE', '/logo/_api').rstrip('/')

CUBE: Dict[str, Any] = {'kind': 'cube'}

CONTROL = re.compile(r'[\x00-\x1f\x7f]')
DATA_URL = re.compile(r'^data:([a-z0-9.+/-]+);base64,([\s\S]+)$', re.I)


class BadMark(Exception):
    """The mark the caller asked for is not one we will store."""


# -- paths ------------------------------------------------------------

def _dir(group: str) -> Path:
    return MARKS / group


def _state_path(group: str, name: str) -> Path:
    return _dir(group) / f'{name}.json'


def _image_path(group: str, name: str, ext: str) -> Path:
    return _dir(group) / f'{name}.{ext}'


# -- read -------------------------------------------------------------

def read(module: str) -> Dict[str, Any]:
    """The stored mark, or the cube. An `image` whose file went missing falls
    back rather than 404ing a broken <img> into somebody's corner forever."""
    group, name, _ = identity.resolve(module)
    try:
        parsed = json.loads(_state_path(group, name).read_text())
    except Exception:
        return dict(CUBE)
    if not isinstance(parsed, dict) or not isinstance(parsed.get('kind'), str):
        return dict(CUBE)
    if parsed['kind'] == 'image':
        file = parsed.get('file')
        if not file or not (_dir(group) / file).is_file():
            return dict(CUBE)
    return parsed


def image_bytes(module: str) -> Optional[Tuple[bytes, str]]:
    group, name, _ = identity.resolve(module)
    state = read(module)
    if state.get('kind') != 'image' or not state.get('file'):
        return None
    path = _dir(group) / state['file']
    if not path.is_file():
        return None
    return path.read_bytes(), state.get('mime') or 'application/octet-stream'


def public(module: str, state: Optional[Dict[str, Any]] = None,
           base: Optional[str] = None) -> Dict[str, Any]:
    """What a client should draw: a glyph, a `src`, or nothing (draw the cube).

    `updated` doubles as the cache-buster, so an upload may be cached hard and
    still be replaced the moment the owner changes it.
    """
    group, name, _ = identity.resolve(module)
    state = read(module) if state is None else state
    base = (base if base is not None else PUBLIC_BASE).rstrip('/')
    updated = state.get('updated')
    out: Dict[str, Any] = {'kind': 'cube', 'updated': updated}
    if state.get('kind') == 'glyph' and state.get('glyph'):
        out = {'kind': 'glyph', 'glyph': state['glyph'], 'updated': updated}
    elif state.get('kind') == 'url' and state.get('url'):
        out = {'kind': 'url', 'src': state['url'], 'updated': updated}
    elif state.get('kind') == 'image' and state.get('file'):
        out = {'kind': 'image', 'mime': state.get('mime'),
               'src': f'{base}/logo/{group}/{name}/image?v={updated or 0}',
               'updated': updated}
    if state.get('by'):
        out['by'] = state['by']
    return out


def marks(base: Optional[str] = None) -> list:
    """Every module that has a mark set. The cube is the default, not a mark,
    so a module that never set one does not appear here."""
    out = []
    if not MARKS.is_dir():
        return out
    for group_dir in sorted(MARKS.iterdir()):
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.glob('*.json')):
            key = f'{group_dir.name}/{path.stem}'
            try:
                state = read(key)
            except identity.UnknownModule:
                continue          # the module was deleted; its mark is stale
            if state.get('kind') == 'cube':
                continue
            out.append({'module': key, 'name': path.stem, 'group': group_dir.name,
                        'logo': public(key, state, base)})
    return out


# -- validate ---------------------------------------------------------

def clean_glyph(value: Any) -> str:
    """One to four visible characters. Longer "glyphs" are a wordmark, and a
    wordmark does not fit a 30px corner — say so instead of clipping it."""
    if not isinstance(value, str):
        raise BadMark('a glyph is text')
    glyph = CONTROL.sub('', value).strip()
    if not glyph:
        raise BadMark('that glyph is empty')
    if len(glyph) > MAX_GLYPH_CHARS:
        raise BadMark(f'a glyph is 1-{MAX_GLYPH_CHARS} characters — '
                      'for anything longer, use an image')
    return glyph


def clean_url(value: Any) -> str:
    from urllib.parse import urlparse
    if not isinstance(value, str) or len(value) > 2048:
        raise BadMark("that isn't an http(s) image URL")
    parsed = urlparse(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise BadMark("that isn't an http(s) image URL")
    return value.strip()


def decode_data_url(value: Any) -> Tuple[bytes, str]:
    """`data:image/png;base64,...` -> bytes, refusing anything we won't serve back."""
    import base64
    import binascii
    if not isinstance(value, str):
        raise BadMark('the image must be a data: URL string')
    match = DATA_URL.match(value.strip())
    if not match:
        raise BadMark('expected a base64 data: URL')
    mime = match.group(1).lower()
    if mime not in ALLOWED_MIME:
        raise BadMark(f'unsupported image type {mime} — png, jpeg, webp, gif or svg')
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise BadMark('could not decode the image')
    if not raw:
        raise BadMark('the image is empty')
    if len(raw) > MAX_IMAGE_BYTES:
        raise BadMark(f'image is {round(len(raw) / 1024)}KB — the limit is '
                      f'{MAX_IMAGE_BYTES // 1024}KB, host it and paste the URL instead')
    return raw, mime


# -- write ------------------------------------------------------------

def write(module: str, state: Dict[str, Any], mirror: bool = True) -> Dict[str, Any]:
    group, name, path = identity.resolve(module)
    _dir(group).mkdir(parents=True, exist_ok=True)
    _state_path(group, name).write_text(json.dumps(state, indent=2, ensure_ascii=False))
    if mirror:
        mirror_to_config(path, public(module, state))
    return state


def save_image(group: str, name: str, raw: bytes, mime: str) -> str:
    """Store uploaded bytes, replacing whatever the last upload left behind."""
    ext = ALLOWED_MIME[mime]
    _dir(group).mkdir(parents=True, exist_ok=True)
    for other in set(ALLOWED_MIME.values()):
        if other != ext:
            stale = _image_path(group, name, other)
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass          # best effort; the state file is the truth
    file = f'{name}.{ext}'
    (_dir(group) / file).write_bytes(raw)
    return file


def apply(module: str, body: Dict[str, Any], by: str) -> Dict[str, Any]:
    """One request body -> one stored mark. The caller has already been
    authorized; this only decides *what* was asked for."""
    group, name, _ = identity.resolve(module)
    now = int(time.time() * 1000)
    if body.get('reset') is True or body.get('kind') == 'cube':
        state = {**CUBE, 'updated': now, 'by': by}
    elif body.get('glyph') is not None:
        state = {'kind': 'glyph', 'glyph': clean_glyph(body['glyph']),
                 'updated': now, 'by': by}
    elif body.get('url') is not None:
        state = {'kind': 'url', 'url': clean_url(body['url']), 'updated': now, 'by': by}
    elif body.get('dataUrl') is not None or body.get('data_url') is not None:
        raw, mime = decode_data_url(body.get('dataUrl') or body.get('data_url'))
        state = {'kind': 'image', 'file': save_image(group, name, raw, mime),
                 'mime': mime, 'bytes': len(raw), 'updated': now, 'by': by}
    else:
        raise BadMark('send one of: glyph, url, dataUrl, or reset:true')
    return write(module, state)


# -- the config.json mirror -------------------------------------------

LOGO_LINE = re.compile(r'^([ \t]*)"logo"\s*:\s*("(?:[^"\\]|\\.)*")(,?)[ \t]*\r?\n', re.M)
ICON_LINE = re.compile(r'^([ \t]*)"icon"\s*:[^\n]*\r?\n', re.M)


def mirror_to_config(module_path: Path, pub: Dict[str, Any]) -> bool:
    """Mirror the mark into the target module's config.json `logo` field so
    module catalogs elsewhere in the fleet can show it on their cards.

    Surgical on purpose: it edits (or inserts, or drops) the ONE line and
    leaves the rest of the manifest byte-identical. A parse-and-reserialize
    would reformat a file that other processes are editing at the same time
    (the registry rewrites `schema` on its own), and would silently no-op the
    day the file's style drifted from ours.

    Never raises. `~/.mod/logo/marks/...` is the source of truth, and a
    read-only or mid-write manifest must not fail a save.
    """
    try:
        path = identity.manifest_path(module_path)
        if path is None:
            return False
        raw = path.read_text()
        json.loads(raw)                    # refuse to touch a manifest mid-write
        value = (pub.get('glyph') if pub['kind'] == 'glyph'
                 else pub.get('src') if pub['kind'] in ('url', 'image')
                 else None)

        if LOGO_LINE.search(raw):
            if value is None:
                nxt = LOGO_LINE.sub('', raw, count=1)
            else:
                def swap(m):
                    return f'{m.group(1)}"logo": {json.dumps(value)}{m.group(3)}\n'
                nxt = LOGO_LINE.sub(swap, raw, count=1)
        elif value is None:
            return False                   # nothing to clear
        else:
            icon = ICON_LINE.search(raw)
            if icon:
                at = icon.end()
                nxt = raw[:at] + f'{icon.group(1)}"logo": {json.dumps(value)},\n' + raw[at:]
            else:
                brace = raw.find('{')
                newline = raw.find('\n', brace) if brace >= 0 else -1
                if newline < 0:
                    return False
                nxt = (raw[:newline + 1] + f'    "logo": {json.dumps(value)},\n'
                       + raw[newline + 1:])

        # Only write something that is still valid JSON and still says what we
        # meant — a bad splice must not land on somebody's manifest.
        parsed = json.loads(nxt)
        if value is None and parsed.get('logo') is not None:
            return False
        if value is not None and parsed.get('logo') != value:
            return False
        if nxt != raw:
            path.write_text(nxt)
            return True
        return False
    except Exception:
        return False
