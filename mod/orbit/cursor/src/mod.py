"""
cursor — agent contract powered by the cursor-agent CLI.

Mirror of claude/codex but routed to Cursor's CLI. The class IS the
contract: declared methods, persistent state at ~/.mod/cursor/state.json,
events at ~/.mod/cursor/events.jsonl, code_hash = sha3 of this source.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dev", "src"))

from agent_base import AgentContract, tx, owner_only  # noqa: E402


class Mod(AgentContract):
    NAME = "cursor"
    ICON = "▶"
    COLOR = "#7c3aed"
    BINARY = "cursor-agent"
    DEFAULT_MODEL = "claude-3.5-sonnet"
    ENV_KEY = "CURSOR_API_KEY"
    DESCRIPTION = "Cursor agent backend (cursor-agent CLI)"

    def build_args(self, prompt, model, work_dir):
        # cursor-agent flag set — adjust when the real CLI lands.
        return [
            "--print",
            "--model", model or self.DEFAULT_MODEL,
            "--workdir", work_dir,
            prompt,
        ]

    @tx
    @owner_only
    def install(self, key=None) -> dict:
        """Install cursor-agent globally via npm (owner-only)."""
        import subprocess
        result = subprocess.run(
            ["npm", "install", "-g", "@cursor/cursor-agent"],
            capture_output=True, text=True, timeout=120,
        )
        self.emit("install_attempted", returncode=result.returncode, stderr=result.stderr[:200])
        self._save_state()
        return {"ok": result.returncode == 0, "stdout": result.stdout[-500:], "stderr": result.stderr[-500:]}
