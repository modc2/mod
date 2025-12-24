
from typing import Dict, List, Union, Optional, Any, Tuple
import mod as m
import json
class Skill:

    anchors = {
            'plan': ['<PLAN>', '</PLAN>'],
            'tool': ['<STEP>', '</STEP>'],
        }

    output_format=  f"""
            make sure the params is a legit json string within the STEP ANCHORS
            YOU CANNOT RESPOND WITH MULTIPLE PLANS BRO JUST ONE PLAN
            <PLAN>
            <STEP>JSON(tool:str, params:dict)</STEP> # STEP 1 
            <STEP>JSON(tool:str, params:dict)</STEP> # STEP 2
            <STEP>JSON(tool:finish, params:dict)</STEP> # FINAL STEP
            </PLAN>
    """

    goal = """
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

    def prepare(self, **params) -> str:


        """
        Generate a prompt for the Dev agent based on the current state and parameters.
        This prompt will be used to guide the agent's actions and decisions.
        It includes the query, goal, path, current step, available files, steps allowed,
        tool schemas, and memory of the agent.
        The output format is strictly defined to ensure consistency in the agent's responses.
        
        """

        params.update({
            'goal': self.goal,
            'output_format': self.output_format,
            'tools':  {t: m.schema(t) for t in params['tools']}
            })
        if len(params['tools']) == 0:
            return params['query']

        prompt =  """
                --INPUTS--
                query={query} # THE QUERY YOU ARE TRYING TO ANSWER
                goal={goal} # THE GOAL YOU ARE TRYING TO ACHIEVE
                tools={tools} # THE TOOLS YOU ARE ALLOWED TO USE AND THEIR SCHEMAS
                memory={memory} # THE MEMORY OF THE AGENT
                output_format={output_format} YOUR OUTPUT MUST STRICTLY ADHERE TO THE OUTPUT FORMAT ABOVE
                --OUTPUT--
            """.format(**params)

        return prompt

    def plan(self, text:str, safety=True) -> str:
        """
        Generate a plan based on the output text from the model.
        """
        plan = self.get_plan(text)
        plan = self.run_plan(plan, safety=safety)
        return plan

    def get_plan(self, output:str) -> list:
        """
        Parse the output text to extract the plan consisting of steps with tools and parameters.
        """


        def get_step(text):
            text = text.split(self.anchors['tool'][0])[1].split(self.anchors['tool'][1])[0]
            m.print("STEP:", text, color='yellow')
            try:
                step = json.loads(text)
            except json.JSONDecodeError as e:
                text = m.tool('fix_json')(text)
                step = json.loads(text)
            assert 'tool' in text
            assert 'params' in text
            return step

        text = ''
        plan = []
        for ch in output:
            text += ch
            m.print(ch, end='')
            if bool(self.anchors['tool'][0] in text and self.anchors['tool'][1] in text):
                step = get_step(text)
                plan.append(step)
                text = text.split(self.anchors['tool'][-1])[-1]
        return plan

    def run_plan(self, plan: List[Dict[str, Any]], safety=True) -> List[Dict[str, Any]]:
        """
        Execute a plan consisting of steps with tools and parameters.
        """
        if safety:
            input_text = input("Do you want to execute the plan? (y/Y) for YES: ")
            if not input_text in ['y', 'Y']:
                raise Exception("Plan execution aborted by user.")
        for i, step in enumerate(plan):
            if step['tool'].lower() in ['finish', 'review']:
                m.print(f"Step {i+1}/{len(plan)}: {step['tool']} with params {step['params']}", color='green')
                break
            else:
                result = m.tool(step['tool'])(**step['params'])
                plan[i]['result'] = result
                m.print(f"Step {i+1}/{len(plan)} executed: {step['tool']} with params {step['params']} -> result: {result}", color='green')
        if len(plan) > 0 and plan[-1]['tool'].lower() == 'finish':
            m.print("Plan is complete, stopping execution.", color='green')
        else:
            m.print("Plan is not complete, continuing to next step.", color='yellow')
        return plan
