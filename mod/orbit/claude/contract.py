"""
claude as an AgentContract — declarative subclass alongside the existing
2100-line orchestrator (src/mod.py). Use this when you want the
contract-shaped interface (ABI, code_hash, state, events).

    from claude.contract import Mod as ClaudeAgent
    a = ClaudeAgent()
    a.abi()         → list of ABI entries
    a.code_hash()   → sha3 of source
    a.submit(...)   → spawns claude CLI, emits job_submitted
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dev", "src"))

from agent_base import AgentContract  # noqa: E402


class Mod(AgentContract):
    NAME = "claude"
    ICON = "C"
    COLOR = "#cc785c"
    BINARY = "claude"
    DEFAULT_MODEL = "claude-sonnet-4-6"
    ENV_KEY = "ANTHROPIC_API_KEY"
    DESCRIPTION = "Claude Code CLI agent — Anthropic"
    DANGEROUS_FLAG = "--dangerously-skip-permissions"

    def build_args(self, prompt, model, work_dir):
        return [
            "--print",
            "--verbose",
            "--model", model or self.DEFAULT_MODEL,
            "--output-format", "stream-json",
            self.DANGEROUS_FLAG,
            prompt,
        ]
