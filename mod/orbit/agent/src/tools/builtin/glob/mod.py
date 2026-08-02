"""glob - find files by pattern"""
from pathlib import Path
from typing import Dict, Any, List

class Skill:
    description = "Find files matching a glob pattern"

    def forward(self, pattern: str, path: str = ".", recursive: bool = True, max_results: int = 500, **kwargs) -> Dict[str, Any]:
        """Find files matching pattern under path"""
        base = Path(path).expanduser().resolve()
        if not base.exists():
            return {"success": False, "matches": [], "error": f"path not found: {path}"}
        search = f"**/{pattern}" if recursive and "**" not in pattern else pattern
        try:
            matches = [str(p) for p in base.glob(search) if p.is_file()][:max_results]
            return {"success": True, "matches": matches, "total": len(matches)}
        except Exception as e:
            return {"success": False, "matches": [], "error": str(e)}

    def test(self):
        r = self.forward("*.py", path=str(Path(__file__).parent.parent))
        assert r["success"] and r["total"] > 0
        return True
