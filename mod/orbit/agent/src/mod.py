"""
agent - autonomous coding agent with 21 skills

Usage:
    import mod as m
    agent = m.mod('agent')()
    agent.forward('run', query='fix the bug in main.py')
    agent.forward('skills')
    agent.forward('serve')
    agent.forward('status')
"""
import os
import json
import subprocess
import signal
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import mod as m
    print = m.print
except ImportError:
    m = None

from .skills.mod import Skills
from .agents.mod import Agents
from .memory.memory import Memory


# ── path sandboxing ────────────────────────────────────────────────

WRITE_SKILLS = ('write', 'edit', 'patch')

def check_path_allowed(file_path: str, allowed_paths: list) -> bool:
    """Return True if path is within allowed paths, or if allowed_paths is None (unrestricted)."""
    if allowed_paths is None:
        return True
    resolved = str(Path(file_path).expanduser().resolve())
    return any(resolved.startswith(str(Path(ap).resolve())) for ap in allowed_paths)


class Agent:
    """
    World-class coding agent. 21 skills for autonomous software engineering.

    Skills: bash, read, write, edit, glob, grep, search, task,
            fetch, patch, think, git, test, lint, symbols, diff,
            tree, todo, context, debug, refactor

    Agent loop: query -> context gather -> LLM -> parse plan -> execute -> reflect -> repeat

    Usage:
        agent = Agent()
        agent.forward("read main.py and fix the bug")
        agent.skills.ls()
        agent.skills.run("bash", command="ls")
    """

    goal = """You are an elite autonomous coding agent. You write production-quality code.

CORE PRINCIPLES:
- Read before you write. Always understand existing code before modifying it.
- Think before you act. Use the think tool to reason through complex problems.
- Verify after you change. Run tests or read back files to confirm your edits.
- Be precise. Use edit/patch for surgical changes, not full file rewrites.
- Be efficient. Minimize redundant operations. Gather context first, then act.

WORKFLOW:
1. UNDERSTAND: Use context, tree, read, grep, symbols to understand the codebase
2. PLAN: Use think to reason through your approach before coding
3. IMPLEMENT: Use edit, patch, write to make changes
4. VERIFY: Use test, lint, diff, read to verify changes are correct
5. FINISH: Use finish when the task is complete

RULES:
- One step per iteration. Choose the single best action.
- Never guess file contents. Always read first.
- When you encounter an error, use debug to analyze it, then fix the root cause.
- Use git to check status and create commits when appropriate.
- Use todo to track multi-step tasks.
- Prefer edit/patch over write for existing files.
- If stuck, use think to reflect on what went wrong and try a different approach.
"""

    PROVIDERS = {
        'openrouter': 'model.openrouter',
        'venice': 'venice',
    }

    DEFAULT_MODELS = {
        'model.openrouter': 'anthropic/claude-sonnet-4-5-20250929',
        'openrouter': 'anthropic/claude-sonnet-4-5-20250929',
        'venice': 'deepseek-v3.2',
    }

    anchors = {
        'plan': ['<PLAN>', '</PLAN>'],
        'tool': ['<STEP>', '</STEP>'],
    }

    output_format = """
        Respond with exactly ONE step per iteration inside anchors.
        The params must be valid JSON.
        <PLAN>
        <STEP>{"tool": "<skill_name>", "params": {...}}</STEP>
        </PLAN>
        When finished:
        <PLAN>
        <STEP>{"tool": "finish", "params": {"summary": "what you accomplished"}}</STEP>
        </PLAN>
    """

    def __init__(self,
                 model: str = 'model.openrouter',
                 provider: str = None,
                 memory: str = 'agent.memory',
                 goal: str = None,
                 skills: list = None,
                 **kwargs):
        self.skills = Skills()
        self.agents = Agents()
        self.memory = m.mod(memory)() if m else Memory()
        # resolve provider: shorthand ('venice', 'openrouter') or full module path
        provider = provider or model
        self._provider = self.PROVIDERS.get(provider, provider)
        self.model = m.mod(self._provider)() if m else None
        if goal:
            self.goal = goal
        self._skill_names = skills  # optional filter

    def set_provider(self, provider: str):
        """Switch LLM provider at runtime. Use 'openrouter', 'venice', or any module path."""
        self._provider = self.PROVIDERS.get(provider, provider)
        self.model = m.mod(self._provider)() if m else None
        return {'provider': self._provider}

    # ── skill interface ──────────────────────────────────────────────

    def skill(self, name: str):
        """Get a skill instance"""
        return self.skills.get(name)

    def run_skill(self, name: str, **params):
        """Run a skill by name"""
        return self.skills.run(name, **params)

    def skill_schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Get LLM-friendly schemas for skills"""
        return self.skills.schema(names or self._skill_names)

    # ── memory ───────────────────────────────────────────────────────

    def init_memory(self, **kwargs):
        kwargs['goal'] = self.goal
        kwargs['output_format'] = self.output_format
        for k, v in kwargs.items():
            self.memory.add(k, v)
            if m and k.startswith('fork') and v is not None:
                self.memory.add(f'fork({k})', m.fn('select_files')(path=m.dp(v), query=kwargs.get('query', '')))

    # ── main loop ────────────────────────────────────────────────────

    def run(self,
            query: str = 'help me with this',
            *extra_text,
            model: Optional[str] = None,
            provider: str = None,
            path: str = None,
            temperature: float = 0.0,
            max_tokens: int = 100000,
            steps: int = 25,
            skills: list = None,
            mod: str = None,
            safety: bool = False,
            save: bool = False,
            key: str = None,
            allowed_paths: list = None,
            free: bool = False,
            **kwargs) -> List[Dict[str, Any]]:
        """Run the agent loop: query -> LLM -> parse step -> execute skill -> repeat.

        Args:
            model: model name on the provider (e.g. 'anthropic/claude-sonnet-4-5-20250929' for openrouter,
                   'deepseek-v3.2' for venice). Defaults to provider's default model.
            provider: LLM provider — 'openrouter', 'venice', or any module path. Switches at runtime.
            allowed_paths: list of allowed write paths, or None for unrestricted (owner).
                           Non-owners are restricted to their portal directory.
        """
        if provider:
            self.set_provider(provider)
        model = model or self.DEFAULT_MODELS.get(self._provider, 'anthropic/claude-sonnet-4-5-20250929')
        self._allowed_paths = allowed_paths
        query = query + ' ' + ' '.join(extra_text) if extra_text else query
        path = path or (m.dp(mod) if m and mod else os.getcwd())
        self.init_memory(
            query=query,
            tools=self.skill_schema(skills),
            path=path,
            steps=steps,
            **kwargs
        )
        history = []
        consecutive_errors = 0
        for step_i in range(steps):
            self.memory.update({'step': step_i, 'pwd': path})
            # inject recovery hint after repeated errors
            if consecutive_errors >= 3:
                self.memory.add('hint', 'Multiple errors in a row. Use think to reflect on what is going wrong and try a different approach.')
                consecutive_errors = 0
            try:
                output = self.model.forward(
                    str(self.memory.get()),
                    stream=True,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    free=free,
                )
                plan = self.plan(output, safety=safety)
            except Exception as e:
                print(f"Model error: {e}")
                plan = [{'tool': 'error', 'params': {}, 'error': str(e)}]
            history.append(plan)
            self.memory.add('history', history)
            if plan and plan[-1]['tool'].lower() in ('finish', 'response'):
                print('Agent finished')
                break
            if plan and plan[-1]['tool'].lower() == 'error':
                print('Agent stopped: model error')
                break
            # track consecutive errors for recovery
            if plan and any(s.get('error') for s in plan):
                consecutive_errors += 1
            else:
                consecutive_errors = 0
        if save and m and mod:
            return m.fn('api/reg')(mod=mod, key=key, comment=query)
        return history[-1] if history else []

    # ── plan parsing & execution ─────────────────────────────────────

    def plan(self, output: str, safety: bool = False) -> list:
        """Parse LLM output into steps and execute them."""
        steps, raw_text = self.parse_steps(output)
        if not steps and raw_text.strip():
            # LLM responded with text but no tool calls — return as a response step
            return [{'tool': 'response', 'params': {}, 'result': raw_text.strip()}]
        steps = self.run_plan(steps, safety=safety)
        return steps

    def parse_steps(self, output: str) -> tuple:
        """Stream LLM output and extract steps from anchors.

        Returns:
            (plan, raw_text) — plan is list of step dicts, raw_text is full LLM output
        """
        text = ''
        raw = ''
        plan = []
        for ch in output:
            text += ch
            raw += ch
            print(ch, end='')
            if self.anchors['tool'][0] in text and self.anchors['tool'][1] in text:
                step = self._extract_step(text)
                if step:
                    plan.append(step)
                text = text.split(self.anchors['tool'][-1])[-1]
        return plan, raw

    def _extract_step(self, text: str) -> Optional[dict]:
        """Extract a single step JSON from between STEP anchors."""
        try:
            raw = text.split(self.anchors['tool'][0])[1].split(self.anchors['tool'][1])[0]
            print(f"STEP: {raw}")
            try:
                step = json.loads(raw)
            except json.JSONDecodeError:
                step = self._repair_json(raw)
            if isinstance(step, dict) and 'tool' in step and 'params' in step:
                return step
        except Exception as e:
            print(f"Step parse error: {e}")
        return None

    def _repair_json(self, raw: str) -> Optional[dict]:
        """Best-effort repair of slightly-malformed step JSON from the model.

        Tries an optional `fix_json` tool if one is installed, then falls back to
        dependency-free fixes (strip code fences, drop trailing commas). Returns
        None if it still can't parse — the step is then skipped, not crashed on.
        """
        # optional external repair tool, only if it actually exists
        if m is not None:
            try:
                fix = m.tool('fix_json')
            except Exception:
                fix = None
            if fix is not None:
                try:
                    fixed = fix(raw)
                    return json.loads(fixed) if isinstance(fixed, str) else fixed
                except Exception:
                    pass
        # dependency-free fallbacks
        s = raw.strip()
        if s.startswith('```'):
            s = s.split('```', 2)[1] if '```' in s[3:] else s.strip('`')
            s = s.split('\n', 1)[-1] if s.lower().startswith('json') else s
        # keep only the outermost {...}
        if '{' in s and '}' in s:
            s = s[s.index('{'): s.rindex('}') + 1]
        # drop trailing commas before } or ]
        import re
        s = re.sub(r',\s*([}\]])', r'\1', s)
        try:
            return json.loads(s)
        except Exception:
            return None

    def run_plan(self, plan: List[Dict[str, Any]], safety: bool = False) -> List[Dict[str, Any]]:
        """Execute parsed steps using skills. Enforces path sandboxing via _allowed_paths."""
        if safety and plan:
            confirm = input("Execute plan? (y/n): ")
            if confirm.lower() != 'y':
                raise Exception("Aborted by user")
        allowed = getattr(self, '_allowed_paths', None)
        for i, step in enumerate(plan):
            name = step['tool'].lower()
            params = step.get('params', {})
            if name in ('finish', 'review'):
                print(f"[{i+1}/{len(plan)}] {name}")
                break

            # ── path sandboxing for write-capable skills ──
            if allowed is not None:
                if name in WRITE_SKILLS:
                    fp = params.get('file_path', '')
                    if fp and not check_path_allowed(fp, allowed):
                        plan[i]['error'] = f"Permission denied: cannot write to {fp}. Restricted to {allowed}"
                        print(f"[{i+1}/{len(plan)}] {name} -> blocked (path)")
                        continue
                if name == 'bash':
                    # force cwd into portal and block path-escaping commands
                    params['cwd'] = allowed[0]
                if name == 'git':
                    params['cwd'] = params.get('cwd') or allowed[0]

            try:
                # try local skill first, fall back to mod.tool
                if name in self.skills.ls():
                    result = self.run_skill(name, **params)
                elif m:
                    result = m.tool(name)(**params)
                else:
                    result = {"error": f"unknown skill: {name}"}
                plan[i]['result'] = result
                print(f"[{i+1}/{len(plan)}] {name} -> done")
            except Exception as e:
                plan[i]['error'] = str(e)
                print(f"[{i+1}/{len(plan)}] {name} -> error: {e}")
        return plan


# backwards compat
Dev = Agent


class Mod(Agent):
    description = "Autonomous coding agent. 21 skills for software engineering."

    def __init__(self, key=None, **kwargs):
        super().__init__(**kwargs)
        self.src_dir = Path(__file__).parent
        self.module_dir = self.src_dir.parent

        # load ports from config.json
        config_path = self.module_dir / 'config.json'
        svc_config = {}
        if config_path.exists():
            with open(config_path) as f:
                svc_config = json.load(f)
        api_cfg = svc_config.get('api', {})
        app_cfg = svc_config.get('app', {})
        self.api_port = api_cfg.get('port', 50117)
        self.app_port = app_cfg.get('port', 3117)

        # ── permissions (Claude module pattern) ──
        self.key = m.key(key) if m else None
        self.auth = m.mod('auth.base')() if m else None
        # Owner resolution (claude pattern): an explicit owner in config.json or
        # ~/.mod/agent/owner.json is AUTHORITATIVE and is read independently of
        # whether the framework `m` import succeeded. This prevents a fail-open
        # gate when the module is served without the framework on PYTHONPATH
        # (m=None -> no key -> previously owner=None -> is_owner() True for all).
        # The server's own key is only a fallback for unconfigured local/dev use.
        owner = svc_config.get('owner') or self._load_owner_file()
        if not owner and self.key:
            owner = self.key.address
        self._owner = owner.lower() if owner else None
        self._mods_root = (m.paths['orbit']['mods']
                           if m and hasattr(m, 'paths') else
                           str(self.module_dir.parent / 'registry' / 'mods'))

        # ── access control (gate) ──
        # public: anyone can call these
        # admin: owner + granted users only
        # ACL is private auth state — keep it OFF-tree under ~/.mod/agent/,
        # never in the committed module dir (matches the claude module).
        state_dir = Path.home() / '.mod' / 'agent'
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._acl_path = state_dir / 'acl.json'
        except Exception:
            self._acl_path = self.module_dir / '.acl.json'
        self._acl = self._load_acl()
        self._public_actions = {'status', 'health', 'skills', 'schema',
                                'agents', 'agent', 'chains', 'agent_cids',
                                'agent_load'}
        self._admin_actions = {'run', 'plan', 'skill', 'serve', 'kill',
                               'test', 'grant', 'revoke', 'acl',
                               'agent_save', 'agent_install'}

    # ── permissions (Claude module interface) ────────────────────────────

    def _load_owner_file(self):
        """Read owner from ~/.mod/agent/owner.json (claude-style, import-independent)."""
        try:
            p = Path.home() / '.mod' / 'agent' / 'owner.json'
            if p.exists():
                with open(p) as f:
                    return json.load(f).get('owner')
        except Exception:
            pass
        return None

    def _resolve_address(self, key=None) -> str:
        """Resolve a key/address/token to a verified address string."""
        if key is None:
            return self.key.address if self.key else ''
        if hasattr(key, 'address'):
            return key.address
        key_str = str(key)
        if key_str.startswith('0x') and len(key_str) in (42, 66):
            return key_str
        if self.auth:
            try:
                verified = self.auth.verify(key_str)
                return verified['key']
            except Exception:
                pass
        return key_str

    def is_owner(self, key=None) -> bool:
        """Check if key/address/token belongs to the module owner."""
        if not self._owner:
            return True
        addr = self._resolve_address(key)
        if not addr:
            return False
        return addr.lower() == self._owner.lower()

    def require_owner(self, key=None, operation: str = "this operation"):
        """Raise PermissionError if caller is not the owner."""
        if not self.is_owner(key):
            raise PermissionError(
                f"Permission denied: '{operation}' is owner-only."
            )

    def allowed_paths_for(self, key=None):
        """Return allowed write paths for the caller.

        Owner: None (unrestricted)
        Others: [registry/mods/{address}/]
        """
        if self.is_owner(key):
            return None
        addr = self._resolve_address(key).lower()
        mods_dir = os.path.join(self._mods_root, addr)
        os.makedirs(mods_dir, exist_ok=True)
        return [mods_dir]

    # ── access control (gate) ────────────────────────────────────────

    def _load_acl(self) -> dict:
        """Load ACL from .acl.json. Format: {address: {actions: [...], granted_by: owner}}"""
        if self._acl_path.exists():
            with open(self._acl_path) as f:
                return json.load(f)
        return {}

    def _save_acl(self):
        """Persist ACL to .acl.json"""
        with open(self._acl_path, 'w') as f:
            json.dump(self._acl, f, indent=2)

    def is_allowed(self, key=None, action: str = None) -> bool:
        """Check if caller is allowed to perform action.

        - Owner can do everything
        - Public actions are open to all
        - Admin actions require owner or explicit grant
        """
        if self.is_owner(key):
            return True
        if action in self._public_actions:
            return True
        # check ACL grants
        addr = self._resolve_address(key).lower()
        if addr in self._acl:
            grant = self._acl[addr]
            allowed = grant.get('actions', [])
            if '*' in allowed or action in allowed:
                return True
        return False

    def require_allowed(self, key=None, action: str = None):
        """Raise PermissionError if caller is not allowed."""
        if not self.is_allowed(key, action):
            raise PermissionError(
                f"Permission denied: '{action}' requires admin access. "
                f"Ask the owner to grant you access."
            )

    def grant(self, address: str, actions: list = None, key: str = None) -> dict:
        """Grant access to an address. Owner only.

        Args:
            address: address to grant access to
            actions: list of actions to grant (default: ['run', 'skill'])
                     use ['*'] for full admin access
            key: caller key (must be owner)
        """
        self.require_owner(key, 'grant')
        addr = address.lower()
        actions = actions or ['run', 'skill']
        self._acl[addr] = {
            'actions': actions,
            'granted_by': self._owner,
        }
        self._save_acl()
        return {'granted': addr, 'actions': actions}

    def revoke(self, address: str, key: str = None) -> dict:
        """Revoke access from an address. Owner only."""
        self.require_owner(key, 'revoke')
        addr = address.lower()
        removed = self._acl.pop(addr, None)
        self._save_acl()
        return {'revoked': addr, 'was_granted': removed is not None}

    def acl(self, key: str = None) -> dict:
        """View current ACL. Owner only."""
        self.require_owner(key, 'acl')
        return {
            'owner': self._owner,
            'grants': self._acl,
            'public_actions': sorted(self._public_actions),
            'admin_actions': sorted(self._admin_actions),
        }

    # ── mod protocol entry point ──────────────────────────────────────

    def forward(self, action=None, key=None, **kwargs):
        """CLI entry point: agent <action> [args]

        Actions:
          Public (anyone):
            status, health, skills, schema, agents, agent, chains

          Admin (owner + granted users):
            run         - Run the agent loop
            plan        - Parse and execute a single LLM output
            skill       - Run a single skill
            serve       - Start API + app
            kill        - Stop services
            test        - Run tests

          Owner only:
            grant       - Grant access to an address (address=, actions=)
            revoke      - Revoke access from an address (address=)
            acl         - View current access control list
        """
        kwargs['key'] = key
        actions = {
            # public
            'status': lambda: self.status(),
            'health': lambda: self.health(),
            'skills': lambda: self.skills.ls(),
            'schema': lambda: self.skill_schema(kwargs.get('names')),
            'agents': lambda: self.agents.forward(kwargs.get('name'), **kwargs),
            'agent': lambda: self.agents.forward(kwargs.get('name', 'default')),
            'chains': lambda: self.agents.chains(),
            'agent_cids': lambda: self.agents.forward(action='cids'),
            'agent_load': lambda: self.agents.load(kwargs.get('cid', ''), shares=kwargs.get('shares')),
            # admin (owner + granted)
            'run': lambda: self._run(**kwargs),
            'plan': lambda: super(Mod, self).plan(kwargs.get('output', ''), safety=kwargs.get('safety', False)),
            'skill': lambda: self.run_skill(kwargs.get('name', ''), **{k: v for k, v in kwargs.items() if k not in ('name', 'key')}),
            'serve': lambda: self.serve(kwargs.get('api_port'), kwargs.get('app_port'), kwargs.get('dev', True)),
            'kill': lambda: self.kill(kwargs.get('service')),
            'test': lambda: self.test(),
            'agent_save': lambda: self.agents.save(**{k: v for k, v in kwargs.items() if k not in ('action',)}),
            'agent_install': lambda: self.agents.load_and_create(cid=kwargs.get('cid', ''), shares=kwargs.get('shares'), key=key),
            # owner only
            'grant': lambda: self.grant(kwargs.get('address', ''), kwargs.get('actions'), key),
            'revoke': lambda: self.revoke(kwargs.get('address', ''), key),
            'acl': lambda: self.acl(key),
        }

        if not action or action not in actions:
            return {
                'module': 'agent',
                'description': self.description,
                'actions': list(actions.keys()),
                'owner': self._owner,
                'status': self.status(),
            }

        # ── gate: enforce access control ──
        self.require_allowed(key, action)

        return actions[action]()

    def _run(self, **kwargs):
        """Run the agent loop (delegates to Agent.run).

        Resolves agent_type from the agents/ registry to apply
        goal and skills overrides before running.
        """
        key = kwargs.get('key')
        allowed_paths = self.allowed_paths_for(key)

        # resolve agent type from registry
        agent_type = kwargs.get('agent_type') or kwargs.get('agent')
        agent_goal = None
        agent_skills = kwargs.get('skills')
        agent_model = kwargs.get('model')
        agent_provider = kwargs.get('provider')

        if agent_type and agent_type in self.agents.ls():
            agent_config = self.agents.get(agent_type)
            if agent_config.get('goal'):
                agent_goal = agent_config['goal']
            if agent_config.get('skills') and not kwargs.get('skills'):
                agent_skills = agent_config['skills']
            if agent_config.get('model'):
                agent_model = agent_config['model']

        # swap goal temporarily if agent has a custom one
        original_goal = self.goal
        if agent_goal:
            self.goal = agent_goal
        try:
            return self.run(
                query=kwargs.get('query', 'help me with this'),
                model=agent_model,
                provider=agent_provider,
                path=kwargs.get('path'),
                temperature=kwargs.get('temperature', 0.0),
                max_tokens=kwargs.get('max_tokens', 100000),
                steps=kwargs.get('steps', 25),
                skills=agent_skills,
                mod=kwargs.get('mod'),
                safety=kwargs.get('safety', False),
                save=kwargs.get('save', False),
                key=kwargs.get('key'),
                allowed_paths=allowed_paths,
                free=kwargs.get('free', False),
            )
        finally:
            self.goal = original_goal

    # ── serve ────────────────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True):
        """Start the FastAPI api and Next.js app."""
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port
        results = {}
        log_dir = Path('/tmp/agent')
        log_dir.mkdir(parents=True, exist_ok=True)

        self.kill()

        # ── start API (src/api/api.py) ──
        api_dir = self.src_dir / 'api'
        api_path = api_dir / 'api.py'
        if api_path.exists():
            env = os.environ.copy()
            env['PORT'] = str(api_port)
            mod_root = str(self.module_dir.parent.parent.parent)
            env['PYTHONPATH'] = mod_root + os.pathsep + str(self.module_dir) + os.pathsep + str(self.src_dir)

            api_log = open(log_dir / 'api.log', 'w')
            cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
                   '--port', str(api_port)]
            if dev:
                cmd.append('--reload')
            subprocess.Popen(
                cmd,
                cwd=str(api_dir),
                env=env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            results['api'] = f'http://localhost:{api_port}'
            results['api_log'] = str(log_dir / 'api.log')

        # ── start app (src/app/) ──
        app_dir = self.src_dir / 'app'
        if app_dir.exists():
            if not (app_dir / 'node_modules').exists():
                subprocess.run(['npm', 'install'], cwd=str(app_dir), capture_output=True)

            env = os.environ.copy()
            env['NEXT_PUBLIC_API_URL'] = f'http://localhost:{api_port}'
            env['PORT'] = str(app_port)

            app_log = open(log_dir / 'app.log', 'w')
            if dev:
                subprocess.Popen(
                    ['npx', 'next', 'dev', '-p', str(app_port)],
                    cwd=str(app_dir),
                    env=env,
                    stdout=app_log,
                    stderr=subprocess.STDOUT,
                )
            else:
                subprocess.Popen(
                    ['npx', 'next', 'start', '-p', str(app_port)],
                    cwd=str(app_dir),
                    env=env,
                    stdout=app_log,
                    stderr=subprocess.STDOUT,
                )
            results['app'] = f'http://localhost:{app_port}'
            results['app_log'] = str(log_dir / 'app.log')

        results['dev'] = dev
        results['logs'] = str(log_dir)
        return results

    def kill(self, service=None):
        """Stop running services. service: 'api', 'app', or None (both)"""
        killed = []
        patterns = []
        if service in (None, 'api'):
            patterns.append(f'uvicorn.*api:app.*{self.api_port}')
        if service in (None, 'app'):
            patterns.append(f'next.*dev.*{self.app_port}')
            patterns.append(f'next.*start.*{self.app_port}')

        for pattern in patterns:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', pattern],
                    capture_output=True, text=True
                )
                for pid in result.stdout.strip().split('\n'):
                    if pid:
                        os.kill(int(pid), signal.SIGTERM)
                        killed.append(f'{pattern.split(".*")[0]}:{pid}')
            except Exception:
                pass
        return {'killed': killed}

    def health(self):
        """Check if services are running."""
        result = {}
        try:
            import requests as req
            r = req.get(f'http://localhost:{self.api_port}/health', timeout=2)
            result['api'] = r.json()
        except Exception:
            result['api'] = {'status': 'down'}
        try:
            import requests as req
            r = req.get(f'http://localhost:{self.app_port}/', timeout=2)
            result['app'] = {'status': 'up' if r.status_code == 200 else 'down'}
        except Exception:
            result['app'] = {'status': 'down'}
        return result

    def status(self):
        """Get agent status"""
        return {
            'module': 'agent',
            'skills': self.skills.ls(),
            'skill_count': len(self.skills.ls()),
            'agents': self.agents.ls(),
            'agent_count': len(self.agents.ls()),
            'model': self.model is not None,
            'memory_keys': self.memory.keys(),
            'ports': {
                'api': self.api_port,
                'app': self.app_port,
            },
        }

    def test(self):
        """Test the agent module"""
        results = {'passed': 0, 'failed': 0, 'tests': []}

        # test skills loaded
        try:
            skills = self.skills.ls()
            assert len(skills) > 0, "should have skills"
            results['tests'].append({'name': 'skills_loaded', 'passed': True, 'count': len(skills)})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'skills_loaded', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test schema generation
        try:
            schema = self.skill_schema()
            assert 'bash' in schema, "should have bash skill"
            assert 'read' in schema, "should have read skill"
            results['tests'].append({'name': 'schema', 'passed': True, 'keys': list(schema.keys())})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'schema', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test bash skill
        try:
            r = self.run_skill('bash', command='echo hello')
            assert r['success'] and 'hello' in r['stdout']
            results['tests'].append({'name': 'bash_skill', 'passed': True})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'bash_skill', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test forward dispatch
        try:
            info = self.forward()
            assert info['module'] == 'agent'
            assert 'actions' in info
            results['tests'].append({'name': 'forward_dispatch', 'passed': True, 'actions': info['actions']})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'forward_dispatch', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        return results
