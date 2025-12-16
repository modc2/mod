import mod as c
import os
from typing import Dict, Any, Optional

print = c.print

class Tool:
    """Advanced file editor with line-based content replacement and robust error handling."""
    
    def forward(
        self,
        path: str,
        content: str,
        start_line: int,
        end_line: int,
        backup: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Edit file by replacing content between line numbers with enhanced safety.
        
        Args:
            path: File path to edit (absolute or relative)
            content: New content to insert
            start_line: Start line number (1-indexed)
            end_line: End line number (1-indexed, inclusive)
            backup: Create backup before editing
            
        Returns:
            Dict with success status, message, and updated content
        """
        try:
            path = os.path.abspath(path)
            
            if not os.path.exists(path):
                return {"success": False, "message": f"File not found: {path}", "content": None}
            
            if start_line is None or end_line is None:
                return {"success": False, "message": "Both start_line and end_line are required", "content": None}
            
            # Create backup if requested
            if backup:
                backup_path = f"{path}.backup"
                with open(path, 'r', encoding='utf-8') as src:
                    with open(backup_path, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
            
            text = self.get_text(path)
            lines = text.splitlines(keepends=True)
            end_line = min(end_line, len(lines))
            start_line = min(max(1, start_line), end_line)
            
            # Validate line ranges
            if not (1 <= start_line <= len(lines)):
                return {"success": False, "message": f"start_line {start_line} out of range (1-{len(lines)})", "content": None}
            if not (1 <= end_line <= len(lines)):
                return {"success": False, "message": f"end_line {end_line} out of range (1-{len(lines)})", "content": None}
            if start_line > end_line:
                return {"success": False, "message": "start_line must be <= end_line", "content": None}
            
            # Convert to 0-indexed
            start_idx = start_line - 1
            end_idx = end_line
            
            # Replace lines with proper newline handling
            inserted_content = content if content.endswith('\n') else content + '\n'
        new_lines = lines[:start_idx] + [inserted_content] + lines[end_idx:]
            new_text = ''.join(new_lines)

            print(f"Editing file: {path}, replacing lines {start_line}-{end_line}")
            
            self.put_text(path, new_text)
            
            return {
                "success": True,
                "message": f"Successfully edited {path}: replaced lines {start_line}-{end_line}",
                "content": new_text,
                "lines_replaced": end_line - start_line + 1
            }
        except Exception as e:
            return {"success": False, "message": f"Error editing file: {str(e)}", "content": None}
    
    def put_text(self, path: str, content: str) -> None:
        """Write content to file with directory creation."""
        path = os.path.abspath(path)
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    
    def get_text(self, path: str) -> str:
        """Read file content with error handling."""
        path = os.path.abspath(path)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def test(self, test_file: str = '~/.mod/edit_file/test.py') -> Dict[str, Any]:
        """Comprehensive test suite for Tool functionality."""
        test_file = os.path.expanduser(test_file)
        sample_content = "# Sample File\nLine 2\nLine 3\nLine 4\nLine 5\n"
        
        try:
            # Test 1: Basic edit
            self.put_text(test_file, sample_content)
            result = self.forward(
                path=test_file,
                content="New Content\n",
                start_line=2,
                end_line=4
            )
            
            assert result["success"], f"Test failed: {result['message']}"
            updated_text = self.get_text(test_file)
            assert "New Content" in updated_text, "Content not updated correctly"
            
            # Test 2: Single line edit
            result2 = self.forward(
                path=test_file,
                content="Single Line Edit",
                start_line=1,
                end_line=1
            )
            assert result2["success"], "Single line edit failed"
            
            # Cleanup
            if os.path.exists(test_file):
                os.remove(test_file)
            
            print("✓ All tests passed successfully!")
            return {"success": True, "message": "All tests passed."}
        except Exception as e:
            return {"success": False, "message": f"Test failed: {str(e)}"}
