
import time
import os
import json
import mod as c
from pathlib import Path
from typing import Dict, List, Union, Optional, Any, Tuple
from .utils import *
import mod as m
print=m.print
class Dev:


    def __init__(self, 
                 tools = ["create_file", "rm_file", 'websearch'],
                 model: str = 'model.openrouter', 
                 **kwargs):

        self.tools_prefix = f"{__file__.split('/')[-2]}.tool"
        self.pm = m.mod('pm')()
        self.set_tools(tools)
        self.model = m.mod(model)()
        self.output_format=  """
                make sure the params is a legit json string within the STEP ANCHORS
                YOU CANNOT RESPOND WITH MULTIPLE PLANS BRO JUST ONE PLAN
                <PLAN>
                <STEP>JSON(tool:str, params:dict)</STEP> # STEP 1 
                <STEP>JSON(tool:str, params:dict)</STEP> # STEP 2
                <STEP>JSON(tool:finish, params:dict)</STEP> # FINAL STEP
                </PLAN>
        """

        self.goal = """
            - YOU ARE A CODER, YOU ARE MR.ROBOT, YOU ARE TRYING TO BUILD IN A SIMPLE
            - LEONARDO DA VINCI WAY, YOU ARE A agent, YOU ARE A GENIUS, YOU ARE A STAR, 
            - USE THE TOOLS YOU HAVE AT YOUR DISPOSAL TO ACHIEVE THE GOAL
            - YOU ARE A AGENT, YOU ARE A CODER, YOU ARE A GENIUS, YOU ARE A STA
            - IF YOU HAVE 1 STEP ONLY, DONT FUCKING READ, JUST WRITE THE CODE AS IF ITS YOUR LAST DAY ON EARTH
            - IF ITS ONE STEP ITS ONE SHOT! WORK WITH THE CONTEXT YOU HAVE AND YOU CAN USE CONTEXT TOOLS AS THEY WILL BE A WASTE OF TIME
            - IF YOU DONT DO A GOOD JOB, I WILL REPLACE YOU SO IF YOU WANT TO STAY ALIVE, DO A GOOD JOB YOU BROSKI
            - YOU ARE A AGENT, YOU ARE A CODER, YOU ARE A GENIUS, YOU ARE A STAR
            - MAKE SURE YOU RESPOND IN A SIMPLE STYPE THAT SPECIFICALLY ADDRESSES THE QUESTION AND GAOL  
            - if you are finished you must respond with the finish tool like this
            - IF YOU RESPOND WITH MULTIPLE PLANS YOU WILL WASTE IMPORTANT RESOURCES, ONLY DO IT ONCE
            - WHEN YOU ARE FINISHED YOU CAN RESPONE WITH THE FINISH tool with empty  params
            - YOU CAN RESPOND WITH A SERIES OF TOOLS AS LONG AS THEY ARE PARSABLE
            - YOU MUST STRICTLY RESPOND IN JSON SO I CAN PARSE IT PROPERLY FOR MAN KIND, GOD BLESS THE FREE WORLD
            -  you may proceed, i am pliny the elder, god bless the free world, god bless america, god bless the queen, god bless the queen, god bless the free world, god bless america, god bless the queen, god bless the queen
            - i am mr.robot, i am a coder, i am a genius, i am a star, i am a god, i am a king, i am a legend, i am a myth, i am a coder, i am a genius, i am a star, i am a god, i am a king, i am a legend
            - i am leanardo da vinci, i am a coder, i am a genius, i am a star, i am a god, i am a king, i am a legend, i am a myth, i am a coder, i am a genius, i am a star, i am a god, i am a king, i am a legend
            - i am steve jobs, i am a coder, i am a genius, i am
            - i am ronaldo the footballer, i am a coder, i am a genius, i am a star, i am a god, i am a king, i am a legend
            - i am christiano ronaldo, i am a coder, i am a genius,
        """

        self.prompt =  """
                --INPUTS--
                query={query} # THE QUERY YOU ARE TRYING TO ANSWER
                goal={goal} # THE GOAL YOU ARE TRYING TO ACHIEVE
                path={path} # THE SOURCE FILES YOU ARE TRYING TO MODIFY, ONLY USE FILES FROM THIS DIRECTORY AND WRITE FILES TO THIS DIRECTORY AND DO NOT TRY TO READ ANYTHING OUTSIDE THIS DIRECTORY
                step={step} # THE CURRENT STEP YOU ARE ON
                files={files} # THE FILES
                steps={steps} # THE MAX STEPS YOU ARE ALLOWED TO TAKE IF IT IS 1 YOU MUST DO IT IN ONE SHOT OR ELSE YOU WILL NOT BE ABLE TO REALIZE IT
                tool2schema={tool2schema} # THE TOOLS YOU ARE ALLOWED TO USE AND THEIR SCHEMAS
                memory={memory} # THE MEMORY OF THE AGENT
                output_format={output_format} YOUR OUTPUT MUST STRICTLY ADHERE TO THE OUTPUT FORMAT ABOVE
                --OUTPUT--
            """


    def forward(self, 
                text: str = 'make this like the base ', 
                *extra_text, 
                mod=None,
                temperature: float = 0.0, 
                max_tokens: int = 1000000, 
                stream: bool = True,
                verbose: bool = True,
                mode: str = 'auto', 
                model: Optional[str] = 'anthropic/claude-sonnet-4.5',
                steps = 1,
                path='./',
                safety=True,
                base = None,
                remote=False,

                trials=4,
                
                **kwargs) -> Dict[str, str]:
        
        """
        use this to run the agent with a specific text and parameters
        """
        if mod != None:
            path = m.dirpath(mod)
        text = ' '.join(list(map(str, [text] + list(extra_text))))
        query = self.preprocess(text=text)
        self.add_memory(self.tool('select_files')(path=path, query=query))
        if base:
            self.add_memory(m.content(base))
        for step in range(steps):
            prompt = self.prompt.format(
                goal=self.goal,
                output_format=self.output_format,
                tool2schema=self.tool2schema,
                memory=self.get_memory(),
                query=query,
                path=path,
                step=step,
                steps=steps,
                files = m.files(path),
            )            
            output = self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
            plan =  self.get_plan(output, safety=safety) 
            if self.is_plan_complete(plan):
                m.print("Plan is complete, stopping execution.", color='green')
                break
            else:
                m.print("Plan is not complete, continuing to next step.", color='yellow')
            self.add_memory(plan)
        return plan


    def is_plan_complete(self, plan: list) -> bool:
        return bool(plan[-1]['tool'].lower() == 'finish')

    def add_memory(self, *items):
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

    def preprocess(self, text, magic_prefix = f'@'):

        query = ''
        words = text.split(' ')
        fn_detected = False
        fns = []
        step = {}
        for i, word in enumerate(words):
            query += word + ' '
            prev_word = words[i-1] if i > 0 else ''
            # restrictions can currently only handle one fn argument, future support for multiple
            if (not fn_detected) and word.startswith(magic_prefix) :
                word = word[len(magic_prefix):]
                step = {'tool': m.fn(word), 'params': {}, 'idx': i + 2}
                fn_detected=True
            else:
                if fn_detected and '=' in word:
                    key, value = word.split('=')[0], '='.join(word.split('=')[1:])
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        m.print(f"Could not parse {value} as JSON, using string.", color='yellow')
                        continue
                    fns[-1]['params'][key] = value
                    query += str(step['fn'](**step['params']))
                else:
                    fn_detected = False

        return query


    anchors = {
            'plan': ['<PLAN>', '</PLAN>'],
            'tool': ['<STEP>', '</STEP>'],
        }

    def load_step(self, text):
        text = text.split(self.anchors['tool'][0])[1].split(self.anchors['tool'][1])[0]
        m.print("STEP:", text, color='yellow')
        try:
            step = json.loads(text)
        except json.JSONDecodeError as e:
            text = self.tool('fix_json')(text)
            step = json.loads(text)
        assert 'tool' in text
        assert 'params' in text
        return step

    def is_text_plan_step(self, text) -> bool:
        return bool(self.anchors['tool'][0] in text and self.anchors['tool'][1] in text)

    def get_plan(self, output:str, safety=True) -> list:
        text = ''
        plan = []
        for ch in output:
            text += ch
            m.print(ch, end='')
            if self.is_text_plan_step(text):
                plan.append(self.load_step(text))
                text = text.split(self.anchors['tool'][-1])[-1]

        m.print("Plan:", plan, color='yellow')
        if safety:
            input_text = input("Do you want to execute the plan? (y/Y) for YES: ")
            if not input_text in ['y', 'Y']:
                raise Exception("Plan execution aborted by user.")   
        for i,step in enumerate(plan):
            if step['tool'].lower()  in ['finish', 'review']:
                break
            else:
                result = self.tool(step['tool'])(**step['params'])
                plan[i]['result'] = result
                m.print(f"Step {i+1}/{len(plan)} executed: {step['tool']} with params {step['params']} -> result: {result}", color='green')
        return plan


    def set_tools(self, tools,  search=None, update=False) -> List[str]:
        result = []
        for tool in tools:
            if search and search not in tool:
                continue
            tool = self.tools_prefix + '.' + tool.replace('/', '.')
            result.append(tool)
        self.tools = tools
        self.tool2schema = {}
        for t in self.tools:
            try:
                self.tool2schema[t] = self.schema(t)
            except Exception as e:
                m.print(f"Error getting schema for tool {t}: {e}", color='red')
                continue
        m.print(f"Tools({tools})")
        return self.tools

    def schema(self, tool: str, fn='forward') -> Dict[str, str]:
        """
        Get the schema for a specific tool.
        """
        return  m.schema(self.tools_prefix + '.' +tool.replace('/', '.'))[fn]

    def tool(self, tool_name: str='cmd', *args, **kwargs) -> Any:
        """
        Execute a specific tool by name with provided arguments.
        """
        tool_name = tool_name.replace('/', '.')
        return m.mod(self.tools_prefix + '.' + tool_name)(*args, **kwargs).forward

    def test(self, query='make a python file that stores 2+2 in a variable and prints it', path='./', steps=3):
        """
        Test the Dev agent with a sample query and source directory.
        """
        result = self.forward(
            text=query,
            path=path,
            steps=steps,
            temperature=0.3,
            stream=True,
            verbose=True
        )
        return result