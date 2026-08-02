"""patch - apply multiple edits to a file in one operation"""
from pathlib import Path
from typing import Dict, Any, List


class Skill:
    description = "Apply multiple find-replace edits to a file atomically. More efficient than calling edit multiple times."

    def forward(self, file_path: str, edits: List[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
        """
        Apply a list of edits to a file. Each edit is {"old": "...", "new": "..."}.
        Edits are applied sequentially on the in-memory content, then written once.
        If any edit fails to match, the whole operation is rolled back.

        Args:
            file_path: Path to the file to edit
            edits: List of {"old": "text to find", "new": "replacement text"} dicts
        """
        if not edits:
            return {"success": False, "error": "no edits provided"}

        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            return {"success": False, "error": f"not a file: {path}"}

        try:
            original = path.read_text(encoding="utf-8")
            content = original

            applied = []
            for i, edit in enumerate(edits):
                old = edit.get("old", "")
                new = edit.get("new", "")
                if not old:
                    return {"success": False, "error": f"edit {i}: empty 'old' string", "applied": applied}
                if old not in content:
                    return {"success": False, "error": f"edit {i}: '{old[:80]}...' not found", "applied": applied, "rolled_back": True}
                count = content.count(old)
                if count > 1:
                    # only replace first occurrence to be safe
                    content = content.replace(old, new, 1)
                else:
                    content = content.replace(old, new)
                applied.append({"index": i, "old_preview": old[:60], "new_preview": new[:60]})

            path.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(path), "edits_applied": len(applied), "applied": applied}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def test(self):
        import tempfile, os
        p = os.path.join(tempfile.gettempdir(), "skill_patch_test.txt")
        Path(p).write_text("hello world\nfoo bar\nbaz qux")
        r = self.forward(p, edits=[
            {"old": "hello", "new": "hi"},
            {"old": "foo", "new": "FOO"},
        ])
        assert r["success"] and r["edits_applied"] == 2
        assert Path(p).read_text() == "hi world\nFOO bar\nbaz qux"
        os.unlink(p)
        return True
