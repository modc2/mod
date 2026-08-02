"""diff - compare files or strings"""
import difflib
from pathlib import Path
from typing import Dict, Any, Optional


class Skill:
    description = "Compare two files or two strings and generate a unified diff. Shows exactly what changed."

    def forward(self, file_a: str = None, file_b: str = None,
                text_a: str = None, text_b: str = None,
                context: int = 3, **kwargs) -> Dict[str, Any]:
        """
        Generate a unified diff between two files or two strings.

        Args:
            file_a: Path to first file
            file_b: Path to second file
            text_a: First text string (alternative to file_a)
            text_b: Second text string (alternative to file_b)
            context: Number of context lines around changes
        """
        label_a = "a"
        label_b = "b"

        # read from files or use text
        if file_a:
            pa = Path(file_a).expanduser().resolve()
            if not pa.is_file():
                return {"success": False, "error": f"not a file: {pa}"}
            text_a = pa.read_text(encoding="utf-8")
            label_a = str(pa)
        if file_b:
            pb = Path(file_b).expanduser().resolve()
            if not pb.is_file():
                return {"success": False, "error": f"not a file: {pb}"}
            text_b = pb.read_text(encoding="utf-8")
            label_b = str(pb)

        if text_a is None or text_b is None:
            return {"success": False, "error": "provide either file_a/file_b or text_a/text_b"}

        lines_a = text_a.splitlines(keepends=True)
        lines_b = text_b.splitlines(keepends=True)

        diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b, n=context))
        diff_text = ''.join(diff)

        # count changes
        added = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
        removed = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))

        return {
            "success": True,
            "diff": diff_text,
            "added": added,
            "removed": removed,
            "changed": diff_text != "",
            "from": label_a,
            "to": label_b,
        }

    def test(self):
        r = self.forward(text_a="hello\nworld\n", text_b="hello\nearth\n")
        assert r["success"] and r["changed"] and r["added"] == 1 and r["removed"] == 1
        return True
