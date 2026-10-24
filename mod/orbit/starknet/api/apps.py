"""starknet/api/apps.py — every app on Starknet, one submod each.

An app is a submod of starknet at  mods/<name>/  (core names it
starknet.<name>: the `mods` folder is dropped from the path):

    mods/<name>/config.json   name, description, fns, and an "app" block:
                                title icon tone tag pitch order
                                kind  page   a page of this Next app   (page: "/stablecoins")
                                      mod    a sibling mod's own app   (mod: "cairo")
                                      html   mods/<name>/app/index.html, served at
                                             /starknet/apps/<name>/  — no build step
    mods/<name>/mod.py        class Mod; facts() puts numbers on its card, every
                              other fn listed in config "fns" is callable at
                              /_api/apps/<name>/<fn>
    mods/<name>/README.md

So the Apps tab, the API and `m starknet.<name>/<fn>` all read the same files,
and a new app is one new() away (templates in api/app_template/).

    apps()                        every app, with live numbers
    app(name)                     one app + its readme and files
    call(name, fn, args)          run one of its fns
    new(name, title, pitch, ...)  scaffold an html app   (owner)
    remove(name)                  delete one you made     (owner)
"""

import importlib.util
import json
import os
import re
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

API = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(API)
MODS = os.path.join(ROOT, 'mods')
TEMPLATE = os.path.join(API, 'app_template')
ORBIT = os.path.dirname(ROOT)
PRIVATE = os.path.expanduser('~/.mod/build/private')
OVERRIDES = os.path.expanduser('~/.mod/caddy/overrides.json')
BASE = '/starknet'
TTL = 15
FACTS_TIMEOUT = 6
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,30}$')
RESERVED = {'new', 'remove', 'apps', 'api', '_api', 'mods', 'app', 'static', 'template'}
KINDS = ('page', 'mod', 'html')
TONES = ['#ff2a6d', '#00f0ff', '#39ff88', '#facc15', '#8b7cf6', '#f97316', '#22d3ee', '#f472b6']

_pool = ThreadPoolExecutor(8)          # lives with the process: a slow probe can't block the next list
_cache = {'at': 0, 'data': None}
_mods = {}                             # name -> (mtime, instance)


class Denied(Exception):
    pass


class AppError(Exception):
    pass


# ── helpers the submods use ──────────────────────────────────────────────────

def get(url, body=None, timeout=2.5):
    """JSON from a sibling mod's API (POST when body is given)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read() or b'null')
    return out.get('result', out) if isinstance(out, dict) and body is not None else out


def num(v, d=2):
    if not isinstance(v, (int, float)):
        return str(v)
    return f'{v:,.0f}' if v == int(v) else f'{v:,.{d}f}'


def short(a):
    return f'{a[:6]}…{a[-4:]}' if isinstance(a, str) and len(a) > 18 else a


def port_of(mod):
    return _json(os.path.join(ORBIT, mod, 'config.json')).get('port')


# ── reading the submods ──────────────────────────────────────────────────────

def _json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — absent or broken = empty
        return {}


def _names():
    if not os.path.isdir(MODS):
        return []
    return sorted(n for n in os.listdir(MODS)
                  if os.path.isfile(os.path.join(MODS, n, 'config.json')))


def _check(name):
    if not isinstance(name, str) or not NAME_RE.match(name) or name in RESERVED:
        raise AppError(f'bad app name {name!r}: lowercase letters, digits and _ '
                       f'(2-31 chars, starts with a letter), not {sorted(RESERVED)}')
    return name


def config(name):
    cfg = _json(os.path.join(MODS, _check(name), 'config.json'))
    if not cfg:
        raise AppError(f'no app {name!r} — see GET /apps')
    return cfg


def instance(name):
    """The submod's Mod(), reloaded whenever its mod.py changes."""
    f = os.path.join(MODS, _check(name), 'mod.py')
    if not os.path.isfile(f):
        return None
    mt = os.path.getmtime(f)
    hit = _mods.get(name)
    if hit and hit[0] == mt:
        return hit[1]
    spec = importlib.util.spec_from_file_location(f'starknet_app_{name}', f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    obj = m.Mod()
    _mods[name] = (mt, obj)
    return obj


def _private(mod):
    return bool(_json(os.path.join(PRIVATE, f'{mod}.json')).get('enabled'))


def _routed():
    o = _json(OVERRIDES)
    return set(o) if isinstance(o, (dict, list)) else set()


def _row(name, routed):
    cfg = config(name)
    a = cfg.get('app') or {}
    kind = a.get('kind') if a.get('kind') in KINDS else 'html'
    row = {'name': name, 'mod': cfg.get('name') or f'starknet.{name}', 'kind': kind,
           'title': a.get('title') or name, 'icon': a.get('icon') or name[0].upper(),
           'tone': a.get('tone') or TONES[sum(map(ord, name)) % len(TONES)],
           'tag': a.get('tag') or 'app', 'pitch': a.get('pitch') or cfg.get('description', ''),
           'order': a.get('order', 100), 'version': cfg.get('version'),
           'fns': [f for f in cfg.get('fns', []) if f != 'facts'],
           'write_fns': cfg.get('write_fns', []), 'created': a.get('created'),
           'address': f'{BASE}/apps/{name}/', 'href': None, 'page': None, 'public': True,
           'linked': None, 'port': None, 'up': True, 'facts': [], 'items': []}
    if kind == 'page':
        row['page'] = a.get('page') or f'/{name}'
        row['href'] = BASE + row['page']
    elif kind == 'html':
        row['href'] = row['address']
        row['up'] = os.path.isfile(os.path.join(MODS, name, 'app', 'index.html'))
    else:
        linked = a.get('mod') or name
        lc = _json(os.path.join(ORBIT, linked, 'config.json'))
        base = None if a.get('has_app') is False else \
            lc.get('base_path') or (f'/{linked}' if lc.get('app_port') else None)
        row.update(linked=linked, port=lc.get('port'), version=lc.get('version') or row['version'],
                   public=bool(linked in routed or (lc.get('route') and not _private(linked))),
                   href=f'{base}/' if base else None)
        row['app'] = row['href']           # the old /mods shape
    return row


def _facts(row):
    """Run the submod's facts() — {facts, items} or a bare [[label, value]] list."""
    m = instance(row['name'])
    if not m or not hasattr(m, 'facts'):
        return {}
    out = m.facts()
    return {'facts': out} if isinstance(out, list) else (out or {})


def apps(fresh=False):
    if not fresh and _cache['data'] and time.time() - _cache['at'] < TTL:
        return _cache['data']
    routed = _routed()
    rows = []
    for n in _names():
        try:
            rows.append(_row(n, routed))
        except Exception as e:  # noqa: BLE001 — one broken app never hides the rest
            rows.append({'name': n, 'title': n, 'kind': 'html', 'up': False, 'error': str(e),
                         'facts': [], 'items': [], 'fns': [], 'order': 999, 'tag': 'broken',
                         'icon': '!', 'tone': '#ff2a6d', 'pitch': '', 'address': f'{BASE}/apps/{n}/'})
    t0 = time.time()
    futs = [(r, _pool.submit(_facts, r)) for r in rows if 'error' not in r]
    for r, f in futs:
        try:
            r.update(f.result(timeout=max(.1, FACTS_TIMEOUT - (time.time() - t0))))
        except FutTimeout:
            r['slow'] = True
        except Exception as e:  # noqa: BLE001 — a down mod is reported, not fatal
            r['up'] = False
            r['error'] = str(e)[:160]
    rows.sort(key=lambda r: (r.get('order', 100), r['name']))
    data = {'apps': rows, 'count': len(rows), 'up': sum(1 for r in rows if r.get('up')),
            'tags': sorted({r.get('tag') for r in rows if r.get('tag')}),
            'how': HOW, 'at': int(time.time())}
    _cache.update(at=time.time(), data=data)
    return data


def app(name):
    row = next((r for r in apps()['apps'] if r['name'] == name), None) or _row(name, _routed())
    d = os.path.join(MODS, name)
    files = sorted(os.path.relpath(os.path.join(p, f), d)
                   for p, ds, fs in os.walk(d) for f in fs
                   if '__pycache__' not in p)
    readme = os.path.join(d, 'README.md')
    return {**row, 'path': d, 'files': files,
            'readme': open(readme).read() if os.path.isfile(readme) else None}


def call(name, fn, args=None, writable=False):
    cfg = config(name)
    if fn not in cfg.get('fns', []):
        raise AppError(f'{name} has no fn {fn!r} — it has {cfg.get("fns", [])}')
    if fn in cfg.get('write_fns', []) and not writable:
        raise Denied(f'{name}.{fn} changes things: owner only (sign in, or call from this box)')
    m = instance(name)
    if not m or not hasattr(m, fn):
        raise AppError(f'{name}/mod.py has no {fn}()')
    args = {k: v for k, v in (args or {}).items() if k not in ('network',) or v}
    return getattr(m, fn)(**args)


# ── making one ───────────────────────────────────────────────────────────────

def _render(text, ctx):
    for k, v in ctx.items():
        text = text.replace('{{' + k + '}}', v)
    return text


def new(name, title=None, pitch=None, icon=None, tag='yours', tone=None, writable=False):
    """Scaffold mods/<name>/ from api/app_template: an html app with a mod.py
    behind it. It shows up in the Apps tab at once; no build, no restart."""
    if not writable:
        raise Denied('making an app writes files on this box: owner only')
    _check(name)
    d = os.path.join(MODS, name)
    if os.path.exists(d):
        raise AppError(f'app {name!r} already exists')
    title = (title or name.replace('_', ' ').title()).strip()[:40]
    ctx = {'name': name, 'title': title,
           'pitch': (pitch or f'{title}: a new app on Starknet.').strip()[:280],
           'icon': (icon or title[0]).strip()[:2].upper(),
           'tag': re.sub(r'[^a-z0-9-]', '', (tag or 'yours').lower())[:16] or 'yours',
           'tone': tone if isinstance(tone, str) and re.match(r'^#[0-9a-fA-F]{6}$', tone)
           else TONES[sum(map(ord, name)) % len(TONES)],
           'created': time.strftime('%Y-%m-%d')}
    # the same text lands in JSON, a Python docstring and HTML: drop what breaks any of them
    ctx = {k: re.sub(r'["\\<>{}`]', '', v) if k in ('title', 'pitch', 'icon') else v
           for k, v in ctx.items()}
    for src_dir, _, files in os.walk(TEMPLATE):
        rel = os.path.relpath(src_dir, TEMPLATE)
        os.makedirs(os.path.join(d, rel), exist_ok=True)
        for f in files:
            out = f[:-5] if f.endswith('.tmpl') else f
            with open(os.path.join(src_dir, f)) as fh:
                body = _render(fh.read(), ctx)
            with open(os.path.join(d, rel, out), 'w') as fh:
                fh.write(body)
    _cache['data'] = None
    return app(name)


def remove(name, writable=False):
    if not writable:
        raise Denied('removing an app deletes files on this box: owner only')
    cfg = config(name)
    if not (cfg.get('app') or {}).get('created'):
        raise AppError(f'{name} is a built-in app; only apps made with new() can be removed here')
    shutil.rmtree(os.path.join(MODS, name))
    _mods.pop(name, None)
    _cache['data'] = None
    return {'removed': name}


def static(name, rest):
    """The file under mods/<name>/app/ for /starknet/apps/<name>/<rest>, or None."""
    root = os.path.realpath(os.path.join(MODS, _check(name), 'app'))
    f = os.path.realpath(os.path.join(root, rest or 'index.html'))
    if os.path.isdir(f):
        f = os.path.join(f, 'index.html')
    return f if f.startswith(root + os.sep) and os.path.isfile(f) else None


HOW = {
    'steps': [
        'Make it: the + New app card in the Apps tab, or  m starknet/new_app name=my_app title="My app"  '
        '(or POST /starknet/_api/apps/new {name, title, pitch}).',
        'Edit mods/<name>/mod.py: add a function, list it in config.json "fns", '
        'and it is live at /starknet/_api/apps/<name>/<fn> — no restart.',
        'Edit mods/<name>/app/index.html: it is your app, served at /starknet/apps/<name>/. '
        'Call your fns with  api("fn", {args}) .',
    ],
    'tree': ['mods/<name>/config.json   title, icon, tag, pitch, fns',
             'mods/<name>/mod.py        class Mod: facts() + your fns',
             'mods/<name>/app/index.html  the app (plain HTML, no build)',
             'mods/<name>/README.md'],
}


if __name__ == '__main__':
    print(json.dumps(apps(), indent=2, default=str))
