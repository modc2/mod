"""todo - task tracking for agent sessions"""
from typing import Dict, Any, List, Optional


class Skill:
    description = "Track tasks during a session. Add, complete, list, and manage a task list to stay organized."

    _tasks = []

    def forward(self, action: str = "list", task: str = None, index: int = None, **kwargs) -> Dict[str, Any]:
        """
        Manage a task list.

        Args:
            action: One of: add, complete, remove, list, clear, progress
            task: Task description (for 'add' action)
            index: Task index (for 'complete' or 'remove' actions, 0-based)
        """
        if action == "add":
            if not task:
                return {"success": False, "error": "task description required"}
            self._tasks.append({"task": task, "done": False})
            return {"success": True, "index": len(self._tasks) - 1, "task": task, "total": len(self._tasks)}

        elif action == "complete":
            if index is None:
                return {"success": False, "error": "index required"}
            if index < 0 or index >= len(self._tasks):
                return {"success": False, "error": f"invalid index: {index}, have {len(self._tasks)} tasks"}
            self._tasks[index]["done"] = True
            return {"success": True, "completed": self._tasks[index]["task"]}

        elif action == "remove":
            if index is None:
                return {"success": False, "error": "index required"}
            if index < 0 or index >= len(self._tasks):
                return {"success": False, "error": f"invalid index: {index}"}
            removed = self._tasks.pop(index)
            return {"success": True, "removed": removed["task"]}

        elif action == "list":
            return {
                "success": True,
                "tasks": [{"index": i, **t} for i, t in enumerate(self._tasks)],
                "total": len(self._tasks),
                "done": sum(1 for t in self._tasks if t["done"]),
                "remaining": sum(1 for t in self._tasks if not t["done"]),
            }

        elif action == "progress":
            total = len(self._tasks)
            done = sum(1 for t in self._tasks if t["done"])
            pct = (done / total * 100) if total else 0
            return {"success": True, "done": done, "total": total, "percent": round(pct, 1)}

        elif action == "clear":
            count = len(self._tasks)
            self._tasks.clear()
            return {"success": True, "cleared": count}

        return {"success": False, "error": f"unknown action: {action}. use: add, complete, remove, list, clear, progress"}

    def test(self):
        self._tasks.clear()
        r = self.forward("add", task="test task")
        assert r["success"]
        r = self.forward("complete", index=0)
        assert r["success"]
        r = self.forward("list")
        assert r["total"] == 1 and r["done"] == 1
        self._tasks.clear()
        return True
