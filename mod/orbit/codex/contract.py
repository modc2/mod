"""
codex as an AgentContract — declarative subclass alongside the existing
src/mod.py orchestrator. Same shape as claude/contract.py and cursor/src/mod.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dev", "src"))

from agent_base import AgentContract  # noqa: E402


class Mod(AgentContract):
    NAME = "codex"
    ICON = "X"
    COLOR = "#10a37f"
    BINARY = "codex"
    DEFAULT_MODEL = "gpt-4o-mini"
    ENV_KEY = "OPENAI_API_KEY"
    DESCRIPTION = "OpenAI codex CLI agent"

    def build_args(self, prompt, model, work_dir):
        return [
            "exec",
            "--model", model or self.DEFAULT_MODEL,
            "--quiet",
            prompt,
        ]
