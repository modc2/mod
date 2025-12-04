
import time
import os
import json
import mod as c
from pathlib import Path
from typing import Dict, List, Union, Optional, Any, Tuple
from .utils import *
import mod as m
print=m.print
class Agent:


    def __init__(self, 
                 model: str = 'model.openrouter', 
                 memory = 'agent.memory',
                tools = ['websearch'],
                 **kwargs):

        self.tools_prefix = 'tool'
        self.model = m.mod(model)()
        self.memory = m.mod(memory)()



    def forward(self, 
                text: str = 'make this like the base ',  *extra_text, 
                model: Optional[str] = 'anthropic/claude-sonnet-4.5',
                topic = None,
                stream: bool = True,
                verbose: bool = True,
                steps = 3,
                tools = None,
                temperature: float = 0.0, 
                max_tokens: int = 1000000, 
                safety=False,
                **kwargs) -> Dict[str, str]:
        
        """
        use this to run the agent with a specific text and parameters
        """
        tools = tools if tools is not None else self.tools()
        query = self.preprocess(text, *extra_text)
        for step in range(steps):
            prompt = self.prepare_prompt(query=query, steps=steps, step=step, tools=tools)       
            output = self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
            plan =  self.get_plan(output, safety=safety) 
            if bool(plan[-1]['tool'].lower() == 'finish'):
                m.print("Agent has finished its plan.", color='green')
                prompt = 'given the prompt and the completed plan, provide a final concise answer to the user:\n' + prompt + f"\n completed plan: {plan}"
                return self.model.forward(prompt, stream=stream, model=model, max_tokens=max_tokens, temperature=temperature )
            self.memory.add_memory(plan)
        return plan


    def prepare_prompt(self, query: str = 'SAMPLE QUERY', steps: int = 3, step: int = 0, tools=None) -> str: 
        output_format=  """
                make sure the params is a legit json string within the STEP ANCHORS
                YOU CANNOT RESPOND WITH MULTIPLE PLANS BRO JUST ONE PLAN
                <PLAN>
                <STEP>JSON(tool:str, params:dict)</STEP> # STEP 1 
                <STEP>JSON(tool:str, params:dict)</STEP> # STEP 2
                <STEP>JSON(tool:finish, params:dict)</STEP> # FINAL STEP
                </PLAN>
        """

        goal = """
            - YOU WILL USE THE TOOLS TO COMPLETE THE QUERY OF THE USER
            - YOU MUST STRICTLY ADHERE TO THE OUTPUT FORMAT
            - YOU MUST USE THE TOOLS PROVIDED AND NOT MAKE UP YOUR OWN TOOLS
            - IF YOU CANNOT COMPLETE THE QUERY WITH THE TOOLS PROVIDED, USE THE finish TOOL
            - YOU DO NOT HAVE TO CHOOSE A TOOL AND CAN JUST PROVIDE THE FINAL ANSWER IF YOU THINK IT IS BEST
            - DO NOT FINISH UNTIL YOU ARE SURE YOU HAVE COMPLETED THE QUERY
        """
        memory = self.memory.get_memory()
        tool_schema = self.get_tool_schema(tools)

        prompt =  f"""
                --INPUTS--
                query={query} # THE QUERY YOU ARE TRYING TO ANSWER
                goal={goal} # THE GOAL YOU ARE TRYING TO ACHIEVE
                step={step} # THE CURRENT STEP YOU ARE ON
                steps={steps} # THE MAX STEPS YOU ARE ALLOWED TO TAKE IF IT IS 1 YOU MUST DO IT IN ONE SHOT OR ELSE YOU WILL NOT BE ABLE TO REALIZE IT
                tool_schema={tool_schema} # THE TOOLS YOU ARE ALLOWED TO USE AND THEIR SCHEMAS
                memory={memory} # THE MEMORY OF THE AGENT
                output_format={output_format} YOUR OUTPUT MUST STRICTLY ADHERE TO THE OUTPUT FORMAT ABOVE
                --OUTPUT--
            """
        return prompt


    def preprocess(self, text, *extra_text, magic_prefix = f'@'):

        
        text = ' '.join(list(map(str, [text] + list(extra_text))))
        query = ''
        words = text.split(' ')
        fn_detected = False
        fns = []
        step = {}
        fn_tool = None
        for i, word in enumerate(words):
            query += word + ' '
            prev_word = words[i-1] if i > 0 else ''
            # restrictions can currently only handle one fn argument, future support for multiple
            if (not fn_detected) and word.startswith(magic_prefix) :
                word = word[len(magic_prefix):]
                fn_tool = m.fn(word)
            else:
                if fn_tool is not None:
                    args = [word]
                    result = fn_tool(*args)
                    query += f"\n# Function {fn_tool.__name__} executed with args {args} -> result: {result}\n"
                    fn_tool = None

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
                print(plan[-1])
                text = text.split(self.anchors['tool'][-1])[-1]

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


    def get_tool_schema(self, tools=None, update=False) -> List[str]:
        if tools is None:
            tools = self.tools()
        result = []
        tool_schema = {}
        for t in tools:
            tool_schema[t] = self.schema(t)
        return tool_schema

    def schema(self, tool: str, fn='forward') -> Dict[str, str]:
        """
        Get the schema for a specific tool.
        """
        return  m.schema(self.tools_prefix + '.' +tool.replace('/', '.'), code=1)[fn]

    def tool(self, tool_name: str='cmd', *args, **kwargs) -> Any:
        """
        Execute a specific tool by name with provided arguments.
        """
        tool_name = tool_name.replace('/', '.')
        return m.mod(self.tools_prefix + '.' + tool_name)(*args, **kwargs).forward
    def tools(self, search=None) -> List[str]:
        """
        List all available tools for the agent.
        """
        mods =  m.mods(self.tools_prefix + '.')
        result = []
        for mod in mods:
            if search and search not in mod:
                continue
            result.append(mod.replace(self.tools_prefix + '.', '').replace('.', '/'))
        return result

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


    def edit(self,  query = 'make a an app in nextjs and make it look nice and make a dockerfile and docker compose in the app folder of the agent and assume the agent is wrapped by the server ', *extra_text):
        query = ' '.join(list(map(str, [query] + list(extra_text))))
        query += m.code('server') + m.code('client')
        return m.edit(query, mod='agent')
