"""
skills - agent skill registry

Auto-discovers skill modules in this directory.
Each skill is a directory with a mod.py containing a Skill class.
Every Skill class has: description, forward(**kwargs), test()

Usage:
    skills = Skills()
    skills.ls()                          # list all skills
    skills.get("bash")                   # get a skill instance
    skills.run("bash", command="ls")     # run a skill
    skills.schema()                      # get all schemas for LLM
    skills.forward("bash", command="ls") # alias for run
"""
import inspect
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional


class Skills:
    description = "Skill registry - discover, load, and run agent skills"

    def __init__(self, **kwargs):
        self._dir = Path(__file__).parent
        self._cache = {}

    def ls(self) -> List[str]:
        """List available skill names"""
        return sorted([
            d.name for d in self._dir.iterdir()
            if d.is_dir() and not d.name.startswith("_") and (d / "mod.py").exists()
        ])

    def get(self, name: str):
        """Get a skill instance by name"""
        if name in self._cache:
            return self._cache[name]
        mod_path = self._dir / name / "mod.py"
        if not mod_path.exists():
            raise KeyError(f"skill not found: {name}")
        spec = importlib.util.spec_from_file_location(f"skills.{name}", str(mod_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Skill", None)
        if cls is None:
            raise AttributeError(f"no Skill class in {name}/mod.py")
        instance = cls()
        self._cache[name] = instance
        return instance

    def run(self, name: str, **kwargs) -> Dict[str, Any]:
        """Run a skill by name with kwargs"""
        return self.get(name).forward(**kwargs)

    def forward(self, name: str = None, **kwargs) -> Any:
        """Run a skill (mod protocol entry point)"""
        if name is None:
            return {"skills": self.ls(), "total": len(self.ls())}
        return self.run(name, **kwargs)

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Get schemas for skills (for LLM tool descriptions)"""
        names = names or self.ls()
        out = {}
        for name in names:
            try:
                skill = self.get(name)
                sig = inspect.signature(skill.forward)
                params = {}
                for pname, p in sig.parameters.items():
                    if pname in ("self", "kwargs"):
                        continue
                    params[pname] = {
                        "type": str(p.annotation) if p.annotation != inspect.Parameter.empty else "Any",
                        "required": p.default == inspect.Parameter.empty,
                    }
                    if p.default != inspect.Parameter.empty:
                        params[pname]["default"] = p.default
                out[name] = {"description": skill.description, "params": params}
            except Exception as e:
                out[name] = {"error": str(e)}
        return out

    def test(self) -> Dict[str, Any]:
        """Test all skills"""
        results = {}
        for name in self.ls():
            try:
                skill = self.get(name)
                results[name] = skill.test()
            except Exception as e:
                results[name] = {"error": str(e)}
        passed = sum(1 for v in results.values() if v is True or (isinstance(v, dict) and v.get("success")))
        return {"passed": passed, "total": len(results), "results": results}
