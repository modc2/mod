
import mod as c
import json
import os
from typing import List, Dict, Union, Optional, Any

print = c.print
class SelectFiles:

    def __init__(self,  model='model.openrouter'):
        self.model = c.mod(model)()

    def forward(self,  
              path: Union[List[str], Dict[Any, str]] = './', 
              query: str = 'most relevant', 
              files = None, 
              n: int = 10, 
              trials = 3,
              number_lines = True,
              mod=None,
              content: bool = True,
              model='anthropic/claude-opus-4.5',
              avoid_paths: List[str] = ['.git', '__pycache__', 'node_modules', '.venv', 'venv', '.env', '/private', '/tmp'],
              depth=8,
               **kwargs) -> List[str]:

        if files == None:
            if mod:
                path = c.dirpath(mod)
            files = c.files(path, depth=depth)

        if len(files) > 1:

            # make the files relative to the path
            for trial in range(trials):
                try:
                    files = c.fn('tool.select_options/')(
                        query=query,
                        options= files,
                        n=n,
                        model=model,
                        **kwargs
                    )
                    break
                except Exception as e: 
                    print(f"Trial {trial+1} failed with error: {e}", color="red")
                    continue
        if content:
            results = {}
            for f in files:
                try:
                    results[f] = self.get_text(f)
                except Exception as e:
                    print(f"Failed to read file {f}: {e}", color="red")
                    continue
        else: 
            results = files
        print(f"Selected files >>> ",files, color="cyan")
        if number_lines:
            for f in results:
                results[f] =  [f"{i+1}   {line}" for i, line in enumerate(results[f].split('\n'))]
        return results

    def get_text(self, path: str) -> str:
        path = os.path.abspath(os.path.expanduser(path))
        with open(path, 'r') as f:
            text = f.read()
        return text

    def test(self):
        dirpath = os.path.dirname(__file__)
        query = f'i want to find the {__file__} file'
        results = self.forward(path=dirpath, query=query, n=1)
        result_files = [os.path.abspath(os.path.expanduser(f)) for f in results]
        assert __file__ in result_files, f"Expected {dirpath} in results, got {result_files}"
        print(f"Test results: {results}", color="green")
        return {
            "success": True,
            "message": f"Tesst with query '{query}' and wanted file '{__file__}'"
            
        }

        



    