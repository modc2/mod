"""edit - find and replace text in files"""
from pathlib import Path
from typing import Dict, Any

class Skill:
    description = "Edit a file by replacing old text with new text"

    def forward(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False, **kwargs) -> Dict[str, Any]:
        """Replace old_string with new_string in file"""
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            return {"success": False, "error": f"not a file: {path}"}
        try:
            content = path.read_text(encoding="utf-8")
            if old_string not in content:
                return {"success": False, "error": "string not found in file"}
            count = content.count(old_string)
            new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")
            return {"success": True, "path": str(path), "replacements": count if replace_all else 1}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test(self):
        import tempfile, os
        p = os.path.join(tempfile.gettempdir(), "skill_edit_test.txt")
        Path(p).write_text("hello world")
        r = self.forward(p, "hello", "hi")
        assert r["success"] and Path(p).read_text() == "hi world"
        os.unlink(p)
        return True
