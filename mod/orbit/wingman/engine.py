#!/usr/bin/env python3
"""wingman engine — a folder of photos in, a dating-app-ready lineup out.

Everything measurable about a dating-profile photo is measured here, and
nothing that is not measurable is pretended to be. What this module can see:
whether there is a face, how big it is and where, how many people are in the
frame, whether the shot is sharp, dark, blown out or flat, whether two photos
are the same photo, and whether the file carries GPS coordinates. What it
cannot see — whether you look happy, whether the outfit works — it does not
score, and the audit says so.

The pipeline:

    add     → a set of photos, kept as they arrived, under ~/.mod/wingman
    audit   → per-photo measurements, issues with a cost each, a score
    lineup  → the best N, deduplicated, one clear solo face leading
    render  → face-aware crop to an app's card ratio, gentle polish, EXIF gone
    export  → a zip in slot order, with the report that justifies it

No face is retouched. No background is replaced. Nothing leaves this box.
"""

import base64
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import time
import urllib.request
import zipfile

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:                                     # HEIC/HEIF from iPhones, if the codec is here
    import pillow_heif                   # noqa: F401
    pillow_heif.register_heif_opener()
    HEIF = True
except Exception:
    HEIF = False

Image.MAX_IMAGE_PIXELS = 80_000_000

STATE_DIR = os.path.expanduser(os.environ.get('WINGMAN_DIR') or '~/.mod/wingman')
SETS_DIR = os.path.join(STATE_DIR, 'sets')
MODELS_DIR = os.path.join(STATE_DIR, 'models')
MAX_BYTES = int(os.environ.get('WINGMAN_MAX_BYTES') or 40 * 1024 * 1024)
MAX_PHOTOS = int(os.environ.get('WINGMAN_MAX_PHOTOS') or 80)
AUDIT_VERSION = 4

FACE_MODEL = 'version-RFB-320.onnx'
FACE_MODEL_URL = ('https://github.com/onnx/models/raw/main/validated/vision/'
                  'body_analysis/ultraface/models/version-RFB-320.onnx')
FACE_THRESHOLD = 0.7

EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.tif', '.tiff', '.bmp'}

# The ratio each app's photo card actually shows. Pass ratio= to override any
# of them — these are observations, not contracts, and apps redesign.
PRESETS = {
    'tinder': {'ratio': [4, 5], 'size': [1080, 1350],
               'note': 'the card is roughly 4:5 (640x800 on the web client); '
                       'top of the frame is safe, the bottom third sits under '
                       'the name overlay'},
    'hinge': {'ratio': [4, 5], 'size': [1080, 1350],
              'note': '4:5 card; Hinge shows the whole photo, no overlay, so '
                      'the crop is exactly what people see'},
    'bumble': {'ratio': [3, 4], 'size': [1080, 1440],
               'note': '3:4 card; slightly taller than Tinder, so the same '
                       'crop keeps a little more body'},
    'square': {'ratio': [1, 1], 'size': [1080, 1080],
               'note': 'a 1:1 headshot — avatars, Instagram grid, Raya'},
    'story': {'ratio': [9, 16], 'size': [1080, 1920],
              'note': '9:16 full screen — Tinder/Hinge prompts and stories'},
}
DEFAULT_PRESET = 'tinder'

# Where the auditor's opinions come from. It is not a taste model; it is the
# list of things every app's own photo guidance agrees on, restricted to the
# ones a program can check.
GUIDE = {
    'lead': 'a clear, sharp, solo shot where the face is 15-45% of the frame '
            'height — a headshot or head-and-shoulders, in focus, well lit',
    'variety': 'one headshot, one half-body, one full-body, one wider '
               'environment or activity shot; repetition of the same framing '
               'is the most common weakness in a set',
    'solo': 'the first three photos should have exactly one face; a group '
            'shot works later, never first, and never more than one',
    'sharp': 'blur reads as low effort; a soft face is a soft photo even when '
             'the background is sharp',
    'light': 'daylight or a bright room; underexposed shots lose detail in the '
             'face and dark shots are the easiest to fix by retaking, not editing',
    'framing': 'face upper-centre, headroom above it, eyes about 40% down the '
               'frame — the crop here does this automatically',
    'duplicates': 'two near-identical photos count as one; keep the sharper',
    'privacy': 'phone photos carry GPS coordinates in EXIF; every render here '
               'strips all metadata',
    'not_measured': 'expression, eye contact, outfit, setting, whether it '
                    'looks like you — no number here claims to know these',
}


class WingmanError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra

    def dict(self):
        return {'error': str(self), **self.extra}


# ── store ────────────────────────────────────────────────────────────────

def _ensure():
    os.makedirs(SETS_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)


def _set_dir(set_id):
    return os.path.join(SETS_DIR, set_id)


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def resolve_set(ref):
    """A set id, an id prefix, or a set name → the set id."""
    _ensure()
    ref = str(ref or '').strip()
    if not ref:
        raise WingmanError('which set? pass set=<id|name>')
    if os.path.isdir(_set_dir(ref)):
        return ref
    ids = sorted(os.listdir(SETS_DIR))
    hits = [i for i in ids if i.startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    for i in ids:
        meta = _read_json(os.path.join(_set_dir(i), 'set.json'), {})
        if meta.get('name') == ref:
            return i
    if len(hits) > 1:
        raise WingmanError(f'{ref!r} matches {len(hits)} sets — be more specific')
    raise WingmanError(f'no set {ref!r}', status=404)


def get_set(ref):
    sid = resolve_set(ref)
    meta = _read_json(os.path.join(_set_dir(sid), 'set.json'))
    if not meta:
        raise WingmanError(f'set {sid} has no set.json', status=500)
    return meta


def _save_set(meta):
    _write_json(os.path.join(_set_dir(meta['id']), 'set.json'), meta)


def new_set(name=None):
    """A fresh, empty set. The id is unguessable on purpose: it is the only
    thing that addresses the photos, so it is also the only thing that
    protects them once the console is behind a public gateway."""
    _ensure()
    sid = secrets.token_hex(8)
    d = _set_dir(sid)
    for sub in ('src', 'out', 'thumb'):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    meta = {'id': sid, 'name': name or time.strftime('set %Y-%m-%d %H:%M'),
            'created': time.time(), 'photos': []}
    _save_set(meta)
    return meta


def sets(limit=100):
    _ensure()
    out = []
    for sid in os.listdir(SETS_DIR):
        meta = _read_json(os.path.join(_set_dir(sid), 'set.json'))
        if not meta:
            continue
        audits = _read_json(os.path.join(_set_dir(sid), 'audit.json'), {})
        rendered = sorted({f.rsplit('-', 1)[-1].rsplit('.', 1)[0]
                           for f in os.listdir(os.path.join(_set_dir(sid), 'out'))
                           if f.endswith('.jpg')})
        out.append({'id': sid, 'name': meta.get('name'), 'created': meta.get('created'),
                    'photos': len(meta.get('photos', [])),
                    'audited': sum(1 for p in meta.get('photos', [])
                                   if audits.get(p['id'], {}).get('v') == AUDIT_VERSION),
                    'rendered': rendered})
    out.sort(key=lambda s: -(s['created'] or 0))
    return {'sets': out[:limit], 'count': len(out), 'dir': SETS_DIR}


def delete_set(ref):
    sid = resolve_set(ref)
    shutil.rmtree(_set_dir(sid), ignore_errors=True)
    return {'deleted': sid}


def _photo(meta, ref):
    ref = str(ref or '')
    for p in meta['photos']:
        if p['id'] == ref or p['name'] == ref:
            return p
    hits = [p for p in meta['photos'] if p['id'].startswith(ref) or p['name'].startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    raise WingmanError(f'no photo {ref!r} in set {meta["id"]}', status=404)


def _open_bytes(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise WingmanError(f'not an image this box can read ({type(e).__name__}: {e})'
                           + ('' if HEIF else ' — HEIC needs `pip install pillow-heif`, '
                                              'or export as JPEG first'))
    return img


def _add_bytes(meta, data, name):
    if len(data) > MAX_BYTES:
        raise WingmanError(f'{name}: {len(data)} bytes is over the {MAX_BYTES} limit')
    if len(meta['photos']) >= MAX_PHOTOS:
        raise WingmanError(f'set {meta["id"]} already holds {MAX_PHOTOS} photos')
    sha = hashlib.sha256(data).hexdigest()
    for p in meta['photos']:
        if p['sha256'] == sha:
            return {**p, 'duplicate': True}
    img = _open_bytes(data)
    fmt = (img.format or 'JPEG').lower()
    ext = {'jpeg': 'jpg', 'mpo': 'jpg'}.get(fmt, fmt)
    exif = _exif_summary(img)
    img = ImageOps.exif_transpose(img)
    pid = sha[:12]
    fname = f'{pid}.{ext}'
    with open(os.path.join(_set_dir(meta['id']), 'src', fname), 'wb') as f:
        f.write(data)
    rec = {'id': pid, 'name': os.path.basename(name or fname), 'file': fname,
           'sha256': sha, 'bytes': len(data), 'format': fmt,
           'w': img.width, 'h': img.height, 'added': time.time(), 'exif': exif}
    meta['photos'].append(rec)
    return rec


def _exif_summary(img):
    try:
        ex = img.getexif()
    except Exception:
        return {'present': False}
    if not ex:
        return {'present': False}
    out = {'present': True, 'gps': False}
    try:
        gps = ex.get_ifd(0x8825)
        out['gps'] = bool(gps and (2 in gps or 4 in gps))
    except Exception:
        pass
    make, model = ex.get(271), ex.get(272)
    if make or model:
        out['camera'] = ' '.join(str(x).strip() for x in (make, model) if x)
    try:
        taken = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if taken:
            out['taken'] = str(taken)
    except Exception:
        pass
    return out


def add(set_ref=None, data=None, path=None, dir=None, url=None, files=None,
        name=None, set_name=None):
    """Photos in. From base64, a path, a directory, a URL, or a list of
    {name, data} — into an existing set, or a new one if none is given."""
    meta = get_set(set_ref) if set_ref else new_set(set_name or name)
    added, skipped = [], []

    def take(blob, nm):
        try:
            rec = _add_bytes(meta, blob, nm)
            (skipped if rec.get('duplicate') else added).append(
                {'id': rec['id'], 'name': rec['name'], 'w': rec['w'], 'h': rec['h'],
                 **({'why': 'already in the set'} if rec.get('duplicate') else {})})
        except WingmanError as e:
            skipped.append({'name': nm, 'why': str(e)})

    if data:
        take(base64.b64decode(data.split(',', 1)[-1] if data.startswith('data:') else data),
             name or 'upload')
    for f in files or []:
        if isinstance(f, dict) and f.get('data'):
            d = f['data']
            take(d if isinstance(d, (bytes, bytearray)) else
                 base64.b64decode(d.split(',', 1)[-1] if d.startswith('data:') else d),
                 f.get('name') or 'upload')
    if path:
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            raise WingmanError(f'no file at {path}', status=404)
        with open(p, 'rb') as fh:
            take(fh.read(), os.path.basename(p))
    if dir:
        d = os.path.expanduser(dir)
        if not os.path.isdir(d):
            raise WingmanError(f'no directory at {dir}', status=404)
        for fn in sorted(os.listdir(d)):
            if os.path.splitext(fn)[1].lower() in EXTS:
                with open(os.path.join(d, fn), 'rb') as fh:
                    take(fh.read(), fn)
    if url:
        if not str(url).startswith(('http://', 'https://')):
            raise WingmanError('url= must be http(s)')
        req = urllib.request.Request(url, headers={'user-agent': 'wingman/0.1'})
        with urllib.request.urlopen(req, timeout=30) as r:
            take(r.read(MAX_BYTES + 1), name or url.rsplit('/', 1)[-1] or 'download')
    if not (data or files or path or dir or url):
        raise WingmanError('nothing to add — pass data=, files=, path=, dir= or url=')
    _save_set(meta)
    return {'set': meta['id'], 'name': meta['name'], 'added': added, 'skipped': skipped,
            'photos': len(meta['photos'])}


def remove(set_ref, photo_ref):
    meta = get_set(set_ref)
    p = _photo(meta, photo_ref)
    meta['photos'] = [x for x in meta['photos'] if x['id'] != p['id']]
    _save_set(meta)
    d = _set_dir(meta['id'])
    for sub in ('src', 'out', 'thumb'):
        for fn in os.listdir(os.path.join(d, sub)):
            if fn.startswith(p['id']):
                os.remove(os.path.join(d, sub, fn))
    audits = _read_json(os.path.join(d, 'audit.json'), {})
    audits.pop(p['id'], None)
    _write_json(os.path.join(d, 'audit.json'), audits)
    return {'removed': p['id'], 'photos': len(meta['photos'])}


def rename(set_ref, name):
    meta = get_set(set_ref)
    meta['name'] = str(name)
    _save_set(meta)
    return {'set': meta['id'], 'name': meta['name']}


def load_image(meta, p):
    path = os.path.join(_set_dir(meta['id']), 'src', p['file'])
    img = Image.open(path)
    img.load()
    icc = img.info.get('icc_profile')
    img = ImageOps.exif_transpose(img)
    if img.mode not in ('RGB',):
        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
            img = bg
        else:
            img = img.convert('RGB')
    if icc:
        img.info['icc_profile'] = icc
    return img


# ── faces ────────────────────────────────────────────────────────────────

_SESSION = None
_SESSION_ERR = None


def face_model_path():
    return os.path.join(MODELS_DIR, FACE_MODEL)


def fetch_face_model():
    """UltraFace RFB-320 — 1.2 MB, from the ONNX model zoo. Fetched once."""
    _ensure()
    path = face_model_path()
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return {'path': path, 'bytes': os.path.getsize(path), 'fetched': False}
    req = urllib.request.Request(FACE_MODEL_URL, headers={'user-agent': 'wingman/0.1'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if len(data) < 100_000:
        raise WingmanError('face model download came back too small')
    with open(path + '.tmp', 'wb') as f:
        f.write(data)
    os.replace(path + '.tmp', path)
    return {'path': path, 'bytes': len(data), 'fetched': True}


def _session():
    global _SESSION, _SESSION_ERR
    if _SESSION is not None or _SESSION_ERR is not None:
        return _SESSION
    try:
        import onnxruntime as ort
        fetch_face_model()
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _SESSION = ort.InferenceSession(face_model_path(), so,
                                        providers=['CPUExecutionProvider'])
    except Exception as e:
        _SESSION_ERR = f'{type(e).__name__}: {e}'
    return _SESSION


def detector():
    """Which face detector is live — the ONNX one, or the fallback."""
    s = _session()
    if s is not None:
        return {'name': 'ultraface-rfb-320', 'runtime': 'onnxruntime', 'ok': True,
                'model': face_model_path()}
    return {'name': 'skin-heuristic', 'runtime': 'numpy', 'ok': False,
            'why': _SESSION_ERR,
            'note': 'YCbCr skin-colour blob: it finds the largest patch of skin, '
                    'not a face — scores from it are marked uncertain'}


def _nms(boxes, scores, iou=0.3):
    order = np.argsort(-scores)
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        b = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / (a + b - inter + 1e-9) <= iou]
    return keep


def _faces_onnx(img, sess, threshold=FACE_THRESHOLD):
    W, H = img.size
    # letterbox into 320x240 so a tall phone photo is not squashed
    s = min(320 / W, 240 / H)
    nw, nh = max(1, round(W * s)), max(1, round(H * s))
    canvas = Image.new('RGB', (320, 240), (127, 127, 127))
    ox, oy = (320 - nw) // 2, (240 - nh) // 2
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), (ox, oy))
    arr = (np.asarray(canvas, dtype=np.float32) - 127.0) / 128.0
    arr = np.transpose(arr, (2, 0, 1))[None]
    scores, boxes = sess.run(None, {sess.get_inputs()[0].name: arr})
    probs, boxes = scores[0][:, 1], boxes[0]
    m = probs > threshold
    probs, boxes = probs[m], boxes[m]
    faces = []
    if probs.size:
        px = boxes * np.array([320, 240, 320, 240], dtype=np.float32)
        px[:, [0, 2]] = (px[:, [0, 2]] - ox) / s
        px[:, [1, 3]] = (px[:, [1, 3]] - oy) / s
        px[:, [0, 2]] = np.clip(px[:, [0, 2]], 0, W)
        px[:, [1, 3]] = np.clip(px[:, [1, 3]], 0, H)
        for i in _nms(px, probs):
            x0, y0, x1, y1 = [float(v) for v in px[i]]
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            faces.append({'box': [round(x0), round(y0), round(x1), round(y1)],
                          'score': round(float(probs[i]), 3),
                          'w': round(x1 - x0), 'h': round(y1 - y0)})
    faces.sort(key=lambda f: -(f['w'] * f['h']))
    return faces


def _faces_skin(img):
    """Fallback: the largest connected patch of skin-coloured pixels. It is a
    guess about where a face is, not a detection — score is capped at 0.5."""
    W, H = img.size
    s = 120 / max(W, H)
    small = img.resize((max(1, int(W * s)), max(1, int(H * s))), Image.BILINEAR)
    ycc = np.asarray(small.convert('YCbCr')).astype(np.int32)
    cb, cr = ycc[..., 1], ycc[..., 2]
    mask = (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best = None
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            stack, pts = [(y, x)], []
            seen[y, x] = True
            while stack:
                cy, cx = stack.pop()
                pts.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if best is None or len(pts) > len(best):
                best = pts
    if not best or len(best) < 0.01 * h * w:
        return []
    ys = [p[0] for p in best]
    xs = [p[1] for p in best]
    x0, x1 = min(xs) / s, (max(xs) + 1) / s
    y0, y1 = min(ys) / s, (max(ys) + 1) / s
    return [{'box': [round(x0), round(y0), round(x1), round(y1)],
             'score': round(min(0.5, len(best) / (h * w) * 5), 3),
             'w': round(x1 - x0), 'h': round(y1 - y0), 'heuristic': True}]


def detect_faces(img, threshold=FACE_THRESHOLD):
    sess = _session()
    if sess is not None:
        return {'detector': 'ultraface-rfb-320', 'faces': _faces_onnx(img, sess, threshold)}
    return {'detector': 'skin-heuristic', 'faces': _faces_skin(img)}


def faces(set_ref, photo_ref, threshold=FACE_THRESHOLD):
    meta = get_set(set_ref)
    p = _photo(meta, photo_ref)
    img = load_image(meta, p)
    out = detect_faces(img, threshold=float(threshold))
    return {'set': meta['id'], 'photo': p['id'], 'w': img.width, 'h': img.height, **out}


# ── measurement ──────────────────────────────────────────────────────────

def _gray(img, width=640):
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.BILINEAR)
    return np.asarray(ImageOps.grayscale(img), dtype=np.float32)


def _laplacian_var(g):
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def dhash(img):
    g = ImageOps.grayscale(img).resize((9, 8), Image.LANCZOS)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int(''.join('1' if b else '0' for b in bits), 2)


def hamming(a, b):
    return bin(int(a) ^ int(b)).count('1')


def _role(faces_, H):
    if not faces_:
        return 'scene', 0.0
    f0 = faces_[0]
    ff = f0['h'] / H
    if len(faces_) > 1 and faces_[1]['h'] >= 0.5 * f0['h']:
        return 'group', ff
    if ff >= 0.28:
        return 'headshot', ff
    if ff >= 0.12:
        return 'portrait', ff
    if ff >= 0.04:
        return 'full', ff
    return 'far', ff


def measure(img):
    """Every number the audit uses, from one decoded image."""
    W, H = img.size
    g = _gray(img)
    lum_mean, lum_std = float(g.mean()), float(g.std())
    clip_dark = float((g < 8).mean())
    clip_bright = float((g > 247).mean())
    sharp = _laplacian_var(g)
    hsv = np.asarray(img.resize((160, max(1, round(160 * H / W))), Image.BILINEAR)
                     .convert('HSV'), dtype=np.float32)
    sat = float(hsv[..., 1].mean() / 255)
    det = detect_faces(img)
    fs = det['faces']
    face_sharp = None
    if fs:
        x0, y0, x1, y1 = fs[0]['box']
        pad = int(0.1 * (x1 - x0))
        crop = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)))
        if crop.width >= 16 and crop.height >= 16:
            face_sharp = _laplacian_var(_gray(crop.resize(
                (200, max(1, round(200 * crop.height / crop.width))), Image.LANCZOS), 200))
    role, ff = _role(fs, H)
    return {
        'w': W, 'h': H, 'megapixels': round(W * H / 1e6, 2),
        'aspect': round(W / H, 3), 'orientation': 'portrait' if H > W else
        ('landscape' if W > H else 'square'),
        'luminance': round(lum_mean, 1), 'contrast': round(lum_std, 1),
        'clipped_dark': round(clip_dark, 4), 'clipped_bright': round(clip_bright, 4),
        'sharpness': round(sharp, 1), 'face_sharpness': None if face_sharp is None
        else round(face_sharp, 1), 'saturation': round(sat, 3),
        'detector': det['detector'], 'faces': fs, 'face_count': len(fs),
        'face_fraction': round(ff, 3),
        'face_center': [round((fs[0]['box'][0] + fs[0]['box'][2]) / 2 / W, 3),
                        round((fs[0]['box'][1] + fs[0]['box'][3]) / 2 / H, 3)] if fs else None,
        'role': role, 'dhash': dhash(img),
    }


def _issues(m, exif):
    """Each issue costs points and says why. `bad` disqualifies a photo from
    leading; `warn` is a real weakness; `info` is worth knowing."""
    out = []

    def add_(code, sev, cost, text):
        out.append({'code': code, 'severity': sev, 'cost': cost, 'text': text})

    short = min(m['w'], m['h'])
    if short < 640:
        add_('low-res', 'bad', 30, f'{m["w"]}x{m["h"]} — under 640 px on the short '
             'side; every app will upscale it and it will look soft')
    elif short < 1000:
        add_('res', 'warn', 10, f'{m["w"]}x{m["h"]} — fine on a phone, thin for a '
             '1080-wide card')
    if m['sharpness'] < 25:
        add_('blurry', 'bad', 35, f'blurry (sharpness {m["sharpness"]}, under 25)')
    elif m['sharpness'] < 60:
        add_('soft', 'warn', 12, f'soft focus (sharpness {m["sharpness"]}, under 60)')
    if m['face_sharpness'] is not None and m['face_count'] and m['sharpness'] >= 25:
        if m['face_sharpness'] < 20:
            add_('face-soft', 'warn', 15, f'the face itself is soft (face sharpness '
                 f'{m["face_sharpness"]}) even if the frame is not')
    if m['luminance'] < 60:
        add_('dark', 'warn', 20, f'underexposed (mean luminance {m["luminance"]}/255)')
    elif m['luminance'] > 200:
        add_('bright', 'warn', 15, f'overexposed (mean luminance {m["luminance"]}/255)')
    if m['clipped_bright'] > 0.08:
        add_('blown', 'warn', 10, f'{round(m["clipped_bright"]*100)}% of pixels are '
             'blown-out white — sky or a window behind you')
    if m['clipped_dark'] > 0.25:
        add_('crushed', 'warn', 8, f'{round(m["clipped_dark"]*100)}% of pixels are '
             'pure black — detail is gone there')
    if m['contrast'] < 30:
        add_('flat', 'warn', 8, f'low contrast (std {m["contrast"]}) — hazy or '
             'flat light; polish lifts this a little')
    if m['saturation'] < 0.06:
        add_('mono', 'info', 3, 'black-and-white or nearly — one is fine, a set '
             'of them reads as a filter')
    fc = m['face_count']
    if fc == 0:
        add_('no-face', 'info', 15, 'no face found — usable as a wider environment '
             'or activity shot, cannot lead')
    elif m['role'] == 'group':
        add_('group', 'warn', 25, f'{fc} faces of similar size — a group shot; '
             'never first, at most one in the set')
    else:
        ff = m['face_fraction']
        if ff < 0.04:
            add_('face-far', 'warn', 20, f'face is {round(ff*100, 1)}% of the frame '
                 'height — too far away to read')
        elif ff > 0.6:
            add_('face-tight', 'warn', 10, f'face is {round(ff*100)}% of the frame — '
                 'so close there is no room to crop')
        if fc > 1:
            add_('others', 'info', 5, f'{fc - 1} other smaller face(s) in frame')
        cx, cy = m['face_center']
        if cx < 0.15 or cx > 0.85:
            add_('off-centre', 'info', 5, 'face is at the edge of the frame — the '
                 'crop re-centres it if there is room')
        if m['faces'][0].get('score', 1) < 0.85 or m['detector'] != 'ultraface-rfb-320':
            add_('face-uncertain', 'info', 5,
                 'face detection is uncertain here — check the box on the thumbnail')
    if exif.get('gps'):
        add_('gps', 'info', 0, 'GPS coordinates in EXIF — stripped on render')
    return out


LEAD_BLOCKERS = {'soft', 'face-soft', 'dark', 'bright', 'face-far', 'face-tight', 'blown'}


def _lead_ok(a):
    """Lead material: a solo headshot or head-and-shoulders, sharp, lit, with
    the face 12-55% of the frame, nothing bad and none of the warnings that
    would make it the weakest first impression in the set."""
    codes = {i['code'] for i in a['issues']}
    return (a['role'] in ('headshot', 'portrait') and
            not any(i['severity'] == 'bad' for i in a['issues']) and
            not (codes & LEAD_BLOCKERS) and
            0.12 <= a['face_fraction'] <= 0.55 and a['score'] >= 60)


def audit_photo(meta, p, force=False):
    d = _set_dir(meta['id'])
    audits = _read_json(os.path.join(d, 'audit.json'), {})
    a = audits.get(p['id'])
    if a and a.get('v') == AUDIT_VERSION and not force:
        return a
    img = load_image(meta, p)
    m = measure(img)
    issues = _issues(m, p.get('exif') or {})
    score = max(0, 100 - sum(i['cost'] for i in issues))
    a = {'v': AUDIT_VERSION, 'photo': p['id'], 'name': p['name'], **m,
         'issues': issues, 'score': score, 'exif': p.get('exif') or {}}
    a['lead_ok'] = _lead_ok(a)
    a['verdict'] = _verdict(a)
    audits = _read_json(os.path.join(d, 'audit.json'), {})     # re-read: threads
    audits[p['id']] = a
    _write_json(os.path.join(d, 'audit.json'), audits)
    return a


def _verdict(a):
    if a['lead_ok']:
        return 'lead material — sharp solo face, well framed'
    bad = [i for i in a['issues'] if i['severity'] == 'bad']
    if bad:
        return 'skip — ' + bad[0]['text'].split(' — ')[0].split(' (')[0]
    if a['role'] == 'group':
        return 'group shot — later slot only'
    if a['role'] == 'scene':
        return 'no face — a wider shot for a later slot'
    if a['role'] == 'far':
        return 'face too small — a wide shot at best'
    if a['role'] == 'full':
        return 'full-body — good in slot 2-4'
    warn = [i for i in a['issues'] if i['severity'] == 'warn']
    if warn:
        return 'usable — ' + warn[0]['text'].split(' (')[0].split(' — ')[0]
    return 'usable'


def audit(set_ref, photo=None, force=False):
    meta = get_set(set_ref)
    photos = [_photo(meta, photo)] if photo else meta['photos']
    rows = [audit_photo(meta, p, force=force) for p in photos]
    rows_sorted = sorted(rows, key=lambda a: -a['score'])
    roles = {}
    for a in rows:
        roles[a['role']] = roles.get(a['role']) or 0
        roles[a['role']] += 1
    return {
        'set': meta['id'], 'name': meta['name'], 'detector': detector()['name'],
        'photos': [{k: a[k] for k in ('photo', 'name', 'w', 'h', 'role', 'score',
                                     'lead_ok', 'verdict', 'issues', 'face_count',
                                     'face_fraction', 'sharpness', 'luminance',
                                     'faces', 'detector')} for a in rows_sorted],
        'roles': roles, 'lead_candidates': sum(1 for a in rows if a['lead_ok']),
        'not_measured': GUIDE['not_measured'],
    }


# ── lineup ───────────────────────────────────────────────────────────────

ROLE_ORDER = ['portrait', 'full', 'headshot', 'scene', 'portrait', 'full', 'far', 'group']


def lineup(set_ref, n=6, dup_bits=10, min_score=35, allow_group=True, force=False):
    """The best N, in the order they should go up. One clear solo face leads;
    then variety; near-duplicates collapse to the sharper one; group shots
    never in the first three and at most one."""
    meta = get_set(set_ref)
    n = max(1, min(int(n), 9))
    if not meta['photos']:
        raise WingmanError(f'set {meta["id"]} has no photos yet')
    auds = [audit_photo(meta, p, force=force) for p in meta['photos']]
    auds.sort(key=lambda a: -a['score'])
    kept, left = [], []
    for a in auds:
        twin = next((k for k in kept if hamming(a['dhash'], k['dhash']) <= dup_bits), None)
        if twin:
            left.append({'photo': a['photo'], 'name': a['name'], 'score': a['score'],
                         'why': f'near-duplicate of {twin["name"]} (kept the higher-scoring)'})
        else:
            kept.append(a)
    slots, used = [], set()

    def take(a, why):
        used.add(a['photo'])
        slots.append({'slot': len(slots) + 1, 'photo': a['photo'], 'name': a['name'],
                      'role': a['role'], 'score': a['score'], 'why': why})

    gaps = []
    lead = next((a for a in kept if a['lead_ok']), None)
    if lead:
        take(lead, 'lead — sharp solo face, ' + f'{round(lead["face_fraction"]*100)}% of frame')
    else:
        solo = next((a for a in kept if a['face_count'] >= 1 and a['role'] != 'group'
                     and a['score'] >= min_score), None)
        if solo:
            take(solo, 'lead by default — best solo face, but not ideal: ' + solo['verdict'])
            gaps.append('no photo qualifies as a proper lead (sharp, solo, face '
                        '15-45% of the frame) — this is the one to reshoot')
        elif kept:
            take(kept[0], 'lead by default — nothing with a clear face scored high enough')
            gaps.append('no usable solo face photo in the set — the lead is a guess')
    def clean(a):
        return not any(i['severity'] == 'bad' for i in a['issues'])

    def group_blocked(a):
        return a['role'] == 'group' and (not allow_group or len(slots) < 3 or
                                         any(s['role'] == 'group' for s in slots))

    # variety first, but never at the price of a `bad` issue: a low-res or
    # blurry wide shot does not beat a sharp headshot just for being wide
    for want in ROLE_ORDER:
        if len(slots) >= n:
            break
        if want == 'group' and (not allow_group or len(slots) < 3):
            continue
        cand = next((a for a in kept if a['photo'] not in used and a['role'] == want
                     and a['score'] >= min_score and clean(a)), None)
        if cand:
            take(cand, f'{want} — variety; score {cand["score"]}')
    for pass_ in ('clean', 'flawed'):
        for a in kept:
            if len(slots) >= n:
                break
            if a['photo'] in used or a['score'] < min_score or group_blocked(a):
                continue
            if (pass_ == 'clean') != clean(a):
                continue
            if pass_ == 'clean':
                take(a, f'filler — best remaining, score {a["score"]}')
            else:
                bad = next(i for i in a['issues'] if i['severity'] == 'bad')
                take(a, f'last resort — {bad["text"].split(" — ")[0]}; score {a["score"]}')
    for a in kept:
        if a['photo'] in used:
            continue
        if a['score'] < min_score:
            top = a['issues'][0]['text'] if a['issues'] else 'low score'
            why = f'score {a["score"]} under {min_score}: {top}'
        elif a['role'] == 'group':
            why = 'second group shot — one is the limit' if any(
                s['role'] == 'group' for s in slots) else 'group shot with no late slot free'
        else:
            why = f'ran out of slots (n={n}); score {a["score"]}'
        left.append({'photo': a['photo'], 'name': a['name'], 'score': a['score'], 'why': why})
    roles = [s['role'] for s in slots]
    if 'full' not in roles and 'far' not in roles:
        gaps.append('no full-body shot — every app\'s guidance asks for one')
    if 'scene' not in roles and len(slots) >= 4:
        gaps.append('every photo is a face shot — one wider environment or activity '
                    'shot adds range')
    if roles.count('headshot') >= 3:
        gaps.append(f'{roles.count("headshot")} headshots — the same framing three '
                    'times reads as one photo')
    if len(slots) < min(n, 4):
        gaps.append(f'only {len(slots)} usable photos for {n} slots — the set needs '
                    'more raw material, not more editing')
    return {'set': meta['id'], 'name': meta['name'], 'n': n, 'slots': slots,
            'left_out': left, 'gaps': gaps, 'detector': detector()['name'],
            'rule': 'lead = sharp solo face 12-55% of frame, sharp and lit; then portrait, full, '
                    'headshot, scene; near-duplicates collapsed; group shots after '
                    'slot 3 and at most one'}


# ── render ───────────────────────────────────────────────────────────────

def _parse_ratio(ratio):
    if ratio is None:
        return None
    if isinstance(ratio, (list, tuple)) and len(ratio) == 2:
        return float(ratio[0]), float(ratio[1])
    s = str(ratio).replace('x', ':').replace('/', ':')
    if ':' in s:
        a, b = s.split(':', 1)
        return float(a), float(b)
    return float(s), 1.0


def preset_spec(preset=None, ratio=None, size=None):
    name = (preset or DEFAULT_PRESET).lower()
    if name not in PRESETS and ratio is None:
        raise WingmanError(f'no preset {name!r} — {", ".join(PRESETS)}; or pass ratio=W:H')
    spec = dict(PRESETS.get(name) or {'ratio': [4, 5], 'size': [1080, 1350], 'note': 'custom'})
    if ratio is not None:
        rw, rh = _parse_ratio(ratio)
        spec['ratio'] = [rw, rh]
        spec['size'] = [1080, round(1080 * rh / rw)]
    if size:
        sw, sh = _parse_ratio(size)
        spec['size'] = [int(sw), int(sh)]
    spec['name'] = name
    return spec


def plan_crop(W, H, faces_, ar, zoom='auto', target_h=1350):
    """Where to cut. ar = width/height of the output. Returns the box and how
    it was decided, so the report can say it and the console can draw it."""
    max_ch = min(H, W / ar)                    # the tallest crop of this aspect that fits
    subj = None
    if faces_:
        f0 = faces_[0]
        group = len(faces_) > 1 and faces_[1]['h'] >= 0.5 * f0['h']
        if group:
            xs0 = min(f['box'][0] for f in faces_ if f['h'] >= 0.5 * f0['h'])
            ys0 = min(f['box'][1] for f in faces_ if f['h'] >= 0.5 * f0['h'])
            xs1 = max(f['box'][2] for f in faces_ if f['h'] >= 0.5 * f0['h'])
            ys1 = max(f['box'][3] for f in faces_ if f['h'] >= 0.5 * f0['h'])
            subj, want = [xs0, ys0, xs1, ys1], 0.45
        else:
            subj, want = f0['box'], 0.30
    if zoom == 'none' or subj is None:
        ch = max_ch
        how = 'largest crop of the aspect' + (' — no face to anchor' if subj is None else '')
    elif zoom == 'auto':
        sh = subj[3] - subj[1]
        ch = min(max_ch, max(sh / want, sh * 2.2))
        # never enlarge the source more than 1.5x to fill the preset
        ch = max(ch, min(max_ch, target_h / 1.5))
        how = f'face {round(sh / ch * 100)}% of crop height (auto)'
    else:
        z = max(1.0, float(zoom))
        ch = max_ch / z
        how = f'zoom {z}x'
    cw = ch * ar
    if subj is not None:
        fcx = (subj[0] + subj[2]) / 2
        fcy = (subj[1] + subj[3]) / 2
        x0 = fcx - cw / 2
        y0 = fcy - 0.40 * ch
        head = subj[1] - 0.08 * ch                # headroom: 8% of the crop above the face
        if y0 > head:
            y0 = head
    else:
        x0 = (W - cw) / 2
        y0 = (H - ch) * 0.35
    x0 = min(max(0.0, x0), W - cw)
    y0 = min(max(0.0, y0), H - ch)
    box = [int(round(x0)), int(round(y0)), int(round(x0 + cw)), int(round(y0 + ch))]
    return box, how


def polish(img, mode='auto'):
    """Gentle, global, reversible-in-spirit: contrast stretch, exposure lift
    when the frame is dark, a hair of colour, a light sharpen. No skin work,
    no warping, no background replacement — this is a crop-and-clean, not a
    filter."""
    applied = []
    if mode in ('none', False, 'off'):
        return img, applied
    g = np.asarray(ImageOps.grayscale(img.resize((160, max(1, round(160 * img.height / img.width))))),
                   dtype=np.float32)
    mean, std = float(g.mean()), float(g.std())
    ac = ImageOps.autocontrast(img, cutoff=0.5)
    img = Image.blend(img, ac, 0.6)
    applied.append('autocontrast 0.5% (60% blend)')
    if mean < 95 or mean > 165:
        target = 118.0
        gamma = math.log(target / 255) / math.log(max(1, mean) / 255)
        gamma = min(1.25, max(0.75, gamma))
        lut = [round(255 * ((i / 255) ** gamma)) for i in range(256)]
        img = img.point(lut * 3)
        applied.append(f'gamma {round(gamma, 2)} (mean {round(mean)} → ~{round(target)})')
    if std < 30:
        img = ImageEnhance.Contrast(img).enhance(1.12)
        applied.append('contrast x1.12 (flat frame)')
    img = ImageEnhance.Color(img).enhance(1.04)
    applied.append('colour x1.04')
    img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=55, threshold=3))
    applied.append('unsharp r1.0 55%')
    return img, applied


def _to_srgb(img):
    icc = img.info.get('icc_profile')
    if not icc:
        return img, False
    try:
        from PIL import ImageCms
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile('sRGB')
        out = ImageCms.profileToProfile(img, src, dst, outputMode='RGB')
        out.info.pop('icc_profile', None)
        return out, True
    except Exception:
        img.info.pop('icc_profile', None)
        return img, False


def render_one(meta, p, spec, zoom='auto', polish_mode='auto', quality=90, force=False):
    d = _set_dir(meta['id'])
    out_name = f'{p["id"]}-{spec["name"]}.jpg'
    out_path = os.path.join(d, 'out', out_name)
    rep_path = out_path + '.json'
    if os.path.exists(out_path) and os.path.exists(rep_path) and not force:
        rep = _read_json(rep_path)
        if rep and rep.get('zoom') == str(zoom) and rep.get('polish_mode') == str(polish_mode) \
                and rep.get('size') == spec['size'] and rep.get('v') == AUDIT_VERSION:
            return {**rep, 'cached': True}
    img = load_image(meta, p)
    W, H = img.size
    a = audit_photo(meta, p)
    rw, rh = spec['ratio']
    ar = rw / rh
    tw, th = spec['size']
    box, how = plan_crop(W, H, a['faces'], ar, zoom=zoom, target_h=th)
    crop = img.crop(tuple(box))
    icc = img.info.get('icc_profile')
    if icc:
        crop.info['icc_profile'] = icc
    upscale = round(tw / max(1, crop.width), 2)
    crop, converted = _to_srgb(crop)
    crop = crop.resize((tw, th), Image.LANCZOS)
    crop, applied = polish(crop, polish_mode)
    buf = io.BytesIO()
    crop.save(buf, 'JPEG', quality=int(quality), optimize=True, progressive=True,
              subsampling=1)          # no exif=, no icc_profile= : metadata is gone
    data = buf.getvalue()
    with open(out_path, 'wb') as f:
        f.write(data)
    rep = {'v': AUDIT_VERSION, 'set': meta['id'], 'photo': p['id'], 'name': p['name'],
           'preset': spec['name'], 'ratio': f'{rw:g}:{rh:g}', 'size': [tw, th],
           'file': out_name, 'bytes': len(data), 'source': [W, H], 'crop': box,
           'crop_how': how, 'face_used': a['faces'][0]['box'] if a['faces'] else None,
           'role': a['role'], 'upscale': upscale, 'zoom': str(zoom),
           'polish_mode': str(polish_mode), 'polish': applied,
           'icc_to_srgb': converted, 'quality': int(quality),
           'stripped': ['exif', 'gps', 'icc', 'xmp', 'thumbnail'],
           'warnings': ([f'source crop was upscaled {upscale}x to fill {tw}x{th}']
                        if upscale > 1.5 else [])}
    _write_json(rep_path, rep)
    return rep


def render(set_ref, photo=None, preset=None, ratio=None, size=None, zoom='auto',
           polish_mode='auto', quality=90, force=False, only_lineup=False, n=6):
    meta = get_set(set_ref)
    spec = preset_spec(preset, ratio, size)
    if photo:
        photos = [_photo(meta, photo)]
    elif only_lineup:
        lu = lineup(meta['id'], n=n)
        photos = [_photo(meta, s['photo']) for s in lu['slots']]
    else:
        photos = meta['photos']
    if not photos:
        raise WingmanError(f'set {meta["id"]} has no photos yet')
    reps = [render_one(meta, p, spec, zoom=zoom, polish_mode=polish_mode,
                       quality=quality, force=force) for p in photos]
    return {'set': meta['id'], 'preset': spec, 'rendered': reps, 'count': len(reps),
            'promise': 'no face retouching, no background replacement; crop, '
                       'contrast, exposure, a light sharpen; all metadata stripped'}


def rendered_path(set_ref, photo_ref, preset=None):
    meta = get_set(set_ref)
    p = _photo(meta, photo_ref)
    name = (preset or DEFAULT_PRESET).lower()
    path = os.path.join(_set_dir(meta['id']), 'out', f'{p["id"]}-{name}.jpg')
    if not os.path.exists(path):
        raise WingmanError(f'{p["id"]} not rendered for {name} yet — POST /render first',
                           status=404)
    return path


def thumb(set_ref, photo_ref, w=320):
    """A JPEG thumbnail of the source, cached. What the console shows."""
    meta = get_set(set_ref)
    p = _photo(meta, photo_ref)
    w = int(max(64, min(int(w), 1600)))
    path = os.path.join(_set_dir(meta['id']), 'thumb', f'{p["id"]}-{w}.jpg')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    img = load_image(meta, p)
    img.info.pop('icc_profile', None)
    if img.width > w:
        img = img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=82)
    data = buf.getvalue()
    with open(path, 'wb') as f:
        f.write(data)
    return data


# ── export ───────────────────────────────────────────────────────────────

def export(set_ref, preset=None, n=6, zoom='auto', polish_mode='auto', quality=90,
           force=False):
    """The lineup, rendered for one app, zipped in slot order with the report."""
    meta = get_set(set_ref)
    spec = preset_spec(preset)
    lu = lineup(meta['id'], n=n)
    reps = []
    for s in lu['slots']:
        p = _photo(meta, s['photo'])
        reps.append({'slot': s['slot'], 'why': s['why'],
                     **render_one(meta, p, spec, zoom=zoom, polish_mode=polish_mode,
                                  quality=quality, force=force)})
    d = _set_dir(meta['id'])
    zname = f'wingman-{spec["name"]}.zip'
    zpath = os.path.join(d, zname)
    report = {'set': meta['id'], 'name': meta['name'], 'preset': spec,
              'lineup': lu, 'renders': reps, 'made': time.time(),
              'guide': GUIDE, 'detector': detector()['name']}
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for r in reps:
            stem = os.path.splitext(r['name'])[0][:40].replace(' ', '_')
            z.write(os.path.join(d, 'out', r['file']),
                    f'{r["slot"]:02d}-{stem}-{spec["name"]}.jpg')
        z.writestr('report.json', json.dumps(report, indent=2, default=str))
    return {'set': meta['id'], 'preset': spec['name'], 'zip': zpath, 'file': zname,
            'bytes': os.path.getsize(zpath),
            'files': [f'{r["slot"]:02d}-{r["name"]}' for r in reps],
            'slots': lu['slots'], 'gaps': lu['gaps'], 'left_out': len(lu['left_out'])}


def zip_path(set_ref, preset=None):
    meta = get_set(set_ref)
    path = os.path.join(_set_dir(meta['id']), f'wingman-{(preset or DEFAULT_PRESET).lower()}.zip')
    if not os.path.exists(path):
        raise WingmanError('nothing exported yet — POST /export first', status=404)
    return path


# ── health ───────────────────────────────────────────────────────────────

def health():
    import PIL
    try:
        import onnxruntime as ort
        ort_v = ort.__version__
    except Exception:
        ort_v = None
    try:
        from PIL import ImageCms          # noqa: F401
        cms = True
    except Exception:
        cms = False
    _ensure()
    return {'ok': True, 'pillow': PIL.__version__, 'numpy': np.__version__,
            'onnxruntime': ort_v, 'heif': HEIF, 'icc': cms, 'detector': detector(),
            'state': STATE_DIR, 'sets': len(os.listdir(SETS_DIR)),
            'presets': list(PRESETS), 'limits': {'max_bytes': MAX_BYTES,
                                                 'max_photos': MAX_PHOTOS},
            'privacy': 'photos never leave this box; renders carry no metadata; '
                       'a set is reachable only by its unguessable id'}
