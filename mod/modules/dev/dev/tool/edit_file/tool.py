import mod as c
import os
from typing import Dict, Any

print = c.print

class Tool:
    """Simple file editor with anchor-based content replacement."""
    
    VERSION = "5.0.0"
    
    def forward(
        self,
        path: str,
        content: str = "",
        start_anchor: str = "",
        end_anchor: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Edit file by replacing content between anchors.
        
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
        
        text = c.text(path)
        
        start_idx = text.find(start_anchor)
        if start_idx == -1:
            return {"success": False, "message": "Start anchor not found"}
        
        end_idx = text.find(end_anchor, start_idx)
        if end_idx == -1:
            return {"success": False, "message": "End anchor not found"}
        
        start_pos = start_idx + len(start_anchor)
        new_text = text[:start_pos] + content + text[end_idx:]
        
        c.put_text(path, new_text)
        
        return {
            "success": True,
            "message": "File edited",
            "content": new_text
        }
