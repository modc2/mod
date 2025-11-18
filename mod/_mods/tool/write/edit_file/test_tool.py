import mod as c
import os
import tempfile
from tool import Tool

def test_edit_file():
    """Test edit_file tool functionality."""
    
    # Create temp file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        test_file = f.name
        f.write("START\n# CODE HERE\nEND")
    
    try:
        tool = Tool()
        
        # Test successful edit
        result = tool.forward(
            path=test_file,
            content="\nprint('Hello World')\n",
            start_anchor="# CODE HERE",
            end_anchor="\nEND"
        )
        
        assert result["success"] == True, "Edit should succeed"
        assert "Hello World" in c.text(test_file), "Content should be inserted"
        
        # Test missing file
        result = tool.forward(
            path="/nonexistent/file.txt",
            content="test",
            start_anchor="START",
            end_anchor="END"
        )
        assert result["success"] == False, "Should fail for missing file"
        
        # Test missing anchors
        result = tool.forward(
            path=test_file,
            content="test",
            start_anchor="",
            end_anchor="END"
        )
        assert result["success"] == False, "Should fail without anchors"
        
        print("✓ All tests passed!")
        
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    test_edit_file()
