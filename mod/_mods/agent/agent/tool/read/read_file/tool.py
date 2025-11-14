import mod as m
import os

class ReadMod:
    def forward(self, path:str):
        """
        read the file
        """
        with open(os.path.abspath(path), 'r') as f:
            return f.read()

    def test(self, path:str):
        test_content = 'This is a test mod file.'
        abs_path = os.path.abspath(path)
        with open(abs_path, 'w') as f:
            f.write(test_content)
        content = self.forward(path)
        assert content == test_content
        os.remove(abs_path)
        return {"success": True, "message": "ReadMod test passed."}