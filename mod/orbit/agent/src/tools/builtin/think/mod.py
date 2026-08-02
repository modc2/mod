"""think - reasoning scratchpad for the agent"""
from typing import Dict, Any


class Skill:
    description = "Think step-by-step. Use this to reason, plan, reflect, or work through problems before acting. No side effects."

    def forward(self, thought: str, **kwargs) -> Dict[str, Any]:
        """
        Record a thought. This tool has no side effects - it simply returns the thought.
        Use it to:
        - Break down complex problems
        - Plan a sequence of steps before executing
        - Reflect on results and decide next action
        - Reason about edge cases
        - Consider tradeoffs between approaches

        Args:
            thought: Your reasoning, plan, or reflection
        """
        return {
            "success": True,
            "thought": thought,
            "note": "Thought recorded. Proceed with your next action."
        }

    def test(self):
        r = self.forward("This is a test thought")
        assert r["success"] and r["thought"] == "This is a test thought"
        return True
