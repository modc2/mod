"""
builtin - the tools that ship with this module

Auto-discovers tool modules in this directory. Each one is a directory with a
mod.py containing a Tool class: description, forward(**kwargs), test().

These are the deployed third of the registry. The other two live next door:
custom/ (shell templates typed into the console) and mods/ (the fleet). All
three are unioned by tools/mod.py, which is what the agent actually holds.

Usage:
    builtin = Builtins()
    builtin.ls()                          # list all tool names
    builtin.get("bash")                   # get a tool instance
    builtin.run("bash", command="ls")     # run one
    builtin.schema()                      # schemas for the LLM
"""
import inspect
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional


class Builtins:
    description = "Built-in tool registry - discover, load, and run the shipped tools"

    def __init__(self, **kwargs):
        self._dir = Path(__file__).parent
        self._cache = {}

    def ls(self) -> List[str]:
        """List available tool names"""
        return sorted([
            d.name for d in self._dir.iterdir()
            if d.is_dir() and not d.name.startswith("_") and (d / "mod.py").exists()
        ])

    def get(self, name: str):
        """Get a tool instance by name"""
        if name in self._cache:
            return self._cache[name]
        mod_path = self._dir / name / "mod.py"
        if not mod_path.exists():
            raise KeyError(f"tool not found: {name}")
        spec = importlib.util.spec_from_file_location(f"tools.builtin.{name}", str(mod_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Tool", None)
        if cls is None:
            raise AttributeError(f"no Tool class in {name}/mod.py")
        instance = cls()
        self._cache[name] = instance
        return instance

    def run(self, name: str, **kwargs) -> Dict[str, Any]:
        """Run a tool by name with kwargs"""
        return self.get(name).forward(**kwargs)

    def forward(self, name: str = None, **kwargs) -> Any:
        """Run a tool (mod protocol entry point)"""
        if name is None:
            return {"tools": self.ls(), "total": len(self.ls())}
        return self.run(name, **kwargs)

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Get schemas for tools (for LLM tool descriptions).

        None means every built-in; an empty list means none of them — a
        loadout of nothing but fleet modules must not hand back all 23.
        """
        names = self.ls() if names is None else names
        out = {}
        for name in names:
            try:
                tool = self.get(name)
                sig = inspect.signature(tool.forward)
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
                out[name] = {"description": tool.description, "params": params}
            except Exception as e:
                out[name] = {"error": str(e)}
        return out

    def test(self) -> Dict[str, Any]:
        """Test all tools"""
        results = {}
        for name in self.ls():
            try:
                tool = self.get(name)
                results[name] = tool.test()
            except Exception as e:
                results[name] = {"error": str(e)}
        passed = sum(1 for v in results.values() if v is True or (isinstance(v, dict) and v.get("success")))
        return {"passed": passed, "total": len(results), "results": results}
