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
    sum_folder = m.mod('tool.sum_folder')()
    def forward(self, module='base', **kwargs):
        return self.sum_folder.forward(path=m.dirpath(module), **kwargs)
