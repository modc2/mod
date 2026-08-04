"""
toolbox - named bundles of tools that snap onto the agent

A Toolbox is a curated set of tools. Toolboxes snap onto an Agent: the agent's
active tool set — and the tool schema shown to the LLM — becomes the union of
everything snapped on. Built-in presets cover the common loadouts; custom
boxes persist off-tree in ~/.mod/agent/toolboxes.json. A box can bundle any
kind of tool, so `["read", "mod.git"]` is a valid loadout.

Usage:
    boxes = Toolboxes(tools=Tools())
    boxes.ls()                            # all box names
    boxes.get("code")                     # a Toolbox instance
    boxes.resolve(["explore", "code"])    # ordered union of tool names
    boxes.add("mybox", ["bash", "git"])   # custom box (persisted)
    boxes.rm("mybox")
"""
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

# built-in presets — protected from remove/update; clone into a custom box instead
BUILTINS = {
    'core':    {'description': 'Everyday essentials: shell, files, planning',
                'tools': ['bash', 'read', 'write', 'edit', 'think', 'todo']},
    'explore': {'description': 'Understand a codebase before touching it',
                'tools': ['tree', 'glob', 'grep', 'search', 'symbols', 'context', 'read']},
    'code':    {'description': 'Surgical code changes',
                'tools': ['read', 'edit', 'patch', 'write', 'refactor', 'symbols', 'diff']},
    'verify':  {'description': 'Prove the change works',
                'tools': ['test', 'lint', 'diff', 'debug', 'read']},
    'vcs':     {'description': 'Version control',
                'tools': ['git', 'diff']},
    'web':     {'description': 'Reach outside the sandbox',
                'tools': ['fetch', 'websurf']},
    'meta':    {'description': 'Reasoning, task tracking, delegation',
                'tools': ['think', 'todo', 'task', 'claudecode']},
}


class Toolbox:
    """A named bundle of tools. Snaps onto an Agent as one unit."""

    def __init__(self, name: str, tools: List[str], description: str = '',
                 builtin: bool = False, cid: str = None):
        self.name = name
        self.tools = list(dict.fromkeys(tools))  # de-dupe, keep order
        self.description = description
        self.builtin = builtin
        self.cid = cid

    def resolve(self, known=None) -> Dict[str, List[str]]:
        """Split tools into those the registry knows and those missing.

        `known` is either a registry with .ls() or a plain list of names — the
        latter lets a caller pass the whole tool surface as one set.
        """
        if known is None:
            return {'tools': self.tools, 'missing': []}
        available = set(known if isinstance(known, (list, tuple, set)) else known.ls())
        return {
            'tools': [t for t in self.tools if t in available],
            'missing': [t for t in self.tools if t not in available],
        }

    def schema(self, registry) -> Dict[str, Dict]:
        """LLM tool schemas for just this box's tools."""
        return registry.schema(self.resolve(registry)['tools'])

    def to_dict(self) -> Dict[str, Any]:
        return {'name': self.name, 'description': self.description,
                'tools': self.tools, 'builtin': self.builtin, 'cid': self.cid}


class Toolboxes:
    """Toolbox registry — built-in presets + custom boxes persisted off-tree.

    Custom boxes are private local state and live under ~/.mod/agent/
    (never in the committed module dir), same as the ACL and vault.
    """
    description = "Toolbox registry - named tool bundles that snap onto the agent"

    def __init__(self, tools=None, path: str = None, **kwargs):
        self.tools = tools  # the tool registry a box's names are checked against
        if path is None:
            state_dir = Path.home() / '.mod' / 'agent'
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                path = state_dir / 'toolboxes.json'
            except Exception:
                path = Path(__file__).parent / '.toolboxes.json'
        self._path = Path(path)
        self._custom = self._load()

    def _load(self) -> Dict[str, Dict]:
        try:
            if self._path.exists():
                with open(self._path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save(self):
        with open(self._path, 'w') as f:
            json.dump(self._custom, f, indent=2)

    def ls(self) -> List[str]:
        """All box names — built-ins first, then custom."""
        return list(BUILTINS.keys()) + sorted(self._custom.keys())

    def known(self) -> List[str]:
        """Every tool a box may reference — the fleet included, since a box is
        exactly how you keep a handful of modules on tap."""
        return list(self.tools.ls(mods=True)) if self.tools is not None else []

    def exists(self, name: str) -> bool:
        return name in BUILTINS or name in self._custom

    def get(self, name: str) -> Toolbox:
        if name in BUILTINS:
            b = BUILTINS[name]
            return Toolbox(name, b['tools'], b['description'], builtin=True)
        if name in self._custom:
            c = self._custom[name]
            return Toolbox(name, c.get('tools', []), c.get('description', ''),
                           cid=c.get('cid'))
        raise KeyError(f"toolbox not found: {name}")

    def add(self, name: str, tools: List[str], description: str = '') -> Dict[str, Any]:
        """Create or update a custom toolbox. Built-ins are read-only."""
        name = name.lower().replace(' ', '-')
        if name in BUILTINS:
            raise PermissionError(f"cannot overwrite built-in toolbox: {name}")
        if not tools:
            raise ValueError("a toolbox needs at least one tool")
        if self.tools is not None:
            unknown = [t for t in tools if not self.tools.exists(t)]
            if unknown:
                raise ValueError(f"unknown tools: {unknown}")
        entry = {'tools': list(dict.fromkeys(tools)), 'description': description}
        entry['cid'] = self._pin(name, entry)
        self._custom[name] = entry
        self._save()
        return self.get(name).to_dict()

    @staticmethod
    def _pin(name: str, entry: Dict) -> Optional[str]:
        """Best-effort pin of a toolbox bundle to localfs (content-addressed)."""
        try:
            import mod as m
            return m.mod('localfs')().put({'type': 'toolbox', 'name': name,
                                           'tools': entry['tools'],
                                           'description': entry.get('description', '')})
        except Exception:
            return None

    def rm(self, name: str) -> Dict[str, Any]:
        if name in BUILTINS:
            raise PermissionError(f"cannot remove built-in toolbox: {name}")
        removed = self._custom.pop(name, None)
        self._save()
        return {'removed': name, 'existed': removed is not None}

    def resolve(self, names) -> List[str]:
        """Ordered de-duped union of tools across one or more boxes."""
        if isinstance(names, str):
            names = [names]
        tools = []
        for name in names:
            for t in self.get(name).tools:
                if t not in tools:
                    tools.append(t)
        return tools

    def schema(self, names) -> Dict[str, Dict]:
        """LLM tool schemas for the union of the given boxes."""
        if self.tools is None:
            raise RuntimeError("no tool registry attached")
        return self.tools.schema(self.resolve(names))

    def items(self) -> List[Dict[str, Any]]:
        return [self.get(n).to_dict() for n in self.ls()]

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                          -> list all toolboxes
        forward("code")                    -> get one toolbox
        forward(action="add", name=..., tools=[...]) -> create custom box
        forward(action="rm", name=...)     -> remove custom box
        forward(action="resolve", names=[...]) -> union tool list
        """
        action = kwargs.get('action')
        if action == 'add':
            return self.add(kwargs.get('name', name or ''),
                            kwargs.get('tools', []),
                            kwargs.get('description', ''))
        if action == 'rm':
            return self.rm(kwargs.get('name', name or ''))
        if action == 'resolve':
            return {'tools': self.resolve(kwargs.get('names', name or []))}
        if name is None:
            return {'toolboxes': self.items(), 'total': len(self.ls())}
        return self.get(name).to_dict()

    def test(self) -> Dict[str, Any]:
        assert 'core' in self.ls()
        box = self.get('core')
        assert 'bash' in box.tools
        union = self.resolve(['core', 'vcs'])
        assert 'git' in union and 'bash' in union
        assert len(union) == len(set(union))
        return {'passed': True, 'boxes': len(self.ls())}
