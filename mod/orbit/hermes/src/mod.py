"""
hermes — NousResearch Hermes weights as a local agent, and as a first-class
citizen of the fleet's two agent protocols.

Two audiences, one loop:

    the mod protocol   `m hermes run query=...` / m.mod('hermes')().chat(...)
    the AGENT contract HTTP: GET /agents, POST /run/stream (see api.py) — the
                       shape orbit/build probes for before it will mount a
                       module as an agent, and the shape orbit/agent's own
                       console speaks. Satisfying it is what lets a hermes run
                       be a build job or an orbit/agent run rather than
                       something you can only reach from a shell.

Everything runs on this box. Backends are resolved in backend.py and are all
local — the llama_cpp package in this process, or a llama-server / ollama on
localhost. There is deliberately no hosted fallback: a module that says
local-only and quietly answers from a cloud key is worse than one that says
it has no backend.

    import mod as m
    h = m.mod('hermes')()
    h.forward('chat', message='hello')
    h.forward('run', query='what does this module do?')
    h.forward('agents')
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import backend as _backend                                   # noqa: E402
import tools as _tools                                       # noqa: E402
from backend import BackendError                             # noqa: E402


def _fleet():
    """The fleet's `mod` package — found without this file shadowing it.

    This file is itself named mod.py. Python's PathFinder sits ahead of the
    editable-install finder that actually provides the fleet package, so any
    process with this directory on sys.path (`python3 api.py`, a test run from
    here) resolves `import mod` to THIS module and imports it into itself.
    Loaded through the mod protocol the name is already bound correctly, so
    check sys.modules first and only go looking when it is us in there.
    """
    already = sys.modules.get('mod')
    if already is not None and getattr(already, '__file__', None) != __file__:
        return already
    saved = sys.path[:]
    sys.path[:] = [p for p in saved if os.path.abspath(p or '.') != HERE]
    hidden = sys.modules.pop('mod', None)
    try:
        import mod as fleet
        return fleet
    except ImportError:
        if hidden is not None:
            sys.modules['mod'] = hidden
        return None
    finally:
        sys.path[:] = saved


m = _fleet()
if m is not None:
    print = m.print


# ── Hermes model registry ────────────────────────────────────────────

HERMES_MODELS = {
    'hermes-3-8b': {
        'url': 'https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B-GGUF/resolve/main/Hermes-3-Llama-3.1-8B.Q4_K_M.gguf',
        'name': 'Hermes-3-Llama-3.1-8B.Q4_K_M.gguf',
        'ctx': 8192,
        'description': 'Hermes 3 8B — good balance of speed and quality',
    },
    'hermes-3-8b-q8': {
        'url': 'https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B-GGUF/resolve/main/Hermes-3-Llama-3.1-8B.Q8_0.gguf',
        'name': 'Hermes-3-Llama-3.1-8B.Q8_0.gguf',
        'ctx': 8192,
        'description': 'Hermes 3 8B Q8 — higher quality, more memory',
    },
    'hermes-3-3b': {
        'url': 'https://huggingface.co/NousResearch/Hermes-3-Llama-3.2-3B-GGUF/resolve/main/Hermes-3-Llama-3.2-3B.Q4_K_M.gguf',
        'name': 'Hermes-3-Llama-3.2-3B.Q4_K_M.gguf',
        'ctx': 4096,
        'description': 'Hermes 3 3B — fast, lightweight',
    },
}

DEFAULT_MODEL = 'hermes-3-8b'

SYSTEM_PROMPT = """You are Hermes, a helpful AI assistant running locally. You are direct, accurate, and efficient.
You have access to tools for software engineering tasks. When given a task, break it down and execute step by step.
Always read before you write. Think before you act. Verify after you change."""


# ── agent personas ───────────────────────────────────────────────────
#
# The AGENT contract's GET /agents answers with these. They are personas over
# one loop, not separate programs: a prompt, an icon, and a model preference.
# Kept to three because a picker of near-identical personas is a worse answer
# than a short list of real ones.

AGENTS: Dict[str, dict] = {
    'hermes': {
        'name': 'Hermes',
        'description': 'General-purpose local agent — reads, edits, runs commands',
        'icon': '☿',
        'goal': SYSTEM_PROMPT,
        'model': None,
    },
    'hermes-coder': {
        'name': 'Hermes Coder',
        'description': 'Writes and changes code, minimal diffs, reads before writing',
        'icon': '⌘',
        'goal': (
            "You are Hermes, working on a codebase on this machine. Read the "
            "surrounding code before you change it and match its idiom. Prefer "
            "the smallest edit that does the job: `edit` over `write` on a file "
            "that already exists. Verify by running something — a test, the "
            "file itself — before you finish."),
        'model': None,
    },
    'hermes-explain': {
        'name': 'Hermes Explain',
        'description': 'Reads and explains — never writes',
        'icon': '☾',
        # sandbox is a property of the AGENT here, not only of the caller: this
        # one answers questions about a tree, so the write tools are not a
        # capability it should have to be trusted not to use
        'goal': (
            "You are Hermes. Answer questions about the code in front of you by "
            "reading it. You have no write tools — do not plan edits, describe "
            "what is there and what it means."),
        'model': None,
        'sandbox': True,
    },
}

DEFAULT_AGENT = 'hermes'


class Hermes:
    """Local-only agent backed by Hermes models.

    All inference runs on-device (llama_cpp in this process, or a llama.cpp /
    ollama server on localhost). No API keys, no cloud calls.
    """

    description = "Local Hermes agent — on-device inference, no cloud APIs"

    anchors = {
        'plan': ['<PLAN>', '</PLAN>'],
        'tool': ['<STEP>', '</STEP>'],
    }

    output_format = """Respond with exactly ONE step inside anchors.
<PLAN>
<STEP>{"tool": "<tool_name>", "params": {...}}</STEP>
</PLAN>
When finished:
<PLAN>
<STEP>{"tool": "finish", "params": {"summary": "what you accomplished"}}</STEP>
</PLAN>"""

    def __init__(self, model: str = None, n_ctx: int = None, system: str = None,
                 backend: str = None, **kwargs):
        self._model_key = model or os.environ.get('HERMES_MODEL') or DEFAULT_MODEL
        self._n_ctx = n_ctx
        self._system = system or SYSTEM_PROMPT
        self._history: List[Dict] = []
        self._backend_pref = backend
        self._backend: Optional[_backend.Backend] = None

    # ── backend ──────────────────────────────────────────────────────

    def backend(self, refresh: bool = False) -> _backend.Backend:
        """The live backend. Re-resolved on request, because a box gains one
        (somebody starts ollama) far more often than it loses one."""
        if refresh or self._backend is None or not self._backend.ready():
            self._backend = _backend.resolve(self._backend_pref)
        return self._backend

    def ready(self) -> bool:
        return self.backend().ready()

    def _model_id(self, model: str = None) -> Optional[str]:
        """The id to hand the backend.

        A registry key ('hermes-3-8b') is this module's own shorthand and means
        the GGUF filename underneath; anything else goes through untouched,
        since a server's model list is its own business.
        """
        key = model or self._model_key
        if not key:
            return None
        info = HERMES_MODELS.get(key)
        if info and self.backend().kind == 'llama-cpp':
            return info['name']
        if info and self.backend().kind == 'server':
            # a server was started with one model, or names its own — the
            # registry key is meaningless to it, so let it choose
            served = self.backend().models()
            hit = next((s for s in served if key.split('-')[-1] in s.lower()), None)
            return hit or (served[0] if served else None)
        return key

    # ── model management ─────────────────────────────────────────────

    def download(self, model: str = None, on_progress=None):
        """Download a Hermes GGUF into the off-tree model directory.

        Args:
            model: registry key (hermes-3-8b, hermes-3-3b, hermes-3-8b-q8)
                   or a direct URL to a .gguf
        """
        key = model or self._model_key
        info = HERMES_MODELS.get(key)
        if info:
            return {'path': _backend.download(info['url'], info['name'], on_progress),
                    'model': key}
        if not str(key).startswith('http'):
            raise ValueError(
                f'{key!r} is not a known model — one of {list(HERMES_MODELS)}, '
                f'or a direct URL to a .gguf file')
        return {'path': _backend.download(key, on_progress=on_progress), 'model': key}

    def load(self, model: str = None, n_ctx: int = None):
        """Select a model. Weights load lazily on the first completion."""
        if model:
            self._model_key = model
        if n_ctx:
            self._n_ctx = n_ctx
        b = self.backend(refresh=True)
        return {'model': self._model_key, 'backend': b.kind, 'ready': b.ready()}

    def models(self):
        """The Hermes registry plus whatever the live backend can already run."""
        b = self.backend()
        local = set(b.models())
        rows = []
        for key, info in HERMES_MODELS.items():
            rows.append({'key': key, 'name': info['name'], 'ctx': info['ctx'],
                         'description': info['description'],
                         'downloaded': info['name'] in local})
        # a model the backend serves that this registry never heard of is
        # still runnable, and hiding it would make the picker a lie
        for extra in sorted(local - {r['name'] for r in rows}):
            rows.append({'key': extra, 'name': extra, 'ctx': None,
                         'description': f'served by the {b.kind} backend',
                         'downloaded': True})
        return rows

    def model_ids(self) -> List[str]:
        """Flat id list — what a model picker in another module renders."""
        return [r['key'] for r in self.models()]

    # ── agents ───────────────────────────────────────────────────────

    def agents(self) -> dict:
        """GET /agents, as a dict. THE compatibility contract.

        orbit/build probes for exactly this shape before it will mount a module
        as an agent backend, and orbit/agent's console reads the same one.
        """
        return {
            'agents': [{'id': key, **{k: v for k, v in cfg.items() if k != 'goal'},
                        'provider': 'hermes', 'local': True}
                       for key, cfg in AGENTS.items()],
            'default': DEFAULT_AGENT,
            'backend': self.backend().info(),
        }

    def agent(self, name: str = None) -> dict:
        key = name or DEFAULT_AGENT
        if key not in AGENTS:
            raise KeyError(f'no agent {key!r} — have {list(AGENTS)}')
        return {'id': key, **AGENTS[key]}

    # ── chat interface ───────────────────────────────────────────────

    def chat(self, message: str = "hello", system: str = None,
             max_tokens: int = 1024, temperature: float = 0.7,
             model: str = None, clear: bool = False, on_token=None):
        """Chat with Hermes. Maintains conversation history."""
        if clear:
            self._history = []
        messages = [{'role': 'system', 'content': system or self._system}]
        messages += self._history
        messages.append({'role': 'user', 'content': message})
        response = self.backend().chat(
            messages, model=self._model_id(model), max_tokens=max_tokens,
            temperature=temperature, on_token=on_token)
        self._history.append({'role': 'user', 'content': message})
        self._history.append({'role': 'assistant', 'content': response})
        return response

    def complete(self, prompt: str = "", max_tokens: int = 512,
                 temperature: float = 0.7, model: str = None):
        """One-shot completion, no history and no system prompt."""
        return self.backend().chat(
            [{'role': 'user', 'content': prompt}], model=self._model_id(model),
            max_tokens=max_tokens, temperature=temperature)

    def clear(self):
        """Clear conversation history."""
        self._history = []
        return {'cleared': True, 'history_length': 0}

    # ── agent loop ───────────────────────────────────────────────────

    def run(self, query: str = "help me", path: str = None,
            steps: int = 15, max_tokens: int = 2048,
            temperature: float = 0.1, agent: str = None, model: str = None,
            prompt: str = None, sandbox: bool = False,
            on_event: Callable[[dict], None] = None, **kwargs) -> List[Dict]:
        """Run the agent loop, optionally streaming events as it goes.

        `on_event` receives the same event objects POST /run/stream emits, so
        the HTTP surface and a direct Python call see the identical run.

        Args:
            query: the task
            path: working directory (default: cwd)
            steps: max iterations
            agent: persona id from AGENTS
            prompt: extra system-prompt text appended to the persona's goal
            sandbox: read-only tools only
        """
        emit = on_event or (lambda ev: None)
        cfg = self.agent(agent) if agent else self.agent(DEFAULT_AGENT)
        sandbox = bool(sandbox or cfg.get('sandbox'))
        path = path or os.getcwd()
        model_id = self._model_id(model or cfg.get('model'))

        system = self._system_for(cfg, path, sandbox, prompt)
        history: List[list] = []
        chat_history: List[dict] = []

        for step_i in range(steps):
            message = query if step_i == 0 else self._step_context(history[-1])
            emit({'type': 'model_start', 'step': step_i, 'model': model_id})
            messages = ([{'role': 'system', 'content': system}] + chat_history
                        + [{'role': 'user', 'content': message}])
            try:
                response = self.backend().chat(
                    messages, model=model_id, max_tokens=max_tokens,
                    temperature=temperature,
                    on_token=lambda t: emit({'type': 'token', 'text': t}))
            except BackendError as e:
                emit({'type': 'error', 'error': str(e)})
                raise
            chat_history.append({'role': 'user', 'content': message})
            chat_history.append({'role': 'assistant', 'content': response})

            plan = self._parse_and_execute(response, sandbox=sandbox, emit=emit)
            history.append(plan)

            if plan and plan[-1].get('tool', '').lower() in ('finish', 'response'):
                print(f'Hermes finished in {step_i + 1} steps')
                break

        return history[-1] if history else []

    def run_stream(self, query: str = "help me", **kwargs):
        """The same run as a generator of events — what the SSE route drains.

        The loop is synchronous, so events are queued by the worker thread and
        yielded here; `done` and `error` are terminal either way, so a consumer
        that reads to exhaustion always sees exactly one of them.
        """
        import queue
        import threading

        events: "queue.Queue" = queue.Queue()
        task_id = uuid.uuid4().hex[:12]
        started = time.time()

        def worker():
            try:
                result = self.run(query=query, on_event=events.put, **kwargs)
                # No `charged` key at all, deliberately. A metering console
                # reads that field to draw a cost line, and `charged: null`
                # still draws one — "charged $0.00 for 0 steps", which reads
                # like a broken meter rather than a free run. `free` says the
                # true thing: this ran on weights nobody was billed for.
                events.put({'type': 'done', 'result': result, 'task_id': task_id,
                            'free': True,
                            'duration': round(time.time() - started, 2)})
            except Exception as e:
                events.put({'type': 'error', 'error': str(e), 'task_id': task_id})
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            ev = events.get()
            if ev is None:
                return
            yield ev

    # ── prompt + parsing ─────────────────────────────────────────────

    def _system_for(self, cfg: dict, path: str, sandbox: bool,
                    extra: str = None) -> str:
        parts = [cfg.get('goal') or self._system]
        if extra:
            parts.append(extra)
        parts.append(f'AVAILABLE TOOLS:\n{_tools.describe(sandbox)}')
        parts.append(f'OUTPUT FORMAT:\n{self.output_format}')
        parts.append(f'WORKING DIRECTORY: {path}')
        if sandbox:
            parts.append('This run is read-only. You cannot write, edit or run commands.')
        return '\n\n'.join(parts)

    def _step_context(self, last_plan: list) -> str:
        """Context message built from the last step's results."""
        if not last_plan:
            return "Continue."
        parts = []
        for step in last_plan:
            tool = step.get('tool', '?')
            if 'result' in step:
                result = step['result']
                if isinstance(result, (dict, list)):
                    result = json.dumps(result, indent=2, default=str)
                parts.append(f"[{tool}] Result:\n{str(result)[:2000]}")
            elif 'error' in step:
                parts.append(f"[{tool}] Error: {step['error']}")
        return '\n'.join(parts) + '\n\nContinue with the next step.'

    def _parse_and_execute(self, output: str, sandbox: bool = False,
                           emit=None) -> list:
        """Parse the model's output for tool calls and execute them."""
        emit = emit or (lambda ev: None)
        steps = self._parse_steps(output)
        if not steps:
            # no anchors: the model answered in prose. That IS the run's
            # answer, so it ends the loop rather than burning the budget
            # asking a small model to try the format again.
            step = {'tool': 'response', 'params': {}, 'result': output.strip()}
            emit({'type': 'step', 'step': step})
            return [step]
        return self._run_steps(steps, sandbox=sandbox, emit=emit)

    def _parse_steps(self, text: str) -> list:
        """Extract step JSONs from between STEP anchors."""
        plans = []
        tag_open, tag_close = self.anchors['tool']
        remaining = text
        while tag_open in remaining and tag_close in remaining:
            try:
                raw = remaining.split(tag_open, 1)[1].split(tag_close, 1)[0]
                remaining = remaining.split(tag_close, 1)[1]
                step = json.loads(raw.strip())
                if 'tool' in step:
                    step.setdefault('params', {})
                    plans.append(step)
            except (json.JSONDecodeError, IndexError):
                break
        return plans

    def _run_steps(self, steps: list, sandbox: bool = False, emit=None) -> list:
        """Execute parsed tool-call steps."""
        emit = emit or (lambda ev: None)
        n = len(steps)
        for i, step in enumerate(steps):
            name = str(step.get('tool', '')).lower()
            params = step.get('params') or {}

            if name in ('finish', 'response'):
                print(f"[{i+1}/{n}] {name}")
                emit({'type': 'step', 'step': step})
                return steps[:i + 1]

            emit({'type': 'tool_start', 'tool': name, 'params': params,
                  'i': i, 'n': n})
            try:
                if name in _tools.REGISTRY:
                    result = _tools.run(name, sandbox=sandbox, **params)
                elif m and not sandbox:
                    # the fleet, for a trusted run: any mod-protocol module is
                    # callable by name. A sandboxed run never gets this.
                    result = m.tool(name)(**params)
                else:
                    raise KeyError(f'unknown tool: {name}')
                step['result'] = result
                print(f"[{i+1}/{n}] {name} -> done")
            except Exception as e:
                step['error'] = str(e)
                print(f"[{i+1}/{n}] {name} -> error: {e}")
            emit({'type': 'step', 'step': step})
        return steps

    # ── info / status ────────────────────────────────────────────────

    def info(self):
        """Module info — including whether a run could actually start."""
        b = self.backend()
        return {
            'name': 'hermes',
            'description': self.description,
            'model': self._model_key,
            'backend': b.info(),
            'ready': b.ready(),
            'agents': list(AGENTS),
            'tools': _tools.names(),
            'history_length': len(self._history),
            'model_dir': str(_backend.MODEL_DIR),
        }

    def health(self):
        b = self.backend(refresh=True)
        ok = b.ready()
        return {'ok': ok, 'backend': b.kind, 'detail': b.detail,
                'models': b.models()[:20],
                **({} if ok else {'why': b.why()})}

    def bench(self, model: str = None, tokens: int = 64, **kwargs):
        """Time a short generation — tokens/sec on this box, honestly measured."""
        started = time.time()
        text = self.complete(prompt='Count from one to twenty.',
                             max_tokens=tokens, model=model)
        elapsed = time.time() - started
        # characters, not tokens: this module does not own the tokenizer the
        # backend used, and a made-up token count is worse than none
        return {'seconds': round(elapsed, 2), 'chars': len(text),
                'chars_per_sec': round(len(text) / elapsed, 1) if elapsed else None,
                'backend': self.backend().kind, 'model': self._model_id(model)}


class Mod(Hermes):
    """Mod protocol wrapper for the Hermes agent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.src_dir = Path(__file__).parent
        self.module_dir = self.src_dir.parent

    def serve(self, port: int = None, host: str = '0.0.0.0'):
        """Start the HTTP surface — the AGENT contract, on this module's port."""
        import api
        return api.serve(port=port, host=host)

    def forward(self, action=None, **kwargs):
        """CLI entry point: hermes <action> [args]

        Actions:
            chat        - Chat with Hermes (message=)
            complete    - Raw text completion (prompt=)
            run         - Run agent loop (query=, path=, steps=, agent=)
            agents      - The agent roster (the AGENT contract's GET /agents)
            agent       - One persona (name=)
            download    - Download a Hermes model (model=)
            load        - Select a model (model=, n_ctx=)
            models      - List models, registry + whatever the backend serves
            clear       - Clear chat history
            bench       - Time a generation on this box
            health      - Is a backend actually reachable?
            serve       - Run the HTTP API (port=)
            info        - Module info
        """
        actions = {
            'chat': lambda: self.chat(
                message=kwargs.get('message', kwargs.get('query', 'hello')),
                system=kwargs.get('system'),
                max_tokens=kwargs.get('max_tokens', 1024),
                temperature=kwargs.get('temperature', 0.7),
                model=kwargs.get('model'),
                clear=kwargs.get('clear', False),
            ),
            'complete': lambda: self.complete(
                prompt=kwargs.get('prompt', ''),
                max_tokens=kwargs.get('max_tokens', 512),
                temperature=kwargs.get('temperature', 0.7),
                model=kwargs.get('model'),
            ),
            'run': lambda: self.run(
                query=kwargs.get('query', 'help me'),
                path=kwargs.get('path'),
                steps=kwargs.get('steps', 15),
                max_tokens=kwargs.get('max_tokens', 2048),
                temperature=kwargs.get('temperature', 0.1),
                agent=kwargs.get('agent', kwargs.get('agent_type')),
                model=kwargs.get('model'),
                prompt=kwargs.get('prompt'),
                sandbox=kwargs.get('sandbox', False),
            ),
            'agents': lambda: self.agents(),
            'agent': lambda: self.agent(kwargs.get('name')),
            'download': lambda: self.download(model=kwargs.get('model')),
            'load': lambda: self.load(model=kwargs.get('model'),
                                      n_ctx=kwargs.get('n_ctx')),
            'models': lambda: self.models(),
            'clear': lambda: self.clear(),
            'bench': lambda: self.bench(model=kwargs.get('model'),
                                        tokens=kwargs.get('tokens', 64)),
            'health': lambda: self.health(),
            'serve': lambda: self.serve(port=kwargs.get('port')),
            'info': lambda: self.info(),
        }

        if not action or action not in actions:
            return self.info()

        return actions[action]()
