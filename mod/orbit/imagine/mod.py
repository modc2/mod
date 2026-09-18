"""
imagine — an image editor that speaks Agent Protocol v1.

    m imagine                                        # what this box can do
    m imagine/providers                              # who can run which op, and why not
    m imagine/new_project name=posters
    m imagine/generate prompt="a lighthouse at dusk" project=posters
    m imagine/edit image=<image_id> prompt="make it winter"
    m imagine/upscale image=<image_id> scale=2
    m imagine/gallery project=posters               # the images, newest first
    m imagine/lineage image=<image_id>              # how that image came to be
    m imagine/export image=<image_id> path=~/out.png
    m imagine/serve                                  # console + API + /ap/v1 on :50560

WHY THE AGENT PROTOCOL
    Editing an image is already the shape the Agent Protocol describes, so this
    module does not invent a second vocabulary for it:

        project  a workspace                (ours — AP has no such noun)
        task     one editing session        POST /ap/v1/agent/tasks
        step     one operation on an image  POST /ap/v1/agent/tasks/{id}/steps
        artifact the image that came back   GET  /ap/v1/agent/tasks/{id}/artifacts

    The protocol permits a step to be `running` when the POST returns, which is
    the whole reason this fits: image work takes tens of seconds, so every step
    is queued to a background worker and the caller polls. Nothing holds a
    socket open, the console renders a live queue, and any Agent Protocol client
    can drive this editor knowing only the protocol.

BACKGROUND WORK
    `serve` starts N worker threads (IMAGINE_WORKERS, default 2) draining one
    queue. A step POSTed to a running server comes back `running` and completes
    later; the same call from the CLI runs inline, because a one-shot process
    has nowhere to put a background thread. Steps left `running` by a server
    that died are failed on the next start rather than waiting forever.

IMAGES ARE CONTENT-ADDRESSED, EDITS ARE A GRAPH
    An image is stored under the SHA-256 of its own bytes, so the same bytes are
    the same image no matter which task produced them. Every artifact records
    the image it was made from, which makes a project the DAG of edits that
    produced it rather than a folder — `lineage` walks it back to the prompt
    that started it.

TWO PROVIDERS, ONE OPERATION TABLE
    Venice does generate/edit/upscale, OpenRouter does generate/edit. An op
    resolves to a provider that both supports it and has a key on this box, so a
    missing key narrows the menu instead of breaking the module. Keys are read
    from the fleet's own stores — the `venice` and `model.openrouter` modules —
    or from the environment; this module keeps none of its own.
"""
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
STATE = Path(os.path.expanduser('~/.mod/imagine'))
BLOBS = STATE / 'blobs'
PORT = int(os.environ.get('IMAGINE_PORT', 50560))
WORKERS = int(os.environ.get('IMAGINE_WORKERS', 2))
AP_MAX_STEPS = int(os.environ.get('IMAGINE_MAX_STEPS', 64))
HTTP_TIMEOUT = int(os.environ.get('IMAGINE_HTTP_TIMEOUT', 300))

OPS = ('generate', 'edit', 'upscale')
PROVIDER_OPS = {'venice': ('generate', 'edit', 'upscale'),
                'openrouter': ('generate', 'edit')}
DEFAULT_MODEL = {'venice': 'venice-sd35',
                 'openrouter': 'google/gemini-2.5-flash-image'}
MAGIC = ((b'\x89PNG', 'png', 'image/png'),
         (b'\xff\xd8\xff', 'jpg', 'image/jpeg'),
         (b'GIF8', 'gif', 'image/gif'),
         (b'RIFF', 'webp', 'image/webp'))

_LOCK = threading.RLock()
_QUEUE: 'queue.Queue' = queue.Queue()
_WORKERS: list = []
_SERVING = False


# ── tiny helpers ─────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


def _iso(ts: int = None) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts or _now()))


def _uid(prefix: str = '') -> str:
    raw = f'{prefix}{time.time()}{os.urandom(8).hex()}'.encode()
    return f'{prefix}{hashlib.sha256(raw).hexdigest()[:20]}' if prefix else \
        hashlib.sha256(raw).hexdigest()[:20]


def _slug(text: str, n: int = 32) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return s[:n] or _uid()


def _read(name: str, default):
    path = STATE / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except ValueError:
        return default


def _write(name: str, value) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / f'.{name}.tmp'
    tmp.write_text(json.dumps(value, indent=2, default=str))
    tmp.replace(STATE / name)


def _fleet():
    """The mod framework, and never this file.

    `import mod` from inside a module directory finds that module's own mod.py,
    because Python puts the script's directory first on the path. Running
    `python3 mod.py serve` therefore imports *this* file as `mod` and every
    fleet lookup silently comes back empty. So: take this directory off the
    path, import the real package, and put the path back."""
    cached = sys.modules.get('mod')
    if cached is not None and hasattr(cached, 'mod'):
        return cached
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if p not in (str(DIR), '', '.')]
    sys.modules.pop('mod', None)
    try:
        import mod as framework
        return framework if hasattr(framework, 'mod') else None
    except Exception:                                         # noqa: BLE001
        return None
    finally:
        sys.path[:] = saved


def _http_json(url: str, body: dict, headers: dict, timeout: int = None):
    """POST JSON, return (bytes, content-type). Errors carry the server's text."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={'content-type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
            return r.read(), (r.headers.get('content-type') or '').split(';')[0].strip()
    except urllib.error.HTTPError as e:
        detail = (e.read() or b'')[:400].decode('utf-8', 'replace')
        raise RuntimeError(f'{url} → {e.code}: {detail}') from None
    except urllib.error.URLError as e:
        raise RuntimeError(f'{url} unreachable: {e.reason}') from None


# ── the blob store: an image is the SHA-256 of its own bytes ─────────────

def _sniff(data: bytes):
    for magic, ext, mime in MAGIC:
        if data.startswith(magic):
            return ext, mime
    return 'png', 'image/png'


def _put_blob(data: bytes) -> dict:
    if not data:
        raise ValueError('refusing to store zero bytes as an image')
    ext, mime = _sniff(data)
    image_id = hashlib.sha256(data).hexdigest()[:32]
    BLOBS.mkdir(parents=True, exist_ok=True)
    path = BLOBS / f'{image_id}.{ext}'
    if not path.exists():
        path.write_bytes(data)
    return {'image_id': image_id, 'ext': ext, 'mime': mime, 'bytes': len(data)}


def _blob_path(image_id: str):
    if not re.fullmatch(r'[0-9a-f]{6,64}', image_id or ''):
        return None
    hits = sorted(BLOBS.glob(f'{image_id}*.*')) if BLOBS.exists() else []
    return hits[0] if hits else None


def _read_blob(image_id: str) -> bytes:
    path = _blob_path(image_id)
    if not path:
        raise ValueError(f'no such image: {image_id}')
    return path.read_bytes()


def _b64(image_id: str) -> str:
    return base64.b64encode(_read_blob(image_id)).decode()


def _data_url(image_id: str) -> str:
    data = _read_blob(image_id)
    _, mime = _sniff(data)
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'


# ── providers ────────────────────────────────────────────────────────────

def _key(provider: str) -> str:
    """Env, then this module's own override, then the fleet module that owns
    the key. Nothing is written here — a key lives where the fleet keeps it."""
    env = {'venice': 'VENICE_API_KEY', 'openrouter': 'OPENROUTER_API_KEY'}[provider]
    if os.environ.get(env):
        return os.environ[env]
    local = _read('keys.json', {}).get(provider)
    if local:
        return local
    m = _fleet()
    if not m:
        return ''
    try:
        owner = {'venice': 'venice', 'openrouter': 'model.openrouter'}[provider]
        key = m.mod(owner)().api_key()
        return key.strip() if isinstance(key, str) and key.strip() else ''
    except Exception:                                         # noqa: BLE001
        return ''


def _venice(op: str, args: dict) -> bytes:
    key = _key('venice')
    if not key:
        raise RuntimeError('venice has no key on this box')
    base, headers = 'https://api.venice.ai/api/v1', {'authorization': f'Bearer {key}'}
    if op == 'generate':
        body = {'model': args.get('model') or DEFAULT_MODEL['venice'],
                'prompt': args['prompt'], 'format': 'webp', 'safe_mode': False}
        if args.get('aspect_ratio'):
            body['aspect_ratio'] = args['aspect_ratio']
        else:
            body['width'] = int(args.get('width') or 1024)
            body['height'] = int(args.get('height') or 1024)
        if args.get('negative_prompt'):
            body['negative_prompt'] = args['negative_prompt']
        raw, ctype = _http_json(f'{base}/image/generate', body, headers)
        if ctype.startswith('image/'):
            return raw
        images = (json.loads(raw) or {}).get('images') or []
        if not images:
            raise RuntimeError('venice image/generate returned no image')
        return base64.b64decode(images[0])
    if op == 'edit':
        body = {'prompt': args['prompt'], 'image': _b64(args['image']),
                'output_format': 'png', 'safe_mode': False}
        if args.get('model'):
            body['model'] = args['model']
        return _http_json(f'{base}/image/edit', body, headers)[0]
    if op == 'upscale':
        body = {'image': _b64(args['image']),
                'scale': max(1, min(4, int(args.get('scale') or 2))),
                'enhance': bool(args.get('enhance'))}
        return _http_json(f'{base}/image/upscale', body, headers)[0]
    raise ValueError(f'venice cannot {op}')


def _openrouter(op: str, args: dict) -> bytes:
    """OpenRouter returns images inside a chat completion: an image model is
    asked for the `image` modality and answers with a data URL."""
    key = _key('openrouter')
    if not key:
        raise RuntimeError('openrouter has no key on this box')
    if op not in ('generate', 'edit'):
        raise ValueError(f'openrouter cannot {op}')
    content = [{'type': 'text', 'text': args['prompt']}]
    if op == 'edit':
        content.append({'type': 'image_url',
                        'image_url': {'url': _data_url(args['image'])}})
    body = {'model': args.get('model') or DEFAULT_MODEL['openrouter'],
            'messages': [{'role': 'user', 'content': content}],
            'modalities': ['image', 'text']}
    raw, _ = _http_json('https://openrouter.ai/api/v1/chat/completions', body,
                        {'authorization': f'Bearer {key}',
                         'http-referer': 'https://modc2.com/imagine',
                         'x-title': 'imagine'})
    payload = json.loads(raw)
    if payload.get('error'):
        raise RuntimeError(f"openrouter: {payload['error'].get('message')}")
    message = ((payload.get('choices') or [{}])[0].get('message')) or {}
    for image in message.get('images') or []:
        url = ((image.get('image_url') or {}).get('url')) or ''
        if url.startswith('data:'):
            return base64.b64decode(url.split(',', 1)[1])
    raise RuntimeError('openrouter returned no image — is that model an image model?')


RUNNERS = {'venice': _venice, 'openrouter': _openrouter}


def _resolve(op: str, provider: str = None) -> str:
    """Pick a provider that can do this op and has a key, or say why not."""
    if op not in OPS:
        raise ValueError(f'no such op: {op} (have {", ".join(OPS)})')
    if provider:
        if op not in PROVIDER_OPS.get(provider, ()):
            raise ValueError(f'{provider} cannot {op}')
        if not _key(provider):
            raise RuntimeError(f'{provider} has no key on this box')
        return provider
    able = [p for p in PROVIDER_OPS if op in PROVIDER_OPS[p]]
    for candidate in able:
        if _key(candidate):
            return candidate
    raise RuntimeError(f'nothing here can {op}: {" or ".join(able)} would, '
                       f'but neither has a key')


# ── projects ─────────────────────────────────────────────────────────────

def _projects() -> dict:
    return _read('projects.json', {})


def _project_id(ref: str) -> str:
    """A project is addressable by id or by name — whichever the caller has."""
    projects = _projects()
    if ref in projects:
        return ref
    for pid, project in projects.items():
        if project.get('name') == ref or project.get('slug') == _slug(ref):
            return pid
    raise ValueError(f'no such project: {ref}')


def _ensure_project(ref: str = None, owner: str = None) -> str:
    ref = ref or 'default'
    try:
        return _project_id(ref)
    except ValueError:
        with _LOCK:
            projects = _projects()
            pid = _slug(ref)
            while pid in projects:
                pid = f'{_slug(ref)}-{os.urandom(2).hex()}'
            projects[pid] = {'id': pid, 'name': ref, 'slug': _slug(ref),
                             'owner': owner, 'created_at': _now(), 'images': []}
            _write('projects.json', projects)
            return pid


def _file_image(project_id: str, record: dict) -> None:
    """Add an artifact to its project's gallery, newest last."""
    with _LOCK:
        projects = _projects()
        project = projects.get(project_id)
        if not project:
            return
        project.setdefault('images', []).append(record)
        project['updated_at'] = _now()
        _write('projects.json', projects)


# ── tasks (Agent Protocol v1) ────────────────────────────────────────────

def _tasks() -> dict:
    return _read('tasks.json', {})


def _get_task(task_id: str) -> dict:
    task = _tasks().get(task_id)
    if not task:
        raise ValueError(f'no such task: {task_id}')
    return task


def _put_task(task: dict) -> None:
    with _LOCK:
        tasks = _tasks()
        tasks[task['task_id']] = task
        _write('tasks.json', tasks)


def _task_view(task: dict) -> dict:
    """A task as the protocol describes it, plus the project it belongs to."""
    return {'task_id': task['task_id'], 'input': task.get('input'),
            'additional_input': task.get('additional_input') or {},
            'artifacts': task.get('artifacts') or [],
            'status': task.get('status'), 'project': task.get('project'),
            'created_at': _iso(task.get('created_at')),
            'modified_at': _iso(task.get('modified_at') or task.get('created_at'))}


def _step_view(step: dict) -> dict:
    return {'step_id': step['step_id'], 'task_id': step['task_id'],
            'name': step.get('name'), 'input': step.get('input'),
            'output': step.get('output'), 'status': step.get('status'),
            'is_last': step.get('is_last', False),
            'artifacts': step.get('artifacts') or [],
            'additional_output': step.get('additional_output') or {},
            'created_at': _iso(step.get('created_at')),
            'modified_at': _iso(step.get('modified_at') or step.get('created_at'))}


def _plan(input_text: str, opts: dict) -> list:
    """What this task will do, as a list of ops. Either the caller handed us a
    plan, or the task is the single op named in additional_input, or — the
    common case — a prompt with no op is a generate."""
    if opts.get('plan'):
        plan = []
        for entry in opts['plan']:
            entry = dict(entry)
            entry.setdefault('op', 'generate')
            entry.setdefault('prompt', input_text)
            plan.append(entry)
        return plan[:AP_MAX_STEPS]
    op = opts.get('op') or ('edit' if opts.get('image') else 'generate')
    step = {k: v for k, v in opts.items()
            if k in ('model', 'provider', 'image', 'width', 'height',
                     'aspect_ratio', 'negative_prompt', 'scale', 'enhance')}
    step['op'] = op
    step['prompt'] = opts.get('prompt') or input_text
    return [step]


def _execute(task: dict, step: dict) -> dict:
    """Run one operation. This is the only place an image is made."""
    args = dict(step.get('args') or {})
    op = args.pop('op', 'generate')
    # an op with no explicit input image chains onto whatever this task made last
    if op in ('edit', 'upscale') and not args.get('image'):
        args['image'] = task.get('latest')
    if op in ('edit', 'upscale') and not args.get('image'):
        raise ValueError(f'{op} needs an image — pass image=<image_id>')
    if op in ('generate', 'edit') and not (args.get('prompt') or '').strip():
        raise ValueError(f'{op} needs a prompt')
    provider = _resolve(op, args.pop('provider', None))
    model = args.get('model') or DEFAULT_MODEL[provider]
    args['model'] = model
    started = time.time()
    blob = _put_blob(RUNNERS[provider](op, args))
    record = {**blob, 'op': op, 'prompt': args.get('prompt'), 'provider': provider,
              'model': model, 'parent': args.get('image'), 'task_id': task['task_id'],
              'step_id': step['step_id'], 'project': task.get('project'),
              'created_at': _now(), 'took_ms': int((time.time() - started) * 1000)}
    _file_image(task['project'], record)
    return record


def _artifact(record: dict) -> dict:
    """An AP artifact — plus the fields that make it an image rather than a file."""
    return {'artifact_id': record['image_id'], 'agent_created': True,
            'file_name': f"{record['image_id']}.{record['ext']}", 'relative_path': None,
            'created_at': _iso(record.get('created_at')),
            'image_id': record['image_id'], 'mime': record['mime'],
            'bytes': record['bytes'], 'op': record.get('op'),
            'prompt': record.get('prompt'), 'provider': record.get('provider'),
            'model': record.get('model'), 'parent': record.get('parent'),
            'url': f"/media/{record['image_id']}"}


def _run_step(task_id: str, step_id: str) -> dict:
    """Execute a queued step and write back what happened. Never raises: a
    failed step is a completed fact about the task, not a crash in the worker."""
    with _LOCK:
        task = _get_task(task_id)
        step = next((s for s in task['steps'] if s['step_id'] == step_id), None)
        if not step or step['status'] not in ('created', 'running'):
            return step or {}
        step['status'] = 'running'
        step['modified_at'] = _now()
        task['status'] = 'running'
        _put_task(task)
    try:
        record = _execute(task, step)
        outcome = {'status': 'completed', 'output': record['image_id'],
                   'artifacts': [_artifact(record)],
                   'additional_output': {'provider': record['provider'],
                                         'model': record['model'],
                                         'took_ms': record['took_ms']}}
    except Exception as e:                                    # noqa: BLE001
        outcome = {'status': 'failed', 'output': f'{type(e).__name__}: {e}',
                   'artifacts': [], 'additional_output': {'error': str(e)}}
    with _LOCK:
        task = _get_task(task_id)
        step = next(s for s in task['steps'] if s['step_id'] == step_id)
        step.update(outcome)
        step['modified_at'] = _now()
        if outcome['artifacts']:
            task['artifacts'] = (task.get('artifacts') or []) + outcome['artifacts']
            task['latest'] = outcome['output']
        pending = [s for s in task['steps'] if s['status'] in ('created', 'running')]
        task['status'] = ('failed' if outcome['status'] == 'failed' and not pending
                          else 'running' if pending or task.get('queue')
                          else 'completed')
        task['modified_at'] = _now()
        _put_task(task)
        return dict(step)


def _worker_loop() -> None:
    while True:
        job = _QUEUE.get()
        if job is None:
            return
        try:
            _run_step(*job)
        except Exception:                                     # noqa: BLE001
            pass
        finally:
            _QUEUE.task_done()


def _ensure_workers() -> None:
    with _LOCK:
        while len(_WORKERS) < WORKERS:
            thread = threading.Thread(target=_worker_loop, daemon=True,
                                      name=f'imagine-worker-{len(_WORKERS)}')
            thread.start()
            _WORKERS.append(thread)


def _reap_orphans() -> int:
    """Steps a dead server left `running` are failed, not waited on."""
    with _LOCK:
        tasks, n = _tasks(), 0
        for task in tasks.values():
            for step in task.get('steps', []):
                if step['status'] in ('created', 'running'):
                    step.update(status='failed', modified_at=_now(),
                                output='the server that queued this step went away')
                    n += 1
            if n and task.get('status') == 'running':
                task['status'] = 'failed'
        if n:
            _write('tasks.json', tasks)
        return n


class Mod:
    description = __doc__
    path = str(DIR)

    def forward(self, fn: str = 'info', *args, **kwargs):
        return getattr(self, fn)(*args, **kwargs)

    # ── what this box can do ─────────────────────────────────────────────

    def info(self) -> dict:
        providers = self.providers()
        return {'name': 'imagine',
                'what': 'an image editor that speaks Agent Protocol v1',
                'protocol': 'agent/1.0',
                'nouns': {'project': 'a workspace', 'task': 'an editing session',
                          'step': 'one operation', 'artifact': 'the image'},
                'ops': {op: [p for p in PROVIDER_OPS if op in PROVIDER_OPS[p]
                             and providers[p]['ready']] for op in OPS},
                'providers': providers,
                'projects': len(_projects()),
                'images': len(list(BLOBS.glob('*.*'))) if BLOBS.exists() else 0,
                'tasks': len(_tasks()),
                'queue': self.queue(),
                'api': f'http://localhost:{PORT}',
                'app': f'http://localhost:{PORT}/imagine',
                'ap': f'http://localhost:{PORT}/ap/v1/agent/tasks'}

    def card(self) -> dict:
        info = self.info()
        return {'name': 'imagine', 'icon': '◈', 'color': '#a78bfa',
                'summary': info['what'], 'ops': info['ops'],
                'projects': info['projects'], 'images': info['images']}

    def schema(self) -> dict:
        return {'ops': {'generate': ['prompt', 'model', 'provider', 'width', 'height',
                                     'aspect_ratio', 'negative_prompt'],
                        'edit': ['image', 'prompt', 'model', 'provider'],
                        'upscale': ['image', 'scale', 'enhance', 'provider']},
                'ap': ['POST /ap/v1/agent/tasks',
                       'POST /ap/v1/agent/tasks/{id}/steps',
                       'GET /ap/v1/agent/tasks/{id}/artifacts']}

    def providers(self) -> dict:
        """Who can run which op here, and — when they cannot — why not."""
        out = {}
        for name, ops in PROVIDER_OPS.items():
            ready = bool(_key(name))
            out[name] = {'ops': list(ops), 'ready': ready,
                         'default_model': DEFAULT_MODEL[name],
                         'why': '' if ready else
                         f'no key: set {name.upper()}_API_KEY or add one to the '
                         f'{"venice" if name == "venice" else "model.openrouter"} module'}
        return out

    def models(self) -> dict:
        return {name: DEFAULT_MODEL[name] for name in PROVIDER_OPS}

    # ── projects ─────────────────────────────────────────────────────────

    def projects(self) -> list:
        return [{'id': p['id'], 'name': p['name'], 'owner': p.get('owner'),
                 'images': len(p.get('images') or []), 'created_at': p['created_at'],
                 'updated_at': p.get('updated_at')}
                for p in sorted(_projects().values(),
                                key=lambda p: p.get('updated_at') or p['created_at'],
                                reverse=True)]

    def project(self, project: str = 'default') -> dict:
        p = _projects()[_project_id(project)]
        tasks = [_task_view(t) for t in _tasks().values() if t.get('project') == p['id']]
        return {**p, 'images': list(reversed(p.get('images') or [])),
                'tasks': sorted(tasks, key=lambda t: t['created_at'], reverse=True)}

    def new_project(self, name: str, owner: str = None) -> dict:
        return {'id': _ensure_project(name, owner), 'name': name}

    def rm_project(self, project: str, keep_images: bool = True) -> dict:
        """Drop the workspace. The blobs stay unless asked otherwise — another
        project's lineage may point at them."""
        pid = _project_id(project)
        with _LOCK:
            projects = _projects()
            removed = projects.pop(pid)
            _write('projects.json', projects)
        if not keep_images:
            for record in removed.get('images') or []:
                path = _blob_path(record['image_id'])
                if path and not self._referenced(record['image_id'], skip=pid):
                    path.unlink(missing_ok=True)
        return {'removed': pid, 'images': len(removed.get('images') or [])}

    def _referenced(self, image_id: str, skip: str = None) -> bool:
        return any(record['image_id'] == image_id
                   for pid, project in _projects().items() if pid != skip
                   for record in project.get('images') or [])

    def gallery(self, project: str = 'default', n: int = 50) -> list:
        return self.project(project)['images'][:int(n)]

    def lineage(self, image: str) -> list:
        """Walk an image back to the prompt it started as."""
        index = {record['image_id']: record
                 for p in _projects().values() for record in p.get('images') or []}
        chain, seen, cursor = [], set(), image
        while cursor and cursor in index and cursor not in seen:
            seen.add(cursor)
            record = index[cursor]
            chain.append({k: record.get(k) for k in
                          ('image_id', 'op', 'prompt', 'provider', 'model', 'created_at')})
            cursor = record.get('parent')
        return list(reversed(chain))

    def image(self, image: str) -> dict:
        index = {record['image_id']: record
                 for p in _projects().values() for record in p.get('images') or []}
        record = index.get(image)
        path = _blob_path(image)
        if not path:
            raise ValueError(f'no such image: {image}')
        return {**(record or {'image_id': image}), 'path': str(path),
                'url': f'/media/{image}'}

    def export(self, image: str, path: str) -> dict:
        target = Path(os.path.expanduser(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        data = _read_blob(image)
        target.write_bytes(data)
        return {'image_id': image, 'path': str(target), 'bytes': len(data)}

    def pin(self, image: str) -> dict:
        """Pin an image to localfs so it can be shared as a CID."""
        m = _fleet()
        if not m:
            raise RuntimeError('the mod framework is not importable from here')
        cid = m.mod('localfs')().put(base64.b64encode(_read_blob(image)).decode())
        return {'image_id': image, 'cid': cid}

    # ── the three ops, run inline (the CLI path) ─────────────────────────

    def generate(self, prompt: str, project: str = 'default', **opts) -> dict:
        return self._oneshot('generate', project, prompt=prompt, **opts)

    def edit(self, image: str, prompt: str, project: str = None, **opts) -> dict:
        return self._oneshot('edit', project or self._project_of(image),
                             image=image, prompt=prompt, **opts)

    def upscale(self, image: str, scale: int = 2, project: str = None, **opts) -> dict:
        return self._oneshot('upscale', project or self._project_of(image),
                             image=image, scale=scale, **opts)

    def _project_of(self, image: str) -> str:
        for pid, project in _projects().items():
            if any(r['image_id'] == image for r in project.get('images') or []):
                return pid
        return 'default'

    def _oneshot(self, op: str, project: str, **opts) -> dict:
        """One op as one task with one step — the same path the server takes,
        so the CLI and an AP client cannot drift apart."""
        task = self.ap_create_task(opts.get('prompt'),
                                   {**opts, 'op': op, 'project': project})
        step = self.ap_step(task['task_id'], background=False)
        if step['status'] == 'failed':
            raise RuntimeError(step['output'])
        return {**step['artifacts'][0], 'task_id': task['task_id']}

    # ── Agent Protocol v1 ────────────────────────────────────────────────

    def ap_create_task(self, input: str = None, additional_input: dict = None,
                       owner: str = None) -> dict:
        """POST /ap/v1/agent/tasks — `input` is the prompt; `additional_input`
        carries the project, the op and its arguments."""
        opts = dict(additional_input or {})
        project = _ensure_project(opts.pop('project', None), owner)
        task = {'task_id': _uid(), 'input': input, 'additional_input': opts,
                'project': project, 'owner': owner, 'created_at': _now(),
                'modified_at': _now(), 'status': 'created',
                'queue': _plan(input, opts), 'steps': [], 'artifacts': [],
                'latest': opts.get('image')}
        _put_task(task)
        return _task_view(task)

    def ap_step(self, task_id: str, input: str = None,
                additional_input: dict = None, background: bool = None) -> dict:
        """POST /ap/v1/agent/tasks/{id}/steps — run the next op.

        The step comes back `running` on a server (a worker picks it up) and
        `completed` from the CLI (nothing else would ever run it). A step
        POSTed with `input` to a task whose plan is spent is the interactive
        editor: it appends an edit of the latest artifact."""
        opts = dict(additional_input or {})
        with _LOCK:
            task = _get_task(task_id)
            if not task['queue']:
                if not (input or opts):
                    raise ValueError(f'task {task_id} has nothing left to do')
                task['queue'] = _plan(input, {**opts, 'op': opts.get('op') or 'edit',
                                              'prompt': opts.get('prompt') or input})
            if len(task['steps']) >= AP_MAX_STEPS:
                raise ValueError(f'task {task_id} hit its step ceiling ({AP_MAX_STEPS})')
            args = task['queue'].pop(0)
            step = {'step_id': f"{task_id}-{len(task['steps']) + 1}", 'task_id': task_id,
                    'name': f"{args.get('op')}", 'input': input, 'output': None,
                    'status': 'created', 'is_last': not task['queue'], 'args': args,
                    'artifacts': [], 'created_at': _now(), 'modified_at': _now()}
            task['steps'].append(step)
            task['status'] = 'running'
            _put_task(task)
        if background is None:
            background = _SERVING
        if not background:
            return _step_view(_run_step(task_id, step['step_id']))
        _ensure_workers()
        _QUEUE.put((task_id, step['step_id']))
        return _step_view({**step, 'status': 'running'})

    def ap_task(self, task_id: str) -> dict:
        return _task_view(_get_task(task_id))

    def ap_tasks(self, n: int = 50, project: str = None) -> dict:
        tasks = [t for t in _tasks().values()
                 if not project or t.get('project') == _project_id(project)]
        tasks.sort(key=lambda t: t['created_at'], reverse=True)
        return {'tasks': [_task_view(t) for t in tasks[:int(n)]]}

    def ap_steps(self, task_id: str, step_id: str = None) -> dict:
        steps = _get_task(task_id).get('steps') or []
        if step_id:
            step = next((s for s in steps if s['step_id'] == step_id), None)
            if not step:
                raise ValueError(f'no such step: {step_id}')
            return _step_view(step)
        return {'steps': [_step_view(s) for s in steps]}

    def ap_artifacts(self, task_id: str) -> dict:
        return {'artifacts': _get_task(task_id).get('artifacts') or []}

    def queue(self) -> dict:
        pending = [s for t in _tasks().values() for s in t.get('steps', [])
                   if s['status'] in ('created', 'running')]
        return {'pending': len(pending), 'workers': len(_WORKERS), 'serving': _SERVING}

    # ── access: reads are open, writes are the owner's ───────────────────

    def _secret(self) -> bytes:
        path = STATE / 'server.secret'
        if not path.exists():
            STATE.mkdir(parents=True, exist_ok=True)
            path.write_text(os.urandom(32).hex())
            path.chmod(0o600)
        return path.read_text().strip().encode()

    def owner(self) -> str:
        return _read('owner.json', {}).get('owner') or ''

    def set_owner(self, address: str) -> dict:
        if self.owner() and not os.environ.get('IMAGINE_OPEN'):
            raise PermissionError('owner is already set')
        _write('owner.json', {'owner': address.lower(), 'at': _now()})
        return {'owner': address.lower()}

    def is_owner(self, address: str = None) -> bool:
        owner = self.owner()
        return bool(owner) and (address or '').lower() == owner

    def access(self) -> dict:
        return {'open': bool(os.environ.get('IMAGINE_OPEN')), 'owner': self.owner(),
                'reads': 'open', 'writes': 'owner or a grant',
                'grants': _read('grants.json', [])}

    def grant(self, address: str) -> dict:
        grants = set(_read('grants.json', []))
        grants.add(address.lower())
        _write('grants.json', sorted(grants))
        return {'grants': sorted(grants)}

    def revoke(self, address: str) -> dict:
        grants = [g for g in _read('grants.json', []) if g != address.lower()]
        _write('grants.json', grants)
        return {'grants': grants}

    def token(self, ttl: int = 86400) -> str:
        """An HMAC session token — minted locally, spent as a Bearer."""
        payload = base64.urlsafe_b64encode(
            json.dumps({'role': 'owner', 'exp': _now() + int(ttl)}).encode()).decode()
        sig = hmac.new(self._secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return f'{payload}.{sig}'

    def whoami(self, headers: dict = None) -> dict:
        return {'caller': self._caller(headers or {}) or 'anonymous',
                'writes': self._may_write(headers or {})}

    def _caller(self, headers: dict) -> str:
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        token = raw[7:].strip() if raw.lower().startswith('bearer ') else raw.strip()
        if not token:
            return ''
        if token.count('.') == 1:
            payload, sig = token.split('.')
            expect = hmac.new(self._secret(), payload.encode(),
                              hashlib.sha256).hexdigest()[:32]
            if hmac.compare_digest(sig, expect):
                claims = json.loads(base64.urlsafe_b64decode(payload))
                return 'owner' if claims.get('exp', 0) > _now() else ''
            return ''
        try:                       # a fleet-signed envelope from the auth module
            m = _fleet()
            return (m.mod('auth')().verify(token) or {}).get('key', '') if m else ''
        except Exception:                                     # noqa: BLE001
            return ''

    def _may_write(self, headers: dict) -> bool:
        if os.environ.get('IMAGINE_OPEN') or not self.owner():
            return True
        caller = self._caller(headers)
        return caller == 'owner' or self.is_owner(caller) or \
            caller.lower() in _read('grants.json', [])

    # ── serving: console + API + /ap/v1 on one port ──────────────────────

    def serve(self, port: int = None, background: bool = False) -> dict:
        global _SERVING
        port = int(port or PORT)
        if background:
            import subprocess
            proc = subprocess.Popen([sys.executable, str(DIR / 'mod.py'), 'serve',
                                     str(port)], start_new_session=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {'pid': proc.pid, 'url': f'http://localhost:{port}/imagine'}
        from http.server import ThreadingHTTPServer
        reaped = _reap_orphans()
        _SERVING = True
        _ensure_workers()
        server = ThreadingHTTPServer(('0.0.0.0', port), self._handler())
        print(f'imagine → console http://localhost:{port}/imagine · '
              f'ap http://localhost:{port}/ap/v1/agent/tasks · '
              f'{WORKERS} workers' + (f' · reaped {reaped} orphaned steps' if reaped else ''))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            _SERVING = False
            server.server_close()
        return {'stopped': port}

    def kill(self, port: int = None) -> dict:
        import subprocess
        port = int(port or PORT)
        subprocess.run(['fuser', '-k', f'{port}/tcp'], capture_output=True)
        return {'killed': port}

    def status(self) -> dict:
        try:
            with urllib.request.urlopen(f'http://localhost:{PORT}/health', timeout=2) as r:
                return {'up': True, **json.loads(r.read())}
        except Exception:
            return {'up': False, 'port': PORT}

    def _handler(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import parse_qs, urlparse
        mod = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _norm(self, path: str) -> str:
                return re.sub(r'^/imagine/_api|^/imagine', '', path) or '/'

            def _send(self, code: int, body, ctype='application/json'):
                data = (body if isinstance(body, bytes) else
                        body.encode() if isinstance(body, str) else
                        json.dumps(body, default=str).encode())
                self.send_response(code)
                self.send_header('content-type', ctype)
                self.send_header('content-length', str(len(data)))
                self.send_header('access-control-allow-origin', '*')
                self.send_header('access-control-allow-headers', 'content-type,authorization')
                self.end_headers()
                self.wfile.write(data)

            def _guard(self, fn, write=False):
                try:
                    if write and not mod._may_write(dict(self.headers)):
                        return self._send(403, {'error': 'writes need the owner or a grant'})
                    return self._send(200, fn())
                except PermissionError as e:
                    self._send(403, {'error': str(e)})
                except (ValueError, KeyError) as e:
                    self._send(400, {'error': str(e)})
                except Exception as e:                        # noqa: BLE001
                    self._send(500, {'error': f'{type(e).__name__}: {e}'})

            def do_OPTIONS(self):
                self._send(204, b'')

            def do_GET(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if path in ('/', '/index.html') and u.path.startswith('/imagine'):
                    return self._send(200, CONSOLE, 'text/html; charset=utf-8')
                if path == '/':
                    return self._guard(lambda: mod.info())
                if path == '/health':
                    return self._send(200, {'ok': True, 'port': PORT, **mod.queue()})
                mm = re.match(r'^/media/([0-9a-f]{6,64})$', path)
                if mm:
                    try:
                        data = _read_blob(mm.group(1))
                    except ValueError as e:
                        return self._send(404, {'error': str(e)})
                    self.send_response(200)
                    self.send_header('content-type', _sniff(data)[1])
                    self.send_header('content-length', str(len(data)))
                    self.send_header('cache-control', 'public, max-age=31536000, immutable')
                    self.send_header('access-control-allow-origin', '*')
                    self.end_headers()
                    return self.wfile.write(data)
                simple = {'/api/info': mod.info, '/api/card': mod.card,
                          '/api/schema': mod.schema, '/api/providers': mod.providers,
                          '/api/access': mod.access, '/api/queue': mod.queue,
                          '/api/projects': lambda: {'projects': mod.projects()},
                          '/api/whoami': lambda: mod.whoami(dict(self.headers))}
                if path in simple:
                    return self._guard(simple[path])
                mm = re.match(r'^/api/projects/([\w-]+)$', path)
                if mm:
                    return self._guard(lambda: mod.project(mm.group(1)))
                mm = re.match(r'^/api/images/([0-9a-f]{6,64})/lineage$', path)
                if mm:
                    return self._guard(lambda: {'lineage': mod.lineage(mm.group(1))})
                if path == '/ap/v1/agent/tasks':
                    return self._guard(lambda: mod.ap_tasks(int(q.get('n', 50)),
                                                            q.get('project')))
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)/steps/([\w-]+)$', path)
                if mm:
                    return self._guard(lambda: mod.ap_steps(mm.group(1), mm.group(2)))
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)/artifacts/([0-9a-f]{6,64})$', path)
                if mm:
                    self.path = f'/media/{mm.group(2)}'
                    return self.do_GET()
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)(/steps|/artifacts)?$', path)
                if mm:
                    tid, tail = mm.group(1), mm.group(2)
                    return self._guard(
                        lambda: mod.ap_steps(tid) if tail == '/steps'
                        else mod.ap_artifacts(tid) if tail == '/artifacts'
                        else mod.ap_task(tid))
                self._send(404, {'error': f'no route {path}'})

            def do_POST(self):
                path = self._norm(urlparse(self.path).path)
                length = int(self.headers.get('content-length') or 0)
                raw = self.rfile.read(length).decode('utf-8', 'replace') if length else '{}'
                try:
                    body = json.loads(raw or '{}')
                except ValueError:
                    return self._send(400, {'error': 'body must be JSON'})
                if not isinstance(body, dict):
                    return self._send(400, {'error': 'body must be a JSON object'})
                caller = mod._caller(dict(self.headers))
                if path == '/api/projects':
                    return self._guard(lambda: mod.new_project(body.get('name'), caller),
                                       write=True)
                if path == '/ap/v1/agent/tasks':
                    return self._guard(lambda: mod.ap_create_task(
                        body.get('input'), body.get('additional_input'), caller), write=True)
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)/steps$', path)
                if mm:
                    return self._guard(lambda: mod.ap_step(
                        mm.group(1), body.get('input'), body.get('additional_input')),
                        write=True)
                self._send(404, {'error': f'no route {path}'})

        return H

    # ── test ─────────────────────────────────────────────────────────────

    def test(self) -> dict:
        """Everything that does not need a provider key: the store, the graph,
        the protocol's own bookkeeping."""
        checks = {}
        blob = _put_blob(b'\x89PNG\r\n\x1a\n' + os.urandom(64))
        checks['blob is content-addressed'] = _blob_path(blob['image_id']) is not None
        checks['blob round-trips'] = len(_read_blob(blob['image_id'])) == blob['bytes']
        checks['png is sniffed'] = blob['mime'] == 'image/png'

        pid = _ensure_project('imagine-selftest')
        checks['project resolves by name'] = _project_id('imagine-selftest') == pid

        task = self.ap_create_task('a test prompt', {'project': 'imagine-selftest'})
        checks['task is created'] = task['status'] == 'created'
        checks['task view is AP-shaped'] = set(task) >= {'task_id', 'input', 'artifacts'}
        plan = _get_task(task['task_id'])['queue']
        checks['a bare prompt plans a generate'] = plan[0]['op'] == 'generate'

        step = self.ap_step(task['task_id'], background=False)
        # no key on this box is a failed step, not an exception — that is the point
        checks['step reports terminally'] = step['status'] in ('completed', 'failed')
        checks['step is AP-shaped'] = set(step) >= {'step_id', 'task_id', 'status', 'is_last'}
        checks['step is last'] = step['is_last'] is True
        if step['status'] == 'completed':
            checks['artifact is filed'] = len(self.ap_artifacts(task['task_id'])['artifacts']) == 1
            checks['lineage walks back'] = len(self.lineage(step['output'])) >= 1

        checks['unknown op is refused'] = self._raises(lambda: _resolve('animate'))
        checks['unknown task is refused'] = self._raises(lambda: self.ap_task('nope'))
        checks['edit with no image is refused'] = self._raises(
            lambda: self._oneshot('edit', 'imagine-selftest', prompt='x'))

        self.rm_project('imagine-selftest', keep_images=False)
        checks['project is removable'] = self._raises(lambda: _project_id('imagine-selftest'))
        with _LOCK:
            tasks = _tasks()
            tasks.pop(task['task_id'], None)
            _write('tasks.json', tasks)
        _blob_path(blob['image_id']).unlink(missing_ok=True)
        return {'passed': all(checks.values()), 'checks': checks}

    @staticmethod
    def _raises(fn) -> bool:
        try:
            fn()
            return False
        except Exception:
            return True


CONSOLE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>imagine</title><style>
:root{--bg:#0a0a0f;--panel:#12121a;--line:#232334;--fg:#d8d8e8;--dim:#6b6b85;--acc:#a78bfa;
 --ok:#4ade80;--bad:#f87171;--warn:#fbbf24}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
 display:grid;grid-template-rows:auto 1fr;height:100vh}
header{display:flex;gap:12px;align-items:center;padding:11px 16px;border-bottom:1px solid var(--line)}
h1{font-size:14px;margin:0;letter-spacing:.18em;color:var(--acc)}
.sp{flex:1}.dim{color:var(--dim)}
main{display:grid;grid-template-columns:210px 1fr;overflow:hidden}
aside{border-right:1px solid var(--line);overflow:auto;padding:10px}
section{overflow:auto;padding:14px}
input,button,select,textarea{background:#0e0e16;color:var(--fg);border:1px solid var(--line);
 border-radius:5px;padding:7px 9px;font:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--acc)}
button{cursor:pointer}button:hover{border-color:var(--acc);color:var(--acc)}
button:disabled{opacity:.45;cursor:default}
.proj{padding:6px 8px;border-radius:5px;cursor:pointer;display:flex;gap:6px}
.proj:hover{background:var(--panel)}.proj.on{background:var(--panel);color:var(--acc)}
.bar{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.bar input[type=text]{flex:1;min-width:240px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--panel)}
.card img{width:100%;display:block;aspect-ratio:1;object-fit:cover;background:#000;cursor:pointer}
.meta{padding:7px 9px;font-size:11px}
.meta b{color:var(--acc);font-weight:normal}
.row{display:flex;gap:6px;padding:0 9px 9px}
.row button{padding:4px 7px;font-size:11px;flex:1}
.steps{margin-top:16px;border-top:1px solid var(--line);padding-top:10px;font-size:11px}
.step{display:flex;gap:8px;padding:3px 0}
.running{color:var(--warn)}.completed{color:var(--ok)}.failed{color:var(--bad)}
dialog{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px;max-width:92vw}
dialog img{max-width:86vw;max-height:74vh;display:block}
</style></head><body>
<header><h1>◈ IMAGINE</h1><span class="dim" id="who">—</span><span class="sp"></span>
<span class="dim" id="q">—</span><button onclick="load()">↻</button></header>
<main>
 <aside>
  <div class="bar" style="margin-bottom:8px">
   <input type="text" id="pname" placeholder="new project" style="min-width:0">
   <button onclick="mkproj()">+</button></div>
  <div id="projects"></div>
 </aside>
 <section>
  <div class="bar">
   <input type="text" id="prompt" placeholder="describe an image, or select one below and describe the edit">
   <select id="provider"><option value="">auto</option></select>
   <button id="go" onclick="run()">generate</button>
   <button onclick="clearSel()" id="clear" style="display:none">✕ selection</button>
  </div>
  <div class="grid" id="grid"></div>
  <div class="steps" id="steps"></div>
 </section>
</main>
<dialog id="big" onclick="this.close()"><img id="bigimg"></dialog>
<script>
const API = location.pathname.replace(/\/$/,'') + '/_api';
let project = localStorage.getItem('imagine.project') || 'default', sel = null, timer = null;
const j = (p, o) => fetch(API + p, o).then(r => r.json());

async function load() {
  const [info, projects] = await Promise.all([j('/api/info'), j('/api/projects')]);
  document.getElementById('q').textContent =
    `${info.queue.pending} queued · ${info.images} images`;
  const ready = Object.entries(info.providers).filter(([, p]) => p.ready).map(([n]) => n);
  document.getElementById('who').textContent = ready.length
    ? ready.join(' · ') : 'no provider key on this box';
  const sel_ = document.getElementById('provider');
  if (sel_.options.length < 2) ready.forEach(n => sel_.add(new Option(n, n)));
  document.getElementById('projects').innerHTML = (projects.projects.length ? projects.projects : [])
    .map(p => `<div class="proj ${p.id === project ? 'on' : ''}" onclick="pick('${p.id}')">
      <span>${p.name}</span><span class="sp"></span><span class="dim">${p.images}</span></div>`).join('')
    || '<div class="dim">no projects yet</div>';
  await refresh();
  clearTimeout(timer);
  timer = setTimeout(load, info.queue.pending ? 2500 : 15000);
}

async function refresh() {
  let p; try { p = await j('/api/projects/' + project); } catch { return; }
  if (p.error) return;
  document.getElementById('grid').innerHTML = (p.images || []).map(i => `
    <div class="card">
      <img src="${API}/media/${i.image_id}" onclick="zoom('${i.image_id}')" loading="lazy">
      <div class="meta"><b>${i.op}</b> ${(i.prompt || '').slice(0, 70)}
        <div class="dim">${i.provider} · ${i.model}</div></div>
      <div class="row">
        <button onclick="select('${i.image_id}')">edit</button>
        <button onclick="op('upscale','${i.image_id}')">upscale</button>
      </div>
    </div>`).join('') || '<div class="dim">nothing here yet — describe an image above</div>';
  const steps = [];
  for (const t of (p.tasks || []).slice(0, 6)) {
    const s = await j(`/ap/v1/agent/tasks/${t.task_id}/steps`);
    (s.steps || []).forEach(x => steps.push(x));
  }
  document.getElementById('steps').innerHTML = steps.slice(-14).reverse().map(s =>
    `<div class="step"><span class="${s.status}">${s.status.padEnd(9)}</span>
     <span>${s.name}</span><span class="dim">${(s.output || '').slice(0, 60)}</span></div>`).join('')
    || '<div class="dim">no steps yet</div>';
}

const pick = p => { project = p; localStorage.setItem('imagine.project', p); load(); };
const zoom = id => { document.getElementById('bigimg').src = `${API}/media/${id}`;
                     document.getElementById('big').showModal(); };
function select(id) { sel = id; document.getElementById('go').textContent = 'edit';
  document.getElementById('clear').style.display = ''; document.getElementById('prompt').focus(); }
function clearSel() { sel = null; document.getElementById('go').textContent = 'generate';
  document.getElementById('clear').style.display = 'none'; }

async function mkproj() {
  const name = document.getElementById('pname').value.trim();
  if (!name) return;
  await j('/api/projects', { method: 'POST', headers: { 'content-type': 'application/json' },
                             body: JSON.stringify({ name }) });
  document.getElementById('pname').value = ''; pick(name);
}

async function op(kind, image, prompt) {
  const t = await j('/ap/v1/agent/tasks', { method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ input: prompt || '', additional_input: {
      project, op: kind, image, prompt,
      provider: document.getElementById('provider').value || undefined } }) });
  if (t.error) return alert(t.error);
  await j(`/ap/v1/agent/tasks/${t.task_id}/steps`, { method: 'POST',
    headers: { 'content-type': 'application/json' }, body: '{}' });
  load();
}

async function run() {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) return;
  await op(sel ? 'edit' : 'generate', sel || undefined, prompt);
  document.getElementById('prompt').value = ''; clearSel();
}
document.getElementById('prompt').addEventListener('keydown', e => e.key === 'Enter' && run());
load();
</script></body></html>
"""


if __name__ == '__main__':
    argv = sys.argv[1:]
    if argv and argv[0] == 'serve':
        Mod().serve(int(argv[1]) if len(argv) > 1 else PORT)
    else:
        print(json.dumps(Mod().forward(*(argv or ['info'])), indent=2, default=str))
