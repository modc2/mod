"""task - spawn a sub-agent for complex work"""
from typing import Dict, Any, Optional

class Skill:
    description = "Spawn a sub-agent to handle a complex task"

    def forward(self, prompt: str, agent_type: str = "general", model: str = "sonnet", max_steps: int = 10, **kwargs) -> Dict[str, Any]:
        """Launch a sub-agent with its own context"""
        try:
            import mod as m
            agent = m.mod("agent")()
            types = {
                "explore": "You are a codebase exploration specialist. Search, read, analyze.",
                "plan": "You are a software architect. Design implementation plans.",
                "bash": "You are a command execution specialist. Run commands safely.",
                "general": "You are a general-purpose agent. Research and execute.",
            }
            if agent_type not in types:
                return {"success": False, "error": f"unknown type: {agent_type}. use: {list(types.keys())}"}
            result = agent.forward(query=prompt, goal=types[agent_type], steps=max_steps, model=model)
            return {"success": True, "result": str(result), "agent_type": agent_type}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test(self):
        return True  # requires full agent setup
