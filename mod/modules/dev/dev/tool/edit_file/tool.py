import mod as c
import os
import re
from typing import Optional, Dict, Any

print = c.print

class Tool:
    """Advanced file editor with anchor-based content manipulation and intelligent insertion strategies."""
    
    def forward(
        self,
        path: str,
        content: str,
        start_anchor: str = "",
        end_anchor: str = "",
        strict: bool = False,
        use_regex: bool = False,
        backup: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Intelligently edit file content using anchor-based positioning.

        Insertion Strategy:
        - Both anchors present → Replace content between them
        - Start anchor only → Insert content after it
        - End anchor only → Insert content before it
        - No anchors → Append to file end (error if strict=True)

        Args:
            path: Target file path
            content: Content to insert/replace
            start_anchor: Pattern marking start position
            end_anchor: Pattern marking end position
            strict: Fail if anchors not found
            use_regex: Enable regex pattern matching
            backup: Create backup before modification

        Returns:
            Dict containing success status, message, updated content, and metadata
        """
        path = os.path.abspath(path)
        
        # Handle non-existent files
        assert os.path.exists(path), f"File not found: {path}"
        
        original_text = c.text(path)
        modified_text = original_text

        # Locate anchor positions
        start_pos = self._find_anchor_end(start_anchor, original_text, use_regex) if start_anchor else None
        end_pos = self._find_anchor(end_anchor, original_text, start_pos or 0, use_regex) if end_anchor else None   

        assert start_pos is not None or end_pos is not None or not strict, "Anchors not found in strict mode"
        if start_pos is not None and end_pos is not None:
            assert end_pos >= start_pos, "End anchor precedes start anchor"
        
        # Execute insertion strategy
        if start_pos is not None and end_pos is not None:
            modified_text = original_text[:start_pos] + content + original_text[end_pos:]
            msg = "Content replaced between anchors"
        elif start_pos is not None:
            modified_text = original_text[:start_pos] + content + original_text[start_pos:]
            msg = "Content inserted after start anchor"
        elif end_pos is not None:
            modified_text = original_text[:end_pos] + content + original_text[end_pos:]
            msg = "Content inserted before end anchor"
        else:
            modified_text = original_text + content
            msg = "Content appended to file"
            
        c.print(f"[✓] {msg}", color="green")
        
        # Persist changes if modified
        if modified_text == original_text:
            return {"success": True, "message": "No changes detected", "content": modified_text, "path": path, "changed": False}
        
        if backup:
            c.put_text(f"{path}.backup", original_text)
            c.print(f"[✓] Backup created: {path}.backup", color="yellow")
        
        c.put_text(path, modified_text)
        c.print(f"[✓] File updated: {path}", color="green")
        return {"success": True, "message": msg, "content": modified_text, "path": path, "changed": True}
            
    def _find_anchor(self, pattern: str, text: str, start: int = 0, use_regex: bool = False) -> Optional[int]:
        """Locate anchor start position in text."""
        if not pattern:
            return None
        if use_regex:
            match = re.search(pattern, text[start:])
            return match.start() + start if match else None
        pos = text.find(pattern, start)
        return pos if pos != -1 else None

    def _find_anchor_end(self, pattern: str, text: str, use_regex: bool = False, start: int = 0) -> Optional[int]:
        """Locate anchor end position in text."""
        if not pattern:
            return None
        if use_regex:
            match = re.search(pattern, text[start:])
            return match.end() + start if match else None
        pos = text.find(pattern, start)
        return pos + len(pattern) if pos != -1 else None
    

    def test(self) -> Dict[str, str]:
        """Execute comprehensive test suite for file editing functionality."""
        test_path = "./test_edit_file.txt"
        test_dir = os.path.dirname(test_path)
        
        if test_dir and not os.path.exists(test_dir):
            os.makedirs(test_dir)
        
        # Initialize test file
        c.put_text(test_path, "Hello\nWorld\n")
        c.print("[TEST] Starting comprehensive test suite...", color="cyan")

        # Test 1: Insert after start anchor
        c.print("\n[TEST 1] Insert after start anchor", color="cyan")
        res = self.forward(path=test_path, content="\nInserted After Start\n", start_anchor="Hello", strict=True)
        assert res['success'], f"Insert after start anchor failed: {res}"
        assert "Inserted After Start" in res['content'], "Content not inserted correctly"
        c.print(f"[✓] Test 1 passed: {res['message']}", color="green")
        
        # Test 2: Insert before end anchor
        c.print("\n[TEST 2] Insert before end anchor", color="cyan")
        c.put_text(test_path, "Hello\nWorld\n")
        res = self.forward(path=test_path, content="Inserted Before End\n", end_anchor="World", strict=True)
        assert res['success'], f"Insert before end anchor failed: {res}"
        assert "Inserted Before End" in res['content'], "Content not inserted correctly"
        c.print(f"[✓] Test 2 passed: {res['message']}", color="green")
        
        # Test 3: Replace between anchors
        c.print("\n[TEST 3] Replace between anchors", color="cyan")
        c.put_text(test_path, "Hello\nInserted After Start\nInserted Before End\nWorld\n")
        res = self.forward(path=test_path, content="\nReplaced Content\n", start_anchor="Inserted After Start", end_anchor="Inserted Before End", strict=True)
        assert res['success'], f"Replace between anchors failed: {res}"
        assert "Replaced Content" in res['content'], "Content not replaced correctly"
        c.print(f"[✓] Test 3 passed: {res['message']}", color="green")
        
        # Test 4: Append without anchors (non-strict)
        c.print("\n[TEST 4] Append without anchors", color="cyan")
        c.put_text(test_path, "Hello\nWorld\n")
        res = self.forward(path=test_path, content="Appended Content\n", start_anchor="", end_anchor="", strict=False)
        assert res['success'], f"Append without anchors failed: {res}"
        assert "Appended Content" in res['content'], "Content not appended correctly"
        c.print(f"[✓] Test 4 passed: {res['message']}", color="green")
        
        # Test 5: Backup functionality
        c.print("\n[TEST 5] Backup functionality", color="cyan")
        c.put_text(test_path, "Original Content\n")
        res = self.forward(path=test_path, content="Modified\n", start_anchor="Original", end_anchor="Content", backup=True)
        assert res['success'], f"Backup test failed: {res}"
        assert os.path.exists(f"{test_path}.backup"), "Backup file not created"
        backup_content = c.text(f"{test_path}.backup")
        assert "Original Content" in backup_content, "Backup content incorrect"
        c.print(f"[✓] Test 5 passed: Backup created successfully", color="green")
        
        # Test 6: No changes detection
        c.print("\n[TEST 6] No changes detection", color="cyan")
        current_content = c.text(test_path)
        res = self.forward(path=test_path, content="", start_anchor="Modified", end_anchor="Modified", strict=False)
        assert res['success'] and not res['changed'], f"No changes detection failed: {res}"
        c.print(f"[✓] Test 6 passed: {res['message']}", color="green")
        
        # Cleanup test artifacts
        c.print("\n[CLEANUP] Removing test files...", color="yellow")
        for cleanup_file in [test_path, f"{test_path}.backup"]:
            if os.path.exists(cleanup_file):
                os.remove(cleanup_file)
                c.print(f"[✓] Removed: {cleanup_file}", color="yellow")
        
        c.print("\n[✓] ALL 6 TEST CASES PASSED SUCCESSFULLY!", color="green")
        return {"status": "All tests passed", "tests_run": 6, "tests_passed": 6}
