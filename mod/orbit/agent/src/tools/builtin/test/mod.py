"""test - detect and run test suites"""
import subprocess
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional


class Skill:
    description = "Detect test framework and run tests. Supports pytest, jest, mocha, cargo test, go test, and more."

    # framework detection: file pattern -> (command, name)
    FRAMEWORKS = [
        ("pytest.ini", "pytest", "pytest"),
        ("pyproject.toml", "pytest", "pytest"),
        ("setup.cfg", "pytest", "pytest"),
        ("conftest.py", "pytest", "pytest"),
        ("jest.config.js", "jest", "npx jest"),
        ("jest.config.ts", "jest", "npx jest"),
        ("jest.config.mjs", "jest", "npx jest"),
        ("vitest.config.ts", "vitest", "npx vitest run"),
        ("vitest.config.js", "vitest", "npx vitest run"),
        ("Cargo.toml", "cargo", "cargo test"),
        ("go.mod", "go", "go test ./..."),
        ("mix.exs", "elixir", "mix test"),
        ("Gemfile", "ruby", "bundle exec rspec"),
        (".mocharc.yml", "mocha", "npx mocha"),
        ("phpunit.xml", "phpunit", "vendor/bin/phpunit"),
    ]

    def forward(self, path: str = None, command: str = None, file: str = None,
                pattern: str = None, verbose: bool = True,
                timeout: int = 120, **kwargs) -> Dict[str, Any]:
        """
        Run tests in a project.

        Args:
            path: Project root directory (auto-detected if None)
            command: Override test command (e.g. "pytest tests/test_foo.py -v")
            file: Run tests in specific file only
            pattern: Filter tests by name pattern (e.g. "-k test_login" for pytest)
            verbose: Show verbose output
            timeout: Max seconds to wait
        """
        cwd = path or os.getcwd()
        cwd = str(Path(cwd).expanduser().resolve())

        if command:
            cmd = command
            framework = "custom"
        else:
            framework, cmd = self._detect(cwd)
            if not cmd:
                return {"success": False, "error": "no test framework detected", "path": cwd,
                        "hint": "pass command= to specify test command"}

        # apply file filter
        if file:
            cmd = f"{cmd} {file}"

        # apply pattern filter
        if pattern:
            if framework == "pytest":
                cmd = f"{cmd} -k '{pattern}'"
            elif framework in ("jest", "vitest"):
                cmd = f"{cmd} -t '{pattern}'"
            elif framework == "go":
                cmd = f"{cmd} -run '{pattern}'"
            elif framework == "cargo":
                cmd = f"{cmd} {pattern}"
            else:
                cmd = f"{cmd} {pattern}"

        # verbose flags
        if verbose:
            if framework == "pytest":
                cmd = f"{cmd} -v"
            elif framework in ("jest", "vitest"):
                cmd = f"{cmd} --verbose"

        try:
            r = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=timeout
            )
            output = r.stdout + ("\n" + r.stderr if r.stderr else "")

            # parse results
            result = {
                "success": r.returncode == 0,
                "framework": framework,
                "command": cmd,
                "output": output[-10000:],  # cap output
                "code": r.returncode,
                "path": cwd,
            }

            # try to extract pass/fail counts
            stats = self._parse_stats(output, framework)
            if stats:
                result["stats"] = stats

            return result

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"tests timed out after {timeout}s", "command": cmd}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _detect(self, cwd: str):
        """Detect test framework from project files"""
        for marker, name, cmd in self.FRAMEWORKS:
            if Path(cwd, marker).exists():
                return name, cmd
        # check package.json for test script
        pkg = Path(cwd, "package.json")
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if "test" in data.get("scripts", {}):
                    return "npm", "npm test"
            except Exception:
                pass
        # check for test directories
        for d in ("tests", "test", "spec", "__tests__"):
            if Path(cwd, d).is_dir():
                # check if python or js
                py_tests = list(Path(cwd, d).glob("*.py"))
                js_tests = list(Path(cwd, d).glob("*.js")) + list(Path(cwd, d).glob("*.ts"))
                if py_tests:
                    return "pytest", "pytest"
                if js_tests:
                    return "jest", "npx jest"
        return None, None

    def _parse_stats(self, output: str, framework: str) -> Optional[dict]:
        """Extract test pass/fail counts from output"""
        import re
        if framework == "pytest":
            m = re.search(r'(\d+) passed', output)
            f = re.search(r'(\d+) failed', output)
            e = re.search(r'(\d+) error', output)
            if m or f:
                return {"passed": int(m.group(1)) if m else 0,
                        "failed": int(f.group(1)) if f else 0,
                        "errors": int(e.group(1)) if e else 0}
        elif framework in ("jest", "vitest"):
            m = re.search(r'Tests:\s+(\d+) passed', output)
            f = re.search(r'Tests:\s+(\d+) failed', output)
            if m or f:
                return {"passed": int(m.group(1)) if m else 0,
                        "failed": int(f.group(1)) if f else 0}
        return None

    def test(self):
        r = self.forward(command="echo 'tests passed'")
        assert r["success"]
        return True
