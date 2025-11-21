
import mod as m
import json
import os
from typing import List, Dict, Union, Optional, Any

class SumFolder:

    def forward(self,  
              path: str = __file__, # Path to the file containing options or a file  
              query: str = 'most relevant', 
              model: str = None,
              temperature: float = 0.5,
              content=  None,
              timeout=50,
              update = False,
              **kwargs) -> List[str]:


        cache_path = f'~/.mod/sumfile/{m.hash(str(path))}'
        summary = m.get(cache_path, update=update)
        if summary != None:
            return summary
        if not os.path.exists(path) and m.mod_exists(path):
            path = m.dirpath(path)
        path = m.abspath(path)        
        assert os.path.exists(path), f"File not found: {path}"
        assert os.path.isdir(path), f"Path is not a directory: {path}"
        files = m.files(path)
        files = m.mod('select_files')().forward(files=files, query=query)
        sumfile = m.mod('tool.sumfile')()
        future2file = {}
        for f in files:
            future = m.future( sumfile.forward, dict(path=f, query=query, model=model), timeout=timeout)
            future2file[future] = f
        results = {}
        path = path.replace(m.home_path, '~')
        for f in m.as_completed(future2file, timeout=timeout):
            file = future2file[f]
            result = f.result()
            relfile = file.replace(path+'/', '' )
            results[relfile] = result
            m.print(f"\nSummary for {file}:\n", color="green")
            for r in result:
                m.print(f"- {r}", color="yellow")
        m.put(cache_path, results)
    
        return results
