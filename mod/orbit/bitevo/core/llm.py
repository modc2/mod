"""
LLM backend factory for bitevo.

Backends resolved through mod protocol (`m.mod(name)()`) except 'claude',
which shells out to the local Claude Code CLI — no API key required, works
wherever the box is already authenticated. All adapters expose the same
forward(prompt, system_prompt=, model=, temperature=, stream=) signature
the miner/validator expect.
"""

import subprocess

CLAUDE_DEFAULT_MODEL = 'haiku'
CLAUDE_TIMEOUT = 180


class ClaudeCLI:
    """LLM adapter over the local `claude -p` CLI (print mode, no tools)."""

    def __init__(self, model: str = None, **kwargs):
        self.model = model or CLAUDE_DEFAULT_MODEL

    def forward(self, prompt: str, system_prompt: str = None, model: str = None,
                temperature: float = None, stream: bool = False, **kwargs) -> str:
        # temperature/stream unsupported by the CLI — accepted for signature parity
        cmd = ['claude', '-p', '--model', model or self.model]
        if system_prompt:
            cmd += ['--system-prompt', system_prompt]
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI failed ({result.returncode}): {result.stderr.strip()[:300]}")
        return result.stdout.strip()


def get_llm(backend: str, model: str = None):
    if backend == 'claude':
        return ClaudeCLI(model=model)
    import mod as m
    return m.mod(backend)()
