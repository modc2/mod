"""context - project detection, dependency analysis, smart context gathering"""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, List


class Skill:
    description = "Analyze project structure: detect language, framework, dependencies, entry points, config. Provides full project context."

    def forward(self, path: str = None, **kwargs) -> Dict[str, Any]:
        """
        Gather comprehensive project context.

        Args:
            path: Project root directory (defaults to cwd)
        """
        cwd = Path(path or os.getcwd()).expanduser().resolve()
        if not cwd.is_dir():
            return {"success": False, "error": f"not a directory: {cwd}"}

        ctx = {
            "success": True,
            "path": str(cwd),
            "name": cwd.name,
        }

        # detect languages by file extensions
        ctx["languages"] = self._detect_languages(cwd)

        # detect project type and framework
        ctx["project"] = self._detect_project(cwd)

        # read key config files
        ctx["configs"] = self._read_configs(cwd)

        # dependencies
        ctx["dependencies"] = self._read_deps(cwd)

        # entry points
        ctx["entry_points"] = self._find_entries(cwd)

        # git info
        ctx["git"] = self._git_info(cwd)

        return ctx

    def _detect_languages(self, cwd: Path) -> dict:
        """Count files by language"""
        ext_map = {
            '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
            '.tsx': 'tsx', '.jsx': 'jsx', '.rs': 'rust', '.go': 'go',
            '.sol': 'solidity', '.rb': 'ruby', '.java': 'java',
            '.cpp': 'c++', '.c': 'c', '.cs': 'c#', '.swift': 'swift',
            '.kt': 'kotlin', '.php': 'php', '.lua': 'lua',
        }
        skip = {'node_modules', '.git', '__pycache__', '.next', 'venv', '.venv', 'target', 'dist', 'build'}
        counts = {}
        for root, dirs, files in os.walk(cwd):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in ext_map:
                    lang = ext_map[ext]
                    counts[lang] = counts.get(lang, 0) + 1
        # sort by count
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def _detect_project(self, cwd: Path) -> dict:
        """Detect project type and framework"""
        info = {"type": "unknown", "frameworks": []}

        files = {f.name for f in cwd.iterdir() if f.is_file()}

        # Python
        if "pyproject.toml" in files or "setup.py" in files or "requirements.txt" in files:
            info["type"] = "python"
            if "manage.py" in files:
                info["frameworks"].append("django")
            req_files = [cwd / "requirements.txt", cwd / "pyproject.toml"]
            for rf in req_files:
                if rf.exists():
                    content = rf.read_text(errors="ignore")
                    if "fastapi" in content.lower():
                        info["frameworks"].append("fastapi")
                    if "flask" in content.lower():
                        info["frameworks"].append("flask")
                    if "django" in content.lower():
                        info["frameworks"].append("django")

        # Node.js
        if "package.json" in files:
            info["type"] = "node"
            try:
                pkg = json.loads((cwd / "package.json").read_text())
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                for fw in ["next", "react", "vue", "svelte", "angular", "express", "fastify", "nest"]:
                    if any(fw in d for d in deps):
                        info["frameworks"].append(fw)
            except Exception:
                pass

        # Rust
        if "Cargo.toml" in files:
            info["type"] = "rust"

        # Go
        if "go.mod" in files:
            info["type"] = "go"

        # Solidity
        if any(cwd.glob("**/*.sol")):
            info["frameworks"].append("solidity")
            if "hardhat.config.js" in files or "hardhat.config.ts" in files:
                info["frameworks"].append("hardhat")
            if "foundry.toml" in files:
                info["frameworks"].append("foundry")

        # Docker
        if "Dockerfile" in files or "docker-compose.yml" in files or "docker-compose.yaml" in files:
            info["frameworks"].append("docker")

        return info

    def _read_configs(self, cwd: Path) -> dict:
        """Read key config file names (not contents, to keep output small)"""
        config_patterns = [
            "*.json", "*.toml", "*.yaml", "*.yml", "*.ini", "*.cfg",
            ".env*", "Makefile", "Dockerfile", "*.config.js", "*.config.ts",
        ]
        configs = []
        for pattern in config_patterns:
            for f in cwd.glob(pattern):
                if f.is_file() and f.stat().st_size < 500000:
                    configs.append(f.name)
        return sorted(set(configs))

    def _read_deps(self, cwd: Path) -> dict:
        """Extract dependency info"""
        deps = {}

        # Python
        req = cwd / "requirements.txt"
        if req.exists():
            lines = req.read_text(errors="ignore").splitlines()
            deps["python"] = [l.strip().split("==")[0].split(">=")[0] for l in lines
                              if l.strip() and not l.startswith("#") and not l.startswith("-")][:50]

        # Node
        pkg = cwd / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps["node"] = list(data.get("dependencies", {}).keys())[:50]
                deps["node_dev"] = list(data.get("devDependencies", {}).keys())[:30]
            except Exception:
                pass

        # Rust
        cargo = cwd / "Cargo.toml"
        if cargo.exists():
            try:
                content = cargo.read_text()
                import re
                deps["rust"] = re.findall(r'^(\w[\w-]*)\s*=', content, re.MULTILINE)[:50]
            except Exception:
                pass

        return deps

    def _find_entries(self, cwd: Path) -> list:
        """Find likely entry point files"""
        candidates = [
            "main.py", "app.py", "server.py", "index.py", "mod.py", "manage.py",
            "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
            "main.rs", "lib.rs", "main.go",
            "src/main.py", "src/app.py", "src/index.ts", "src/main.ts",
            "src/index.js", "src/main.rs", "src/lib.rs", "src/main.go",
        ]
        found = []
        for c in candidates:
            if (cwd / c).exists():
                found.append(c)
        return found

    def _git_info(self, cwd: Path) -> dict:
        """Get git info if available"""
        import subprocess
        try:
            branch = subprocess.run(
                "git branch --show-current", shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            remote = subprocess.run(
                "git remote get-url origin", shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            return {"branch": branch, "remote": remote}
        except Exception:
            return {}

    def test(self):
        r = self.forward(str(Path(__file__).parent.parent.parent))
        assert r["success"]
        return True
