"""
hermes backends — where the tokens actually come from, all of them local.

The module shipped pointing at `m.mod('llama-cpp')`, which is a scaffold stub
with no chat(), no load() and a `path` on somebody's laptop. So every run died
in an AttributeError rather than in an answer. This file is the replacement:
one small interface, three ways to satisfy it, and an honest refusal when none
of them is present.

    LlamaCpp       the llama_cpp python package, weights loaded in THIS process
    OpenAICompat   a llama.cpp `llama-server` or an `ollama serve` on this box,
                   spoken to over its OpenAI-compatible /v1/chat/completions
    none           nothing is installed — `ready()` is False and the error
                   names the two commands that fix it

All three are local by construction: no key, no cloud, nothing leaves the box.
That is the module's whole premise, so `resolve()` will not fall back to a
hosted provider however convenient it would be — a "local-only agent" that
quietly answers from someone's API is a lie with a latency profile.

Discovery order is deliberate: an in-process load is the fastest path to a
token, but a server that is ALREADY warm beats loading 5 GB of weights again,
so a reachable endpoint wins unless HERMES_BACKEND says otherwise.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

# Where GGUF weights live. Off-tree, like every other piece of module state
# that is too big or too personal to commit.
MODEL_DIR = Path(os.environ.get(
    'HERMES_MODEL_DIR', Path.home() / '.mod' / 'hermes' / 'models'))

# Local servers probed when HERMES_API_URL is unset, in the order a box is
# likely to be running them.
PROBES = (
    'http://127.0.0.1:8080',    # llama.cpp llama-server
    'http://127.0.0.1:11434',   # ollama (its /v1 is OpenAI-compatible)
)

PROBE_TIMEOUT = float(os.environ.get('HERMES_PROBE_TIMEOUT', 1.5))
CHAT_TIMEOUT = float(os.environ.get('HERMES_TIMEOUT', 600))

NO_BACKEND = (
    "hermes has no local inference backend on this box. It runs weights here, "
    "never on a cloud API, so one of these has to exist first:\n"
    "  • pip install llama-cpp-python   — loads a GGUF in this process\n"
    "  • llama-server -m <model.gguf>   — llama.cpp's own server on :8080\n"
    "  • ollama serve                   — then `ollama pull hermes3`\n"
    "Point HERMES_API_URL at a server on another port if it isn't on one of "
    f"{', '.join(PROBES)}."
)


class BackendError(RuntimeError):
    """Inference could not happen, with the reason a human can act on."""


# ── the interface ────────────────────────────────────────────────────

class Backend:
    kind = 'none'
    detail = ''

    def ready(self) -> bool:
        return False

    def why(self) -> str:
        """Why a run cannot start, in words that name the fix.

        `detail` says WHICH backend was picked; this says what is wrong with
        it. They are different questions, and a 503 that answers only the
        first ("http://127.0.0.1:8080") tells the reader nothing.
        """
        return self.detail or NO_BACKEND

    def models(self) -> List[str]:
        return []

    def chat(self, messages: List[Dict], model: str = None,
             max_tokens: int = 1024, temperature: float = 0.7,
             on_token=None) -> str:
        raise BackendError(NO_BACKEND)

    def info(self) -> dict:
        ready = self.ready()
        return {'kind': self.kind, 'detail': self.detail, 'ready': ready,
                **({} if ready else {'why': self.why()})}


class NoBackend(Backend):
    """The honest answer when nothing local is installed."""

    detail = NO_BACKEND


# ── a local OpenAI-compatible server ─────────────────────────────────

class OpenAICompat(Backend):
    """llama.cpp's llama-server, or ollama, over /v1/chat/completions.

    Both speak the same dialect, so one client covers them; `models()` reads
    the server's own list, which is what makes an ollama box's pulled models
    selectable in a console that has never heard of them.
    """

    kind = 'server'

    def __init__(self, base: str):
        self.base = base.rstrip('/')
        self.detail = self.base

    # ── discovery ────────────────────────────────────────────────────

    @classmethod
    def find(cls) -> Optional['OpenAICompat']:
        """The first local server that answers, or None.

        An explicit HERMES_API_URL is taken on trust and NOT probed: a server
        still loading its weights refuses for a minute and then works, and a
        URL somebody set by hand should not be silently discarded for that.
        """
        explicit = os.environ.get('HERMES_API_URL')
        if explicit:
            return cls(explicit)
        for base in PROBES:
            probe = cls(base)
            if probe.ready():
                return probe
        return None

    def ready(self) -> bool:
        try:
            self._get('/v1/models', timeout=PROBE_TIMEOUT)
            return True
        except Exception:
            return False

    def why(self) -> str:
        if self.ready():
            return ''
        return (f'{self.base} is not answering. Start it — `llama-server -m '
                f'<model.gguf>` or `ollama serve` — or unset HERMES_API_URL to '
                f'let this module probe {" and ".join(PROBES)} and the '
                f'in-process llama_cpp backend instead.')

    # ── http ─────────────────────────────────────────────────────────

    def _get(self, path: str, timeout: float = None) -> dict:
        req = urllib.request.Request(self.base + path,
                                     headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout or PROBE_TIMEOUT) as r:
            return json.loads(r.read().decode() or '{}')

    def _post(self, path: str, body: dict, timeout: float = None):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={'Content-Type': 'application/json'})
        return urllib.request.urlopen(req, timeout=timeout or CHAT_TIMEOUT)

    # ── the two calls ────────────────────────────────────────────────

    def models(self) -> List[str]:
        try:
            data = self._get('/v1/models', timeout=PROBE_TIMEOUT * 2)
        except Exception:
            return []
        return [row.get('id') for row in data.get('data', []) if row.get('id')]

    def chat(self, messages, model=None, max_tokens=1024, temperature=0.7,
             on_token=None) -> str:
        body = {
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'stream': bool(on_token),
        }
        # llama-server ignores the field and serves whatever it was started
        # with; ollama needs it. Sending the server's own first model when the
        # caller named none keeps both happy.
        body['model'] = model or (self.models() or ['hermes'])[0]
        try:
            resp = self._post('/v1/chat/completions', body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:400]
            raise BackendError(f'{self.base} refused the completion ({e.code}): {detail}')
        except Exception as e:
            raise BackendError(f'{self.base} unreachable: {e}')
        if not body['stream']:
            data = json.loads(resp.read().decode() or '{}')
            return _first_choice(data)
        return self._read_stream(resp, on_token)

    @staticmethod
    def _read_stream(resp, on_token) -> str:
        """SSE off the completion endpoint, folded back into one string.

        Tokens are handed to `on_token` as they land so a run can stream them
        onward, and also accumulated — the agent loop needs the whole step
        before it can parse an anchor out of it.
        """
        out = []
        for raw in resp:
            line = raw.decode(errors='replace').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get('choices') or [{}]
            piece = (choices[0].get('delta') or {}).get('content') or ''
            if piece:
                out.append(piece)
                if on_token:
                    on_token(piece)
        return ''.join(out)


def _first_choice(data: dict) -> str:
    choices = data.get('choices') or []
    if not choices:
        raise BackendError(f'no completion in the response: {str(data)[:300]}')
    msg = choices[0].get('message') or {}
    return msg.get('content') or choices[0].get('text') or ''


# ── weights in this process ──────────────────────────────────────────

class LlamaCpp(Backend):
    """llama_cpp.Llama over a GGUF in MODEL_DIR.

    The model is loaded once and cached on the class, because loading is the
    expensive part: an 8B Q4 is seconds of mmap and gigabytes of RSS, and a
    console asks for a completion far more often than it changes model.
    """

    kind = 'llama-cpp'
    _cache: Dict[str, object] = {}

    def __init__(self, path: str = None, n_ctx: int = 8192, n_gpu_layers: int = -1):
        self.path = path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.detail = path or str(MODEL_DIR)

    @staticmethod
    def available() -> bool:
        try:
            import llama_cpp  # noqa: F401
            return True
        except ImportError:
            return False

    def ready(self) -> bool:
        return self.available() and bool(self.models())

    def models(self) -> List[str]:
        if not MODEL_DIR.exists():
            return []
        return sorted(p.name for p in MODEL_DIR.glob('*.gguf'))

    def why(self) -> str:
        if not self.available():
            return ('llama-cpp-python is not installed — `pip install '
                    'llama-cpp-python`, or start a llama-server / ollama and '
                    'point HERMES_API_URL at it.')
        if not self.models():
            return (f'no GGUF weights in {MODEL_DIR} — `m hermes download '
                    f'model=hermes-3-8b` fetches one.')
        return ''

    def _resolve(self, model: str = None) -> str:
        found = self.models()
        if model:
            # a bare filename, or an absolute path somebody keeps elsewhere
            if os.path.isabs(model) and os.path.exists(model):
                return model
            if model in found:
                return str(MODEL_DIR / model)
            raise BackendError(
                f'{model!r} is not in {MODEL_DIR} — downloaded: '
                f'{", ".join(found) or "nothing yet"}')
        if self.path:
            return self.path
        if not found:
            raise BackendError(
                f'no GGUF weights in {MODEL_DIR} — run `m hermes download` first')
        return str(MODEL_DIR / found[0])

    def _llama(self, model: str = None):
        path = self._resolve(model)
        hit = self._cache.get(path)
        if hit is not None:
            return hit
        from llama_cpp import Llama
        llama = Llama(model_path=path, n_ctx=self.n_ctx,
                      n_gpu_layers=self.n_gpu_layers, verbose=False)
        # One model resident at a time. Two 8B models is most of a laptop's
        # memory, and the second load is what makes the box swap.
        self._cache.clear()
        self._cache[path] = llama
        return llama

    def chat(self, messages, model=None, max_tokens=1024, temperature=0.7,
             on_token=None) -> str:
        if not self.available():
            raise BackendError(
                'llama-cpp-python is not installed — `pip install llama-cpp-python`')
        llama = self._llama(model)
        if not on_token:
            out = llama.create_chat_completion(
                messages=messages, max_tokens=max_tokens, temperature=temperature)
            return _first_choice(out)
        pieces = []
        for chunk in llama.create_chat_completion(
                messages=messages, max_tokens=max_tokens,
                temperature=temperature, stream=True):
            delta = (chunk.get('choices') or [{}])[0].get('delta') or {}
            piece = delta.get('content') or ''
            if piece:
                pieces.append(piece)
                on_token(piece)
        return ''.join(pieces)


# ── model downloads ──────────────────────────────────────────────────

def download(url: str, name: str = None, on_progress=None) -> str:
    """Fetch a GGUF into MODEL_DIR. Already-there is a no-op, not a re-fetch.

    Writes to `<name>.part` and renames on completion, so an interrupted
    download is never mistaken for a usable model by `models()`.
    """
    name = name or url.rsplit('/', 1)[-1].split('?')[0]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODEL_DIR / name
    if dest.exists():
        return str(dest)
    part = dest.with_suffix(dest.suffix + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': 'mod-hermes/1'})
    started = time.time()
    with urllib.request.urlopen(req, timeout=60) as r, open(part, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        got = 0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            got += len(block)
            if on_progress:
                on_progress(got, total, time.time() - started)
    part.rename(dest)
    return str(dest)


# ── what a run actually gets ─────────────────────────────────────────

def resolve(prefer: str = None) -> Backend:
    """Pick a backend. `prefer` (or HERMES_BACKEND) forces one by kind.

    Forcing is not a hint: asking for `llama-cpp` on a box without the package
    returns a backend that refuses with that reason, rather than silently
    running somewhere else. A run that quietly changed where it executed is
    worse than one that failed.
    """
    prefer = (prefer or os.environ.get('HERMES_BACKEND') or '').strip().lower()
    if prefer in ('llama-cpp', 'llama_cpp', 'local'):
        return LlamaCpp()
    if prefer in ('server', 'openai', 'llama-server', 'ollama'):
        return OpenAICompat.find() or NoBackend()
    server = OpenAICompat.find()
    if server:
        return server
    if LlamaCpp.available():
        return LlamaCpp()
    return NoBackend()
