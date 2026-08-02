"""lint - run linters, formatters, and type checkers"""
import subprocess
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, List


class Skill:
    description = "Run linters, formatters, and type checkers. Auto-detects tools. Supports ruff, eslint, prettier, mypy, tsc, and more."

    TOOLS = {
        "ruff":     {"ext": ".py",  "check": "ruff check", "fix": "ruff check --fix", "format": "ruff format"},
        "pylint":   {"ext": ".py",  "check": "pylint", "fix": None},
        "mypy":     {"ext": ".py",  "check": "mypy", "fix": None},
        "eslint":   {"ext": ".js",  "check": "npx eslint", "fix": "npx eslint --fix"},
        "prettier": {"ext": ".js",  "check": "npx prettier --check", "fix": "npx prettier --write"},
        "tsc":      {"ext": ".ts",  "check": "npx tsc --noEmit", "fix": None},
        "rustfmt":  {"ext": ".rs",  "check": "rustfmt --check", "fix": "rustfmt"},
        "clippy":   {"ext": ".rs",  "check": "cargo clippy", "fix": "cargo clippy --fix --allow-dirty"},
        "gofmt":    {"ext": ".go",  "check": "gofmt -l", "fix": "gofmt -w"},
        "golint":   {"ext": ".go",  "check": "golangci-lint run", "fix": "golangci-lint run --fix"},
    }

    def forward(self, path: str = None, tool: str = None, file: str = None,
                fix: bool = False, format: bool = False,
                timeout: int = 60, **kwargs) -> Dict[str, Any]:
        """
        Run a linter or formatter.

        Args:
            path: Project root directory
            tool: Specific tool to run (ruff, eslint, prettier, mypy, tsc, etc.)
            file: Specific file to lint (otherwise lints whole project)
            fix: Auto-fix issues if tool supports it
            format: Run formatter instead of linter
            timeout: Max seconds
        """
        cwd = path or os.getcwd()
        cwd = str(Path(cwd).expanduser().resolve())

        if tool:
            if tool not in self.TOOLS:
                return {"success": False, "error": f"unknown tool: {tool}",
                        "available": list(self.TOOLS.keys())}
            tools_to_run = [tool]
        else:
            tools_to_run = self._detect(cwd)
            if not tools_to_run:
                return {"success": False, "error": "no linting tools detected",
                        "hint": "pass tool= to specify (ruff, eslint, prettier, mypy, tsc, clippy, etc.)"}

        results = []
        all_ok = True
        for t in tools_to_run:
            info = self.TOOLS[t]
            if format and info.get("format"):
                cmd = info["format"]
            elif fix and info.get("fix"):
                cmd = info["fix"]
            else:
                cmd = info["check"]

            if file:
                cmd = f"{cmd} {file}"
            elif t in ("ruff", "pylint", "mypy"):
                cmd = f"{cmd} ."

            try:
                r = subprocess.run(
                    cmd, shell=True, cwd=cwd,
                    capture_output=True, text=True, timeout=timeout
                )
                output = (r.stdout + "\n" + r.stderr).strip()
                ok = r.returncode == 0
                if not ok:
                    all_ok = False
                results.append({
                    "tool": t, "success": ok, "command": cmd,
                    "output": output[-5000:], "code": r.returncode
                })
            except subprocess.TimeoutExpired:
                results.append({"tool": t, "success": False, "error": f"timeout after {timeout}s"})
                all_ok = False
            except Exception as e:
                results.append({"tool": t, "success": False, "error": str(e)})
                all_ok = False

        return {"success": all_ok, "results": results, "path": cwd}

    def _detect(self, cwd: str) -> List[str]:
        """Auto-detect available linting tools"""
        found = []
        files = {f.name for f in Path(cwd).iterdir() if f.is_file()}

        # python
        if any(Path(cwd).glob("**/*.py")):
            for t in ["ruff", "pylint", "mypy"]:
                try:
                    subprocess.run(f"which {t}", shell=True, capture_output=True, timeout=5)
                    if t == "ruff":
                        found.append("ruff")
                        break  # ruff is enough for python
                except Exception:
                    pass

        # javascript/typescript
        pkg = Path(cwd, "package.json")
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "eslint" in deps:
                    found.append("eslint")
                if "prettier" in deps:
                    found.append("prettier")
                if "typescript" in deps:
                    found.append("tsc")
            except Exception:
                pass

        # rust
        if Path(cwd, "Cargo.toml").exists():
            found.append("clippy")

        # go
        if Path(cwd, "go.mod").exists():
            found.append("golint")

        return found

    def test(self):
        r = self.forward(tool="ruff", path="/tmp")
        return isinstance(r, dict) and "success" in r
