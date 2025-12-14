
import time
import os
import json
from pathlib import Path
from typing import Dict, List, Union, Optional, Any, Tuple
import mod as m

class Agent:

    def __init__(self, 

                 model: str = 'model.openrouter', 
                 skill = 'dev.skill',
                 memory = 'dev.memory',
                 **kwargs):

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
                tools  = ['websearch'],
                model: Optional[str] = 'anthropic/claude-sonnet-4.5',
                steps = 1,
                path='./',
                safety=False,
                base = None,
                remote=False,
                **kwargs) -> Dict[str, str]:
        
        """
        use this to run the agent with a specific text and parameters
        """

        if mod != None:
            path = m.dirpath(mod)
        query = ' '.join(list(map(str, [text] + list(extra_text))))
        if len(tools) == 0:
            return self.model.forward(query, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
        else:
            for step in range(steps):   
                params = dict(query=query, path=path, step=step, steps=steps, files=m.files(path), memory=self.memory.get(), tools=tools)
                prompt = self.skill.prepare(**params)
                output = self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
                plan = self.skill.plan(output, safety=safety)
                self.memory.add(plan)
                if plan[-1]['tool'].lower() == 'finish':
                    break
        return self.model.forward('given the above, finish the task: ' + query + str(self.memory.get()), stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )