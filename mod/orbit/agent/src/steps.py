"""
steps - reading a tool call out of whatever the model actually wrote

The loop asks for one step inside anchors:

    <STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP>

Frontier models comply. Everything else emits the convention it was trained
on, and each of those is a tool call this module used to throw away:

    <tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>
    ```json\n{"tool": "bash", "params": {…}}\n```
    [bash(command="ls -la")]                     ← LFM2/Llama pythonic
    {"function": {"name": "read", "arguments": "{\\"file_path\\": \\"a.py\\"}"}}
    TOOL: bash\nPARAMS: {"command": "ls"}

None of that is ambiguity — the intent is plain in every one. So the anchors
stay the asked-for format (they frame the streaming parser, which is what
lets a step be spotted before the response ends), and this file is the
fallback that reads the rest.

Two halves:

    extract()    text -> candidate {tool, params} dicts, format-agnostic
    normalize()  candidate -> a step this registry can actually run:
                 the name a model invented mapped onto a real tool, and its
                 params onto that tool's real parameters

normalize() is schema-driven wherever it can be. A hardcoded alias table
ages badly — the tool it names gets renamed and the alias silently rots —
so the table only covers habits (read_file, cmd=, file=), and everything
else is resolved against the live tool schema: a param the tool doesn't
have, when the tool has exactly one required param missing, is that param.

Usage:
    from .steps import parse
    parse(raw_text, schema=agent.tool_schema())
"""
import ast
import json
import re
from typing import Any, Dict, List, Optional

# names models reach for, mapped onto the tools this module ships. Only
# habits belong here: things a model types because another harness taught
# it to, not synonyms invented on the spot.
TOOL_ALIASES = {
    'read_file': 'read', 'readfile': 'read', 'open_file': 'read',
    'view_file': 'read', 'cat': 'read', 'view': 'read',
    'write_file': 'write', 'writefile': 'write', 'create_file': 'write',
    'save_file': 'write', 'str_replace': 'edit', 'str_replace_editor': 'edit',
    'edit_file': 'edit', 'replace': 'edit', 'apply_patch': 'patch',
    'run_command': 'bash', 'run_shell': 'bash', 'shell': 'bash',
    'terminal': 'bash', 'execute': 'bash', 'exec': 'bash', 'sh': 'bash',
    'command': 'bash', 'run': 'bash', 'bash_tool': 'bash',
    'list_files': 'tree', 'ls': 'tree', 'list_directory': 'tree', 'dir': 'tree',
    'find': 'glob', 'find_files': 'glob', 'file_search': 'glob',
    'search_files': 'grep', 'grep_search': 'grep', 'ripgrep': 'grep', 'rg': 'grep',
    'web_search': 'search', 'google': 'search', 'browse': 'fetch',
    'http': 'fetch', 'curl': 'fetch', 'get': 'fetch',
    'reasoning': 'think', 'reflect': 'think', 'thinking': 'think',
    'done': 'finish', 'complete': 'finish', 'final_answer': 'finish',
    'answer': 'finish', 'respond': 'finish', 'reply': 'finish',
    'end': 'finish', 'stop': 'finish', 'finish_task': 'finish',
    'submit': 'finish', 'memory_search': 'recall', 'save_memory': 'remember',
}

# param synonyms, applied only when the tool has no parameter by that name
# and does have the one it maps to
PARAM_ALIASES = {
    'path': 'file_path', 'filepath': 'file_path', 'file': 'file_path',
    'filename': 'file_path', 'file_name': 'file_path', 'target': 'file_path',
    'cmd': 'command', 'shell_command': 'command', 'script': 'command',
    'text': 'content', 'body': 'content', 'data': 'content', 'code': 'content',
    'contents': 'content', 'new_content': 'content',
    'old': 'old_string', 'new': 'new_string', 'old_text': 'old_string',
    'new_text': 'new_string', 'old_str': 'old_string', 'new_str': 'new_string',
    'regex': 'pattern', 'search': 'pattern', 'q': 'query',
    'directory': 'path', 'dir': 'path', 'folder': 'path', 'cwd': 'path',
    'url': 'url', 'thought': 'thought', 'reasoning': 'thought',
    'answer': 'summary', 'response': 'summary', 'message': 'summary',
    'result': 'summary', 'output': 'summary', 'final_answer': 'summary',
}

# where a step's name lives, whatever the convention
NAME_KEYS = ('tool', 'name', 'tool_name', 'function_name', 'action', 'use_tool',
             'tool_to_use', 'call')
# …and its arguments
ARG_KEYS = ('params', 'arguments', 'args', 'parameters', 'input', 'tool_input',
            'kwargs', 'argument', 'inputs', 'param')

# tools the loop implements itself — never in the registry, always callable
VIRTUAL_TOOLS = ('finish', 'response', 'review')

_FENCE = re.compile(r'```(?:json|python|tool_code|js)?\s*(.+?)(?:```|$)', re.S | re.I)
_TAGGED = re.compile(
    r'<\s*(STEP|tool_call|function_call|tool▁call|invoke)\s*>(.*?)'
    r'(?:<\s*/\s*\1\s*>|$)', re.S | re.I)
# the same thing again as a special token rather than a tag: LFM2.5 and
# friends emit `<|tool_call_start|>[bash(command="ls")]<|tool_call_end|>`
_SPECIAL = re.compile(r'<\|\s*tool_call_start\s*\|>(.*?)(?:<\|\s*tool_call_end\s*\|>|$)',
                      re.S | re.I)
# a reasoning block is the model's scratchpad. The call it settles on is
# written after it, and the calls it talks itself out of are inside it.
THINK = re.compile(r'<\s*(think|thinking|reasoning|scratchpad)\s*>.*?'
                   r'(?:<\s*/\s*\1\s*>|$)', re.S | re.I)
_PYCALL = re.compile(r'([A-Za-z_][A-Za-z0-9_.]{1,40})\s*\(([^()]{0,4000})\)', re.S)
_LABELLED = re.compile(
    r'^\s*(?:tool|action|function)\s*[:=]\s*["\']?([A-Za-z_][\w.\-]*)["\']?\s*$'
    r'(?:\s*^\s*(?:params|arguments|args|input)\s*[:=]\s*(.+?)$)?',
    re.I | re.M | re.S)


# ── finding JSON in prose ────────────────────────────────────────────

def json_objects(text: str, limit: int = 12) -> List[str]:
    """Every brace-balanced {...} in text, outermost first, strings respected."""
    out, i, n = [], 0, len(text)
    while i < n and len(out) < limit:
        if text[i] != '{':
            i += 1
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    out.append(text[i:j + 1])
                    i = j
                    break
        i += 1
    return out


def loads(raw: str) -> Optional[Any]:
    """JSON, then Python literal — small models quote with '' about as often
    as with "". Returns None rather than raising: a candidate that doesn't
    parse is a candidate, not an error."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        return ast.literal_eval(raw)
    except Exception:
        return None


# ── candidate extraction ─────────────────────────────────────────────

def _candidates_from(value: Any) -> List[Dict[str, Any]]:
    """A parsed JSON value flattened into candidate step dicts."""
    if isinstance(value, dict):
        # {"tool_calls": [...]} — the OpenAI envelope, occasionally emitted
        # as text by models trained to produce it natively
        for key in ('tool_calls', 'steps', 'plan', 'actions', 'calls'):
            inner = value.get(key)
            if isinstance(inner, list):
                return [c for item in inner for c in _candidates_from(item)]
        return [value]
    if isinstance(value, list):
        return [c for item in value for c in _candidates_from(item)]
    return []


def _pythonic(text: str, known) -> List[Dict[str, Any]]:
    """`[bash(command="ls -la")]` — the pythonic tool-call convention LFM2
    and Llama-style tool models were trained on.

    Only a name the registry actually has counts: prose is full of `foo(bar)`
    and a false positive here would run something the model never asked for.
    """
    out = []
    for name, arglist in _PYCALL.findall(text):
        base = name.split('.')[-1].strip().lower()
        if resolve_name(base, known) is None:
            continue
        params, ok = {}, True
        try:
            call = ast.parse(f"_f({arglist})", mode='eval').body
        except SyntaxError:
            continue
        for kw in getattr(call, 'keywords', []):
            try:
                params[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                ok = False
        if not ok:
            continue
        # a lone positional (`bash("ls")`) is the tool's own first param;
        # normalize() places it once it knows which tool this is
        positional = []
        for arg in getattr(call, 'args', []):
            try:
                positional.append(ast.literal_eval(arg))
            except Exception:
                ok = False
        if not ok:
            continue
        if positional:
            params['_positional'] = positional
        out.append({'tool': base, 'params': params})
    return out


def strategies(text: str, known=None) -> List[List[Dict[str, Any]]]:
    """Candidate steps per reading of the text, most-explicit reading first.

    Kept as a list of lists rather than one flat list so the caller can stop
    at the first reading that yields a *runnable* step: a response carrying a
    real <STEP> must not also be mined for the JSON example quoted inside it,
    but a reading that turns out to name no tool at all shouldn't shadow the
    one that does.
    """
    text = text or ''
    if not text.strip():
        return []
    # a thinking model reasons its way past several calls before picking one.
    # Read what it settled on; only fall back to the scratchpad if that is
    # all there is (a truncated response can end mid-thought).
    outside = THINK.sub(' ', text)
    if outside.strip() and outside.strip() != text.strip():
        found = strategies(outside, known=known)
        if found:
            return found

    def anchored():
        out = []
        for _, body in list(_TAGGED.findall(text)) + \
                [('special', b) for b in _SPECIAL.findall(text)]:
            for value in (loads(body), *(loads(o) for o in json_objects(body))):
                cands = _candidates_from(value)
                if cands:
                    out.extend(cands)
                    break
            else:
                # the anchors are one convention and what goes between them is
                # another: `<|tool_call_start|>[bash(command="ls")]`
                out.extend(_pythonic(body, known))
        return out

    def fenced():
        out = []
        for body in _FENCE.findall(text):
            value = loads(body)
            if value is None:
                value = next((loads(o) for o in json_objects(body)), None)
            out.extend(_candidates_from(value))
        return out

    def bare():
        return [c for obj in json_objects(text)
                for c in _candidates_from(loads(obj))]

    def labelled():
        out = []
        for name, args in _LABELLED.findall(text):
            params = loads(args) if args else {}
            out.append({'tool': name,
                        'params': params if isinstance(params, dict) else {}})
        return out

    readings = []
    for strategy in (anchored, fenced, bare,
                     lambda: _pythonic(text, known), labelled):
        try:
            found = strategy()
        except Exception:
            found = []
        if found:
            readings.append(found)
    return readings


def extract(text: str, known=None) -> List[Dict[str, Any]]:
    """Every candidate step in the text, best reading first."""
    return [c for reading in strategies(text, known=known) for c in reading]


# ── normalisation ────────────────────────────────────────────────────

def resolve_name(name: str, known=None) -> Optional[str]:
    """The registry's name for what the model called it, or None.

    With no `known` set to check against, anything plausible passes — the
    registry itself is the final judge and gives a better error than a guess
    here would.
    """
    if not isinstance(name, str):
        return None
    base = name.strip().strip('"\'').lower()
    base = base.split('.')[-1] if base.startswith(('functions.', 'tools.')) else base
    if not base:
        return None
    if known is None:
        return TOOL_ALIASES.get(base, base)
    known = set(known) | set(VIRTUAL_TOOLS)
    for candidate in (base, TOOL_ALIASES.get(base), base.replace('_', ''),
                      base.replace('-', '_'), base.rstrip('s')):
        if candidate in known:
            return candidate
    # `mod.git` may arrive as `git` when the fleet tool is the one loaded
    for k in known:
        if k.split('.')[-1] == base:
            return k
    return None


def _params_of(raw: Dict[str, Any], tool_key: str) -> Dict[str, Any]:
    """The arguments out of a candidate, wherever the model put them."""
    for key in ARG_KEYS:
        if key in raw:
            value = raw[key]
            if isinstance(value, str):          # double-encoded arguments…
                value = loads(value) if loads(value) is not None else value
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, list):
                return {'_positional': list(value)}
            # …or arguments that were never encoded at all: `"params": "the
            # thing to think about"` is the tool's one parameter, unwrapped
            if value is not None:
                return {'_positional': [value]}
    # no arguments key at all: the rest of the object IS the arguments
    # ({"tool": "bash", "command": "ls"})
    rest = {k: v for k, v in raw.items()
            if k not in NAME_KEYS and k not in ARG_KEYS and not k.startswith('_')}
    return rest


def _required(schema: Dict[str, Any]) -> List[str]:
    return [p for p, d in (schema.get('params') or {}).items()
            if isinstance(d, dict) and d.get('required')]


def _fit_params(tool: str, params: Dict[str, Any],
                schema: Dict[str, Any] = None) -> Dict[str, Any]:
    """Map what the model passed onto what the tool actually takes.

    Three passes, each only firing when the tool confirms it: a synonym for a
    real parameter, a positional value for the first required one, and — the
    one that saves the most runs — a single unrecognised argument on a tool
    with a single unfilled required parameter, which is that parameter under
    another name.
    """
    params = dict(params or {})
    positional = params.pop('_positional', None)
    if schema is None:
        if positional:
            params.setdefault('_positional', positional)
        return params
    valid = set((schema.get('params') or {}))
    required = _required(schema)

    for given in list(params):
        if given in valid:
            continue
        alias = PARAM_ALIASES.get(given.lower())
        if alias and alias in valid and alias not in params:
            params[alias] = params.pop(given)

    if positional:
        slots = [p for p in ((schema.get('params') or {})) if p not in params]
        for value, slot in zip(positional, required + [s for s in slots
                                                       if s not in required]):
            params.setdefault(slot, value)

    missing = [p for p in required if p not in params]
    strays = [p for p in params if p not in valid]
    if len(missing) == 1 and len(strays) == 1:
        params[missing[0]] = params.pop(strays[0])
    return params


def normalize(raw: Any, schemas: Dict[str, Dict] = None,
              known=None) -> Optional[Dict[str, Any]]:
    """One candidate into a runnable step, or None if it isn't a call at all."""
    if not isinstance(raw, dict):
        return None
    # OpenAI shape: {"type": "function", "function": {"name": …, "arguments": …}}
    inner = raw.get('function')
    if isinstance(inner, dict) and any(k in inner for k in NAME_KEYS):
        merged = dict(inner)
        merged.setdefault('id', raw.get('id'))
        raw = merged
    known = known if known is not None else (list(schemas) if schemas else None)

    name_key = next((k for k in NAME_KEYS if isinstance(raw.get(k), str)), None)
    if name_key:
        tool = resolve_name(raw[name_key], known)
        params = _params_of(raw, name_key)
    else:
        # {"bash": {"command": "ls"}} — the name is the only key
        entries = [(k, v) for k, v in raw.items() if not k.startswith('_')]
        if len(entries) != 1:
            return None
        key, value = entries[0]
        tool = resolve_name(key, known)
        params = dict(value) if isinstance(value, dict) else \
            ({'_positional': [value]} if value is not None else {})
    if not tool:
        return None
    params = _fit_params(tool, params, (schemas or {}).get(tool))
    return {'tool': tool, 'params': {k: v for k, v in params.items()
                                     if not str(k).startswith('_')}}


def parse(text: str, schemas: Dict[str, Dict] = None,
          known=None) -> List[Dict[str, Any]]:
    """Every tool call in one model response, in order. [] means it wrote prose."""
    known = known if known is not None else (list(schemas) if schemas else None)
    for reading in strategies(text, known=known):
        out = [step for step in
               (normalize(c, schemas=schemas, known=known) for c in reading)
               if step]
        if out:
            return out
    return []


def first(text: str, schemas: Dict[str, Dict] = None,
          known=None) -> Optional[Dict[str, Any]]:
    """The one step the loop runs — models asked for one often write two."""
    found = parse(text, schemas=schemas, known=known)
    return found[0] if found else None
