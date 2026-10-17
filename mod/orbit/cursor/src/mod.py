"""
cursor — agent contract powered by the cursor-agent CLI.

Mirror of claude/codex but routed to Cursor's CLI. The class IS the
contract: declared methods, persistent state at ~/.mod/cursor/state.json,
events at ~/.mod/cursor/events.jsonl, code_hash = sha3 of this source.

The job server behind submit()/jobs()/health() is src/server.py (stdlib
only, ports from config.json), run as the pm2 proc `cursor-api`.
"""
import importlib.util
import os
import sys


def _load_agent_base():
    """Load agent_base.py by file path — no sys.path insert, because a bare
    path insert makes `import mod` resolve to a sibling module's mod.py
    (the fleet's import-shadowing trap). A vendored copy next to this file
    wins; the shared base in dev/src is the canonical fallback."""
    if "agent_base" in sys.modules:
        return sys.modules["agent_base"]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "agent_base.py"),
        os.path.normpath(os.path.join(here, "..", "..", "dev", "src", "agent_base.py")),
    ]
    for path in candidates:
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("agent_base", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["agent_base"] = module
            spec.loader.exec_module(module)
            return module
    raise ImportError(f"agent_base.py not found; looked in {candidates}")


_ab = _load_agent_base()
AgentContract, view, tx, owner_only = _ab.AgentContract, _ab.view, _ab.tx, _ab.owner_only


class Mod(AgentContract):
    NAME = "cursor"
    ICON = "▶"
    COLOR = "#7c3aed"
    BINARY = "cursor-agent"
    DEFAULT_MODEL = ""  # empty → Cursor account default (e.g. gpt-5, sonnet-4-thinking)
    ENV_KEY = "CURSOR_API_KEY"
    DESCRIPTION = "Cursor agent backend (cursor-agent CLI)"

    def build_args(self, prompt, model, work_dir):
        # Verified against cursor-agent 2026.09.18: -p/--print is the
        # non-interactive mode, the workspace flag is --workspace (there is
        # no --workdir), and --model omitted falls back to the account default.
        args = ["-p", "--output-format", "text"]
        m = model or self.DEFAULT_MODEL
        if m:
            args += ["--model", m]
        if work_dir:
            args += ["--workspace", work_dir]
        args.append(prompt)
        return args

    @view
    def cli_path(self):
        # The official installer drops the binary in ~/.local/bin, which pm2
        # procs don't have on PATH.
        found = super().cli_path()
        if found:
            return found
        local = os.path.expanduser("~/.local/bin/cursor-agent")
        return local if os.path.isfile(local) else None

    @tx
    @owner_only
    def install(self, key=None) -> dict:
        """Install the official cursor-agent CLI (owner-only). Uses Cursor's
        own installer — the `cursor-agent` / `@cursor/cursor-agent` names on
        npm are unrelated third-party packages, not this CLI."""
        import subprocess
        result = subprocess.run(
            ["bash", "-c", "curl -fsS https://cursor.com/install | bash"],
            capture_output=True, text=True, timeout=300,
        )
        self.emit("install_attempted", returncode=result.returncode, stderr=result.stderr[:200])
        self._save_state()
        return {"ok": result.returncode == 0, "stdout": result.stdout[-500:], "stderr": result.stderr[-500:]}
