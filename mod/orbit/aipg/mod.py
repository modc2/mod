"""aipg — the fleet's handle on AI Power Grid, the community-powered AI grid.

    m aipg/info                          what this is, and what it can reach
    m aipg/grid                          the whole grid on one screen
    m aipg/models                        every model, typed, online-or-not, priced
    m aipg/models type=image             only the image models
    m aipg/workers                       who is actually serving right now
    m aipg/pricing model=gpt-oss-120b    what one million tokens costs
    m aipg/set_key sk-...                store a key in ~/.mod/aipg (0600)
    m aipg/credits                       what is left on that key
    m aipg/ask "what is a GPU"           one turn against the grid
    m aipg/image "a tin robot"           one image, saved to ~/.mod/aipg/out
    m aipg/video "a tin robot waving"    one clip, same place

AI Power Grid (aipowergrid.io) is a network of community-run workers that serve
open-weight models for text, image, video and audio, paid in USD-denominated
credits. Its API is OpenAI-shaped: POST /v1/chat/completions and a key is all
most callers need.

Two facts shape this module, and both were measured rather than assumed:

  * `/v1/models` is the TEXT surface only — 6 ids the day this was written,
    while `/v1/status/models` listed 12 across text, image, video and audio.
    An OpenAI client pointed at the grid cannot see the image or video models
    at all. So the roster here comes from `/v1/status/models`, and `/v1/models`
    is reduced to one boolean per row: openai_visible.

  * a price is not availability. The upstream price book says so itself:
    "Configured rates do not assert that a model is online." Online means a
    worker is holding the weights, which only `/v1/status/models` and
    `/v1/workers` know. This module joins the two so that one row answers both
    questions, and joins them case-insensitively, because the grid spells the
    same model "FLUX.2 Klein 4B FP8" in the roster and "flux.2 klein 4b fp8"
    in the price book.

Stdlib only — urllib and http.server. `serve()` puts the fn surface on :50147
in the mod protocol's wire shape. The public reads need no key;
generation and credits need one, and it lives in ~/.mod/aipg, never in the repo.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

# Never `import mod` here. Every mod in the fleet ships a mod.py, so whichever
# directory leads sys.path decides what `import mod` means — and inside this
# file, that is usually this file. Everything below is stdlib for that reason.

HERE = Path(__file__).resolve().parent
BASE = os.environ.get('AIPG_API', 'https://api.aipowergrid.io/v1').rstrip('/')
STATE = Path(os.environ.get('AIPG_DIR', Path.home() / '.mod' / 'aipg'))
KEY_FILE = STATE / 'key.json'
OUT = STATE / 'out'
UA = 'mod-aipg/0.2.0'
TIMEOUT = float(os.environ.get('AIPG_TIMEOUT', 180))
CONFIG = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
PORT = int(os.environ.get('AIPG_PORT', CONFIG.get('port', 50147)))
HOST = os.environ.get('AIPG_HOST', '127.0.0.1')
# reachable by anyone who can reach the port: the free reads
PUBLIC = set(CONFIG.get('public_fns', []))
# never over HTTP, from anywhere: it would hand out the raw key, or re-serve
NEVER = {'key', 'serve', 'call'}

# which rate in the price book actually applies, per job type
UNIT = {
    'text': ('input_per_mtok_usd', 'output_per_mtok_usd'),
    'image': ('per_image_usd',),
    'video': ('per_video_second_usd',),
    'audio': ('per_audio_second_usd',),
    '3d': ('per_3d_generation_usd',),
}


class NoKey(Exception):
    """No API key on this machine. console.aipowergrid.io issues them."""


class Refused(Exception):
    """The grid answered, and the answer was an error."""

    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f'{status} from {path}: {body[:400]}')
        self.status, self.body, self.path = status, body, path


class Mod:
    description = ('A handle on AI Power Grid — the community-powered grid of '
                   'open-weight models for text, image, video and audio, served '
                   'behind an OpenAI-shaped API and paid in USD credits. Reports '
                   'the real roster (the one /v1/models hides), joins it to the '
                   'price book and the live worker list so one row says both what '
                   'a model costs and whether anyone is actually serving it, and '
                   'drives chat, image, video and audio generation with a key kept '
                   'off this repo in ~/.mod/aipg.')
    path = str(HERE)
    upstream = 'https://aipowergrid.io'
    api = BASE
    docs = 'https://aipowergrid.io/docs/developers'
    console = 'https://console.aipowergrid.io'

    # ── what this is ─────────────────────────────────────────────────

    def forward(self, **kwargs: Any) -> Dict[str, Any]:
        """The null call: the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        """What this module is and what it can reach."""
        return {
            'name': 'aipg',
            'description': self.description,
            'path': self.path,
            'api': BASE,
            'upstream': self.upstream,
            'docs': self.docs,
            'state': str(STATE),
            'key': self.key_status(),
            'public_reads': ['models', 'workers', 'pricing'],
            'needs_key': ['ask', 'chat', 'image', 'video', 'audio', 'credits'],
            'fns': self.fns(),
        }

    def fns(self) -> List[str]:
        """Every callable function on this module."""
        return [f for f in dir(self)
                if not f.startswith('_') and callable(getattr(self, f))]

    def readme(self) -> Optional[str]:
        """The README, as text."""
        return self._doc('README.md')

    def skill(self) -> Optional[str]:
        """The skill sheet, as text."""
        return self._doc('skill.md')

    def _doc(self, name: str) -> Optional[str]:
        p = HERE / name
        return p.read_text(encoding='utf-8') if p.exists() else None

    # ── the key ──────────────────────────────────────────────────────

    def set_key(self, key: str) -> Dict[str, Any]:
        """Store an API key at ~/.mod/aipg/key.json, readable only by you.

        Written with os.open(0o600) rather than a plain write: this is a
        bearer credential that spends real credits, and the default umask is
        not a permission model. Keys come from console.aipowergrid.io.
        """
        key = str(key).strip()
        if not key:
            raise ValueError('empty key')
        STATE.mkdir(parents=True, exist_ok=True)
        blob = json.dumps({'key': key, 'set_at': int(time.time())}, indent=2)
        fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(blob)
        return {'stored': str(KEY_FILE), 'mode': '0600', 'key': self._mask(key)}

    def forget_key(self) -> Dict[str, Any]:
        """Delete the stored key."""
        existed = KEY_FILE.exists()
        if existed:
            KEY_FILE.unlink()
        return {'removed': existed, 'path': str(KEY_FILE),
                'env_still_set': bool(os.environ.get('AIPG_API_KEY'))}

    def key(self) -> str:
        """The key in force: AIPG_API_KEY if set, else the stored one."""
        env = os.environ.get('AIPG_API_KEY')
        if env:
            return env.strip()
        if KEY_FILE.exists():
            try:
                got = json.loads(KEY_FILE.read_text(encoding='utf-8')).get('key')
            except (ValueError, OSError) as e:
                raise NoKey(f'{KEY_FILE} is unreadable: {e}') from e
            if got:
                return str(got).strip()
        raise NoKey(f'no AIPG key — get one at {self.console}, then: '
                    'm aipg/set_key <key>  (or export AIPG_API_KEY)')

    def key_status(self) -> Dict[str, Any]:
        """Whether a key is present, and where it came from. Never the key."""
        if os.environ.get('AIPG_API_KEY'):
            return {'present': True, 'source': 'AIPG_API_KEY',
                    'key': self._mask(os.environ['AIPG_API_KEY'])}
        if KEY_FILE.exists():
            try:
                k = json.loads(KEY_FILE.read_text(encoding='utf-8')).get('key', '')
            except (ValueError, OSError):
                return {'present': False, 'source': str(KEY_FILE),
                        'error': 'unreadable'}
            return {'present': bool(k), 'source': str(KEY_FILE),
                    'key': self._mask(k)}
        return {'present': False, 'source': None, 'get_one': self.console}

    @staticmethod
    def _mask(key: str) -> str:
        k = str(key)
        return f'{k[:4]}…{k[-4:]}' if len(k) > 12 else '…'

    # ── the wire ─────────────────────────────────────────────────────

    def _req(self, path: str, body: Optional[Dict] = None, keyed: bool = False,
             raw: bool = False, stream: bool = False) -> Any:
        """One request. Keyed or not, JSON or bytes, streaming or not."""
        url = f'{BASE}/{path.lstrip("/")}'
        headers = {'User-Agent': UA, 'Accept': 'application/json'}
        if keyed:
            headers['Authorization'] = f'Bearer {self.key()}'
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if data else 'GET')
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')
            if e.code == 401:
                raise NoKey(f'the grid rejected the key: {detail[:200]} — '
                            f'get one at {self.console}') from e
            raise Refused(e.code, detail, path) from e
        except urllib.error.URLError as e:
            raise Refused(0, f'unreachable: {e.reason}', path) from e
        if stream:
            return resp
        payload = resp.read()
        return payload if raw else json.loads(payload.decode('utf-8', 'replace'))

    # ── the grid, as it actually is ──────────────────────────────────

    def roster(self) -> List[Dict[str, Any]]:
        """GET /v1/status/models — the real roster, all four job types."""
        return self._req('/status/models')

    def openai_models(self) -> List[str]:
        """GET /v1/models — the ids an OpenAI client can see. Text only."""
        got = self._req('/models')
        return [m['id'] for m in got.get('data', []) if 'id' in m]

    def workers(self, online: bool = True) -> Dict[str, Any]:
        """GET /v1/workers — who is serving, and what they hold."""
        got = self._req('/workers')
        rows = got.get('workers', [])
        if online:
            rows = [w for w in rows if w.get('online')]
        return {'count': len(rows), 'online_only': online, 'workers': rows}

    def price_book(self) -> Dict[str, Any]:
        """GET /v1/pricing, verbatim — schema, book, aliases, evidence and all."""
        return self._req('/pricing')

    def pricing(self, model: Optional[str] = None) -> Any:
        """GET /v1/pricing — the price book, whole or resolved for one model.

        Resolution follows the book's own rules, in order: an exact name
        (case-insensitive — the book lowercases what the roster does not),
        then the book's `aliases` (the roster says `deepseek-v4-flash-nvfp4`,
        the book bills it as `deepseek-v4-flash`). A hit means a rate is
        configured, NOT that anyone is serving it — `models()` is where those
        two facts meet, and where `default_text` fills in for unpriced text.
        """
        book = self.price_book()
        pb = book.get('price_book', {}) or {}
        rows = pb.get('models', []) or []
        if model is None:
            return {'schema': book.get('schema'),
                    'version': pb.get('version'),
                    'currency': book.get('currency'),
                    'ledger_unit': book.get('ledger_unit'),
                    'availability': pb.get('availability'),
                    'aliases': pb.get('aliases') or {},
                    'default_text': pb.get('default_text'),
                    'comparison_evidence': book.get('comparison_evidence'),
                    'source': f'{BASE}/pricing',
                    'count': len(rows), 'models': rows}
        hit = self._resolve(str(model), self._index(book))
        if hit:
            row, via = hit
            return {**row, 'requested': str(model), 'via': via,
                    'version': pb.get('version')}
        want = str(model).strip().lower()
        near = [r['model'] for r in rows if want in str(r.get('model', '')).lower()]
        near += [a for a in (pb.get('aliases') or {}) if want in a.lower()]
        raise KeyError(f'no rate for {model!r}' +
                       (f' — did you mean {near}?' if near else
                        ' (a model can be online and unpriced)'))

    @staticmethod
    def _index(book: Dict[str, Any]) -> Dict[str, Any]:
        """The book, keyed for lookup: lowercased rows and lowercased aliases."""
        pb = book.get('price_book', {}) or {}
        return {
            'rows': {str(r.get('model', '')).lower(): r
                     for r in pb.get('models', []) or []},
            'aliases': {str(k).lower(): str(v).lower()
                        for k, v in (pb.get('aliases') or {}).items()},
            'default_text': pb.get('default_text'),
            'version': pb.get('version'),
        }

    @staticmethod
    def _resolve(name: str, idx: Dict[str, Any]) -> Optional[tuple]:
        """(row, via) for a model name — 'book' or 'alias:<target>' — or None."""
        key = name.strip().lower()
        if key in idx['rows']:
            return idx['rows'][key], 'book'
        target = idx['aliases'].get(key)
        if target and target in idx['rows']:
            return idx['rows'][target], f'alias:{target}'
        return None

    def models(self, type: Optional[str] = None, online: Optional[bool] = None,
               sort: str = 'type') -> List[Dict[str, Any]]:
        """Every model the grid knows: typed, priced, and online or not.

        One row per model, joining the three public reads — the roster, the
        price book and the OpenAI id list — because no single one of them
        answers "can I use this, and what will it cost".
        """
        rows = self.roster()
        try:
            idx = self._index(self.price_book())
        except (Refused, KeyError, TypeError, AttributeError, ValueError):
            idx = {'rows': {}, 'aliases': {}, 'default_text': None,
                   'version': None}
        try:
            visible = {m.lower() for m in self.openai_models()}
        except Refused:
            visible = set()
        out = []
        for r in rows:
            name = r.get('name', '')
            kind = r.get('type', 'text')
            price, source = self._rate(name, kind, r, idx)
            out.append({
                'model': name,
                'type': kind,
                'online': (r.get('count') or 0) > 0,
                'workers': r.get('count') or 0,
                'context': r.get('max_context_length'),
                'price': price,
                'priced': bool(price),
                'price_source': source,
                'openai_visible': name.lower() in visible,
                'tokens_per_s': r.get('tokens_per_s'),
                'avg_latency_s': r.get('avg_latency_s'),
            })
        if type:
            out = [r for r in out if r['type'] == str(type).lower()]
        if online is not None:
            out = [r for r in out if r['online'] is bool(online)]
        keys = {'type': lambda r: (r['type'], r['model'].lower()),
                'model': lambda r: r['model'].lower(),
                'workers': lambda r: -r['workers']}
        return sorted(out, key=keys.get(sort, keys['type']))

    @classmethod
    def _rate(cls, name: str, kind: str, roster_row: Dict[str, Any],
              idx: Dict[str, Any]) -> tuple:
        """(price, source) for one roster row.

        Sources, most specific first: the book row ('book'), the book's alias
        table ('alias:<target>'), the rate the roster itself carries
        ('roster'), and for text only the book's `default_text` ('default') —
        which is what the grid bills an unlisted text model at. An unpriced
        model comes back ({}, None) rather than guessed at.
        """
        hit = cls._resolve(name, idx)
        if hit:
            price = cls._price(kind, hit[0].get('rates', {}) or {})
            if price:
                return price, hit[1]
        inline = cls._price(kind, roster_row.get('pricing') or {})
        if inline:
            return inline, 'roster'
        if kind == 'text' and idx.get('default_text'):
            price = cls._price(kind, idx['default_text'])
            if price:
                return price, 'default'
        return {}, None

    @staticmethod
    def _price(kind: str, rates: Dict[str, Any]) -> Dict[str, Any]:
        """The rates that apply to this job type, dropping the nulls."""
        return {f: rates[f] for f in UNIT.get(kind, ())
                if rates.get(f) is not None}

    def grid(self) -> Dict[str, Any]:
        """The whole grid on one screen: what is up, and the cheapest way in."""
        rows = self.models()
        live = [r for r in rows if r['online']]
        by_type: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            slot = by_type.setdefault(r['type'], {'models': 0, 'online': 0,
                                                  'names': []})
            slot['models'] += 1
            if r['online']:
                slot['online'] += 1
                slot['names'].append(r['model'])
        text = [r for r in live if r['type'] == 'text'
                and r['price'].get('output_per_mtok_usd') is not None]
        cheapest = min(text, key=lambda r: r['price']['output_per_mtok_usd'],
                       default=None)
        hidden = [r['model'] for r in live if not r['openai_visible']]
        return {
            'api': BASE,
            'models': len(rows),
            'online': len(live),
            'workers': self.workers()['count'],
            'by_type': by_type,
            'cheapest_text': ({'model': cheapest['model'],
                               'usd_per_mtok_out':
                                   cheapest['price']['output_per_mtok_usd']}
                              if cheapest else None),
            'hidden_from_openai_clients': hidden,
            'key': self.key_status(),
        }

    def estimate(self, model: str, input_tokens: int = 0,
                 output_tokens: int = 0, images: int = 0, seconds: float = 0,
                 generations: int = 0) -> Dict[str, Any]:
        """What a call of this shape would cost, at book rates.

        Text bills per million tokens in and out; image per image, video and
        audio per second, 3d per generation. Names resolve through the book's
        aliases, and an unlisted text model falls back to `default_text` —
        the same rule the grid bills by.
        """
        book = self.price_book()
        idx = self._index(book)
        hit = self._resolve(str(model), idx)
        if hit:
            rates, via = hit[0].get('rates', {}) or {}, hit[1]
        elif idx.get('default_text') and (input_tokens or output_tokens):
            rates, via = idx['default_text'], 'default'
        else:
            raise KeyError(f'no rate for {model!r} — m aipg/pricing')
        parts = {
            'input_tokens': (int(input_tokens) / 1e6, 'input_per_mtok_usd'),
            'output_tokens': (int(output_tokens) / 1e6, 'output_per_mtok_usd'),
            'images': (int(images), 'per_image_usd'),
            'video_seconds': (float(seconds), 'per_video_second_usd'),
            'audio_seconds': (float(seconds), 'per_audio_second_usd'),
            'generations': (int(generations), 'per_3d_generation_usd'),
        }
        used = {k: (q, f) for k, (q, f) in parts.items()
                if q and rates.get(f) is not None}
        if not used:
            have = {f: v for f, v in rates.items() if v is not None}
            raise KeyError(f'{model} is billed by {sorted(have)} — pass the '
                           'matching quantity (input_tokens/output_tokens, '
                           'images, seconds or generations)')
        usd = sum(q * rates[f] for q, f in used.values())
        return {'model': model, 'via': via, 'version': idx.get('version'),
                'input_tokens': int(input_tokens),
                'output_tokens': int(output_tokens),
                'usd': round(usd, 8),
                'rates': {f: rates[f] for _, f in used.values()},
                **({'images': int(images)} if images else {}),
                **({'seconds': float(seconds)} if seconds else {}),
                **({'generations': int(generations)} if generations else {})}

    # ── spending the key ─────────────────────────────────────────────

    def credits(self) -> Dict[str, Any]:
        """GET /v1/account/credits — what is left to spend."""
        return self._req('/account/credits', keyed=True)

    def chat(self, prompt: Optional[str] = None, model: str = 'auto',
             messages: Optional[List[Dict[str, str]]] = None,
             system: Optional[str] = None, max_tokens: int = 512,
             temperature: float = 0.7, stream: bool = False,
             **extra: Any) -> Dict[str, Any]:
        """POST /v1/chat/completions — one turn, OpenAI-shaped.

        `model='auto'` lets the grid route, which is the only safe default when
        the roster changes with whoever is online.
        """
        if messages is None:
            if prompt is None:
                raise ValueError('give a prompt, or messages')
            messages = [{'role': 'user', 'content': str(prompt)}]
        if system:
            messages = [{'role': 'system', 'content': system}] + list(messages)
        body = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
                'temperature': temperature, 'stream': bool(stream), **extra}
        if stream:
            return self._stream(body)
        got = self._req('/chat/completions', body=body, keyed=True)
        choice = (got.get('choices') or [{}])[0]
        return {'model': got.get('model', model),
                'text': (choice.get('message') or {}).get('content', ''),
                'finish_reason': choice.get('finish_reason'),
                'usage': got.get('usage'), 'raw': got}

    def ask(self, prompt: str, model: str = 'auto', **kwargs: Any) -> str:
        """One turn, just the text. The call you actually make from a shell."""
        return self.chat(prompt=prompt, model=model, **kwargs)['text']

    def _stream(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Consume an SSE completion, printing as it lands, returning the whole.

        A generator would be the pretty answer, but this is called from a CLI
        that prints return values — so the deltas go to stdout as they arrive
        and the assembled text comes back for anything holding the result.
        """
        resp = self._req('/chat/completions', body=body, keyed=True, stream=True)
        parts: List[str] = []
        with resp:
            for line in resp:
                line = line.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                chunk = line[5:].strip()
                if chunk in ('', '[DONE]'):
                    continue
                try:
                    delta = json.loads(chunk)
                except ValueError:
                    continue
                bit = ((delta.get('choices') or [{}])[0]
                       .get('delta', {}).get('content'))
                if bit:
                    parts.append(bit)
                    print(bit, end='', flush=True)
        print()
        return {'model': body.get('model'), 'text': ''.join(parts),
                'streamed': True}

    def image(self, prompt: str, model: Optional[str] = None, n: int = 1,
              size: Optional[str] = None, save: bool = True,
              **extra: Any) -> Dict[str, Any]:
        """POST /v1/images/generations — one image, saved to ~/.mod/aipg/out."""
        return self._media('images', prompt, model, save, 'image',
                           {'n': int(n), **({'size': size} if size else {})},
                           extra)

    def video(self, prompt: str, model: Optional[str] = None,
              seconds: Optional[int] = None, save: bool = True,
              **extra: Any) -> Dict[str, Any]:
        """POST /v1/videos/generations — one clip. LTX models serve these."""
        return self._media('videos', prompt, model, save, 'video',
                           {**({'seconds': int(seconds)} if seconds else {})},
                           extra)

    def audio(self, prompt: str, model: Optional[str] = None,
              save: bool = True, **extra: Any) -> Dict[str, Any]:
        """POST /v1/audio/generations — one track. ACE-Step serves these."""
        return self._media('audio', prompt, model, save, 'audio', {}, extra)

    def _media(self, route: str, prompt: str, model: Optional[str], save: bool,
               kind: str, opts: Dict[str, Any],
               extra: Dict[str, Any]) -> Dict[str, Any]:
        """The shared half of image, video and audio.

        Model defaults to the first ONLINE model of the right type rather than
        a hardcoded name — the media roster turns over with whoever is running
        a worker, so a pinned default is a default that breaks.
        """
        if model is None:
            live = self.models(type=kind, online=True)
            if not live:
                raise Refused(503, f'no {kind} worker is online right now — '
                                   'm aipg/workers', f'/{route}/generations')
            model = live[0]['model']
        body = {'model': model, 'prompt': str(prompt), **opts, **extra}
        got = self._req(f'/{route}/generations', body=body, keyed=True)
        saved = self._save(got, kind) if save else []
        return {'model': model, 'kind': kind, 'saved': saved, 'raw': got}

    @staticmethod
    def _save(payload: Dict[str, Any], kind: str) -> List[str]:
        """Write whatever came back that looks like bytes.

        Handles both shapes OpenAI-compatible servers use — b64_json inline and
        a url to fetch — because which one the grid returns depends on the
        worker, and neither is worth a second round trip to discover.
        """
        ext = {'image': 'png', 'video': 'mp4', 'audio': 'mp3'}.get(kind, 'bin')
        rows = payload.get('data') or payload.get(f'{kind}s') or []
        if isinstance(rows, dict):
            rows = [rows]
        OUT.mkdir(parents=True, exist_ok=True)
        out: List[str] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            blob = None
            for field in ('b64_json', 'b64', 'data'):
                if isinstance(row.get(field), str) and len(row[field]) > 256:
                    try:
                        blob = base64.b64decode(row[field], validate=False)
                    except (ValueError, TypeError):
                        blob = None
                    break
            url = row.get('url') or row.get('video_url') or row.get('audio_url')
            if blob is None and isinstance(url, str) and url.startswith('http'):
                try:
                    with urllib.request.urlopen(
                            urllib.request.Request(url, headers={'User-Agent': UA}),
                            timeout=TIMEOUT) as r:
                        blob = r.read()
                except (urllib.error.URLError, OSError):
                    continue
            if not blob:
                continue
            p = OUT / f'{kind}-{int(time.time())}-{i}.{ext}'
            p.write_bytes(blob)
            out.append(str(p))
        return out

    # ── the protocol surface ─────────────────────────────────────────

    def serve(self, port: Optional[int] = None, host: Optional[str] = None) -> None:
        """Serve the fn surface over HTTP, in the mod protocol's wire shape.

        `POST /{fn}` with a JSON object of params answers `{"result": ...}`,
        exactly like core/server — so `{host}/aipg/api/{fn}` and the legacy
        `/api/aipg/{fn}` both work behind the router. GET with a query string
        works too, so `curl .../aipg/api/pricing?model=gpt-oss-120b` does.

        This exists because the generic core gate only knows one fleet-wide
        public list, which refuses every read here. Instead: `public_fns` from
        config.json are open to anyone; everything else (credits, chat, media,
        set_key — the calls that spend the key) answers only to a direct
        loopback caller, i.e. the operator on this box, never to a request
        relayed by a proxy. `key` and `serve` are never callable over HTTP.

        Blocks. core/server hands off here when it sees a module-owned serve,
        so the existing `m.serve('aipg')` pm2 entry runs this unchanged.
        """
        port, host = int(port or PORT), host or HOST
        srv = ThreadingHTTPServer((host, port), _handler(self))
        print(f'aipg serving the mod protocol on http://{host}:{port} '
              f'({len(PUBLIC)} public fns, upstream {BASE})', flush=True)
        try:
            srv.serve_forever()
        finally:
            srv.server_close()

    def call(self, fn: str, params: Optional[Dict[str, Any]] = None,
             local: bool = False) -> Any:
        """Run one fn under the same rules the HTTP surface enforces."""
        fn = str(fn).strip('/').split('/')[-1]
        if fn in NEVER or fn.startswith('_') or fn not in CONFIG.get('fns', []):
            raise PermissionError(f'{fn!r} is not callable over the protocol')
        if fn not in PUBLIC and not local:
            raise PermissionError(f'{fn!r} spends the key — loopback only, '
                                  f'public fns: {sorted(PUBLIC)}')
        params = dict(params or {})
        params.pop('token', None)
        if fn == 'chat':
            params['stream'] = False   # nobody is watching this stdout
        return getattr(self, fn)(**params)

    # ── does it work ─────────────────────────────────────────────────

    def test(self) -> Dict[str, Any]:
        """Hit the three public reads and check they agree with each other."""
        rows = self.models()
        checks = {
            'roster_not_empty': len(rows) > 0,
            'types_known': all(r['type'] in UNIT or r['type'] == 'text'
                               for r in rows),
            'workers_reachable': isinstance(self.workers()['count'], int),
            'price_book_joins': any(r['priced'] for r in rows),
            'roster_exceeds_openai_list': len(rows) >= len(self.openai_models()),
        }
        return {'ok': all(checks.values()), 'checks': checks,
                'models': len(rows),
                'online': sum(1 for r in rows if r['online']),
                'key': self.key_status()['present']}


# ── HTTP ─────────────────────────────────────────────────────────────

# where a fn name can sit in the path, depending on who relayed the request:
# direct, core/server's /mod/{name}/, the canonical router, the legacy alias
_PREFIXES = ('/aipg/api/', '/api/aipg/', '/mod/aipg/', '/')
_FORWARDED = ('x-forwarded-for', 'x-real-ip', 'forwarded', 'cf-connecting-ip')
_STATUS = ((PermissionError, 403), (NoKey, 401), (KeyError, 404),
           (Refused, 424), (TypeError, 400), (ValueError, 400))


def _query_value(v: str) -> Any:
    """Query strings are text; `online=false` must not arrive as truthy."""
    try:
        return json.loads(v)
    except ValueError:
        return v


def _handler(mod: 'Mod') -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = UA

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _local(self) -> bool:
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                return False
            return not any(self.headers.get(h) for h in _FORWARDED)

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers',
                             'Content-Type, Authorization, token')
            self.end_headers()

        def _route(self, params: Dict[str, Any]) -> None:
            path = urllib.parse.urlsplit(self.path).path
            fn = ''
            for pre in _PREFIXES:
                if path.startswith(pre) or path + '/' == pre:
                    fn = path[len(pre):].strip('/')
                    break
            fn = fn or 'info'
            # core clients wrap path calls as fn='call' {fn, params}
            if fn in ('call', 'forward') and isinstance(params.get('fn'), str):
                fn, params = params['fn'], params.get('params') or {}
            if fn == 'health':
                return self._send(200, {'ok': True, 'mod': 'aipg',
                                        'upstream': BASE})
            try:
                result = mod.call(fn, params, local=self._local())
            except Exception as e:  # noqa: BLE001 — every error gets a status
                code = next((c for t, c in _STATUS if isinstance(e, t)), 400)
                msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                return self._send(code, {'error': str(msg), 'fn': fn})
            self._send(200, {'result': result})

        def do_GET(self) -> None:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._route({k: _query_value(v[-1]) for k, v in q.items()})

        def do_POST(self) -> None:
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b''
            try:
                params = json.loads(raw) if raw.strip() else {}
            except ValueError:
                return self._send(400, {'error': 'body must be JSON'})
            if not isinstance(params, dict):
                return self._send(400, {'error': 'body must be a JSON object'})
            # core clients wrap params as {"params": {...}}; accept both
            if set(params) <= {'params', 'token'} and isinstance(
                    params.get('params'), dict):
                params = params['params']
            self._route(params)

    return Handler


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['serve']:
        Mod().serve(*(sys.argv[2:3] or [None]))
