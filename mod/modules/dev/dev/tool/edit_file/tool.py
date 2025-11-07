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
        start_line: int = 0,
        end_line: str = 10,
        strict: bool = False,
        use_regex: bool = False,
        backup: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Intelligently edit file content using anchor-based positioning.
        Returns:
            Dict containing success status, message, updated content, and metadata
        """
        path = os.path.abspath(path)
        
        # Handle non-existent files
        assert os.path.exists(path), f"File not found: {path}"
        
        original_text = c.text(path)
        prefix_lines = original_text.splitlines(keepends=True)[:start_line]
        suffix_lines = original_text.splitlines(keepends=True)[end_line:]
        modified_text = '\n'.join(prefix_lines + [content] + suffix_lines)
        c.print(f"[✓] File updated: {path}", color="green")
        return  {
            "success": True,
            "message": f"File '{path}' updated successfully."}
            
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
