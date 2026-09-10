#!/usr/bin/env python3
"""wingman ↔ venice — the part of a photo a program cannot measure.

Everything in `engine.py` is measured: a face detector finds faces, the
Laplacian says sharp or not, the histogram says dark or not. That leaves a
list this module has always been honest about not knowing — expression, eye
contact, outfit, setting, whether the shot reads as a mirror selfie, whether
every photo in the set is the same shirt in the same room. Those need
something that can look at a picture.

This is that, and it is the one place in wingman where a photo leaves the box.

    orbit/venice (:50880, or :9000/api/venice through the activator) is a
    mod-protocol gateway to Venice's model catalogue. We authenticate with a
    wallet-signed protocol token, POST an OpenAI-shaped chat completion with
    the image inline, and get back JSON.

The rules that make this safe to ship next to a module whose README says
"nothing leaves this box":

  * **Nothing leaves unless you call `read`.** `audit`, `lineup`, `render`
    and `export` never open a socket to venice; they read the cache on disk
    and nothing more. Consent is the verb.
  * **The original never leaves.** What goes out is a 768 px JPEG re-encoded
    from the decoded pixels — no EXIF, no GPS, no maker notes, no thumbnail.
  * **There is a receipt.** Every send appends to `sent.json`: when, which
    photo, how many bytes, to which model, at which URL. `status()` counts it
    and `wingman_health` surfaces it.
  * **The measured score does not move.** A read produces `read_flags`, kept
    in their own list with `source` on every one. The number the auditor
    computed from pixels stays the number the auditor computed from pixels.
  * **One kill switch.** `WINGMAN_VENICE=off` makes `read` refuse, whatever
    the config file says.
"""

import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
from engine import WingmanError                             # noqa: E402

# The gateway. The activator path is the default: it wakes venice if the
# module has been slept, which the direct port does not.
DEFAULT_URL = 'http://localhost:9000/api/venice'
FALLBACK_URL = 'http://localhost:50880'
# Vision-capable, cheap, and reliable at returning JSON. Override per call.
DEFAULT_MODEL = 'qwen3-vl-235b-a22b'

CONFIG_PATH = os.path.join(E.STATE_DIR, 'venice.json')
READ_VERSION = 1
SEND_PX = int(os.environ.get('WINGMAN_VENICE_PX') or 768)
SEND_QUALITY = int(os.environ.get('WINGMAN_VENICE_QUALITY') or 82)
TIMEOUT = int(os.environ.get('WINGMAN_VENICE_TIMEOUT') or 120)

# What the model is asked for, and what each answer is allowed to be. Kept
# here rather than in the prompt string so the schema, the parser and the
# flag rules cannot drift apart.
FIELDS = {
    'expression': ['smiling', 'slight-smile', 'laughing', 'neutral', 'serious',
                   'obscured', 'unclear'],
    'eyes': ['open', 'closed', 'squinting', 'sunglasses', 'hidden', 'not-visible'],
    'eye_contact': ['camera', 'away', 'none', 'unclear'],
    'shot': ['selfie', 'mirror-selfie', 'taken-by-someone', 'timer-or-tripod',
             'candid', 'unclear'],
    'subject': ['one-clear-subject', 'ambiguous-several-people', 'no-person'],
}

READ_PROMPT = """You are looking at one photo from someone's dating-app profile \
(Hinge, Tinder, Bumble). Report only what is visibly in the frame.

Rules:
- Describe, do not rate the person. No comment on attractiveness, body, age, \
race or desirability. If you are asked for none of it, give none of it.
- If something is not clearly visible, say the "unclear" option. Guessing is \
worse than useless here, because the caller already has measured numbers and \
is asking you only for what a measurement cannot reach.
- `reads_as` is one plain sentence: what this photo tells a stranger about \
this person's life. Not a compliment, not advice.
- `distractions` are concrete things in the frame that pull attention off the \
subject: a toilet, a cluttered counter, a phone across the face, a stranger \
mid-frame, a car interior, a caption burned into the image. Empty list if none.

Answer with JSON only, no prose around it, exactly these keys:

{
  "expression": one of %(expression)s,
  "eyes": one of %(eyes)s,
  "eye_contact": one of %(eye_contact)s,
  "shot": one of %(shot)s,
  "subject": one of %(subject)s,
  "people_visible": integer,
  "setting": short phrase, e.g. "restaurant patio at night",
  "outfit": short phrase, e.g. "black t-shirt", or "not visible",
  "activity": short phrase, e.g. "holding a fish", or "none",
  "distractions": [short phrases],
  "text_overlay": true or false,
  "reads_as": one sentence
}""" % {k: json.dumps(v) for k, v in FIELDS.items()}

SUMMARY_PROMPT = """Here is what a vision model saw in each photo of one \
person's dating-app set, in the order the photos were given. You are not \
looking at the photos; you are reading the notes.

Say what the SET is missing — repetition a single photo cannot show. Be \
concrete and short. Then, from what is actually in these photos and nothing \
else, suggest Hinge-style prompt openings the person could answer honestly.

Answer with JSON only, exactly these keys:

{
  "reads_as": one sentence on what this set says about this person overall,
  "repetition": [short phrases naming what repeats across the set — same \
setting, same outfit, same expression, same shot type; empty list if varied],
  "missing": [short phrases naming what a new photo should add],
  "prompt_hooks": [up to 4 objects, each {"prompt": a real Hinge prompt, \
"hook": what in these photos it would draw on}]
}"""


# ── config ───────────────────────────────────────────────────────────────

def config():
    """Where venice is, which model reads a photo, and whether reads are
    allowed at all. Env wins over the file; the file wins over the default."""
    cfg = E._read_json(CONFIG_PATH, {}) or {}
    env = (os.environ.get('WINGMAN_VENICE') or '').strip().lower()
    enabled = cfg.get('enabled', True)
    if env in ('off', '0', 'false', 'no'):
        enabled = False
    elif env in ('on', '1', 'true', 'yes'):
        enabled = True
    return {
        'url': (os.environ.get('WINGMAN_VENICE_URL') or cfg.get('url')
                or DEFAULT_URL).rstrip('/'),
        'model': os.environ.get('WINGMAN_VENICE_MODEL') or cfg.get('model') or DEFAULT_MODEL,
        'enabled': bool(enabled),
        'locked_by_env': env in ('off', '0', 'false', 'no'),
    }


def configure(url=None, model=None, enabled=None):
    """Persist the gateway URL, the reading model, or the off switch."""
    cfg = E._read_json(CONFIG_PATH, {}) or {}
    if url is not None:
        cfg['url'] = str(url).rstrip('/')
    if model is not None:
        cfg['model'] = str(model)
    if enabled is not None:
        cfg['enabled'] = bool(enabled) if not isinstance(enabled, str) else \
            str(enabled).lower() not in ('0', 'false', 'no', 'off', '')
    E._ensure()
    E._write_json(CONFIG_PATH, cfg)
    return config()


# ── protocol auth ────────────────────────────────────────────────────────

_TOKEN = {'token': None, 'at': 0.0, 'address': None}


def _protocol_mod():
    """The protocol's `mod` package — not this directory's `mod.py`, which
    would shadow it for anything importing after our sys.path append."""
    m = sys.modules.get('mod')
    if m is not None and hasattr(m, 'mod'):
        return m
    stashed = sys.modules.pop('mod', None)
    saved = list(sys.path)
    try:
        sys.path = [p for p in sys.path if os.path.abspath(p or '.') != HERE]
        import mod as protocol                               # noqa: F811
        if not hasattr(protocol, 'mod'):
            raise WingmanError('imported the wrong `mod` — the protocol package '
                               'is not on sys.path', status=500)
        return protocol
    except ImportError as e:
        raise WingmanError(f'the mod protocol package is not importable ({e}); '
                           'venice reads need it to sign the request', status=500)
    finally:
        sys.path = saved
        if stashed is not None:
            sys.modules['mod'] = stashed


def token(max_age=45):
    """A wallet-signed protocol token venice can recover an address from.
    Cached briefly — signing is ~10 ms, but a set of 20 photos is 20 calls."""
    now = time.time()
    if _TOKEN['token'] and now - _TOKEN['at'] < max_age:
        return _TOKEN['token']
    auth = _protocol_mod().mod('auth')()
    t = auth.generate({'module': 'wingman', 'purpose': 'photo-read'})
    tok = t['token'] if isinstance(t, dict) else t
    _TOKEN.update(token=tok, at=now)
    return tok


def address():
    """The address venice sees us as — the one a BYOK key is filed under."""
    if _TOKEN['address']:
        return _TOKEN['address']
    try:
        _TOKEN['address'] = (_call('/me', method='GET') or {}).get('address')
    except Exception:
        pass
    return _TOKEN['address']


# ── transport ────────────────────────────────────────────────────────────

def _call(path, body=None, method=None, url=None, timeout=None, auth=True):
    cfg = config()
    base = (url or cfg['url']).rstrip('/')
    method = method or ('POST' if body is not None else 'GET')
    data = json.dumps(body).encode() if body is not None else None
    headers = {'content-type': 'application/json', 'user-agent': 'wingman/0.2'}
    if auth:
        headers['authorization'] = 'Bearer ' + token()
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            raw = r.read().decode('utf-8', 'replace')
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = (e.read() or b'').decode('utf-8', 'replace')[:600]
        try:
            msg = json.loads(detail).get('error') or detail
        except Exception:
            msg = detail
        if e.code == 402:
            raise WingmanError(
                'venice has no key to spend for this caller. Either put your own '
                'Venice key on file — `m wingman/venice_key <key>` — or configure '
                'the paid path on the venice module. venice said: ' + msg,
                status=402, address=address())
        if e.code in (401, 403):
            raise WingmanError(f'venice rejected the protocol token ({e.code}): {msg}',
                               status=e.code)
        raise WingmanError(f'venice {e.code} on {path}: {msg}', status=502)
    except urllib.error.URLError as e:
        raise WingmanError(
            f'venice is not reachable at {base} ({e.reason}). Start orbit/venice, or '
            'point wingman elsewhere with `m wingman/venice url=<...>`.', status=503)


def status():
    """Is the read path live, and what would it cost — one call, no photo."""
    cfg = config()
    out = dict(cfg, reachable=False, address=None, has_key=None,
               paid_available=None, error=None)
    try:
        out['health'] = _call('/health', method='GET', auth=False, timeout=10)
        out['reachable'] = bool(out['health'].get('ok'))
    except WingmanError as e:
        out['error'] = e.args[0]
        return out
    try:
        me = _call('/me', method='GET', timeout=20)
        _TOKEN['address'] = me.get('address')
        out.update(address=me.get('address'), has_key=me.get('has_key'),
                   paid_available=me.get('paid_available'),
                   price=me.get('price'), currency=me.get('currency'))
    except WingmanError as e:
        out['error'] = e.args[0]
    out['can_read'] = bool(out['reachable'] and cfg['enabled'] and
                           (out.get('has_key') or out.get('paid_available')))
    out['sends'] = sends()
    return out


def models(vision_only=True):
    """The catalogue, filtered to what can actually look at a photo."""
    data = (_call('/models', method='GET', auth=False, timeout=30) or {}).get('data') or []
    rows = []
    for m in data:
        spec = m.get('model_spec') or {}
        caps = spec.get('capabilities') or {}
        if vision_only and not caps.get('supportsVision'):
            continue
        rows.append({'id': m.get('id'), 'name': spec.get('name'),
                     'vision': bool(caps.get('supportsVision')),
                     'json_schema': bool(caps.get('supportsResponseSchema'))})
    return {'models': rows, 'count': len(rows), 'default': config()['model']}


def set_key(key):
    """File a Venice key under this box's address. It is stored by venice,
    encrypted at rest, not by wingman."""
    if not key or not str(key).strip():
        raise WingmanError('pass the Venice API key')
    _call('/key', {'key': str(key).strip()})
    return {'ok': True, 'address': address(), 'has_key': True}


def forget_key():
    _call('/key', method='DELETE')
    return {'ok': True, 'address': address(), 'has_key': False}


# ── what actually goes out ───────────────────────────────────────────────

def payload(meta, p):
    """The bytes that leave: decoded pixels, re-encoded at 768 px as JPEG,
    with nothing else attached. The original file never moves."""
    img, converted = E._to_srgb(E.load_image(meta, p))
    img = img.convert('RGB')
    img.thumbnail((SEND_PX, SEND_PX), E.Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=SEND_QUALITY, optimize=True)   # no exif=, no icc=
    raw = buf.getvalue()
    return raw, {'px': list(img.size), 'bytes': len(raw), 'format': 'jpeg',
                 'srgb_converted': converted,
                 'stripped': ['exif', 'gps', 'icc', 'xmp', 'thumbnail']}


def _sent_path(set_id):
    return os.path.join(E._set_dir(set_id), 'sent.json')


def _receipt(meta, p, sent, model, url):
    """Record a send *before* it happens. A request that venice refuses still
    left this process, so it still belongs in the log — a receipt written only
    on success would quietly under-count what went out. Returns the index the
    outcome is written back to."""
    path = _sent_path(meta['id'])
    log = E._read_json(path, []) or []
    log.append({'time': round(time.time()), 'photo': p['id'], 'name': p.get('name'),
                'model': model, 'url': url, 'outcome': 'sent', **sent})
    E._write_json(path, log)
    return len(log) - 1


def _receipt_done(meta, idx, outcome):
    path = _sent_path(meta['id'])
    log = E._read_json(path, []) or []
    if 0 <= idx < len(log):
        log[idx]['outcome'] = outcome
        E._write_json(path, log)


def sends(set_ref=None):
    """How many photos have left this box, and when the last one did."""
    if set_ref:
        log = E._read_json(_sent_path(E.resolve_set(set_ref)), []) or []
        return {'count': len(log), 'last': log[-1]['time'] if log else None, 'log': log}
    total, last = 0, None
    for sid in os.listdir(E.SETS_DIR) if os.path.isdir(E.SETS_DIR) else []:
        log = E._read_json(_sent_path(sid), []) or []
        total += len(log)
        if log:
            last = max(last or 0, log[-1]['time'])
    return {'count': total, 'last': last}


# ── the read ─────────────────────────────────────────────────────────────

def _chat(model, messages, max_tokens=700):
    body = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
            'temperature': 0.2}
    r = _call('/chat', body)
    try:
        return r['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise WingmanError(f'venice returned no message: {json.dumps(r)[:300]}', status=502)


def _json_from(text):
    """Models wrap JSON in prose and fences no matter how firmly you ask."""
    if isinstance(text, list):                     # some models return parts
        text = ''.join(c.get('text', '') for c in text if isinstance(c, dict))
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I | re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}' and depth:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    start = None
    raise WingmanError('the model did not return JSON: ' + text[:200], status=502)


def _one_of(value, key, default='unclear'):
    allowed = FIELDS.get(key)
    v = str(value or '').strip().lower().replace(' ', '-')
    return v if (not allowed or v in allowed) else (default if not allowed
                                                    else (v if v in allowed else allowed[-1]))


def _flags(r):
    """Read findings, in the shape of an issue but never mixed with them:
    every one carries `source`, and none of them touches the score."""
    out = []

    def add_(code, sev, text):
        out.append({'code': code, 'severity': sev, 'text': text, 'source': 'read'})

    if r['eyes'] == 'closed':
        add_('eyes-closed', 'bad', 'eyes are closed — nothing else about the photo '
             'matters if this is right')
    elif r['eyes'] == 'sunglasses':
        add_('sunglasses', 'warn', 'sunglasses — fine once in a set, never in the '
             'lead photo')
    elif r['eyes'] == 'hidden':
        add_('eyes-hidden', 'warn', 'the eyes are not visible')
    if r['eye_contact'] == 'none' and r['subject'] != 'no-person':
        add_('no-eye-contact', 'info', 'not looking at the camera — one of these '
             'is a good photo, a set of them reads as avoidant')
    if r['expression'] in ('serious', 'neutral'):
        add_('unsmiling', 'info', f'expression reads {r["expression"]} — the most '
             'reliably repeated finding in every app\'s own photo guidance is that '
             'the lead photo smiles')
    if r['shot'] == 'mirror-selfie':
        add_('mirror-selfie', 'warn', 'a mirror selfie — reads as the photo you had, '
             'not the photo you chose')
    if r['subject'] == 'ambiguous-several-people':
        add_('ambiguous-subject', 'bad', 'more than one person is equally prominent — '
             'a stranger cannot tell which one is you')
    if r.get('text_overlay'):
        add_('text-overlay', 'warn', 'text burned into the image')
    for d in (r.get('distractions') or [])[:4]:
        add_('distraction', 'info', f'in frame: {d}')
    return out


def _read_path(set_id):
    return os.path.join(E._set_dir(set_id), 'read.json')


def cached(set_ref):
    """Every read already on disk for a set. No network, ever — this is what
    `audit` and `lineup` are allowed to use."""
    try:
        return E._read_json(_read_path(E.resolve_set(set_ref)), {}) or {}
    except WingmanError:
        return {}


def read_photo(meta, p, model=None, force=False):
    cache = E._read_json(_read_path(meta['id']), {}) or {}
    have = cache.get(p['id'])
    if have and have.get('v') == READ_VERSION and not force:
        return have

    cfg = config()
    if not cfg['enabled']:
        raise WingmanError(
            'reads are off. This is the only part of wingman that sends a photo '
            'anywhere; turn it on deliberately with `m wingman/venice enabled=1`'
            + (' (WINGMAN_VENICE=off in the environment overrides the config file)'
               if cfg['locked_by_env'] else ''), status=403)
    model = model or cfg['model']

    raw, sent = payload(meta, p)
    url = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()
    idx = _receipt(meta, p, sent, model, cfg['url'])
    try:
        text = _chat(model, [{'role': 'user', 'content': [
            {'type': 'text', 'text': READ_PROMPT},
            {'type': 'image_url', 'image_url': {'url': url}},
        ]}])
        j = _json_from(text)
    except WingmanError as e:
        _receipt_done(meta, idx, f'failed: {e.status}')
        raise
    _receipt_done(meta, idx, 'read')

    r = {
        'v': READ_VERSION, 'photo': p['id'], 'name': p.get('name'),
        'expression': _one_of(j.get('expression'), 'expression'),
        'eyes': _one_of(j.get('eyes'), 'eyes'),
        'eye_contact': _one_of(j.get('eye_contact'), 'eye_contact'),
        'shot': _one_of(j.get('shot'), 'shot'),
        'subject': _one_of(j.get('subject'), 'subject'),
        'people_visible': int(j.get('people_visible') or 0)
        if str(j.get('people_visible', '')).strip().lstrip('-').isdigit() else None,
        'setting': str(j.get('setting') or '')[:120],
        'outfit': str(j.get('outfit') or '')[:120],
        'activity': str(j.get('activity') or '')[:120],
        'distractions': [str(d)[:80] for d in (j.get('distractions') or [])][:6],
        'text_overlay': bool(j.get('text_overlay')),
        'reads_as': str(j.get('reads_as') or '')[:300],
        'model': model, 'at': round(time.time()), 'sent': sent,
        'said_by': 'a language model looking at a 768 px copy — not a measurement',
    }
    r['flags'] = _flags(r)
    cache = E._read_json(_read_path(meta['id']), {}) or {}      # re-read: threads
    cache[p['id']] = r
    E._write_json(_read_path(meta['id']), cache)
    return r


def summarise(meta, reads, model=None):
    """One more call, on the notes rather than the photos, for the thing no
    single photo can show: what the set repeats and what it is missing."""
    model = model or config()['model']
    notes = [{k: r[k] for k in ('name', 'expression', 'eyes', 'eye_contact', 'shot',
                                'subject', 'setting', 'outfit', 'activity', 'reads_as')}
             for r in reads]
    text = _chat(model, [{'role': 'user', 'content': SUMMARY_PROMPT + '\n\n' +
                          json.dumps(notes, indent=1)}], max_tokens=800)
    j = _json_from(text)
    return {
        'reads_as': str(j.get('reads_as') or '')[:300],
        'repetition': [str(x)[:120] for x in (j.get('repetition') or [])][:6],
        'missing': [str(x)[:120] for x in (j.get('missing') or [])][:6],
        'prompt_hooks': [{'prompt': str((h or {}).get('prompt') or '')[:140],
                          'hook': str((h or {}).get('hook') or '')[:200]}
                         for h in (j.get('prompt_hooks') or []) if isinstance(h, dict)][:4],
        'model': model, 'at': round(time.time()),
    }


def read(set_ref, photo=None, model=None, force=False, summary=True, limit=None):
    """Look at a set — the verb that sends. One call per photo, plus one on
    the notes. Returns the reads, the flags, and what the set repeats."""
    meta = E.get_set(set_ref)
    photos = [E._photo(meta, photo)] if photo else meta['photos']
    if limit:
        photos = photos[:int(limit)]
    if not photos:
        raise WingmanError('the set has no photos yet')

    reads, errors = [], []
    for p in photos:
        try:
            reads.append(read_photo(meta, p, model=model, force=force))
        except WingmanError as e:
            # A gateway-level refusal is not this photo's fault and will hit
            # every other one too; and when the caller named a single photo,
            # an error buried in a list is an error they will not see.
            if photo or e.status in (402, 403, 503):
                raise
            errors.append({'photo': p['id'], 'name': p.get('name'), 'error': e.args[0]})

    out = {'set': meta['id'], 'name': meta['name'], 'model': model or config()['model'],
           'photos': reads, 'errors': errors,
           'flags': [dict(f, photo=r['photo'], name=r['name'])
                     for r in reads for f in r['flags']],
           'sent': sends(meta['id']),
           'said_by': 'a language model, not a measurement — `audit` is the measured '
                      'half and its scores are untouched by anything here'}
    if summary and len(reads) > 1:
        path = os.path.join(E._set_dir(meta['id']), 'read-summary.json')
        have = E._read_json(path, None)
        if force or not have or have.get('n') != len(reads):
            have = dict(summarise(meta, reads, model=model), n=len(reads))
            E._write_json(path, have)
        out['summary'] = have
    return out


# ── what the cached read adds to the measured half ───────────────────────

def attach(meta_id, rows):
    """Fold cached reads onto audit rows. Cache only — no network."""
    cache = E._read_json(_read_path(meta_id), {}) or {}
    if not cache:
        return rows, None
    for a in rows:
        r = cache.get(a.get('photo'))
        if not r:
            continue
        a['read'] = {k: r[k] for k in ('expression', 'eyes', 'eye_contact', 'shot',
                                       'subject', 'setting', 'outfit', 'activity',
                                       'reads_as', 'model')}
        a['read_flags'] = r['flags']
    return rows, {'photos_read': len(cache),
                  'model': next(iter(cache.values())).get('model')}


def gaps(set_id, chosen_ids=None):
    """What the set repeats, said as gaps — settings, outfits, expressions and
    shot types that are the same photo twice. Cache only."""
    cache = E._read_json(_read_path(set_id), {}) or {}
    rows = [r for r in cache.values()
            if chosen_ids is None or r['photo'] in set(chosen_ids)]
    if len(rows) < 2:
        return []
    out = []

    def same(key, label, floor=0.7):
        vals = [str(r.get(key) or '').strip().lower() for r in rows]
        vals = [v for v in vals if v and v not in ('unclear', 'not visible', 'none')]
        if len(vals) < 2:
            return
        top, n = max(((v, vals.count(v)) for v in set(vals)), key=lambda x: x[1])
        if n / len(vals) >= floor and n >= 2:
            out.append(f'{n} of {len(vals)} {label} — "{top}"; the set shows one '
                       'side of your life twice')

    same('setting', 'photos are in the same kind of place')
    same('outfit', 'photos are the same outfit')
    same('shot', 'photos are the same kind of shot')
    if sum(1 for r in rows if r.get('expression') in ('neutral', 'serious')) == len(rows):
        out.append('nobody smiles in any photo — one warm, obviously-smiling shot '
                   'is the single most repeated piece of advice every app publishes')
    if all(r.get('eye_contact') != 'camera' for r in rows):
        out.append('no photo looks at the camera')
    summary = E._read_json(os.path.join(E._set_dir(set_id), 'read-summary.json'), None)
    if summary:
        out.extend(summary.get('missing') or [])
    return out[:8]


if __name__ == '__main__':                                    # python3 venice.py [set]
    print(json.dumps(read(sys.argv[1]) if len(sys.argv) > 1 else status(), indent=2))
