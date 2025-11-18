
import mod as c
import os
import json
import time
from typing import Dict, List, Any, Optional, Union
from pathlib import Path

class Memory:
    """
    A memory management tool that provides both short-term and long-term memory capabilities.
    
    This tool helps maintain context across interactions by:
    - Storing temporary information in short-term memory (in-memory)
    - Persisting important information in long-term memory (file-based)
    - Retrieving and filtering memories based on relevance
    - Managing memory expiration and prioritization
    """
    
    
    def __init__(
        self,
        path = "~/.mod/agent/memory",
        **kwargs
    ):
        """
        Initialize the Memory module.
        """
        self.path = os.path.expanduser(path)
        self.memory = []
        
    def add_memory(self, *items, topic=None) -> List[Any]:
        if not hasattr(self, 'memory'):
            self.memory = []
        for item in items:
            self.memory.append(item)
        return self.memory

    def clear_memory(self):
        self.memory = []
        return self.memory

    def get_memory(self):
        if not hasattr(self, 'memory'):
            self.memory = []
        return self.memory