"""
tools - the one registry the agent holds

Six kinds of tool land in the same namespace, and the model can't tell them
apart — it sees one list of names, descriptions and params:

    builtin/  the tools shipped in this repo   (bash, read, edit, git …)
    custom/   shell templates typed into the console
    mods/     every module in the fleet        (mod.git, mod.chain …)
    mcp/      every fleet MCP server's tools   (mcp.build.list_modules …)
    x402/     paid x402 services, mounted      (x402.automatom-forecast …)
    agents/   every agent, as one call         (agent.reviewer …)

The fleet kinds are the odd ones out: there are hundreds of modules and
hundreds more MCP tools, so putting them all in one prompt would drown the
actual job. They are *potential* tools — in the registry, off the default
loadout, snapped on by name or by toolbox when a run needs them. `mod.git`
is the generic in-process door (one fn/params convention); `mcp.dns.…` is
the richer one — each MCP tool carries its own real argument schema.

Usage:
    tools = Tools()
    tools.ls()                          # built-ins + custom (the default loadout)
    tools.ls(mods=True)                 # + the whole fleet, both doors
    tools.run('bash', command='ls')
    tools.run('mod.git', fn='status')
    tools.run('mcp.dns.dns_resolve', name='agent')
    tools.schema(['read', 'mcp.dns.dns_resolve'])   # what the LLM is handed
"""
from typing import Any, Dict, List, Optional

from .builtin.mod import Builtins
from .custom.mod import CustomTools
from .mods.mod import ModTools, PREFIX as MOD_PREFIX
from .mcp.mod import McpTools, PREFIX as MCP_PREFIX
from .x402.mod import X402Tools, PREFIX as X402_PREFIX
from .agents.mod import AgentTools, PREFIX as AGENT_PREFIX

FLEET_PREFIXES = (MOD_PREFIX, MCP_PREFIX, X402_PREFIX, AGENT_PREFIX)


class Tools:
    """Built-in tools, custom shell tools and the fleet, as one registry."""
    description = "Tool registry - built-in tools, custom shell tools, and the fleet"

    def __init__(self, path: str = None, context=None, **kwargs):
        self.builtin = Builtins(context=context)
        self.custom = CustomTools(builtin=self.builtin, path=path)
        self.mods = ModTools()
        self.mcp = McpTools()
        self.x402 = X402Tools(mcp=self.mcp)
        self.agents = AgentTools(context=context, tools=self)
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
        self.agents.bind(context)
        return self

    # ── names ────────────────────────────────────────────────────────

    def ls(self, mods: bool = False) -> List[str]:
        """Every callable name: built-ins, then custom, then (opt-in) the
        fleet — both doors, module objects and MCP tools."""
        names = self.builtin.ls() + [t for t in self.custom.ls()
                                     if t not in self.builtin.ls()]
        if not mods:
            return names
        return names + self.mods.ls() + self.mcp.ls() + self.x402.ls() + \
            self.agents.ls()

    def kind(self, name: str) -> Optional[str]:
        """'builtin' | 'custom' | 'mod' | 'mcp' | 'x402' | 'agent', or None
        if nothing answers to that name."""
        if name in self.builtin.ls():
            return 'builtin'
        if self.custom.exists(name):
            return 'custom'
        if self.mods.exists(name):
            return 'mod'
        if self.mcp.exists(name):
            return 'mcp'
        if self.x402.exists(name):
            return 'x402'
        if self.agents.exists(name):
            return 'agent'
        return None

    def exists(self, name: str) -> bool:
        return self.kind(name) is not None

    def is_shell(self, name: str) -> bool:
        """Tools that run a shell command — the ones a sandbox has to pin to a cwd."""
        return name == 'bash' or self.custom.exists(name)

    def is_mod(self, name: str) -> bool:
        return name.startswith(MOD_PREFIX)

    def is_mcp(self, name: str) -> bool:
        return name.startswith(MCP_PREFIX)

    def is_x402(self, name: str) -> bool:
        return name.startswith(X402_PREFIX)

    def is_agent(self, name: str) -> bool:
        return name.startswith(AGENT_PREFIX)

    def registry(self, name: str):
        """The sub-registry a fleet-kind name belongs to (mods/mcp/x402/
        agents), or None for builtin/custom."""
        return {'mod': self.mods, 'mcp': self.mcp, 'x402': self.x402,
                'agent': self.agents}.get(self.kind(name))

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
        if kind == 'mcp':
            return self.mcp.get(name)
        if kind == 'x402':
            return self.x402.get(name)
        if kind == 'agent':
            return self.agents.get(name)
        raise KeyError(f"tool not found: {name}")

    def run(self, name: str, /, **params) -> Any:
        """Run any tool by name, whichever registry it came from.

        `name` is positional-only: an MCP tool's own argument is allowed to
        be called `name` too, and it has to land in params, not here.
        """
        kind = self.kind(name)
        if kind == 'custom':
            return self.custom.run(name, **params)
        if kind == 'mod':
            return self.mods.run(name, **params)
        if kind == 'mcp':
            return self.mcp.run(name, **params)
        if kind == 'x402':
            return self.x402.run(name, **params)
        if kind == 'agent':
            return self.agents.run(name, **params)
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
        schema.update(self.mcp.schema([n for n in names if self.mcp.exists(n)]))
        schema.update(self.x402.schema([n for n in names if self.x402.exists(n)]))
        schema.update(self.agents.schema([n for n in names if self.agents.exists(n)]))
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
            out += [{**e, 'builtin': False,
                     'params': self.mcp.schema([e['name']])
                                       .get(e['name'], {}).get('params', {})}
                    for e in self.mcp.items(q, limit)]
            for reg in (self.x402, self.agents):
                schemas_k = reg.schema([e['name'] for e in reg.items(q, limit)])
                out += [{**e, 'builtin': False,
                         'params': schemas_k.get(e['name'], {}).get('params', {})}
                        for e in reg.items(q, limit)]
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
        assert 'bash' in schema and not any(
            n.startswith(FLEET_PREFIXES) for n in schema)
        assert 'bash' in [t['name'] for t in self.items(q='bash')]
        assert not self.items(q='no-such-tool-anywhere')
        fleet = self.mods.ls()
        if fleet:
            assert fleet[0] in self.ls(mods=True)
            assert list(self.schema([fleet[0]])) == [fleet[0]]
            assert self.kind(fleet[0]) == 'mod' and self.is_mod(fleet[0])
            # the fleet is opt-in: off the default listing and the default schema
            assert fleet[0] not in [t['name'] for t in self.items()]
        served = self.mcp.ls()
        if served:
            assert self.kind(served[0]) == 'mcp' and self.is_mcp(served[0])
            assert list(self.schema([served[0]])) == [served[0]]
            assert served[0] not in [t['name'] for t in self.items()]
        agents = self.agents.ls()
        if agents:
            assert self.kind(agents[0]) == 'agent'
            assert agents[0] not in [t['name'] for t in self.items()]
        return {'passed': True, 'tools': len(names), 'fleet': len(fleet),
                'mcp': len(served), 'x402': len(self.x402.ls()),
                'agents': len(agents)}
