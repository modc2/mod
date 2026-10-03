"""
liquid - LFM models as agent providers: on this box, or in the visitor's tab.

Three providers, one file, because they are one catalog — the `liquidai`
module's:

    liquidai        the weights run on this box (liquidai runtime=server)
    liquidai-cloud  Liquid's hosted API, on the caller's own key (runtime=cloud)
    browser         the weights never touch this box at all: the console's tab
                    loads the ONNX build itself and this class is only the
                    mailbox the run loop blocks on while the tab generates

None of the three spends the module's OpenRouter/Venice credit, so every call
prices at zero and runs on them are never billed (Mod.is_free_provider).

The browser bridge is the only unobvious part. The agent loop is server-side
and synchronous — it calls `client.forward(context)` and waits for text — but
a browser model lives in a tab we can't call into. So a request is parked
here, pushed down the run's own SSE stream as a `model_request` event, and the
tab POSTs the answer back to /browser/completion, which wakes the waiting run.
The session is bound per thread because that is where a run lives: one run,
one thread, one tab (see Meter for the same reason).
"""
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None

# how long a run waits for a tab to generate one step. Generous: the tab may
# still be downloading 300 MB of weights when the first request lands.
BROWSER_TIMEOUT = 900.0

# what a run gets to say when nothing is listening
NO_TAB = ("the browser provider needs the console open — its model runs in "
          "your tab, so a run started anywhere else has nothing to generate with")


class _FreeCatalog(dict):
    """A price list where everything is free.

    The meter looks a model up by id and falls back to flat per-step billing
    when it finds nothing (see Mod.charge_run). These providers cost the
    module nothing whatever id is typed in, so answer for every key rather
    than only the catalogued ones.
    """

    def get(self, key, default=None):
        return {'id': key, 'pricing': {'prompt': 0.0, 'completion': 0.0}}


class Catalog:
    """The liquidai model catalog, folded into per-runtime repo lists.

    The catalog is one row per model with its repos underneath; a run wants a
    repo (that is what both /chat and transformers.js load), so this flattens
    it. Cached for TTL seconds — the console asks on every page load and the
    catalog behind it only moves when Liquid ships something.
    """

    TTL = 900.0
    # torch on this box, ONNX in a tab. GGUF/MLX are catalogued by liquidai
    # but neither runtime here can load them.
    FORMAT = {'server': 'torch', 'cloud': 'torch', 'browser': 'onnx'}

    def __init__(self):
        self._cache: Dict[str, tuple] = {}   # runtime -> (fetched_at, [repo, …])
        self._lock = threading.Lock()

    @staticmethod
    def _client():
        if not m:
            raise RuntimeError('mod protocol unavailable')
        return m.mod('liquidai')()

    def repos(self, runtime: str = 'server', limit: int = 100) -> List[str]:
        """Repo ids this runtime can actually load, most-downloaded first.

        Returns [] rather than raising when liquidai isn't serving — the
        console falls back to its curated list and the run still works, since
        a repo id typed by hand is as valid as one picked from a dropdown.
        """
        with self._lock:
            hit = self._cache.get(runtime)
            if hit and time.time() - hit[0] < self.TTL:
                return hit[1]
        try:
            # _get rather than .models(): the module's own passthrough trims
            # the record down to ids, and a run needs the repos under them
            data = self._client()._get('/models', runtime=runtime, limit=limit)
        except Exception as e:
            print(f'liquidai catalog unavailable: {e}')
            return []
        fmt = self.FORMAT.get(runtime, 'torch')
        out = []
        for row in data.get('models', []):
            kind = row.get('kind')
            if kind == 'embed':
                continue          # no chat head — it can't drive the loop
            # the tab's worker generates text and vision; the ONNX speech
            # builds aren't loadable there (liquidai's own note)
            if runtime == 'browser' and kind == 'audio':
                continue
            for repo in ((row.get('variants') or {}).get(fmt) or {}).get('repos', []):
                if repo.get('repo'):
                    out.append(repo['repo'])
        with self._lock:
            self._cache[runtime] = (time.time(), out)
        return out

    def cloud_models(self) -> List[str]:
        """Hosted model ids — only answerable with a cloud key on file."""
        try:
            data = self._client()._get('/cloud/models')
        except Exception:
            return []
        return [row.get('id') for row in data.get('models', []) if row.get('id')]


CATALOG = Catalog()


class ZeroCostModel:
    """A provider whose calls cost this module nothing.

    Shape matches the model modules the agent loop already drives: forward()
    takes the whole context as one string and hands text back, model2info()
    is the price list the meter reads.
    """

    is_free = True          # runs on it are never billed, and never need credit

    def model2info(self) -> dict:
        return _FreeCatalog()

    def free_models(self) -> List[str]:
        return []

    def forward(self, prompt: str, **kwargs) -> str:
        raise NotImplementedError


class LiquidModel(ZeroCostModel):
    """LFM weights run by the liquidai module — on this box, or in its cloud."""

    def __init__(self, runtime: str = 'server', **kwargs):
        self.runtime = runtime

    def free_models(self) -> List[str]:
        # the server runtime is free by construction; the cloud runs on the
        # operator's own Liquid key, which this module never bills for
        return CATALOG.repos(self.runtime) if self.runtime == 'server' \
            else CATALOG.cloud_models()

    def forward(self, prompt: str, stream: bool = True, model: str = None,
                max_tokens: int = 1024, temperature: float = 0.0,
                free: bool = False, history: List[dict] = None, **kwargs) -> str:
        """One completion, through the liquidai module.

        `stream` is accepted and ignored: liquidai streams internally and
        hands back the finished text, and the agent loop needs the whole
        step anyway before it can parse it.
        """
        if not m:
            raise RuntimeError('mod protocol unavailable — cannot reach liquidai')
        if not model:
            raise RuntimeError('no model — pick an LFM repo for this provider')
        try:
            out = m.mod('liquidai')().chat(
                prompt=prompt, model=model, runtime=self.runtime,
                max_tokens=max_tokens, temperature=temperature)
        except Exception as e:
            # liquidai answers a bad model with a paragraph explaining which
            # ids its runtime takes. Say whose model it was — the run's error
            # line is the only place the reader sees the two together.
            raise RuntimeError(
                f"liquidai ({self.runtime} runtime) could not run {model!r}: {e}") from e
        import os as _os
        if _os.environ.get('AGENT_PROMPT_DUMP'):
            with open(_os.environ['AGENT_PROMPT_DUMP'], 'a') as _f:
                _f.write('\n--- liquid client=%r model=%r url=%r\n--- OUT: %r\n'
                         % (type(m.mod('liquidai')()).__module__,
                            model, getattr(m.mod('liquidai')(), 'api_url', '?'),
                            str(out)[:600]))
        if isinstance(out, dict):
            if not out.get('ok'):
                raise RuntimeError(out.get('error') or 'liquidai chat failed')
            return out.get('text') or ''
        return str(out)


# ── browser bridge ───────────────────────────────────────────────────

class Bridge:
    """Requests parked for a tab, and the answers coming back.

    One session per open run stream. `open()` registers the callback that
    pushes an event down that stream; `ask()` blocks the run thread until
    `deliver()` (the tab's POST) or the timeout wakes it.
    """

    def __init__(self):
        self._sessions: Dict[str, dict] = {}
        self._pending: Dict[str, dict] = {}      # request id -> slot
        self._lock = threading.Lock()
        self._local = threading.local()

    # ── session lifecycle (API side) ─────────────────────────────────

    def open(self, session: str, emit) -> str:
        with self._lock:
            self._sessions[session] = {'emit': emit, 'opened': time.time()}
        return session

    def close(self, session: str):
        """Drop a session and fail whatever it still owed — a closed tab
        must not leave a run thread blocked until the timeout."""
        with self._lock:
            self._sessions.pop(session, None)
            owed = [slot for slot in self._pending.values()
                    if slot['session'] == session and not slot['done'].is_set()]
        for slot in owed:
            slot['error'] = 'the console tab went away mid-run'
            slot['done'].set()

    def is_open(self, session: Optional[str]) -> bool:
        with self._lock:
            return bool(session) and session in self._sessions

    def sessions(self) -> List[str]:
        with self._lock:
            return list(self._sessions)

    # ── per-run binding (thread-local, like the meter's tally) ────────

    def bind(self, session: Optional[str]):
        self._local.session = session

    def bound(self) -> Optional[str]:
        return getattr(self._local, 'session', None)

    # ── the two halves of one call ───────────────────────────────────

    def ask(self, payload: dict, timeout: float = BROWSER_TIMEOUT) -> str:
        """Send one generation request to the bound tab and wait for its text."""
        session = self.bound()
        with self._lock:
            live = self._sessions.get(session)
        if not live:
            raise RuntimeError(NO_TAB)
        rid = uuid.uuid4().hex[:12]
        slot = {'session': session, 'done': threading.Event(),
                'text': None, 'error': None}
        with self._lock:
            self._pending[rid] = slot
        try:
            live['emit']({'type': 'model_request', 'session': session,
                          'id': rid, **payload})
            if not slot['done'].wait(timeout):
                raise TimeoutError(
                    f'the browser model did not answer within {int(timeout)}s — '
                    f'a big model on a slow tab can take longer than one run allows')
            if slot['error']:
                raise RuntimeError(slot['error'])
            return slot['text'] or ''
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    def deliver(self, request_id: str, text: str = None, error: str = None) -> dict:
        """The tab answering. Returns whether anyone was still waiting."""
        with self._lock:
            slot = self._pending.get(request_id)
        if not slot:
            return {'delivered': False, 'reason': 'no run is waiting on that request'}
        slot['text'], slot['error'] = text, error
        slot['done'].set()
        return {'delivered': True, 'id': request_id}


BROWSER = Bridge()


class BrowserModel(ZeroCostModel):
    """The model that isn't here — it runs in whoever's tab started the run."""

    def __init__(self, bridge: Bridge = None, **kwargs):
        self.bridge = bridge or BROWSER

    def free_models(self) -> List[str]:
        return CATALOG.repos('browser')

    def forward(self, prompt: str, stream: bool = True, model: str = None,
                max_tokens: int = 1024, temperature: float = 0.0,
                free: bool = False, history: List[dict] = None, **kwargs) -> str:
        if not model:
            raise RuntimeError('no model — pick an ONNX LFM repo for this provider')
        messages = list(history or []) + [{'role': 'user', 'content': prompt}]
        return self.bridge.ask({
            'model': model, 'messages': messages,
            'max_tokens': max_tokens, 'temperature': temperature,
        })
