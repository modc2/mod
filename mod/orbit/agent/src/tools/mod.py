"""
tools - custom tools, added from the console instead of shipped in the repo

A shipped skill is a Python module under src/skills/. A custom tool is the
same idea without the deploy: a name, a description, typed params, and a
shell command template with {placeholders}. The agent sees it in its tool
schema exactly like a built-in and calls it the same way — the difference is
only where it came from.

Values are shell-quoted when the template is rendered, so a param carrying
`; rm -rf /` lands as one literal argument, not a second command.

Custom tools are user state, not module code, so they live off-tree in
~/.mod/agent/tools.json — same place as toolboxes, the ACL and the vault.
They run shell, so creating one is host-only (see Mod._admin_actions);
library skills stay documents-only for exactly that reason.

Usage:
    tools = Tools(skills=Skills())
    tools.add('loc', 'Count lines in a file', command='wc -l {path}')
    tools.ls()                          # ['loc']
    tools.run('loc', path='src/mod.py')
    tools.rm('loc')
"""
import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PLACEHOLDER = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')
NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,39}$')
# passed to the runner, never substituted into the template
RESERVED = ('cwd', 'timeout')
MAX_OUTPUT = 20_000


def _quote(value: Any) -> str:
    """Shell-quote a param value. Booleans render as flag-friendly words."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return shlex.quote(str(value))


class Tool:
    """One custom tool: a described, parameterised shell command."""

    def __init__(self, name: str, command: str, description: str = '',
                 params: Dict[str, Dict] = None, cwd: str = None,
                 timeout: int = 60, owner: str = None, created: float = None,
                 updated: float = None, cid: str = None):
        self.name = name
        self.command = command
        self.description = description or f"Run: {command}"
        self.params = params or self.infer_params(command)
        self.cwd = cwd
        self.timeout = int(timeout or 60)
        self.owner = owner
        self.created = created or time.time()
        self.updated = updated or self.created
        self.cid = cid

    @staticmethod
    def placeholders(command: str) -> List[str]:
        """Placeholder names in the template, in order, de-duped."""
        return list(dict.fromkeys(PLACEHOLDER.findall(command or '')))

    @classmethod
    def infer_params(cls, command: str) -> Dict[str, Dict]:
        """A template alone is a valid definition — every {placeholder}
        becomes a required string param."""
        return {p: {'type': 'string', 'required': True}
                for p in cls.placeholders(command) if p not in RESERVED}

    def schema(self) -> Dict[str, Any]:
        """The shape Skills.schema() produces, so the LLM sees one tool list."""
        params = {}
        for pname, spec in self.params.items():
            entry = {'type': spec.get('type', 'string'),
                     'required': bool(spec.get('required', False))}
            if 'default' in spec and spec['default'] is not None:
                entry['default'] = spec['default']
            if spec.get('hint'):
                entry['hint'] = spec['hint']
            params[pname] = entry
        return {'description': self.description, 'params': params,
                'custom': True}

    def render(self, values: Dict[str, Any]) -> str:
        """Fill the template. Missing required params raise before anything runs."""
        resolved: Dict[str, str] = {}
        for pname in self.placeholders(self.command):
            spec = self.params.get(pname, {})
            if pname in values and values[pname] is not None:
                resolved[pname] = _quote(values[pname])
            elif spec.get('default') is not None:
                resolved[pname] = _quote(spec['default'])
            elif spec.get('required', True):
                raise ValueError(f"missing required param: {pname}")
            else:
                resolved[pname] = ''
        return PLACEHOLDER.sub(lambda mt: resolved.get(mt.group(1), mt.group(0)),
                               self.command)

    def to_dict(self) -> Dict[str, Any]:
        return {'name': self.name, 'description': self.description,
                'command': self.command, 'params': self.params,
                'cwd': self.cwd, 'timeout': self.timeout, 'owner': self.owner,
                'created': self.created, 'updated': self.updated,
                'cid': self.cid, 'kind': 'custom'}


class Tools:
    """Custom tool registry — persisted off-tree, callable like a skill."""
    description = "Custom tool registry - shell-backed tools the agent calls like a skill"

    def __init__(self, skills=None, path: str = None, **kwargs):
        self.skills = skills
        if path is None:
            state_dir = Path.home() / '.mod' / 'agent'
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                path = state_dir / 'tools.json'
            except Exception:
                path = Path(__file__).parent / '.tools.json'
        self._path = Path(path)
        self._tools = self._load()

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
            json.dump(self._tools, f, indent=2, default=str)

    def ls(self) -> List[str]:
        return sorted(self._tools.keys())

    def exists(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"tool not found: {name}")
        return Tool(name=name, **{k: v for k, v in self._tools[name].items()
                                  if k not in ('name', 'kind')})

    def items(self) -> List[Dict[str, Any]]:
        return [self.get(n).to_dict() for n in self.ls()]

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        names = self.ls() if names is None else [n for n in names if self.exists(n)]
        return {n: self.get(n).schema() for n in names}

    def owner_of(self, name: str) -> Optional[str]:
        return self._tools.get(name, {}).get('owner')

    def add(self, name: str, command: str, description: str = '',
            params: Dict[str, Dict] = None, cwd: str = None, timeout: int = 60,
            owner: str = None) -> Dict[str, Any]:
        """Create or update a custom tool. Shipped skill names are reserved."""
        name = (name or '').strip().lower().replace(' ', '-')
        if not NAME_RE.match(name):
            raise ValueError("name must be lowercase letters, digits, - or _ "
                             "(max 40 chars, starting with a letter)")
        if self.skills is not None and name in self.skills.ls():
            raise ValueError(f"'{name}' is a built-in skill — pick another name")
        if name in ('finish', 'review'):
            raise ValueError(f"'{name}' is reserved by the agent loop")
        if not (command or '').strip():
            raise ValueError("a tool needs a command template")
        prev = self._tools.get(name, {})
        tool = Tool(name=name, command=command.strip(), description=description,
                    params=params or None, cwd=cwd or None, timeout=timeout,
                    owner=owner or prev.get('owner'),
                    created=prev.get('created'), updated=time.time())
        entry = tool.to_dict()
        entry.pop('kind', None)
        entry.pop('name', None)
        entry['cid'] = self._pin(name, entry)
        self._tools[name] = entry
        self._save()
        return self.get(name).to_dict()

    @staticmethod
    def _pin(name: str, entry: Dict) -> Optional[str]:
        """Best-effort pin to localfs so a tool is shareable by CID."""
        try:
            import mod as m
            return m.mod('localfs')().put({'type': 'tool', 'name': name,
                                           'command': entry['command'],
                                           'description': entry['description'],
                                           'params': entry['params']})
        except Exception:
            return None

    def rm(self, name: str) -> Dict[str, Any]:
        removed = self._tools.pop(name, None)
        self._save()
        return {'removed': name, 'existed': removed is not None}

    def run(self, name: str, **params) -> Dict[str, Any]:
        """Render the template and execute it. Same result shape as `bash`."""
        tool = self.get(name)
        cwd = params.pop('cwd', None) or tool.cwd or None
        timeout = int(params.pop('timeout', None) or tool.timeout)
        command = tool.render(params)
        try:
            r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout)
            return {'success': r.returncode == 0, 'command': command,
                    'stdout': r.stdout[:MAX_OUTPUT], 'stderr': r.stderr[:MAX_OUTPUT],
                    'code': r.returncode}
        except subprocess.TimeoutExpired:
            return {'success': False, 'command': command, 'stdout': '',
                    'stderr': f'timeout after {timeout}s', 'code': -1}
        except Exception as e:
            return {'success': False, 'command': command, 'stdout': '',
                    'stderr': str(e), 'code': -1}

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                  -> list all custom tools
        forward("loc")                             -> one tool
        forward(action="add", name=…, command=…)   -> create/update
        forward(action="rm", name=…)               -> remove
        forward(action="run", name=…, params={…})  -> execute
        """
        action = kwargs.get('action')
        if action == 'add':
            return self.add(kwargs.get('name', name or ''),
                            kwargs.get('command', ''),
                            kwargs.get('description', ''),
                            kwargs.get('params'), kwargs.get('cwd'),
                            kwargs.get('timeout', 60), kwargs.get('owner'))
        if action == 'rm':
            return self.rm(kwargs.get('name', name or ''))
        if action == 'run':
            return self.run(kwargs.get('name', name or ''),
                            **(kwargs.get('params') or {}))
        if name is None:
            return {'tools': self.items(), 'total': len(self._tools)}
        return self.get(name).to_dict()

    def test(self) -> Dict[str, Any]:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            t = Tools(path=Path(tmp) / 'tools.json')
            t.add('echoer', 'echo {msg}', description='Echo a message')
            assert t.ls() == ['echoer']
            assert t.schema()['echoer']['params']['msg']['required']
            r = t.run('echoer', msg='hi there')
            assert r['success'] and 'hi there' in r['stdout'], r
            # a param can't smuggle a second command
            r = t.run('echoer', msg='x; touch /tmp/pwned_by_agent_tools')
            assert not Path('/tmp/pwned_by_agent_tools').exists()
            try:
                t.run('echoer')
                assert False, 'missing param should raise'
            except ValueError:
                pass
            assert t.rm('echoer')['existed'] and not t.ls()
        return {'passed': True}
