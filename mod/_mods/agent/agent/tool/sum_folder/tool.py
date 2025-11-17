
import mod as m
import json
import os
from typing import List, Dict, Union, Optional, Any

print = m.print
class SumFolder:

    def forward(self, path: str = './', timeout=timeout **kwargs) -> List[str]:
        """
        Summarize the contents of a folder.
        """
        sum_file = m.mod('sum_file')()
        files = m.files(path)
        results = {}
        n = len(files)
        future2file = {}
        for i, file in enumerate(files):
            print(f"Summarizing file {i + 1}/{n}: {file}")
            future = m.submit(sum_file.forward, dict(path=file, **kwargs), timeout=timeout)
            future2file[future] = file
        for future in m.as_completed(future2file, timeout=timeout):
            file = future2file[future]
            # make relative to path
            file = os.path.relpath(file, path)
            results[file] = future.result()
            print(f"Completed summarizing file: {file}", color='green')
        return results