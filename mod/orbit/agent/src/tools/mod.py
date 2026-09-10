"""
tools - the one registry the agent holds

Three kinds of tool land in the same namespace, and the model can't tell them
apart — it sees one list of names, descriptions and params:

    builtin/  the tools shipped in this repo   (bash, read, edit, git …)
    custom/   shell templates typed into the console
    mods/     every module in the fleet        (mod.git, mod.chain …)

The fleet is the odd one out: there are hundreds of modules, so putting them
all in one prompt would drown the actual job. They are *potential* tools —
in the registry, off the default loadout, snapped on by name or by toolbox
when a run needs them.

Usage:
    tools = Tools()
    tools.ls()                          # built-ins + custom (the default loadout)
    tools.ls(mods=True)                 # + the whole fleet
    tools.run('bash', command='ls')
    tools.run('mod.git', fn='status')
    tools.schema(['read', 'mod.git'])   # what the LLM is handed
"""
from typing import Any, Dict, List, Optional

from .builtin.mod import Builtins
from .custom.mod import CustomTools
from .mods.mod import ModTools, PREFIX as MOD_PREFIX


class Tools:
    """Built-in tools, custom shell tools and the fleet, as one registry."""
    description = "Tool registry - built-in tools, custom shell tools, and the fleet"

    def __init__(self, path: str = None, context=None, **kwargs):
        self.builtin = Builtins(context=context)
        self.custom = CustomTools(builtin=self.builtin, path=path)
        self.mods = ModTools()
        self.context = context

    def bind(self, context) -> "Tools":
        """Attach the agent these tools belong to.

        A tool that acts on one of the agent's own sub-components — recall and
        remember on its memory module, toolbox on its loadout — has to reach
        the live one. Binding is how the box hands itself to the tools inside
        it, and it is why those three are tools at all rather than API calls.
        """
        self.context = context
        self.builtin.bind(context)
        return self

    # ── names ────────────────────────────────────────────────────────

    def ls(self, mods: bool = False) -> List[str]:
        """Every callable name: built-ins, then custom, then (opt-in) the fleet."""
        names = self.builtin.ls() + [t for t in self.custom.ls()
                                     if t not in self.builtin.ls()]
        return names + self.mods.ls() if mods else names

    def kind(self, name: str) -> Optional[str]:
        """'builtin' | 'custom' | 'mod', or None if nothing answers to that name."""
        if name in self.builtin.ls():
            return 'builtin'
        if self.custom.exists(name):
            return 'custom'
        if self.mods.exists(name):
            return 'mod'
        return None

    def exists(self, name: str) -> bool:
        return self.kind(name) is not None

    def is_shell(self, name: str) -> bool:
        """Tools that run a shell command — the ones a sandbox has to pin to a cwd."""
        return name == 'bash' or self.custom.exists(name)

    def is_mod(self, name: str) -> bool:
        return name.startswith(MOD_PREFIX)

    # ── one tool ─────────────────────────────────────────────────────

    def get(self, name: str):
        """The built-in instance, else the custom/mod entry as a dict."""
        kind = self.kind(name)
        if kind == 'builtin':
            return self.builtin.get(name)
        if kind == 'custom':
            return self.custom.get(name).to_dict()
        if kind == 'mod':
            return self.mods.get(name)
        raise KeyError(f"tool not found: {name}")

    def run(self, name: str, **params) -> Any:
        """Run any tool by name, whichever registry it came from."""
        kind = self.kind(name)
        if kind == 'custom':
            return self.custom.run(name, **params)
        if kind == 'mod':
            return self.mods.run(name, **params)
        return self.builtin.run(name, **params)

    # ── writing (custom tools only — the other two are code) ─────────
    # A built-in ships in the repo and a mod is its own module, so the only
    # tool this registry can create is a custom one. Delegated here so the
    # console and the mod protocol talk to one object, not three.

    def add(self, name: str, command: str, description: str = '',
            params: Dict[str, Dict] = None, cwd: str = None, timeout: int = 60,
            owner: str = None) -> Dict[str, Any]:
        return self.custom.add(name, command, description, params, cwd,
                               timeout, owner)

    def rm(self, name: str) -> Dict[str, Any]:
        return self.custom.rm(name)

    def owner_of(self, name: str) -> Optional[str]:
        return self.custom.owner_of(name)

    # ── the LLM view ─────────────────────────────────────────────────

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Schemas for the given tools, or for the default loadout.

        Default leaves the fleet out — a name has to be asked for by hand (or
        by toolbox) before a module reaches the prompt.
        """
        if names is None:
            schema = self.builtin.schema()
            schema.update(self.custom.schema())
            return schema
        schema = self.builtin.schema([n for n in names if n in self.builtin.ls()])
        schema.update(self.custom.schema(names))
        schema.update(self.mods.schema([n for n in names if self.mods.exists(n)]))
        return schema

    def items(self, mods: bool = False, q: str = '',
              limit: int = None) -> List[Dict[str, Any]]:
        """Console listing: one dict per tool, with its kind.

        `q` narrows every kind by name and description — the fleet alone is
        three hundred entries, so the console searches the server, not a list
        it downloaded.
        """
        def matches(name: str, description: str) -> bool:
            return not q or q in name.lower() or q in (description or '').lower()

        q = (q or '').strip().lower()
        schemas = self.builtin.schema()
        out = [{'name': n, 'kind': 'builtin', 'builtin': True,
                'description': schemas.get(n, {}).get('description', ''),
                'params': schemas.get(n, {}).get('params', {})}
               for n in self.builtin.ls()
               if matches(n, schemas.get(n, {}).get('description', ''))]
        out += [{**t, 'kind': 'custom', 'builtin': False,
                 'params': self.custom.get(t['name']).schema()['params']}
                for t in self.custom.items()
                if matches(t['name'], t.get('description', '')) or
                   (q and q in (t.get('command') or '').lower())]
        if mods:
            mod_schemas = self.mods.schema()
            out += [{**e, 'builtin': False,
                     'description': mod_schemas[e['name']]['description'],
                     'params': mod_schemas[e['name']]['params']}
                    for e in self.mods.items(q, limit)]
        return out

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                    -> the whole registry
        forward("bash")              -> one tool
        forward(action="run", …)     -> run one
        """
        if kwargs.get('action') == 'run':
            return self.run(kwargs.get('name', name or ''),
                            **(kwargs.get('params') or {}))
        if name is None:
            items = self.items(mods=bool(kwargs.get('mods')), q=kwargs.get('q', ''),
                               limit=kwargs.get('limit'))
            return {'tools': items, 'total': len(items)}
        return self.get(name)

    def test(self) -> Dict[str, Any]:
        names = self.ls()
        assert 'bash' in names and 'read' in names, names[:5]
        assert self.kind('bash') == 'builtin'
        assert self.kind('nope-not-a-tool') is None
        schema = self.schema()
        assert 'bash' in schema and not any(n.startswith(MOD_PREFIX) for n in schema)
        assert 'bash' in [t['name'] for t in self.items(q='bash')]
        assert not self.items(q='no-such-tool-anywhere')
        fleet = self.mods.ls()
        if fleet:
            assert fleet[0] in self.ls(mods=True)
            assert list(self.schema([fleet[0]])) == [fleet[0]]
            assert self.kind(fleet[0]) == 'mod' and self.is_mod(fleet[0])
            # the fleet is opt-in: off the default listing and the default schema
            assert fleet[0] not in [t['name'] for t in self.items()]
            assert len(self.items(mods=True, limit=3)) == len(self.items()) + 3
        return {'passed': True, 'tools': len(names), 'fleet': len(fleet)}
