
import time
import os
import json
from pathlib import Path
from typing import Dict, List, Union, Optional, Any, Tuple
from .utils import *
import mod as m
print=m.print
class Dev:

    def __init__(self, model: str = 'model.openrouter',  skill = 'dev.skill', memory = 'dev.memory', **kwargs):

        self.memory = m.mod(memory)()
        self.skill = m.mod(skill)()
        self.model = m.mod(model)()

    def forward(self, 
                text: str = 'make this like the base ', 
                *extra_text, 
                mod=None,
                temperature: float = 0.0, 
                max_tokens: int = 1000000, 
                stream: bool = True,
                tools  = ['create_file', 'rm_file'],
                model: Optional[str] = 'anthropic/claude-sonnet-4.5',
                steps = 1,
                path=None,
                safety=True,
                base = None,
                remote=False,
                **kwargs) -> Dict[str, str]:
        
        """
        use this to run the agent with a specific text and parameters
        """
        if mod != None:
            path = m.dirpath(mod)
        text = ' '.join(list(map(str, [text] + list(extra_text))))
        query = text 
        if path != None:
            self.memory.add(m.tool('select_files')(path=path, query=query))
        if base != None:
            self.memory.add(m.content(base))
        for step in range(steps):   
            params = dict(query=query, path=path, step=step, steps=steps, files=m.files(path), memory=self.memory.get(), tools=tools, base=base)
            prompt = self.skill.prepare(**params)
            output = self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
            plan = self.skill.plan(output, safety=safety)
            self.memory.add(plan)
            if plan[-1]['tool'].lower() == 'finish':
                break
            
        return plan
