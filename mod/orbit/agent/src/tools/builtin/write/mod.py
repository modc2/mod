"""write - write content to files"""
import os
from pathlib import Path
from typing import Dict, Any

class Skill:
    description = "Write content to a file, creating directories as needed"

    def forward(self, file_path: str, content: str, **kwargs) -> Dict[str, Any]:
        """Write content to file_path"""
        path = Path(file_path).expanduser().resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(path), "bytes": len(content.encode("utf-8"))}
        except Exception as e:
            return {"success": False, "path": str(path), "error": str(e)}

    def test(self):
        import tempfile
        p = os.path.join(tempfile.gettempdir(), "skill_write_test.txt")
        r = self.forward(p, "hello")
        assert r["success"]
        os.unlink(p)
        return True
