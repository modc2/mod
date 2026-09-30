"""
fleet - any model module in the fleet as an agent provider.

The console's provider pill used to be a fixed list: openrouter and venice
(model2info clients), the LFM runtimes and hermes (built in this module). Every
other model module on the box — chutes, aipg, freetoken, grokbot, the orbit
openrouter router, … — was unreachable from here, though each one already
knows how to answer a prompt with its own key.

This file is the adapter that makes them pickable, and it asks nothing of the
modules themselves. A fleet provider is found by READING source, not by
importing it: a module whose class has a `chat` (or `complete`) method AND a
`models` method is a model module. Listing the fleet therefore imports nothing,
wakes nothing and costs nothing; a module is only loaded when a run (or a model
list) actually asks it for something.

Provider keys are `mod:<name>` so a fleet module can never shadow a built-in
of the same name (`openrouter` is model.openrouter; `mod:openrouter` is the
orbit router module).

Who pays: the module's own key — the operator's. So a fleet run is never
billed against a guest's agent credit (there is no price list to bill from),
and for the same reason it is HOST-ONLY: a guest must not spend the operator's
chutes or aipg balance through this console. See Mod.run.
"""
import re
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .liquid import ZeroCostModel

PREFIX = 'mod:'

# the directory that holds this module's siblings (…/orbit)
ROOT = Path(os.environ.get('AGENT_FLEET_ROOT') or Path(__file__).resolve().parents[2])
WORKER = str(Path(__file__).resolve().parent / 'fleet_worker.py')
LOG_DIR = Path(os.environ.get('AGENT_FLEET_LOGS', '/tmp/agent/fleet'))

# where a module's class may live, in the order the framework anchors it
ANCHORS = ('mod.py', '{n}/mod.py', 'src/mod.py', '{n}.py', '{n}/{n}.py')

CHAT_RE = re.compile(r'^\s{4}def (chat|complete)\(', re.M)
MODELS_RE = re.compile(r'^\s{4}def (models|model2info)\(', re.M)

# modules this one already drives natively (see Mod.PROVIDERS) — listing them
# twice would offer two routes to the same weights with different behaviour
NATIVE = {'agent', 'liquidai', 'hermes', 'venice'}

SCAN_TTL = 300.0          # the fleet only changes when someone adds a module
MODELS_TTL = 600.0        # a catalog moves when its upstream ships something
MODELS_TIMEOUT = float(os.environ.get('AGENT_FLEET_MODELS_TIMEOUT', 12))
LOAD_TIMEOUT = float(os.environ.get('AGENT_FLEET_LOAD_TIMEOUT', 60))
CHAT_TIMEOUT = float(os.environ.get('AGENT_FLEET_CHAT_TIMEOUT', 600))
IDLE_SECONDS = float(os.environ.get('AGENT_FLEET_IDLE', 900))


def is_fleet(provider: Optional[str]) -> bool:
    return bool(provider) and str(provider).startswith(PREFIX)


def name_of(provider: str) -> str:
    return str(provider)[len(PREFIX):] if is_fleet(provider) else str(provider)


class Worker:
    """One fleet module in its own interpreter (see fleet_worker.py).

    One request at a time per module — the lock is the queue. A request that
    times out kills the process: its reply could still arrive and would be
    read as the answer to the next question.
    """

    def __init__(self, name: str):
        self.name = name
        self.proc: Optional[subprocess.Popen] = None
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.lock = threading.Lock()
        self.used = 0.0

    def _start(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_DIR / f'{self.name}.log', 'ab')
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(
            [sys.executable, '-u', WORKER, self.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
            cwd=str(ROOT.parent.parent), text=True, bufsize=1,
            start_new_session=True)
        log.close()
        lines, out = self.lines, self.proc.stdout

        def pump():
            for line in out:
                lines.put(line)
            lines.put(None)                 # EOF: the process is gone
        threading.Thread(target=pump, daemon=True, name=f'fleet-{self.name}').start()
        hello = self._read(LOAD_TIMEOUT)
        if not hello.get('ok'):
            self.stop()
            raise RuntimeError(hello.get('error') or f'{self.name} failed to load')

    def _read(self, timeout: float) -> dict:
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            self.stop()
            raise RuntimeError(f'{self.name} did not answer within {int(timeout)}s')
        if line is None:
            self.stop()
            raise RuntimeError(f'the {self.name} worker exited — see {LOG_DIR}/{self.name}.log')
        return json.loads(line)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def request(self, op: str, timeout: float, **kwargs):
        with self.lock:
            if not self.alive():
                self._start()
            self.used = time.time()
            self.proc.stdin.write(json.dumps({'op': op, **kwargs}, default=str) + '\n')
            self.proc.stdin.flush()
            reply = self._read(timeout)
            self.used = time.time()
        if not reply.get('ok'):
            raise RuntimeError(reply.get('error') or f'{self.name} {op} failed')
        return reply.get('result')

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass


class Workers:
    """The worker per module, reaped when idle so an unused module costs nothing."""

    def __init__(self):
        self._w: Dict[str, Worker] = {}
        self._lock = threading.Lock()
        self._reaper = None

    def get(self, name: str) -> Worker:
        with self._lock:
            w = self._w.get(name)
            if w is None:
                w = self._w[name] = Worker(name)
            if self._reaper is None:
                self._reaper = threading.Thread(target=self._reap, daemon=True,
                                                name='fleet-reaper')
                self._reaper.start()
            return w

    def _reap(self) -> None:
        while True:
            time.sleep(60)
            for w in list(self._w.values()):
                if w.alive() and time.time() - w.used > IDLE_SECONDS \
                        and w.lock.acquire(blocking=False):
                    try:
                        w.stop()
                    finally:
                        w.lock.release()

    def status(self) -> Dict[str, bool]:
        return {n: w.alive() for n, w in self._w.items()}


WORKERS = Workers()


class Fleet:
    """The model modules on this box, found by reading their source."""

    def __init__(self, root: Path = None):
        self.root = Path(root or ROOT)
        self._scan: Optional[tuple] = None          # (at, {name: row})
        self._models: Dict[str, tuple] = {}         # name -> (at, [ids])
        self._warming: set = set()
        self._lock = threading.Lock()

    # ── discovery ────────────────────────────────────────────────────

    def scan(self, refresh: bool = False) -> Dict[str, dict]:
        with self._lock:
            if self._scan and not refresh and time.time() - self._scan[0] < SCAN_TTL:
                return self._scan[1]
        found: Dict[str, dict] = {}
        try:
            dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
        except OSError:
            dirs = []
        for d in dirs:
            name = d.name
            if name in NATIVE or name.startswith(('.', '_')):
                continue
            cfg_path = d / 'config.json'
            if not cfg_path.is_file():
                continue
            src = self._anchor(d)
            if not src:
                continue
            try:
                text = src.read_text(errors='ignore')
            except OSError:
                continue
            if not (CHAT_RE.search(text) and MODELS_RE.search(text)):
                continue
            cfg = {}
            try:
                cfg = json.loads(cfg_path.read_text())
            except Exception:
                pass
            desc = str(cfg.get('description') or '').strip()
            found[name] = {
                'name': name,
                'key': PREFIX + name,
                'description': desc,
                'hint': (re.split(r'\.\s| — ', desc)[0][:90] if desc else f'the {name} module'),
                'icon': cfg.get('icon'),
                'default_model': cfg.get('default_model') or '',
                'source': str(src.relative_to(self.root)),
            }
        with self._lock:
            self._scan = (time.time(), found)
        return found

    @staticmethod
    def _anchor(d: Path) -> Optional[Path]:
        for a in ANCHORS:
            p = d / a.format(n=d.name)
            if p.is_file():
                return p
        return None

    def keys(self) -> List[str]:
        return [row['key'] for row in self.scan().values()]

    def get(self, provider: str) -> Optional[dict]:
        return self.scan().get(name_of(provider))

    # ── model lists ──────────────────────────────────────────────────

    def cached_models(self, provider: str, warm: bool = True) -> List[str]:
        """Whatever list is on hand now, never blocking. A stale or missing
        one is refreshed in the background so the next ask has it."""
        name = name_of(provider)
        hit = self._models.get(name)
        if warm and (not hit or time.time() - hit[0] > MODELS_TTL):
            self._warm(name)
        return list(hit[1]) if hit else []

    def models(self, provider: str, timeout: float = MODELS_TIMEOUT) -> List[str]:
        """The module's model list, waiting up to `timeout` for it."""
        name = name_of(provider)
        hit = self._models.get(name)
        if hit and time.time() - hit[0] < MODELS_TTL:
            return list(hit[1])
        box: Dict[str, list] = {}
        t = threading.Thread(target=lambda: box.update(ids=self._fetch(name)), daemon=True)
        t.start()
        t.join(timeout)
        return list(box.get('ids') or (hit[1] if hit else []))

    def _warm(self, name: str) -> None:
        with self._lock:
            if name in self._warming:
                return
            self._warming.add(name)

        def go():
            try:
                self._fetch(name)
            finally:
                with self._lock:
                    self._warming.discard(name)
        threading.Thread(target=go, daemon=True).start()

    def _fetch(self, name: str) -> List[str]:
        try:
            ids = list(WORKERS.get(name).request('models', MODELS_TIMEOUT * 5) or [])
        except Exception as e:
            print(f'[agent] fleet model list for {name} failed: {e}')
            ids = []
        # an empty answer is cached too (briefly, via the same TTL) — a module
        # that can't list is not asked again on every page load
        self._models[name] = (time.time(), ids)
        return ids


FLEET = Fleet()


class FleetModel(ZeroCostModel):
    """One fleet module as a provider, driven through its worker.

    ZeroCostModel because nothing here is billed against agent credit — the
    module spends its own key. `host_only` is what keeps guests off it.
    """

    host_only = True

    def __init__(self, provider: str, **kwargs):
        self.provider = provider if is_fleet(provider) else PREFIX + provider
        self.name = name_of(self.provider)

    def free_models(self) -> List[str]:
        return []

    def default_model(self) -> Optional[str]:
        row = FLEET.get(self.provider) or {}
        return row.get('default_model') or None

    def forward(self, prompt: str, stream: bool = True, model: str = None,
                max_tokens: int = 1024, temperature: float = 0.0,
                free: bool = False, history: List[dict] = None, **kwargs) -> str:
        """One completion. `stream` is ignored: the loop needs the whole step
        before it can parse it, and every module here returns one either way."""
        model = model or self.default_model()
        try:
            return WORKERS.get(self.name).request(
                'complete', CHAT_TIMEOUT, prompt=prompt, model=model,
                max_tokens=max_tokens, temperature=temperature,
                history=history or None)
        except RuntimeError as e:
            raise RuntimeError(f"{self.name} could not run "
                               f"{model or 'its default model'}: {e}") from e
