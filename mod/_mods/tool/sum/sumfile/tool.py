
import mod as c
import json
import os
from typing import List, Dict, Union, Optional, Any

print = c.print
class SumFile:
    """
    Advanced search and relevance ranking module powered by LLMs.
    
    This module helps find the most relevant items from a list of options based on a query,
    using LLM-based semantic understanding to rank and filter options.
    """

    task = """
        - summarize the follwoing based on the format based on the wquery 
        - topic is the topuc of the section
        - data is the summary of the topic
        """
    anchors = ["<START_JSON>", "</END_JSON>"]
    result_format = f'{anchors[0]}(LIST(DICT(topic:str, data:str))){anchors[1]}'
    cache_dir: str = '~/.summarize/cache'

    def __init__(self,  provider='model.openrouter'):
        self.model = c.mod(provider)()

    def forward(self,  
              path: str = __file__, # Path to the file containing options or a file  
              query: str = 'most relevant', 
              temperature: float = 0.5,
              model='google/gemma-3-27b-it:free',
              content=  None,
              update = False,
              **kwargs) -> List[str]:

        path = self.abspath(path)        
        assert os.path.exists(path), f"File not found: {path}"
        assert os.path.isfile(path), f"Path is not a file: {path}"
        print(f"Summarizing file: {path}", color="cyan")       
        content = content or  c.text(path)
        # hash
        prompt = f'''
        TASK={self.task}
        QUERY={query}
        CONTENT={content} 
        RESULT_FORMAT={self.result_format}
        '''
        path = self.cache_dir +  c.hash(prompt)
        result = c.get(path, update=update)
        # Generate the response
        if result != None:
            return result
        result = self.model.forward( prompt, model=model,  stream=True, temperature=temperature )
        return self.postprocess(result)

    def postprocess(self, result):
        output = ''
        for ch in result: 
            print(ch, end='')
            output += ch
        if self.anchors[0] in output and self.anchors[1] in output:
            json_str = output.split(self.anchors[0])[1].split(self.anchors[1])[0].strip()
        elif "```json" in output and "```" in output:
            start_index = output.find('```json')
            end_index = output.find('```', start_index + 1)
            json_str = output[start_index + 7:end_index].strip()
        return json.loads(json_str)

    def abspath(self, path: str) -> str:
        return os.path.abspath(os.path.expanduser(path))
