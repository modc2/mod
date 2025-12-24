
import time
import os
import json
from pathlib import Path
from typing import Dict, List, Union, Optional, Any, Tuple
import mod as m
print=m.print
class Dev:

    tools  = ['create_file', 'rm_file', 'edit_file']

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
                model: Optional[str] = 'anthropic/claude-sonnet-4.5',
                steps = 1,
                tools = None,
                path=None,
                safety=True,
                remote=False,
                **kwargs) -> Dict[str, str]:
        
        """
        use this to run the agent with a specific text and parameters
        """
        tools = tools or self.tools
        query = ' '.join(list(map(str, [text] + list(extra_text))))
        path = path or  m.dirpath(mod)
        self.memory.add('content', m.tool('select_files')(path=path, query=query)) if path != None else None
        def is_b_param(k):
            return k.startswith('b') and k[1:].isdigit()
        for k,v in kwargs.items():
            if is_b_param(k):
                print(f'Adding to memory: {v}')
                self.memory.add(v, m.code(v))
        self.memory.add('path', path)
        self.memory.add('steps', steps)
        for step in range(steps):   
            self.memory.add('files', m.files(path))
            self.memory.add('step', step)
            prompt = self.skill.prepare(query=query, memory=self.memory.get(), tools=tools)
            output = self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
            plan = self.skill.plan(output, safety=safety)
            self.memory.add('plan', plan)
            if plan[-1]['tool'].lower() == 'finish':
                break
        return plan
