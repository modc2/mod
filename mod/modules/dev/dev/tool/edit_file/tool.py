import mod as c
import os
import re
from typing import Optional, Dict, Any, Tuple

print = c.print

class Tool:
    """Smart file content editor with anchor-based insertion and advanced editing capabilities."""
    
    def forward(
        self,
        path: str = "./",
        content: str = "",
        start_anchor: Optional[str] = None,
        end_anchor: Optional[str] = None,
        create_if_missing: bool = True,
        strict: bool = False,
        use_regex: bool = False,
        backup: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Intelligently insert/replace content in a file using anchors.

        Strategy:
        - Both anchors exist → replace content between them
        - Only start anchor → insert after it
        - Only end anchor → insert before it
        - No anchors → append to end (or error if strict=True)

        Args:
            path: File path to edit
            content: Content to insert
            start_anchor: Pattern marking insertion start point
            end_anchor: Pattern marking insertion end point
            create_if_missing: Create file if it doesn't exist
            strict: Raise error if anchors not found
            use_regex: Treat anchors as regex patterns
            backup: Create backup before modifying

        Returns:
            Dict with success status, message, and updated content
        """
        path = os.path.abspath(path)
        
        # Handle missing file
        if not os.path.exists(path):
            if create_if_missing:
                self._ensure_parent_dirs(path)
                c.write(path, content)
                c.print(f"[✓] Created new file: {path}", color="green")
                return {"success": True, "message": f"Created new file: {path}", "content": content, "path": path}
            elif strict:
                raise FileNotFoundError(f"File not found: {path}")
            else:
                return {"success": False, "message": f"File not found: {path}", "content": "", "path": path}
        
        # Backup if requested
        if backup:
            self._create_backup(path)
        
        text = c.text(path)
        result = text

        # Find anchor positions
        start_pos = self._find_anchor_end(start_anchor, text, use_regex) if start_anchor else None
        end_pos = self._find_anchor(end_anchor, text, start_pos or 0, use_regex) if end_anchor else None

        # Apply insertion logic
        if start_pos is not None and end_pos is not None and end_pos > start_pos:
            result = text[:start_pos] + content + text[end_pos:]
            msg = "Replaced content between anchors"
            c.print(f"[✓] {msg}", color="green")
        elif start_pos is not None:
            result = text[:start_pos] + content + text[start_pos:]
            msg = "Inserted after start anchor"
            c.print(f"[✓] {msg}", color="cyan")
        elif end_pos is not None:
            result = text[:end_pos] + content + text[end_pos:]
            msg = "Inserted before end anchor"
            c.print(f"[✓] {msg}", color="cyan")
        else:
            msg = "No anchors found"
            if strict:
                raise ValueError(f"{msg}. Cannot insert content.")
            result = text + "\n" + content if text and not text.endswith("\n") else text + content
            c.print(f"[!] {msg}. Appending to end.", color="yellow")

        # Write if changed
        if result != text:
            c.put_text(path, result)
            c.print(f"[✓] Updated: {path}", color="green")
            return {"success": True, "message": msg, "content": result, "path": path, "changed": True}
        else:
            c.print(f"[=] No changes needed: {path}", color="blue")
            return {"success": True, "message": "No changes needed", "content": result, "path": path, "changed": False}

    def _find_anchor(self, pattern: str, text: str, start: int = 0, use_regex: bool = False) -> Optional[int]:
        """Find anchor start position."""
        if not pattern:
            return None
        if use_regex:
            match = re.search(pattern, text[start:])
            return match.start() + start if match else None
        pos = text.find(pattern, start)
        return pos if pos != -1 else None

    def _find_anchor_end(self, pattern: str, text: str, use_regex: bool = False, start: int = 0) -> Optional[int]:
        """Find anchor end position."""
        if not pattern:
            return None
        if use_regex:
            match = re.search(pattern, text[start:])
            return match.end() + start if match else None
        pos = text.find(pattern, start)
        return pos + len(pattern) if pos != -1 else None
    
    def _ensure_parent_dirs(self, path: str) -> None:
        """Ensure parent directories exist."""
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
    
    def _create_backup(self, path: str) -> str:
        """Create a backup of the file."""
        backup_path = f"{path}.backup"
        if os.path.exists(path):
            c.write(backup_path, c.text(path))
            c.print(f"[✓] Backup created: {backup_path}", color="blue")
        return backup_path


    def test(self):

        """Run test cases for the edit_file tool.

        Test 1: Create file if missing
        Test 2: Insert after start anchor
        Test 3: Insert before end anchor
        Test 4: Replace between anchors
        Test 5: No anchors found, append

        """
        test_path = "./test_edit_file.txt"
        if not os.path.exists(os.path.dirname(test_path)):
            os.makedirs(os.path.dirname(test_path))
            c.put_text(test_path, "")

        # Test 2: Insert after start anchor
        res = self.forward(path=test_path, content="Inserted After Start\n", start_anchor="Hello", strict=True)
        assert res['success'],  f"Test 2 Failed {res}"
        
        # Test 3: Insert before end anchor
        res = self.forward(path=test_path, content="Inserted Before End\n", end_anchor="World", strict=True)
        assert res['success'], "Test 3 Failed"
        
        # Test 4: Replace between anchors
        res = self.forward(path=test_path, content="Replaced Content\n", start_anchor="Inserted After Start", end_anchor="Inserted Before End", strict=True)
        assert res['success'], "Test 4 Failed"
        
        # Test 5: No anchors found, append
        res = self.forward(path=test_path, content="Appended Content\n", strict=False)
        assert res['success'], "Test 5 Failed"
        
        # Cleanup
        if os.path.exists(test_path):
            os.remove(test_path)
        if os.path.exists(f"{test_path}.backup"):
            os.remove(f"{test_path}.backup")
        
        c.print("[✓] All tests passed!", color="green")

        return {"status": "All tests passed"}