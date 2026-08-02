"""tree - directory tree visualization"""
import os
from pathlib import Path
from typing import Dict, Any, List, Optional


class Skill:
    description = "Show directory tree with smart filtering. Skips node_modules, .git, __pycache__, etc."

    SKIP = {
        'node_modules', '.git', '__pycache__', '.next', '.venv', 'venv',
        '.tox', '.mypy_cache', '.pytest_cache', '.ruff_cache',
        'dist', 'build', '.DS_Store', '.env', 'target',
        'coverage', '.nyc_output', '.turbo', '.cache',
    }

    def forward(self, path: str = ".", depth: int = 4, show_hidden: bool = False,
                pattern: str = None, show_size: bool = False,
                max_items: int = 500, **kwargs) -> Dict[str, Any]:
        """
        Display a directory tree.

        Args:
            path: Root directory to display
            depth: Maximum depth to traverse
            show_hidden: Include hidden files/dirs (default False)
            pattern: Only show files matching this extension (e.g. ".py", ".ts")
            show_size: Show file sizes
            max_items: Max items to display
        """
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            return {"success": False, "error": f"not a directory: {root}"}

        lines = []
        counts = {"files": 0, "dirs": 0}
        self._walk(root, lines, counts, depth=depth, prefix="",
                   show_hidden=show_hidden, pattern=pattern,
                   show_size=show_size, max_items=max_items)

        tree_text = f"{root.name}/\n" + "\n".join(lines)
        return {
            "success": True,
            "tree": tree_text,
            "files": counts["files"],
            "dirs": counts["dirs"],
            "path": str(root),
        }

    def _walk(self, path: Path, lines: list, counts: dict,
              depth: int, prefix: str, show_hidden: bool,
              pattern: str, show_size: bool, max_items: int):
        if depth <= 0 or counts["files"] + counts["dirs"] >= max_items:
            return

        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        # filter entries
        filtered = []
        for e in entries:
            if e.name in self.SKIP:
                continue
            if not show_hidden and e.name.startswith('.'):
                continue
            if pattern and e.is_file() and not e.name.endswith(pattern):
                continue
            filtered.append(e)

        for i, entry in enumerate(filtered):
            if counts["files"] + counts["dirs"] >= max_items:
                lines.append(f"{prefix}... (truncated)")
                return

            is_last = i == len(filtered) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            name = entry.name
            if entry.is_dir():
                name += "/"
                counts["dirs"] += 1
            else:
                counts["files"] += 1
                if show_size:
                    try:
                        size = entry.stat().st_size
                        if size > 1024 * 1024:
                            name += f" ({size / 1024 / 1024:.1f}MB)"
                        elif size > 1024:
                            name += f" ({size / 1024:.1f}KB)"
                        else:
                            name += f" ({size}B)"
                    except OSError:
                        pass

            lines.append(f"{prefix}{connector}{name}")

            if entry.is_dir():
                self._walk(entry, lines, counts, depth=depth - 1,
                           prefix=child_prefix, show_hidden=show_hidden,
                           pattern=pattern, show_size=show_size, max_items=max_items)

    def test(self):
        r = self.forward(str(Path(__file__).parent.parent.parent), depth=2)
        assert r["success"] and r["files"] > 0
        return True
