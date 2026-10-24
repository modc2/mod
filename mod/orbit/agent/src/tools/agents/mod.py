"""
agents - every agent in the registry, as a tool

The other direction of "agents are tools": a tool-agent (agents/<name> with
`tool = "x402.…"`) wraps one tool in a persona; this registry hands every
agent to the others as `agent.<name>`, one call that runs that agent whole
and returns its answer. An agent's loadout can therefore hold builtins, fleet
modules, MCP tools, paid x402 services AND other agents in one list, and the
model can't tell (or care) which is which.

Same rules as the fleet kinds: potential tools (off the default loadout,
snapped on by name or toolbox), host-only in sandboxed runs. A delegated run
inherits the caller's key, runs on its own thread (the run state is
thread-local, see Mod._run_local), and nesting is capped so two agents that
list each other can't recurse forever.

A TOOL-AGENT (`tool = "x402.…"`, "mcp.…", any tool) is both at once: its
schema is its tool's own params plus an optional `task`. Called with the
params it is just the tool — one call, no model, nothing billed but the
tool; called with a `task` it is an agent that holds that tool and works
out the arguments itself. Every mounted x402 service gets one of these
(agents/x402-<slug>), so the paid-API world reaches the model as tools AND
agents through the same names.

Usage:
    tools.run('agent.reviewer', task='review src/api/api.py')
    tools.run('agent.x402-automatom-forecast', lat='40.7', lon='-74')  # = the tool
    tools.run('agent.x402-automatom-forecast', task='weather in NYC?')  # = an agent
"""
import json
import threading
from typing import Any, Dict, List, Optional

PREFIX = 'agent.'
MAX_ACTIVE = 4              # delegated runs in flight at once, all callers
DEFAULT_STEPS = 15
TIMEOUT = 900


class AgentTools:
    """The agent registry, callable — `agent.<name>`."""
    description = "Agents as tools - every agent, callable by the others"

    _active = 0
    _guard = threading.Lock()

    def __init__(self, context=None, tools=None, **kwargs):
        self.context = context
        self._tools = tools            # the union registry — a tool-agent's tool
        self._registry = None

    def bind(self, context) -> "AgentTools":
        self.context = context
        return self

    def _agents(self):
        reg = getattr(self.context, 'agents', None)
        if reg is not None:
            return reg
        if self._registry is None:
            from ...agents.mod import Agents
            self._registry = Agents()
        return self._registry

    def _cfg(self, name: str) -> Optional[Dict]:
        slug = name[len(PREFIX):] if name.startswith(PREFIX) else name
        try:
            return self._agents().get(slug)
        except Exception:
            low = slug.lower()   # the loop lowercases tool names
            match = next((n for n in self._agents().ls() if n.lower() == low), None)
            return self._agents().get(match) if match else None

    def _slug(self, name: str) -> str:
        slug = name[len(PREFIX):] if name.startswith(PREFIX) else name
        if slug in self._agents().ls():
            return slug
        low = slug.lower()
        return next((n for n in self._agents().ls() if n.lower() == low), slug)

    # ── registry surface ─────────────────────────────────────────────

    def ls(self) -> List[str]:
        try:
            return [PREFIX + n for n in self._agents().ls()]
        except Exception:
            return []

    def exists(self, name: str) -> bool:
        return name.startswith(PREFIX) and self._cfg(name) is not None

    def get(self, name: str) -> Dict[str, Any]:
        cfg = self._cfg(name)
        if cfg is None:
            raise KeyError(f"agent not found: {name}")
        return {'name': PREFIX + self._slug(name), 'kind': 'agent',
                'description': self._describe(cfg),
                'tool': cfg.get('tool'), 'harness': cfg.get('harness')}

    @staticmethod
    def _describe(cfg: Dict) -> str:
        what = cfg.get('description') or (cfg.get('goal') or '')[:160]
        how = (f"tool-agent over {cfg['tool']}: pass its params to call it "
               f"directly, or a task to have the agent work it out"
               if cfg.get('tool') else
               f"runs the {cfg['harness']} CLI" if cfg.get('harness') else
               "a feedback loop" if cfg.get('loop') else "a full agent run")
        return f"Delegate to the {cfg.get('name')} agent — {what} [agent · {how}]"

    def items(self, q: str = '', limit: int = None) -> List[Dict[str, Any]]:
        q = (q or '').strip().lower()
        out = []
        for name in self.ls():
            cfg = self._cfg(name)
            if cfg is None:
                continue
            desc = self._describe(cfg)
            if q and q not in name.lower() and q not in desc.lower():
                continue
            out.append({'name': name, 'kind': 'agent', 'description': desc,
                        'tool': cfg.get('tool')})
            if limit and len(out) >= limit:
                break
        return out

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        names = self.ls() if names is None else [n for n in names if self.exists(n)]
        out = {}
        for name in names:
            cfg = self._cfg(name)
            params = {
                'task': {'type': 'string', 'required': not cfg.get('tool'),
                         'hint': 'what you want this agent to do, in full — '
                                 'it does not see your conversation'},
                'steps': {'type': 'integer', 'required': False,
                          'default': DEFAULT_STEPS},
            }
            for k, v in self._tool_params(cfg).items():
                # its tool's own arguments: optional here, since a `task`
                # alone is also a valid call
                params.setdefault(k, {**v, 'required': False})
            out[name] = {'description': self._describe(cfg), 'params': params,
                         'kind': 'agent', 'tool': cfg.get('tool')}
        return out

    def _tool_params(self, cfg: Dict) -> Dict[str, Dict]:
        tool = cfg.get('tool')
        if not tool or self._tools is None or tool.startswith(PREFIX):
            return {}
        try:
            return (self._tools.schema([tool]).get(tool) or {}).get('params') or {}
        except Exception:
            return {}

    # ── calling ──────────────────────────────────────────────────────

    def run(self, name: str, /, task: str = None, steps: int = None,
            params: Dict = None, **kwargs) -> Dict[str, Any]:
        """Run the agent whole and return its answer (not its trace)."""
        args = {**(params if isinstance(params, dict) else {}), **kwargs}
        task = task or args.pop('task', None)
        steps = steps or args.pop('steps', None)
        cfg = self._cfg(name)
        if cfg is None:
            return {'success': False, 'error': f"agent not found: {name}"}
        if cfg.get('tool') and not task and args:
            # a tool-agent handed its tool's arguments: that IS the call
            # (Mod._run_tool_agent reads a JSON-object query as params)
            task = json.dumps(args, default=str)
        task = task or args.get('query') or args.get('prompt')
        if not task:
            return {'success': False, 'error': 'agent tools need a `task`'
                    + (f" or the params of {cfg['tool']}" if cfg.get('tool') else '')}
        ctx = self.context
        if ctx is None or not hasattr(ctx, '_graph_agent'):
            return {'success': False,
                    'error': 'agent tools run inside an agent — no agent bound'}
        with AgentTools._guard:
            if AgentTools._active >= MAX_ACTIVE:
                return {'success': False,
                        'error': f"too many delegated agent runs in flight "
                                 f"({MAX_ACTIVE}) — finish with what you have"}
            AgentTools._active += 1
        slug = self._slug(name)
        key = getattr(ctx, '_run_key', None)
        box: Dict[str, Any] = {}

        def work():
            try:
                box['out'] = ctx._graph_agent(agent=slug, query=str(task),
                                              steps=int(steps or DEFAULT_STEPS),
                                              key=key)
            except Exception as e:      # noqa: BLE001
                box['out'] = {'ok': False, 'error': str(e)}
            finally:
                try:
                    ctx._clear_run_state()
                except Exception:
                    pass

        try:
            t = threading.Thread(target=work, name=f'agent-tool-{slug}', daemon=True)
            t.start()
            t.join(TIMEOUT)
        finally:
            with AgentTools._guard:
                AgentTools._active -= 1
        out = box.get('out')
        if out is None:
            return {'success': False, 'agent': slug,
                    'error': f'{slug} did not finish within {TIMEOUT}s'}
        if not out.get('ok'):
            return {'success': False, 'agent': slug, 'error': out.get('error')}
        res = {'success': True, 'agent': slug, 'result': out.get('text'),
               'steps': len(out.get('trace') or [])}
        if cfg.get('tool'):
            res['tool'] = cfg['tool']
        return res

    def test(self) -> Dict[str, Any]:
        names = self.ls()
        if names:
            assert self.exists(names[0])
            assert names[0] in self.schema([names[0]])
        assert not self.exists('bash') and not self.exists('agent.no-such-agent')
        return {'passed': True, 'agents': len(names)}
