import mod as c
import os
from typing import Dict, Any

print = c.print

class Tool:
    """Simple file editor with anchor-based content replacement."""
    
    
    def forward(
        self,
        path: str,
        content: str = "",
        start_anchor: str = "",
        end_anchor: str = "",
        enforce_non_duplicate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Edit file by replacing content between anchors.
        
        WARNING: This will overwrite file content between anchors.
        Args:
            path: File path to edit
            content: New content to insert
            start_anchor: Start marker
            end_anchor: End marker
            
        Returns:
            Dict with success status and content
        """
        path = os.path.abspath(path)
        
        if not os.path.exists(path):
            return {"success": False, "message": f"File not found: {path}"}
        
        if not start_anchor or not end_anchor:
            return {"success": False, "message": "Both anchors required"}
        
        text = self.get_text(path)

        if enforce_non_duplicate:
            assert text.count(start_anchor) == 1, "Duplicate start anchors found"
            assert text.count(end_anchor) == 1, "Duplicate end anchors found" 

        start_idx = text.find(start_anchor)

        if start_idx == -1:
            return {"success": False, "message": "Start anchor not found"}
        
        end_idx = text.find(end_anchor)
        if end_idx == -1:
            return {"success": False, "message": "End anchor not found"}
        
        start_pos = start_idx + len(start_anchor)
        new_text = text[:start_pos] + content + text[end_idx:]
        
        self.put_text(path, new_text)
        
        return {
            "success": True,
            "message": "File edited",
            "content": new_text
        }


    def put_text(self, path: str, content: str) -> None:
        """Write content to file."""
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        path = os.path.abspath(path)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    def get_text(self, path: str) -> str:
        """Read file content."""
        path = os.path.abspath(path)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def test(self, test_file='~/.mod/edit_file/test.py') -> None:
        """Test the Tool functionality."""
        test_file = os.path.expanduser(test_file)
        sample_content = """# Sample File
        # START ANCHOR
        Old Content
        # END ANCHOR
        """

        self.put_text(test_file, sample_content)
        result = self.forward(
            path=test_file,
            content="New Content",
            start_anchor="# START ANCHOR",
            end_anchor="# END ANCHOR"
        )

        assert result["success"], f"Test failed: {result['message']}"
        updated_text = self.get_text(test_file)
        assert "New Content" in updated_text, "Content not updated correctly"
        print("Test passed. File content updated successfully.")
        os.remove(test_file)
        assert not os.path.exists(test_file), "Test file not deleted"
        return {"success": True, "message": "All tests passed."}

        


