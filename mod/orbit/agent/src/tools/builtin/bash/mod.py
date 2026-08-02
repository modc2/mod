"""bash - run shell commands"""
import subprocess
import os
from typing import Dict, Any, Optional

class Skill:
    description = "Run shell commands and return output"

    def forward(self, command: str, cwd: str = None, timeout: int = 30, **kwargs) -> Dict[str, Any]:
        """Execute a bash command"""
        cwd = cwd or os.getcwd()
        try:
            r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            return {"success": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr, "code": r.returncode}
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": f"timeout after {timeout}s", "code": -1}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

    def test(self):
        r = self.forward("echo hello")
        assert r["success"] and "hello" in r["stdout"]
        return True
