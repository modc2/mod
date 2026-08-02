"""grep - search file contents by regex"""
import re
from pathlib import Path
from typing import Dict, Any, Optional

class Skill:
    description = "Search for regex patterns in files"

    def forward(self, pattern: str, path: str = ".", file_pattern: str = None, ignore_case: bool = False, context: int = 0, max_results: int = 100, **kwargs) -> Dict[str, Any]:
        """Search for pattern in files under path"""
        base = Path(path).expanduser().resolve()
        if not base.exists():
            return {"success": False, "matches": [], "error": f"path not found: {path}"}
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"success": False, "matches": [], "error": f"bad regex: {e}"}

        glob_pat = f"**/{file_pattern}" if file_pattern else "**/*"
        files = [f for f in base.glob(glob_pat) if f.is_file()] if base.is_dir() else [base]
        matches = []
        for fp in files:
            if len(matches) >= max_results:
                break
            try:
                with open(fp, "rb") as bf:
                    if b"\x00" in bf.read(512):
                        continue
                lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                for i, line in enumerate(lines):
                    if regex.search(line):
                        m = {"file": str(fp), "line": i + 1, "text": line.rstrip()}
                        if context > 0:
                            m["before"] = [lines[j].rstrip() for j in range(max(0, i - context), i)]
                            m["after"] = [lines[j].rstrip() for j in range(i + 1, min(len(lines), i + context + 1))]
                        matches.append(m)
                        if len(matches) >= max_results:
                            break
            except (PermissionError, UnicodeDecodeError, OSError):
                continue
        return {"success": True, "matches": matches, "total": len(matches)}

    def test(self):
        r = self.forward("class Skill", path=str(Path(__file__).parent.parent), file_pattern="*.py")
        assert r["success"] and r["total"] > 0
        return True
