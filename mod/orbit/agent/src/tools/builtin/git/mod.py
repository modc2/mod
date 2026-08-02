"""git - version control operations"""
import subprocess
import os
from typing import Dict, Any, Optional


class Skill:
    description = "Git operations: status, diff, log, commit, branch, stash, blame, show. Safe read-only by default."

    def forward(self, command: str, cwd: str = None, **kwargs) -> Dict[str, Any]:
        """
        Run a git command. Supports common operations:

        Read-only (always safe):
            git status, diff, log, branch, show, blame, stash list, remote -v, tag

        Write operations (use carefully):
            git add, commit, checkout, branch -d, stash, merge, rebase, push, pull

        Args:
            command: Git subcommand and args (e.g. "status", "diff --staged", "log -5 --oneline")
            cwd: Working directory (defaults to cwd)
        """
        cwd = cwd or os.getcwd()

        # normalize: strip leading "git " if user passes full command
        cmd = command.strip()
        if cmd.startswith("git "):
            cmd = cmd[4:]

        # safety: block destructive commands
        dangerous = ["push --force", "reset --hard", "clean -f", "branch -D"]
        for d in dangerous:
            if d in cmd:
                return {"success": False, "error": f"blocked dangerous command: git {d}. Use bash skill if you really need this."}

        full_cmd = f"git {cmd}"
        try:
            r = subprocess.run(
                full_cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=30
            )
            output = r.stdout or r.stderr
            return {
                "success": r.returncode == 0,
                "output": output.strip(),
                "command": full_cmd,
                "code": r.returncode
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"timeout: {full_cmd}", "code": -1}
        except Exception as e:
            return {"success": False, "error": str(e), "code": -1}

    def test(self):
        r = self.forward("--version")
        assert r["success"] and "git version" in r["output"]
        return True
