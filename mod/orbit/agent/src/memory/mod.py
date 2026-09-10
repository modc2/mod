"""
memory - the agent's memory subsystem, as its own mod

Memory is more than a prompt. It is a layered subsystem with its own
class logic and its own servable process:

    working   - the per-run dict that gets compiled into the LLM prompt
                (the classic behavior: add/get/update/rm)
    episodic  - append-only log of every step the agent executes,
                persisted as JSONL so runs leave a durable trail
    dialogue  - what the caller asked and what the agent answered, scoped
                to whoever asked, so a new conversation is not a stranger
    semantic  - keyed facts with tags, injected back into future runs

Every layer is written the same way and, more to the point, *read* the same
way: one retrieval engine (retrieval.py) ranks all of them, so recall() on
facts, on past turns and on the step trail mean the same thing and their
scores are comparable. retrieve() is the layer-agnostic entry point —
compile() is just its prompt-shaped rendering.

This file is the base class and the default implementation. Backends that
want to store or rank differently subclass it in their own directory
(default/, ephemeral/, …) and the registry (registry.py) hands agents the
one they were built with. The framework anchor (`m.mod('agent.memory')`)
resolves to the Memory class at the bottom of this file — keep it last.
It speaks the mod protocol (forward) and can run standalone: serve()
starts its own FastAPI process on :50119.

Usage:
    mem = Memory()
    mem.add('goal', '...')                    # working (prompt state)
    mem.observe({'tool': 'bash', ...})        # episodic
    mem.remember('style', 'tabs not spaces')  # semantic
    mem.recall('what code style?')            # scored facts
    mem.retrieve('the relay port')            # scored hits, every layer
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

from .retrieval import rank, hit, token_set

DEFAULT_PORT = 50119

# the layers retrieve() searches when the caller names none. Working memory is
# left out on purpose: it is the prompt being built right now, so retrieving
# from it would only hand the model back what it is already reading.
DEFAULT_LAYERS = ('semantic', 'dialogue', 'episodic')


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

    def tail(self, n: int = 50, session: str = None, who: str = None) -> List[Dict]:
        """Most recent n events, newest last. Reads back from disk if the
        ring is cold (e.g. a fresh process inspecting an old trail).

        `who` is the address the event was recorded for. Filtering on it is
        what keeps one caller's conversation out of another's context.
        """
        events = self.all(n * 4)
        if session:
            events = [e for e in events if e.get('session') == session]
        if who:
            events = [e for e in events if e.get('who') == who]
        return events[-n:]

    def all(self, n: int = 200) -> List[Dict]:
        """The most recent n events with no filter applied — the pool a
        scored recall runs over."""
        events = self._events
        if not events and self.path is not None and self.path.exists():
            try:
                with open(self.path) as f:
                    lines = f.readlines()[-max(n, 1):]
                events = [json.loads(l) for l in lines if l.strip()]
            except Exception:
                events = []
        return events[-max(n, 1):]

    def count(self) -> int:
        if self.path is not None and self.path.exists():
            try:
                with open(self.path) as f:
                    return sum(1 for l in f if l.strip())
            except Exception:
                pass
        return len(self._events)


class FactStore:
    """Semantic memory: keyed facts with tags, ranked by the shared retriever.

    The cache follows the file rather than being loaded once. More
    than one process writes here — the API, the memory service on :50119, a
    `m agent/memory/remember` on the command line — and a store cached for the
    life of a process would answer every later retrieval from a snapshot taken
    at startup: a fact stored one way stayed invisible to the other until a
    restart.
    """

    def __init__(self, path: Path = None):
        self.path = path
        self._facts: Optional[Dict[str, Dict]] = None
        self._stat = None          # the (mtime, size) the cache was read at

    def _stamp(self):
        """(mtime, size) — the cheap "has this changed?" signature.

        Size is in there because mtime alone isn't enough: two writes inside
        one filesystem timestamp tick carry the same mtime, and on a store
        that is appended to, the second one is the bigger file.
        """
        try:
            st = self.path.stat() if self.path is not None else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st else None

    @property
    def facts(self) -> Dict[str, Dict]:
        stamp = self._stamp()
        if self._facts is None or stamp != self._stat:
            loaded: Dict[str, Dict] = {}
            if stamp is not None:
                try:
                    with open(self.path) as f:
                        loaded = json.load(f)
                except Exception:
                    # a torn or unreadable file must not wipe what we hold
                    loaded = self._facts or {}
            self._facts = loaded
            self._stat = stamp
        return self._facts

    def _save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump(self._facts if self._facts is not None else {},
                          f, indent=2, default=str)
            self._stat = self._stamp()
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

    # the pre-retrieval tokenizer, kept as an alias — callers outside this
    # file used it to split text the way memory does
    _tokens = staticmethod(token_set)

    @staticmethod
    def text_of(fact: Dict) -> str:
        """What a fact is searched on: its name, its content and its tags."""
        return f"{fact.get('name', '')} {fact.get('content', '')} " \
               f"{' '.join(fact.get('tags') or [])}"

    def recall(self, query: str, k: int = 5) -> List[Dict]:
        """Facts ranked against the query by the shared retriever, best first."""
        return [{**f, 'score': round(s, 3)}
                for s, f in rank(query, self.facts.values(), self.text_of,
                                 ts_of=lambda f: f.get('updated'), k=k)]


class Memory:
    """Layered agent memory with its own process. See module docstring.

    Backwards compatible with the classic prompt-dict interface
    (add/get/keys/rm/update/clear/track_*/save/load/summary) — everything
    the Agent loop already calls keeps working unchanged.

    This is also the base every memory backend subclasses: a backend that
    stores or ranks differently overrides what it changes and inherits the
    rest, and the agent it is snapped into never learns the difference.
    """
    description = "Agent memory subsystem - working/episodic/semantic layers, servable process"

    # registry identity — what the console shows and what an agent config
    # names when it is built with this memory module (see registry.py)
    kind = 'base'
    label = 'Memory'
    layers = DEFAULT_LAYERS

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
        # dialogue: what the user asked and what the agent answered. Kept apart
        # from the step trail because it is the only layer written for a human
        # to read back, and the only one scoped to a caller.
        self.dialogue = EpisodicLog(base / 'exchanges.jsonl' if persist else None)
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

    # ── dialogue layer (what the user and the agent said) ────────────
    #
    # A run is stateless on its own: the console starts a fresh conversation
    # every time, so without this layer the agent meets the same person as a
    # stranger on every message. Each finished run leaves one exchange here,
    # and the next run's prompt is compiled with the relevant ones back in.

    MAX_TURN = 1200   # chars kept per side — enough to recall, cheap to inject

    def exchange(self, query: str, answer: str, session: str = None,
                 who: str = None, agent: str = None) -> Dict:
        """Record one user↔agent turn. `who` is the caller's address (None for
        an anonymous visitor), `session` the console conversation it came from."""
        return self.dialogue.append({
            'session': session or self.session,
            'who': who or None,
            'agent': agent or None,
            'query': str(query or '')[:self.MAX_TURN],
            'answer': str(answer or '')[:self.MAX_TURN],
        })

    def exchanges(self, n: int = 20, session: str = None, who: str = None) -> List[Dict]:
        """Recent turns, newest last, scoped to a session and/or a caller."""
        return self.dialogue.tail(n, session=session, who=who)

    def history(self, n: int = 20, session: str = None, who: str = None) -> List[Dict]:
        """The turns this caller may read back — the same scoping the prompt
        gets, so what a client can list is exactly what the agent recalls."""
        return self._mine(session, who)[-max(n, 1):]

    def _mine(self, session: str = None, who: str = None) -> List[Dict]:
        """The turns this caller is allowed to be reminded of.

        A signed-in caller owns every turn recorded under their address, across
        conversations and devices. An anonymous one only gets the session they
        are sitting in — two strangers on one host must never read each other's
        chats back out of the prompt.
        """
        pool = self.dialogue.all(400)
        if who:
            return [e for e in pool if e.get('who') == who]
        if session:
            return [e for e in pool if e.get('session') == session and not e.get('who')]
        return []

    @staticmethod
    def _turn_text(e: Dict) -> str:
        """What a past turn is searched on — both halves of the exchange."""
        return f"{e.get('query', '')} {e.get('answer', '')}"

    def recall_exchanges(self, query: str, k: int = 3, session: str = None,
                         who: str = None, exclude: int = 0) -> List[Dict]:
        """Past turns ranked against the query, best first.

        `exclude` drops the n most recent turns, which the caller injects
        verbatim anyway — recall is for what fell out of the recent window.
        """
        mine = self._mine(session, who)
        if exclude:
            mine = mine[:-exclude] or []
        return [{**e, 'score': round(s, 3)}
                for s, e in rank(query, mine, self._turn_text,
                                 ts_of=lambda e: e.get('ts'), k=k)]

    # ── episodic recall ──────────────────────────────────────────────

    @staticmethod
    def _episode_text(e: Dict) -> str:
        """What a step is searched on: the tool, its params, what came back."""
        return (f"{e.get('tool', '')} "
                f"{json.dumps(e.get('params') or {}, default=str)} "
                f"{str(e.get('result') or '')[:400]} {e.get('error') or ''}")

    def recall_episodes(self, query: str, k: int = 3, session: str = None,
                        pool: int = 200) -> List[Dict]:
        """Steps from the trail that match the query, best first.

        Retrieval over the trail is what turns a log into memory: the agent
        can ask what it already tried instead of trying it again.
        """
        events = self.episodic.all(pool)
        if session:
            events = [e for e in events if e.get('session') == session]
        return [{**e, 'score': round(s, 3)}
                for s, e in rank(query, events, self._episode_text,
                                 ts_of=lambda e: e.get('ts'), k=k)]

    # ── unified retrieval (every layer, one ranking) ─────────────────

    # A hit that covers almost none of the question is noise, and noise in a
    # prompt is worse than silence: one shared word between a query and a file
    # path is not a memory of anything. Callers who want the raw ranking
    # (a debugging view, an eval) pass min_score=0.
    MIN_SCORE = 0.12

    def retrieve(self, query: str, k: int = 5, layers=None,
                 session: str = None, who: str = None,
                 min_score: float = None) -> List[Dict]:
        """Everything this memory holds that bears on the query, ranked.

        One call, one shape, whichever layer the hit came from — a caller
        (the recall tool, the console's retrieval panel, compile()) asks
        memory a question instead of asking each store in turn.

        Every layer is ranked in ONE pass, best first, with `k` applied per
        layer so one chatty layer can't crowd the others out. Dialogue stays
        scoped: signed-in callers get their own turns, an anonymous one gets
        only the session they are sitting in.
        """
        wanted = [l for l in (layers or self.layers) if l in
                  ('semantic', 'dialogue', 'episodic', 'working')]
        min_score = self.MIN_SCORE if min_score is None else min_score
        # One ranking over every layer at once, not three rankings merged.
        # Scoring each layer against only its own documents makes the numbers
        # incomparable — a word the step trail has never seen counts for
        # nothing there and everything in the facts — so a junk episode that
        # shares one word with the question can outrank the fact that answers
        # it. Ranked together, the same word is worth the same everywhere.
        docs = self._candidates(wanted, session=session, who=who)
        ranked = rank(query, docs, text_of=lambda d: d['match'],
                      ts_of=lambda d: d.get('ts'), k=None, min_score=min_score)
        hits: List[Dict] = []
        per_layer: Dict[str, int] = {}
        for score, doc in ranked:
            # k is per layer, so one chatty layer can't crowd the others out
            if per_layer.get(doc['layer'], 0) >= k:
                continue
            per_layer[doc['layer']] = per_layer.get(doc['layer'], 0) + 1
            hits.append(hit(doc['layer'], doc, score, doc['text'],
                            id=doc.get('id'), name=doc.get('name')))
        return hits

    def _candidates(self, layers, session: str = None, who: str = None,
                    pool: int = 200) -> List[Dict]:
        """Everything retrieval may rank, in one flat list of typed documents.

        Each carries the layer it came from, the text it is matched on and the
        timestamp recency uses — the layers differ in how they are written,
        not in how they are searched.
        """
        docs: List[Dict] = []
        if 'semantic' in layers:
            for f in self.semantic.facts.values():
                docs.append({'layer': 'semantic', 'id': f.get('id'),
                             'name': f.get('name'), 'ts': f.get('updated'),
                             'text': f.get('content', ''),
                             'match': FactStore.text_of(f)})
        if 'dialogue' in layers:
            for e in self._mine(session, who):
                docs.append({'layer': 'dialogue', 'id': str(e.get('ts', '')),
                             'name': 'past turn', 'ts': e.get('ts'),
                             'text': f"they asked: {e.get('query', '')}\n"
                                     f"you answered: {e.get('answer', '')}"})
        if 'episodic' in layers:
            events = self.episodic.all(pool)
            if session:
                events = [e for e in events if e.get('session') == session]
            for e in events:
                docs.append({'layer': 'episodic', 'id': str(e.get('ts', '')),
                             'name': e.get('tool', 'step'), 'ts': e.get('ts'),
                             'text': self._episode_text(e)[:400]})
        if 'working' in layers:
            for key, val in self.memory.items():
                docs.append({'layer': 'working', 'id': key, 'name': key,
                             'ts': None, 'text': str(val)[:400]})
        # a doc is matched on `match` when it has one (a fact is findable by
        # its name and tags, which are not part of what it says)
        for d in docs:
            d.setdefault('match', d['text'])
        return docs

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

    def compile(self, query: str = None, k: int = 5, episodes: int = 0,
                session: str = None, who: str = None, turns: int = 3) -> str:
        """Render the layers into a prompt-ready context block.

        This is retrieval with a prompt shape: the same ranking retrieve()
        exposes, laid out as text the model reads. Working memory stays the
        caller's concern (the agent already serializes it); this adds what the
        dict alone can't — the conversation so far, older turns that match
        what was just asked, recalled facts and, optionally, the recent
        episode trail.
        """
        parts = []
        recent = self._mine(session, who)[-turns:] if turns else []
        if recent:
            lines = []
            for e in recent:
                lines.append(f"user: {str(e.get('query', ''))[:400]}")
                lines.append(f"you: {str(e.get('answer', ''))[:400]}")
            parts.append("CONVERSATION SO FAR (earlier turns with this same user):\n"
                         + '\n'.join(lines))
        related = self.recall_exchanges(query, k=3, session=session, who=who,
                                        exclude=len(recent)) if query else []
        if related:
            lines = [f"- they asked: {str(e.get('query', ''))[:200]}\n"
                     f"  you answered: {str(e.get('answer', ''))[:300]}" for e in related]
            parts.append("RELATED PAST TURNS (older, matched to this question):\n"
                         + '\n'.join(lines))
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

    @classmethod
    def describe(cls) -> dict:
        """The registry card for this memory module — what the console lists
        and what an agent config points at."""
        return {
            'name': cls.kind,
            'label': cls.label,
            'description': cls.description,
            'layers': list(cls.layers),
        }

    def status(self) -> dict:
        return {
            'module': 'agent.memory',
            'kind': self.kind,
            'layers': list(self.layers),
            'session': self.session,
            'working_keys': self.keys(),
            'episodes': self.episodic.count(),
            'exchanges': self.dialogue.count(),
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
                 retrieve, recall_steps, compile, serve, kill, health, test
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
            'exchange': lambda: self.exchange(kwargs.get('query', ''), kwargs.get('answer', ''),
                                              session=kwargs.get('session'),
                                              who=kwargs.get('who'), agent=kwargs.get('agent')),
            # the local view — filtered, not access-controlled. Anything a
            # caller over HTTP can reach goes through Mod.forward('exchanges'),
            # which scopes by verified address (see history()).
            'exchanges': lambda: self.exchanges(kwargs.get('n', 20), kwargs.get('session'),
                                                kwargs.get('who')),
            'remember': lambda: self.remember(kwargs.get('name', ''), kwargs.get('content', ''), kwargs.get('tags')),
            'forget': lambda: self.forget(kwargs.get('id', '')),
            'facts': lambda: self.facts(),
            'recall': lambda: self.recall(kwargs.get('query', kwargs.get('q', '')), kwargs.get('k', 5)),
            # retrieval across every layer at once — one ranking, one shape
            'retrieve': lambda: {'query': kwargs.get('query', kwargs.get('q', '')),
                                 'hits': self.retrieve(
                                     kwargs.get('query', kwargs.get('q', '')),
                                     k=int(kwargs.get('k', 5)),
                                     layers=kwargs.get('layers'),
                                     session=kwargs.get('session'),
                                     who=kwargs.get('who'),
                                     min_score=float(kwargs.get('min_score', 0.0)))},
            'recall_steps': lambda: self.recall_episodes(
                kwargs.get('query', kwargs.get('q', '')), kwargs.get('k', 3),
                kwargs.get('session')),
            'compile': lambda: self.compile(kwargs.get('query'), kwargs.get('k', 5),
                                            kwargs.get('episodes', 0),
                                            session=kwargs.get('session'),
                                            who=kwargs.get('who')),
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
        # dialogue layer: recorded, scoped to its caller, recalled by keyword
        scratch.exchange('what port does the relay use?', 'it listens on 8412',
                         session='s1', who='0xabc')
        scratch.exchange('someone else entirely', 'not yours', session='s2', who='0xdef')
        assert len(scratch.exchanges(10, who='0xabc')) == 1
        ctx = scratch.compile('remind me about the relay port', who='0xabc')
        assert '8412' in ctx and 'not yours' not in ctx
        # an anonymous visitor sees their own session and nothing signed
        assert scratch.compile('relay port', session='s1') == ''
        # retrieval: every layer answers one query in one shape, and the
        # dialogue scoping that holds for compile() holds here too
        scratch.remember('relay', 'the relay listens on port 8412', tags=['net'])
        hits = scratch.retrieve('relay port', who='0xabc')
        assert hits and hits[0]['score'] >= hits[-1]['score']
        assert {'layer', 'id', 'name', 'text', 'score', 'ts'} <= set(hits[0])
        assert {'semantic', 'dialogue'} & {h['layer'] for h in hits}
        assert not [h for h in scratch.retrieve('relay port', who='0xdef')
                    if h['layer'] == 'dialogue' and '8412' in h['text']]
        # the step trail is retrievable, not just tailable
        scratch.observe({'tool': 'bash', 'params': {'command': 'pytest -q'}})
        steps = scratch.recall_episodes('pytest')
        assert steps and steps[0]['tool'] == 'bash'
        self.clear()
        return True
