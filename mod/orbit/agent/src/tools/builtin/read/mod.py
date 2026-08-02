"""read - read files from disk"""
import os
from pathlib import Path
from typing import Dict, Any, Optional

class Skill:
    description = "Read file contents with optional line range"

    def forward(self, file_path: str, offset: int = 0, limit: int = None, **kwargs) -> Dict[str, Any]:
        """Read a file, optionally slicing by line offset/limit"""
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return {"success": False, "content": "", "error": f"not found: {path}"}
        if not path.is_file():
            return {"success": False, "content": "", "error": f"not a file: {path}"}
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            end = offset + limit if limit else len(lines)
            selected = lines[offset:end]
            content = "".join(selected)
            return {"success": True, "content": content, "lines": len(selected), "total": len(lines), "path": str(path)}
        except Exception as e:
            return {"success": False, "content": "", "error": str(e)}

    def test(self):
        r = self.forward(__file__)
        assert r["success"] and "Skill" in r["content"]
        return True
