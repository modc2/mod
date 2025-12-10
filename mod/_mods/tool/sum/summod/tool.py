import mod as m
import json
import os
from typing import List, Dict, Union, Optional, Any
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed

print = m.print

class SumMod:
    """
    Module to summarize all files in a folder using LLM-based semantic understanding.
    
    This module processes all files in a directory and provides summaries based on
    a query, with support for caching and parallel processing.
    """
    sum_folder = m.mod('tool.sumfolder')()
    def forward(self, mod='base', update=False ,**kwargs):
        path = f'~/.mod/summod/{mod}'
        summary = m.get(path, update=update)
        if summary == None:
            summary  =  self.sum_folder.forward(path=m.dirpath(mod), update=update, **kwargs)
            m.put(path, summary)
        return summary
        