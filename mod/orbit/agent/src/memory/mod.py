"""
memory - the agent's memory subsystem, as its own mod

Memory is more than a prompt. It is a layered subsystem with its own
class logic and its own servable process:

    working   - the per-run dict that gets compiled into the LLM prompt
                (the classic behavior: add/get/update/rm)
    episodic  - append-only log of every step the agent executes,
                persisted as JSONL so runs leave a durable trail
    semantic  - keyed facts with tags, recalled by keyword-overlap score
                and injected back into future runs via compile()

The framework anchor (`m.mod('agent.memory')`) resolves to the Memory
class at the bottom of this file. It speaks the mod protocol (forward)
and can run standalone: serve() starts its own FastAPI process on :50119.

Usage:
    mem = Memory()
    mem.add('goal', '...')                    # working (prompt state)
    mem.observe({'tool': 'bash', ...})        # episodic
    mem.remember('style', 'tabs not spaces')  # semantic
    mem.recall('what code style?')            # scored facts
    mem.compile('fix the bug')                # prompt-ready context block
    mem.serve()                               # standalone memory service
"""
import os
import json
import time
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PORT = 50119


class EpisodicLog:
    """Append-only event log. In-RAM ring buffer + JSONL persistence.

    The file self-rotates: past MAX_BYTES it is rewritten with only the
    most recent KEEP_LINES events, so the trail never grows unbounded.
    """

    MAX_BYTES = 2_000_000
    KEEP_LINES = 2000

    def __init__(self, path: Path = None, ring: int = 200):
        self.path = path
        self._ring = ring
        self._events: List[Dict] = []

    def append(self, event: Dict[str, Any]):
        event = dict(event)
        event.setdefault('ts', time.time())
        self._events.append(event)
        if len(self._events) > self._ring:
            self._events = self._events[-self._ring:]
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, 'a') as f:
                    f.write(json.dumps(event, default=str) + '\n')
                if self.path.stat().st_size > self.MAX_BYTES:
                    self._rotate()
            except Exception:
                pass
        return event

    def _rotate(self):
        with open(self.path) as f:
            lines = f.readlines()[-self.KEEP_LINES:]
        tmp = self.path.with_suffix('.jsonl.tmp')
        tmp.write_text(''.join(lines))
        tmp.replace(self.path)

    def tail(self, n: int = 50, session: str = None) -> List[Dict]:
        """Most recent n events, newest last. Reads back from disk if the
        ring is cold (e.g. a fresh process inspecting an old trail)."""
        events = self._events
        if not events and self.path is not None and self.path.exists():
            try:
                with open(self.path) as f:
                    lines = f.readlines()[-max(n * 4, n):]
                events = [json.loads(l) for l in lines if l.strip()]
            except Exception:
                events = []
        if session:
            events = [e for e in events if e.get('session') == session]
        return events[-n:]

    def count(self) -> int:
        if self.path is not None and self.path.exists():
            try:
                with open(self.path) as f:
                    return sum(1 for l in f if l.strip())
            except Exception:
                pass
        return len(self._events)


class FactStore:
    """Semantic memory: keyed facts with tags, keyword-overlap recall."""

    def __init__(self, path: Path = None):
        self.path = path
        self._facts: Optional[Dict[str, Dict]] = None

    @property
    def facts(self) -> Dict[str, Dict]:
        if self._facts is None:
            self._facts = {}
            if self.path is not None and self.path.exists():
                try:
                    with open(self.path) as f:
                        self._facts = json.load(f)
                except Exception:
                    pass
        return self._facts

    def _save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump(self.facts, f, indent=2, default=str)
        except Exception:
            pass

    def put(self, name: str, content: str, tags: List[str] = None) -> Dict:
        fid = name.lower().strip().replace(' ', '-')
        fact = {'id': fid, 'name': name, 'content': content,
                'tags': tags or [], 'updated': time.time()}
        self.facts[fid] = fact
        self._save()
        return fact

    def rm(self, fid: str) -> bool:
        existed = self.facts.pop(fid, None) is not None
        self._save()
        return existed

    def ls(self) -> List[Dict]:
        return sorted(self.facts.values(), key=lambda f: -f.get('updated', 0))

    @staticmethod
    def _tokens(text: str) -> set:
        return {w for w in ''.join(c.lower() if c.isalnum() else ' '
                                   for c in str(text)).split() if len(w) > 2}

    def recall(self, query: str, k: int = 5) -> List[Dict]:
        """Facts scored by keyword overlap with the query, best first."""
        q = self._tokens(query)
        if not q:
            return []
        scored = []
        for fact in self.facts.values():
            doc = self._tokens(f"{fact['name']} {fact['content']} {' '.join(fact.get('tags', []))}")
            overlap = len(q & doc)
            if overlap:
                scored.append((overlap / len(q), fact))
        scored.sort(key=lambda s: -s[0])
        return [{**f, 'score': round(s, 3)} for s, f in scored[:k]]


class Memory:
    """Layered agent memory with its own process. See module docstring.

    Backwards compatible with the classic prompt-dict interface
    (add/get/keys/rm/update/clear/track_*/save/load/summary) — everything
    the Agent loop already calls keeps working unchanged.
    """
    description = "Agent memory subsystem - working/episodic/semantic layers, servable process"

    def __init__(self, dir: str = None, persist: bool = True, session: str = None, **kwargs):
        # working memory (the classic prompt state)
        self.memory: Dict[str, Any] = {}
        self._files_read = set()
        self._files_written = set()
        self._errors: List[str] = []
        # durable layers live off-tree under ~/.mod/agent/memory/
        base = Path(dir) if dir else Path.home() / '.mod' / 'agent' / 'memory'
        self.dir = base
        self.persist = persist
        self.session = session or f"s{int(time.time())}"
        self.episodic = EpisodicLog(base / 'episodes.jsonl' if persist else None)
        self.semantic = FactStore(base / 'facts.json' if persist else None)
        self._port = DEFAULT_PORT

    # ── working memory (classic interface) ───────────────────────────

    def add(self, k, v: Any = None):
        if isinstance(k, dict):
            self.memory.update(k)
        else:
            self.memory[k] = v
        return self.memory

    def clear(self):
        self.memory = {}
        self._files_read.clear()
        self._files_written.clear()
        self._errors.clear()
        return self.memory

    def get(self, key=None):
        return self.memory.get(key, None) if key is not None else self.memory

    def keys(self):
        return list(self.memory.keys())

    def rm(self, key):
        if key in self.memory:
            del self.memory[key]
        return self.memory

    def update(self, data: dict):
        assert isinstance(data, dict), 'Data must be a dictionary'
        self.memory.update(data)
        return self.memory

    # ── file tracking ────────────────────────────────────────────────

    def track_read(self, file_path: str):
        self._files_read.add(file_path)

    def track_write(self, file_path: str):
        self._files_written.add(file_path)

    def get_files_read(self):
        return list(self._files_read)

    def get_files_written(self):
        return list(self._files_written)

    # ── error tracking ───────────────────────────────────────────────

    def track_error(self, error: str):
        self._errors.append(error)
        if len(self._errors) > 20:
            self._errors = self._errors[-20:]

    def get_errors(self):
        return list(self._errors)

    # ── episodic layer ───────────────────────────────────────────────

    def observe(self, event: Dict[str, Any], max_result: int = 500) -> Dict:
        """Record one executed agent step as an episode. Results are
        truncated so the trail stays cheap to write and read back."""
        e = {'session': self.session,
             'tool': event.get('tool'),
             'params': event.get('params')}
        if 'result' in event:
            e['result'] = str(event['result'])[:max_result]
        if event.get('error'):
            e['error'] = str(event['error'])[:max_result]
            self.track_error(e['error'])
        # tool-aware side effects: keep file tracking honest automatically
        fp = (event.get('params') or {}).get('file_path')
        if fp:
            if event.get('tool') in ('write', 'edit', 'patch'):
                self.track_write(fp)
            elif event.get('tool') == 'read':
                self.track_read(fp)
        return self.episodic.append(e)

    def episodes(self, n: int = 50, session: str = None) -> List[Dict]:
        return self.episodic.tail(n, session=session)

    # ── semantic layer ───────────────────────────────────────────────

    def remember(self, name: str, content: str, tags: List[str] = None) -> Dict:
        """Store a durable fact that future runs can recall."""
        return self.semantic.put(name, content, tags)

    def forget(self, fid: str) -> Dict:
        return {'forgot': fid, 'existed': self.semantic.rm(fid)}

    def facts(self) -> List[Dict]:
        return self.semantic.ls()

    def recall(self, query: str, k: int = 5) -> List[Dict]:
        """Facts relevant to a query, scored by keyword overlap."""
        return self.semantic.recall(query, k=k)

    # ── compile: memory -> prompt context ────────────────────────────

    def compile(self, query: str = None, k: int = 5, episodes: int = 0) -> str:
        """Render the layers into a prompt-ready context block.

        Working memory stays the caller's concern (the agent already
        serializes it); this adds what the dict alone can't: recalled
        facts and, optionally, the recent episode trail.
        """
        parts = []
        recalled = self.recall(query, k=k) if query else []
        if recalled:
            lines = [f"- [{f['name']}] {f['content']}" for f in recalled]
            parts.append("RECALLED FACTS (from past runs):\n" + '\n'.join(lines))
        if episodes:
            trail = self.episodes(episodes)
            if trail:
                lines = [f"- {e.get('tool')}({json.dumps(e.get('params', {}), default=str)[:120]})"
                         + (' !error' if e.get('error') else '')
                         for e in trail]
                parts.append("RECENT STEPS:\n" + '\n'.join(lines))
        return '\n\n'.join(parts)

    # ── persistence of working state (classic interface) ────────────

    def save(self, path: str = None):
        path = path or os.path.join(os.getcwd(), '.agent_memory.json')
        data = {
            'memory': {k: v for k, v in self.memory.items()
                       if k not in ('tools', 'goal', 'output_format')},
            'files_read': list(self._files_read),
            'files_written': list(self._files_written),
        }
        try:
            Path(path).write_text(json.dumps(data, default=str, indent=2))
            return True
        except Exception:
            return False

    def load(self, path: str = None):
        path = path or os.path.join(os.getcwd(), '.agent_memory.json')
        try:
            data = json.loads(Path(path).read_text())
            self.memory.update(data.get('memory', {}))
            self._files_read.update(data.get('files_read', []))
            self._files_written.update(data.get('files_written', []))
            return True
        except Exception:
            return False

    # ── status / summary ─────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            'keys': self.keys(),
            'step': self.get('step'),
            'files_read': len(self._files_read),
            'files_written': len(self._files_written),
            'errors': len(self._errors),
            'history_length': len(self.get('history') or []),
        }

    def status(self) -> dict:
        return {
            'module': 'agent.memory',
            'session': self.session,
            'working_keys': self.keys(),
            'episodes': self.episodic.count(),
            'facts': len(self.semantic.facts),
            'dir': str(self.dir),
            'persist': self.persist,
            'port': self._port,
        }

    # ── own process (standalone memory service) ──────────────────────

    def serve(self, port: int = None, dev: bool = False) -> dict:
        """Run the memory service as its own process (FastAPI on :50119)."""
        port = port or self._port
        self.kill(port)
        api_dir = Path(__file__).parent
        log_dir = Path('/tmp/agent')
        log_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['PORT'] = str(port)
        env['AGENT_MEMORY_DIR'] = str(self.dir)
        log = open(log_dir / 'memory.log', 'w')
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port)]
        if dev:
            cmd.append('--reload')
        subprocess.Popen(cmd, cwd=str(api_dir), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
        return {'memory': f'http://localhost:{port}',
                'log': str(log_dir / 'memory.log')}

    def kill(self, port: int = None) -> dict:
        port = port or self._port
        killed = []
        try:
            result = subprocess.run(
                ['pgrep', '-f', f'uvicorn.*api:app.*{port}'],
                capture_output=True, text=True)
            for pid in result.stdout.strip().split('\n'):
                if pid:
                    os.kill(int(pid), signal.SIGTERM)
                    killed.append(pid)
        except Exception:
            pass
        return {'killed': killed}

    def health(self) -> dict:
        try:
            import requests as req
            r = req.get(f'http://localhost:{self._port}/health', timeout=2)
            return r.json()
        except Exception:
            return {'status': 'down'}

    # ── mod protocol entry point ─────────────────────────────────────

    def forward(self, action: str = None, **kwargs) -> Any:
        """memory <action> [args]

        Actions: status, summary, get, add, rm, keys, clear,
                 observe, episodes, remember, forget, facts, recall,
                 compile, serve, kill, health, test
        """
        actions = {
            'status': lambda: self.status(),
            'summary': lambda: self.summary(),
            'get': lambda: self.get(kwargs.get('key')),
            'add': lambda: self.add(kwargs.get('key', ''), kwargs.get('value')),
            'rm': lambda: self.rm(kwargs.get('key', '')),
            'keys': lambda: self.keys(),
            'clear': lambda: self.clear(),
            'observe': lambda: self.observe(kwargs.get('event', kwargs)),
            'episodes': lambda: self.episodes(kwargs.get('n', 50), kwargs.get('session')),
            'remember': lambda: self.remember(kwargs.get('name', ''), kwargs.get('content', ''), kwargs.get('tags')),
            'forget': lambda: self.forget(kwargs.get('id', '')),
            'facts': lambda: self.facts(),
            'recall': lambda: self.recall(kwargs.get('query', kwargs.get('q', '')), kwargs.get('k', 5)),
            'compile': lambda: self.compile(kwargs.get('query'), kwargs.get('k', 5), kwargs.get('episodes', 0)),
            'serve': lambda: self.serve(kwargs.get('port'), kwargs.get('dev', False)),
            'kill': lambda: self.kill(kwargs.get('port')),
            'health': lambda: self.health(),
            'test': lambda: self.test(),
        }
        if not action or action not in actions:
            return {'module': 'agent.memory',
                    'description': self.description,
                    'actions': list(actions.keys()),
                    'status': self.status()}
        return actions[action]()

    def test(self):
        self.add('test1', 'This is a test memory item one.')
        self.add('test2', 'This is a test memory item two.')
        assert self.get('test1') == 'This is a test memory item one.'
        assert self.keys() == ['test1', 'test2']
        self.rm('test1')
        assert self.get('test1') is None
        self.clear()
        assert self.memory == {}
        # file tracking
        self.track_read('/tmp/test.py')
        assert '/tmp/test.py' in self.get_files_read()
        self.track_write('/tmp/out.py')
        assert '/tmp/out.py' in self.get_files_written()
        # error tracking
        self.track_error('test error')
        assert len(self.get_errors()) == 1
        # episodic + semantic layers — on a throwaway in-RAM instance so the
        # self-test never writes into the durable ~/.mod/agent/memory stores
        scratch = Memory(persist=False)
        scratch.observe({'tool': 'bash', 'params': {'command': 'ls'}, 'result': 'ok'})
        assert scratch.episodes(1)[0]['tool'] == 'bash'
        scratch.remember('style', 'use tabs not spaces', tags=['code'])
        hits = scratch.recall('what style of tabs?')
        assert hits and hits[0]['id'] == 'style'
        assert 'RECALLED FACTS' in scratch.compile('tabs style')
        scratch.forget('style')
        self.clear()
        return True
