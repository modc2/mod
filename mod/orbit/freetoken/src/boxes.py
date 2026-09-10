"""The boxes — every FreeToken engine this module can reach.

FreeToken runs where the GPU is, which is very often not where you are sitting.
Both of its own tools already know that: `ft ctl --base-url` and `ft shell
--server` drive a server over HTTP with no GPU on the client. A box here is
that URL pair, named and remembered:

    url     the serve process   (default :1919) — OpenAI + Anthropic + control
    daemon  the control daemon  (default :1900) — start/stop/switch a model

A box with only `url` can be read and generated from. A box that also has a
reachable `daemon` can be told to load a different model, from anywhere.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src import state

FILE = 'boxes.json'
SERVE_PORT = 1919      # ft serve --port default
DAEMON_PORT = 1900     # ft daemon --port default
LOCAL_URL = f'http://127.0.0.1:{SERVE_PORT}'
LOCAL_DAEMON = f'http://127.0.0.1:{DAEMON_PORT}'


def _seed() -> Dict[str, Any]:
    """First read on a fresh box: assume the engine, if any, is the local one.

    Nothing is contacted here — an unreachable `local` is the honest default,
    not an error, and `m freetoken/boxes` will say so.
    """
    return {'default': 'local',
            'boxes': [{'name': 'local', 'url': LOCAL_URL, 'daemon': LOCAL_DAEMON,
                       'token': None, 'note': 'this machine, ft serve defaults',
                       'added': int(time.time())}]}


def _load() -> Dict[str, Any]:
    data = state.read(FILE)
    if not isinstance(data, dict) or 'boxes' not in data:
        data = _seed()
        state.write(FILE, data, private=True)
    return data


def _save(data: Dict[str, Any]) -> Dict[str, Any]:
    return state.write(FILE, data, private=True)


def _norm(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip().rstrip('/')
    if not url:
        return None
    return url if '://' in url else f'http://{url}'


def all() -> List[Dict[str, Any]]:
    return _load()['boxes']


def get(name: str) -> Dict[str, Any]:
    for box in all():
        if box['name'] == name:
            return box
    raise KeyError(f'no box named {name!r} — see m freetoken/boxes')


def default_name() -> str:
    data = _load()
    names = [b['name'] for b in data['boxes']]
    if data.get('default') in names:
        return data['default']
    if not names:
        raise KeyError('no boxes registered — m freetoken/add_box name=gpu url=http://host:1919')
    return names[0]


def resolve(target: Optional[str] = None) -> Dict[str, Any]:
    """A name, a bare URL, or nothing at all (the default box)."""
    if not target:
        return get(default_name())
    if '://' in target or target.replace('.', '').replace(':', '').isdigit():
        url = _norm(target)
        return {'name': url, 'url': url, 'daemon': None, 'token': None,
                'note': 'ad hoc', 'added': None}
    return get(target)


def add(name: str, url: str = None, daemon: str = None, token: str = None,
        note: str = '', use: bool = False) -> Dict[str, Any]:
    """Register a box. `url` defaults to the serve port on the same host as `daemon`."""
    url, daemon = _norm(url), _norm(daemon)
    if not url and not daemon:
        raise ValueError('a box needs a url (:1919) or a daemon (:1900)')
    if not url and daemon:
        url = daemon.rsplit(':', 1)[0] + f':{SERVE_PORT}'
    data = _load()
    box = {'name': name, 'url': url, 'daemon': daemon, 'token': token or None,
           'note': note, 'added': int(time.time())}
    data['boxes'] = [b for b in data['boxes'] if b['name'] != name] + [box]
    if use or len(data['boxes']) == 1:
        data['default'] = name
    _save(data)
    return _mask(box)


def drop(name: str) -> Dict[str, Any]:
    data = _load()
    before = len(data['boxes'])
    data['boxes'] = [b for b in data['boxes'] if b['name'] != name]
    if data.get('default') == name:
        data['default'] = data['boxes'][0]['name'] if data['boxes'] else None
    _save(data)
    return {'dropped': before != len(data['boxes']), 'name': name,
            'default': data.get('default')}


def use(name: str) -> Dict[str, Any]:
    get(name)                                   # raises if unknown
    data = _load()
    data['default'] = name
    _save(data)
    return {'default': name}


def _mask(box: Dict[str, Any]) -> Dict[str, Any]:
    """The token never leaves the state directory."""
    out = dict(box)
    out['token'] = '••••' if box.get('token') else None
    return out


def listing() -> Dict[str, Any]:
    name = None
    try:
        name = default_name()
    except KeyError:
        pass
    return {'default': name, 'boxes': [_mask(b) for b in all()],
            'state': str(state.home() / FILE)}
