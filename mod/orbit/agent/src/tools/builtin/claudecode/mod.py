"""claudecode - run Claude Code CLI as an agent skill"""
import subprocess
import os
import json
from typing import Dict, Any, Optional


class Skill:
    description = "Run Claude Code CLI to perform coding tasks via claude-code"

    def forward(self, prompt: str, path: str = None, model: str = None,
                max_turns: int = 5, output_format: str = "text",
                allowedTools: str = None, timeout: int = 120,
                **kwargs) -> Dict[str, Any]:
        """Run a Claude Code CLI command.

        Args:
            prompt: the task/question to send to claude code
            path: working directory for the command
            model: model to use (e.g. 'sonnet', 'opus')
            max_turns: max agentic turns (default 5)
            output_format: 'text' or 'json'
            allowedTools: comma-separated list of allowed tools
            timeout: timeout in seconds (default 120)
        """
        if not prompt.strip():
            return {"success": False, "error": "empty prompt"}

        # check if claude CLI is available
        which = subprocess.run(["which", "claude"], capture_output=True, text=True)
        if which.returncode != 0:
            return {"success": False, "error": "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"}

        cmd = ["claude", "--print"]

        if model:
            cmd.extend(["--model", model])
        if max_turns:
            cmd.extend(["--max-turns", str(max_turns)])
        if output_format == "json":
            cmd.extend(["--output-format", "json"])
        if allowedTools:
            cmd.extend(["--allowedTools", allowedTools])

        cmd.extend(["--prompt", prompt])

        cwd = path or os.getcwd()

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "agent-skill"},
            )

            output = result.stdout
            if output_format == "json":
                try:
                    parsed = json.loads(output)
                    return {"success": result.returncode == 0, "output": parsed, "format": "json"}
                except json.JSONDecodeError:
                    pass

            return {
                "success": result.returncode == 0,
                "output": output,
                "stderr": result.stderr,
                "code": result.returncode,
                "format": "text",
            }

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"timeout after {timeout}s", "code": -1}
        except Exception as e:
            return {"success": False, "error": str(e), "code": -1}

    def test(self):
        """Test that claude CLI is accessible."""
        r = subprocess.run(["which", "claude"], capture_output=True, text=True)
        if r.returncode != 0:
            return {"success": True, "note": "claude CLI not installed, skip"}
        # just verify it can show version
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
