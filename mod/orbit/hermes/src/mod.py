"""
hermes - local-only agent powered by NousResearch Hermes models via llama-cpp

Usage:
    import mod as m
    hermes = m.mod('hermes')()
    hermes.forward('run', query='explain this code')
    hermes.forward('chat', message='hello')
    hermes.forward('models')
    hermes.forward('download')
"""
import os
import json
from typing import Optional, List, Dict, Any
from pathlib import Path

try:
    import mod as m
    print = m.print
except ImportError:
    m = None


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


class Hermes:
    """Local-only agent backed by Hermes models via llama-cpp.

    All inference runs on-device (Metal on Apple Silicon, CPU otherwise).
    No API keys, no cloud calls, fully offline capable.
    """

    description = "Local Hermes agent — on-device inference via llama-cpp"

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

    def __init__(self, model: str = None, n_ctx: int = None, system: str = None, **kwargs):
        self._llama_mod = m.mod('llama-cpp')() if m else None
        self._model_key = model or DEFAULT_MODEL
        self._n_ctx = n_ctx
        self._system = system or SYSTEM_PROMPT
        self._history: List[Dict] = []
        self._loaded = False

        # resolve skills from agent module if available
        self._skills = None
        try:
            if m:
                from mod.orbit.agent.src.skills.mod import Skills
                self._skills = Skills()
        except Exception:
            pass

    # ── model management ─────────────────────────────────────────────

    def _ensure_loaded(self):
        """Load the Hermes model if not already loaded."""
        if self._loaded and self._llama_mod and self._llama_mod._llama:
            return
        model_info = HERMES_MODELS.get(self._model_key)
        if model_info:
            path = self._llama_mod.download(url=model_info['url'], name=model_info['name'])
            ctx = self._n_ctx or model_info['ctx']
        else:
            # treat as direct filename or path
            path = self._model_key
            ctx = self._n_ctx or 4096
        self._llama_mod.load(model=path, n_ctx=ctx, n_gpu_layers=-1)
        self._loaded = True

    def download(self, model: str = None):
        """Download a Hermes model.

        Args:
            model: Key from registry (hermes-3-8b, hermes-3-3b, hermes-3-8b-q8)
                   or a direct HuggingFace URL to a GGUF file.
        """
        key = model or self._model_key
        info = HERMES_MODELS.get(key)
        if info:
            return self._llama_mod.download(url=info['url'], name=info['name'])
        # direct URL
        return self._llama_mod.download(url=key)

    def load(self, model: str = None, n_ctx: int = None):
        """Load a specific model. Reloads if different from current."""
        if model:
            self._model_key = model
        if n_ctx:
            self._n_ctx = n_ctx
        self._loaded = False
        self._ensure_loaded()
        return {'model': self._model_key, 'loaded': True}

    def models(self):
        """List available Hermes models and their download status."""
        downloaded = set(self._llama_mod.models()) if self._llama_mod else set()
        result = []
        for key, info in HERMES_MODELS.items():
            result.append({
                'key': key,
                'name': info['name'],
                'ctx': info['ctx'],
                'description': info['description'],
                'downloaded': info['name'] in downloaded,
            })
        return result

    # ── chat interface ───────────────────────────────────────────────

    def chat(self, message: str = "hello", system: str = None,
             max_tokens: int = 1024, temperature: float = 0.7,
             clear: bool = False):
        """Chat with Hermes. Maintains conversation history.

        Args:
            message: User message
            system: Override system prompt
            max_tokens: Max tokens to generate
            temperature: Sampling temperature
            clear: Clear history before this message
        """
        self._ensure_loaded()
        if clear:
            self._history = []
        system = system or self._system
        response = self._llama_mod.chat(
            message=message,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            history=self._history,
        )
        self._history.append({'role': 'user', 'content': message})
        self._history.append({'role': 'assistant', 'content': response})
        return response

    def complete(self, prompt: str = "", max_tokens: int = 512, temperature: float = 0.7):
        """Raw text completion (no chat template)."""
        self._ensure_loaded()
        return self._llama_mod.forward(prompt=prompt, max_tokens=max_tokens, temperature=temperature)

    def clear(self):
        """Clear conversation history."""
        self._history = []
        return {'cleared': True, 'history_length': 0}

    # ── agent loop (local) ───────────────────────────────────────────

    def run(self, query: str = "help me", path: str = None,
            steps: int = 15, max_tokens: int = 2048,
            temperature: float = 0.1, **kwargs) -> List[Dict]:
        """Run an agent loop using local Hermes model.

        Args:
            query: Task description
            path: Working directory (default: cwd)
            steps: Max agent iterations
            max_tokens: Max tokens per LLM call
            temperature: Sampling temperature (low for deterministic tool use)
        """
        self._ensure_loaded()
        path = path or os.getcwd()

        tools_desc = self._tools_description()
        system = f"""{self._system}

AVAILABLE TOOLS:
{tools_desc}

OUTPUT FORMAT:
{self.output_format}

WORKING DIRECTORY: {path}
"""
        history = []
        chat_history = []

        for step_i in range(steps):
            prompt = query if step_i == 0 else self._step_context(history[-1])
            chat_history.append({'role': 'user', 'content': prompt})

            response = self._llama_mod.chat(
                message=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                history=chat_history[:-1],  # exclude current (passed as message)
            )
            chat_history.append({'role': 'assistant', 'content': response})

            plan = self._parse_and_execute(response)
            history.append(plan)

            if plan and plan[-1].get('tool', '').lower() in ('finish', 'response'):
                print(f'Hermes finished in {step_i + 1} steps')
                break

        return history[-1] if history else []

    def _tools_description(self) -> str:
        """Build a text description of available tools for the system prompt."""
        if self._skills:
            schema = self._skills.schema()
            lines = []
            for name, info in schema.items():
                desc = info.get('description', '')
                params = info.get('params', {})
                param_str = ', '.join(f'{k}: {v}' for k, v in params.items()) if params else ''
                lines.append(f"- {name}({param_str}): {desc}")
            return '\n'.join(lines)
        # fallback minimal set
        return """- bash(command): Run a shell command
- read(file_path): Read a file
- write(file_path, content): Write a file
- edit(file_path, old_string, new_string): Edit a file
- think(thought): Reason through a problem
- finish(summary): Signal task completion"""

    def _step_context(self, last_plan: list) -> str:
        """Build context message from the last step's results."""
        if not last_plan:
            return "Continue."
        parts = []
        for step in last_plan:
            tool = step.get('tool', '?')
            if 'result' in step:
                result = step['result']
                if isinstance(result, dict):
                    result = json.dumps(result, indent=2, default=str)
                parts.append(f"[{tool}] Result:\n{str(result)[:2000]}")
            elif 'error' in step:
                parts.append(f"[{tool}] Error: {step['error']}")
        return '\n'.join(parts) + '\n\nContinue with the next step.'

    def _parse_and_execute(self, output: str) -> list:
        """Parse LLM output for tool calls and execute them."""
        steps = self._parse_steps(output)
        if not steps:
            return [{'tool': 'response', 'params': {}, 'result': output.strip()}]
        return self._run_steps(steps)

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
                if 'tool' in step and 'params' in step:
                    plans.append(step)
            except (json.JSONDecodeError, IndexError):
                break
        return plans

    def _run_steps(self, steps: list) -> list:
        """Execute parsed tool-call steps."""
        for i, step in enumerate(steps):
            name = step['tool'].lower()
            params = step.get('params', {})

            if name in ('finish', 'response'):
                print(f"[{i+1}/{len(steps)}] {name}")
                break

            try:
                if self._skills and name in self._skills.ls():
                    result = self._skills.run(name, **params)
                elif m:
                    result = m.tool(name)(**params)
                else:
                    result = {'error': f'unknown tool: {name}'}
                steps[i]['result'] = result
                print(f"[{i+1}/{len(steps)}] {name} -> done")
            except Exception as e:
                steps[i]['error'] = str(e)
                print(f"[{i+1}/{len(steps)}] {name} -> error: {e}")
        return steps

    # ── info / status ────────────────────────────────────────────────

    def info(self):
        """Module info."""
        return {
            'name': 'hermes',
            'description': self.description,
            'model': self._model_key,
            'loaded': self._loaded,
            'backend': self._llama_mod._detect_backend() if self._llama_mod else 'unknown',
            'history_length': len(self._history),
            'skills': self._skills.ls() if self._skills else [],
        }

    def bench(self, model: str = None, **kwargs):
        """Benchmark the loaded model."""
        self._ensure_loaded()
        return self._llama_mod.bench(**kwargs)


class Mod(Hermes):
    """Mod protocol wrapper for Hermes agent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.src_dir = Path(__file__).parent
        self.module_dir = self.src_dir.parent

    def forward(self, action=None, **kwargs):
        """CLI entry point: hermes <action> [args]

        Actions:
            chat        - Chat with Hermes (message=)
            complete    - Raw text completion (prompt=)
            run         - Run agent loop (query=, path=, steps=)
            download    - Download a Hermes model (model=)
            load        - Load a model (model=, n_ctx=)
            models      - List available models
            clear       - Clear chat history
            bench       - Benchmark inference speed
            info        - Module info
        """
        actions = {
            'chat': lambda: self.chat(
                message=kwargs.get('message', kwargs.get('query', 'hello')),
                system=kwargs.get('system'),
                max_tokens=kwargs.get('max_tokens', 1024),
                temperature=kwargs.get('temperature', 0.7),
                clear=kwargs.get('clear', False),
            ),
            'complete': lambda: self.complete(
                prompt=kwargs.get('prompt', ''),
                max_tokens=kwargs.get('max_tokens', 512),
                temperature=kwargs.get('temperature', 0.7),
            ),
            'run': lambda: self.run(
                query=kwargs.get('query', 'help me'),
                path=kwargs.get('path'),
                steps=kwargs.get('steps', 15),
                max_tokens=kwargs.get('max_tokens', 2048),
                temperature=kwargs.get('temperature', 0.1),
            ),
            'download': lambda: self.download(model=kwargs.get('model')),
            'load': lambda: self.load(model=kwargs.get('model'), n_ctx=kwargs.get('n_ctx')),
            'models': lambda: self.models(),
            'clear': lambda: self.clear(),
            'bench': lambda: self.bench(**{k: v for k, v in kwargs.items() if k != 'action'}),
            'info': lambda: self.info(),
        }

        if not action or action not in actions:
            return self.info()

        return actions[action]()
