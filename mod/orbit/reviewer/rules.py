"""The static review engine: rules, the scanner, and the score.

Everything here is stdlib and offline. A review is a list of findings:

    {'rule': 'fleet.pattern_kill', 'severity': 'high', 'file': 'mod.py',
     'line': 42, 'message': '...', 'fix': '...'}

Three rule families:

* ``protocol.*`` — does the directory look like a mod? config.json present
  and parseable, name matches the folder, anchor exists and holds a ``Mod``
  class, every declared fn is a method (and the other way round), ports do
  not collide with a sibling, README and tests exist.
* ``fleet.*`` — the house rules this box has learned the hard way: pattern
  kills take the fleet down, git stash wrecks the repo, native dialogs are
  banned in consoles, secrets never sit in source, Cloudflare swaps 5xx
  bodies, the anchor dir must be appended (never prepended) to sys.path.
* ``py.*`` / ``js.*`` / ``hygiene.*`` — plain code hygiene: syntax errors,
  bare excepts, eval/exec, shell=True, TODO/FIXME, oversized files.

Line rules honour two pragmas, so a file can document a dangerous pattern
without being flagged for it:

    # reviewer: off        ... # reviewer: on        (a block)
    some code              # reviewer: ignore       (one line)
"""

import ast
import io
import json
import os
import re
import tokenize

SEVERITIES = ('high', 'medium', 'low', 'info')
WEIGHTS = {'high': 15, 'medium': 6, 'low': 2, 'info': 0}

SKIP_DIRS = {'node_modules', '.git', '__pycache__', '.next', 'dist', 'build',
             'target', 'venv', '.venv', 'out', '.pytest_cache', '.mypy_cache',
             'coverage', '.cache', 'vendor', 'snapshots'}
CODE_EXT = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.sh', '.rs',
            '.go', '.html', '.css', '.toml', '.yaml', '.yml', '.json', '.env',
            '.cairo', '.sol', '.move'}
MAX_FILES = 600
MAX_FILE_BYTES = 512 * 1024
BIG_FILE_LINES = 2000

# ── line rules ───────────────────────────────────────────────────────
# (id, severity, extensions or None for any, compiled regex, message, fix)
# reviewer: off
_SECRET_PATTERNS = [
    (r'sk-ant-[A-Za-z0-9_\-]{20,}', 'an Anthropic API key'),
    (r'\bAKIA[0-9A-Z]{16}\b', 'an AWS access key id'),
    (r'\bghp_[A-Za-z0-9]{30,}\b', 'a GitHub personal token'),
    (r'\bxox[baprs]-[A-Za-z0-9\-]{10,}\b', 'a Slack token'),
    (r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', 'a private key block'),
    (r'(?i)(?:private_?key|secret|api_?key|token|password)\s*[:=]\s*["\'](?:0x)?[A-Za-z0-9+/_\-]{24,}["\']',
     'a credential literal'),
]
LINE_RULES = [
    ('fleet.pattern_kill', 'high', None,
     re.compile(r'\b(?:pkill|killall)\b'),
     'Pattern kill: this box runs ~100 services, a name match takes the fleet down.',
     'Kill by port (lsof -ti tcp:PORT) or by the pid you spawned.'),
    ('fleet.git_stash', 'high', None,
     re.compile(r'\bgit\s+stash\b'),
     'git stash on this repo loses work across the fleet.',
     'Commit to a branch, or copy the files aside.'),
    ('fleet.native_dialog', 'medium', {'.js', '.jsx', '.ts', '.tsx', '.html', '.mjs'},
     re.compile(r'(?<![\w.])(?:window\.)?(?:confirm|alert|prompt)\s*\('),
     'Native browser dialog: blocks the tab and looks wrong inside a framed console.',
     'Use an inline control (a second-click confirm button, a toast, or ask()).'),
    ('fleet.sys_path_prepend', 'medium', {'.py'},
     re.compile(r'sys\.path\.insert\(\s*0\s*,'),
     'Prepending the module dir to sys.path lets this mod.py shadow the protocol\'s own `mod` package.',
     'Use sys.path.append(HERE).'),
    ('fleet.server_5xx', 'low', {'.py', '.js', '.ts', '.rs', '.go'},
     re.compile(r'(?:status(?:_code)?\s*[=(]\s*5\d\d\b|send_response\(\s*5\d\d|sendStatus\(\s*5\d\d|\.status\(\s*5\d\d|HTTP_5\d\d|INTERNAL_SERVER_ERROR|,\s*5\d\d\s*\)\s*$)'),
     'A 5xx body never reaches the user behind Cloudflare; it is swapped for a generic page.',
     'Answer handled failures with 4xx (or 200 + error field) so the reason survives.'),
    ('py.bare_except', 'low', {'.py'},
     re.compile(r'^\s*except\s*:'),
     'Bare except swallows KeyboardInterrupt and SystemExit too.',
     'Catch Exception (and say why it is broad).'),
    ('py.eval_exec', 'medium', {'.py'},
     re.compile(r'(?<![\w.])(?:eval|exec)\s*\('),
     'eval/exec on data is an injection point.',
     'Parse with json/ast.literal_eval, or dispatch through a dict of callables.'),
    ('py.shell_true', 'medium', {'.py'},
     re.compile(r'shell\s*=\s*True'),
     'shell=True builds a command line from strings; any argument becomes an injection.',
     'Pass an argv list, or bash -c with the script as one fixed argument.'),
    ('js.eval', 'medium', {'.js', '.jsx', '.ts', '.tsx', '.html', '.mjs'},
     re.compile(r'(?<![\w.])eval\s*\(|new\s+Function\s*\('),
     'eval / new Function on data is an injection point.',
     'Use JSON.parse or a lookup table.'),
    ('js.innerhtml_dynamic', 'low', {'.js', '.jsx', '.ts', '.tsx', '.html', '.mjs'},
     re.compile(r'\.innerHTML\s*[+]?=(?!\s*["\'`][^$]*["\'`]\s*;?\s*$)'),
     'innerHTML from a non-constant string renders whatever the data holds.',
     'Build nodes with textContent, or escape every interpolated value.'),
    ('hygiene.todo', 'info', None,
     re.compile(r'(?:#|//|/\*|<!--|\*)\s*(?:TODO|FIXME|XXX|HACK)\b'),
     'Open TODO/FIXME left in the code.',
     'Resolve it or turn it into a tracked task.'),
    ('hygiene.debugger', 'medium', None,
     re.compile(r'(?<![\w.])(?:breakpoint\(\)|pdb\.set_trace\(\)|debugger;)'),
     'Debugger hook left in.',
     'Remove it before deploying.'),
]
SECRET_RULES = [
    ('fleet.secret_in_source', 'high', None, re.compile(p),
     f'Looks like {what} committed to source.',
     'Move it to ~/.mod/<mod>/ or an env var; secrets stay off the repo and off-chain.')
    for p, what in _SECRET_PATTERNS
]
# reviewer: on
PROSE_ONLY_RULES = {'fleet.secret_in_source', 'hygiene.todo'}
PROSE_EXT = {'.json', '.md', '.yaml', '.yml', '.toml', '.env'}
PLACEHOLDER = re.compile(r'(?i)example|placeholder|xxx+|your[_-]?(?:key|token)|changeme|dummy|<[^>]+>')
COMMENT_LINE = re.compile(r'^\s*(?:#|//|/\*|\*|<!--|--)')

RULE_DOCS = {
    'protocol.config_missing': ('high', 'config.json is absent', 'Add config.json with name, version, description, anchor.'),
    'protocol.config_invalid': ('high', 'config.json does not parse', 'Fix the JSON.'),
    'protocol.config_field': ('medium', 'A required config field is missing', 'Add name, version, description, anchor.'),
    'protocol.name_mismatch': ('medium', 'config name differs from the folder', 'Name the module after its folder.'),
    'protocol.name_src': ('high', 'A module named "src"', 'src/ is the implementation dir of its parent, never a mod.'),
    'protocol.anchor_missing': ('high', 'The anchor file is absent', 'Add mod.py (or point "anchor" at it).'),
    'protocol.anchor_no_mod_class': ('high', 'The anchor has no Mod class', 'Define class Mod with the public fns as methods.'),
    'protocol.fn_not_method': ('medium', 'A declared fn is not a Mod method', 'Add the method or drop it from fns.'),
    'protocol.method_not_declared': ('low', 'A public Mod method is not in fns', 'Add it to fns so the CLI/gateway expose it.'),
    'protocol.api_fn_not_in_fns': ('medium', 'api_fns lists a fn outside fns', 'Every api_fn must also be a fn.'),
    'protocol.no_info_health': ('low', 'Mod lacks info() / health()', 'Add the null call and a liveness fn.'),
    'protocol.port_collision': ('high', 'Another module declares the same port', 'Pick a free port and update urls.'),
    'protocol.port_without_urls': ('low', 'A port is declared but urls are not', 'Add urls.app / urls.api.'),
    'protocol.endpoints_missing': ('low', 'api_fns declared but no endpoints docs', 'Add an endpoints map so callers know the shapes.'),
    'protocol.readme_missing': ('medium', 'README.md is absent', 'Write what it is, how to use it, the API table.'),
    'protocol.tests_missing': ('low', 'No tests directory', 'Add tests/ with offline tests and a test fn.'),
    'py.syntax_error': ('high', 'A Python file does not parse', 'Fix the syntax.'),
    'hygiene.big_file': ('low', 'A very large source file', 'Split it by concern.'),
    'hygiene.json_invalid': ('medium', 'A JSON file does not parse', 'Fix the JSON.'),
    'diff.no_added_lines': ('info', 'The diff adds nothing', 'Nothing to review.'),
}
for rid, sev, _exts, _rx, msg, fix in LINE_RULES + SECRET_RULES:
    RULE_DOCS.setdefault(rid, (sev, msg, fix))


def rules():
    """Every rule as a list of dicts, for /rules and the console."""
    out = []
    for rid, (sev, what, fix) in RULE_DOCS.items():
        out.append({'id': rid, 'family': rid.split('.')[0], 'severity': sev,
                    'what': what, 'fix': fix})
    out.sort(key=lambda r: (SEVERITIES.index(r['severity']), r['id']))
    return out


# ── helpers ──────────────────────────────────────────────────────────

def _finding(rule, file, line, message=None, fix=None, severity=None):
    sev, what, dfix = RULE_DOCS.get(rule, ('low', '', ''))
    return {'rule': rule, 'severity': severity or sev, 'file': file,
            'line': line, 'message': message or what, 'fix': fix or dfix}


def _read(path):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def iter_files(root):
    """Source files under root, bounded, skipping vendored/build dirs."""
    n = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext in CODE_EXT or fn in ('.env',):
                n += 1
                if n > MAX_FILES:
                    return
                yield os.path.join(dirpath, fn)


# ── line scanning ────────────────────────────────────────────────────

def prose_lines(text, ext):
    """Line numbers that are prose, not code: every line of a data/doc file,
    and the lines of multi-line strings (docstrings) in Python."""
    if ext in PROSE_EXT:
        return None  # None = every line
    if ext != '.py':
        return set()
    out = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                out.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return out


def scan_lines(text, rel, ext, only_lines=None):
    """Run every line rule over text. ``only_lines`` restricts to a set of
    1-based line numbers (a diff's added lines)."""
    out = []
    off = False
    rules_for = [r for r in LINE_RULES + SECRET_RULES if r[2] is None or ext in r[2]]
    prose = prose_lines(text, ext)
    seen = set()
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if 'reviewer: off' in low:
            off = True
            continue
        if 'reviewer: on' in low:
            off = False
            continue
        if off or 'reviewer: ignore' in low:
            continue
        if only_lines is not None and i not in only_lines:
            continue
        is_comment = bool(COMMENT_LINE.match(line))
        is_prose = prose is None or i in prose
        for rid, sev, _exts, rx, msg, fix in rules_for:
            if (is_comment or is_prose) and rid not in PROSE_ONLY_RULES:
                continue
            if not rx.search(line):
                continue
            if rid == 'fleet.secret_in_source' and PLACEHOLDER.search(line):
                continue
            if rid == 'fleet.server_5xx' and ext == '.py' and 'raise' in line:
                continue
            if (rid, i) in seen:
                continue
            seen.add((rid, i))
            out.append(_finding(rid, rel, i, msg, fix, sev))
    return out


def scan_file(path, rel=None):
    rel = rel or os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    text = _read(path)
    if text is None:
        return []
    return scan_text(text, rel, ext)


def scan_text(text, rel, ext=None):
    ext = (ext or os.path.splitext(rel)[1]).lower()
    out = []
    if ext == '.py':
        try:
            ast.parse(text, filename=rel)
        except SyntaxError as e:
            out.append(_finding('py.syntax_error', rel, e.lineno or 1,
                                f'Syntax error: {e.msg}'))
    if ext == '.json':
        try:
            json.loads(text)
        except ValueError as e:
            out.append(_finding('hygiene.json_invalid', rel, getattr(e, 'lineno', 1),
                                f'Invalid JSON: {e}'))
    n = text.count('\n') + 1
    if n > BIG_FILE_LINES:
        out.append(_finding('hygiene.big_file', rel, 1, f'{n} lines in one file.'))
    out.extend(scan_lines(text, rel, ext))
    return out


# ── protocol checks on a module directory ────────────────────────────

def _mod_methods(anchor_path):
    """Public method names of ``class Mod`` in the anchor (plus aliases
    assigned at class level, like ``forward = info``)."""
    text = _read(anchor_path)
    if text is None:
        return None, []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None, []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'Mod':
            names = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_prop = any(getattr(d, 'id', getattr(d, 'attr', '')) == 'property'
                                  for d in item.decorator_list)
                    if not item.name.startswith('_') and not is_prop:
                        names.append(item.name)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith('_'):
                            names.append(t.id)
            return node, names
    return None, []


def sibling_ports(module_dir):
    """{port: module_name} for every other module beside this one."""
    parent = os.path.dirname(os.path.abspath(module_dir))
    me = os.path.basename(os.path.abspath(module_dir))
    out = {}
    try:
        names = os.listdir(parent)
    except OSError:
        return out
    for name in names:
        if name == me or name.startswith('.'):
            continue
        cfg = os.path.join(parent, name, 'config.json')
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg) as f:
                c = json.load(f)
        except Exception:
            continue
        for key in ('port', 'app_port'):
            p = c.get(key)
            if isinstance(p, int) and p > 0:
                out.setdefault(p, name)
    return out


def check_protocol(module_dir):
    out = []
    module_dir = os.path.abspath(module_dir)
    folder = os.path.basename(module_dir)
    cfg_path = os.path.join(module_dir, 'config.json')
    cfg = None
    if not os.path.isfile(cfg_path):
        out.append(_finding('protocol.config_missing', 'config.json', 1))
    else:
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception as e:
            out.append(_finding('protocol.config_invalid', 'config.json', 1, f'config.json: {e}'))
    if not isinstance(cfg, dict):
        cfg = {}
    name = cfg.get('name')
    if cfg_path and os.path.isfile(cfg_path):
        for field in ('name', 'version', 'description', 'anchor'):
            if not cfg.get(field):
                out.append(_finding('protocol.config_field', 'config.json', 1,
                                    f'config.json has no "{field}".'))
        if name and name != folder and folder != 'src':
            out.append(_finding('protocol.name_mismatch', 'config.json', 1,
                                f'config name "{name}" but folder is "{folder}".'))
        if name == 'src':
            out.append(_finding('protocol.name_src', 'config.json', 1))

    # anchor
    anchor = cfg.get('anchor') or 'mod.py'
    candidates = [anchor, 'mod.py', 'src/mod.py']
    anchor_path = None
    for c in candidates:
        p = os.path.join(module_dir, c)
        if os.path.isfile(p):
            anchor_path, anchor = p, c
            break
    if anchor_path is None:
        out.append(_finding('protocol.anchor_missing', anchor, 1,
                            f'No anchor at {anchor} (nor mod.py / src/mod.py).'))
        methods = []
    else:
        node, methods = _mod_methods(anchor_path)
        if node is None:
            out.append(_finding('protocol.anchor_no_mod_class', anchor, 1))
        else:
            fns = [f for f in (cfg.get('fns') or []) if isinstance(f, str)]
            api_fns = [f for f in (cfg.get('api_fns') or []) if isinstance(f, str)]
            for f in fns:
                if f not in methods:
                    out.append(_finding('protocol.fn_not_method', 'config.json', 1,
                                        f'fns lists "{f}" but Mod has no such method.'))
            if fns:
                for mname in methods:
                    if mname not in fns and mname not in ('description', 'config'):
                        out.append(_finding('protocol.method_not_declared', anchor, node.lineno,
                                            f'Mod.{mname}() is public but not in config fns.'))
            for f in api_fns:
                if f not in fns:
                    out.append(_finding('protocol.api_fn_not_in_fns', 'config.json', 1,
                                        f'api_fns lists "{f}" which is not in fns.'))
            if api_fns and not cfg.get('endpoints'):
                out.append(_finding('protocol.endpoints_missing', 'config.json', 1))
            missing = [f for f in ('info', 'health') if f not in methods]
            if missing:
                out.append(_finding('protocol.no_info_health', anchor, node.lineno,
                                    f'Mod lacks {", ".join(missing)}().'))

    # ports
    port = cfg.get('port')
    if isinstance(port, int) and port > 0:
        taken = sibling_ports(module_dir)
        if port in taken:
            out.append(_finding('protocol.port_collision', 'config.json', 1,
                                f'port {port} is also declared by "{taken[port]}".'))
        if not cfg.get('urls'):
            out.append(_finding('protocol.port_without_urls', 'config.json', 1))

    # docs + tests
    if not any(os.path.isfile(os.path.join(module_dir, r)) for r in ('README.md', 'readme.md', 'README')):
        out.append(_finding('protocol.readme_missing', 'README.md', 1))
    has_tests = any(os.path.isdir(os.path.join(module_dir, d)) for d in ('tests', 'test', 'src/tests'))
    has_tests = has_tests or any(f.startswith('test_') and f.endswith('.py')
                                 for f in os.listdir(module_dir) if os.path.isfile(os.path.join(module_dir, f)))
    if not has_tests:
        out.append(_finding('protocol.tests_missing', 'tests/', 1))
    return out, cfg


# ── diffs ────────────────────────────────────────────────────────────

HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def scan_diff(diff_text):
    """Review only the lines a unified diff adds, at their new line numbers."""
    out = []
    file, new_line, added = None, 0, {}
    for raw in diff_text.splitlines():
        if raw.startswith('+++ '):
            file = raw[4:].strip()
            if file.startswith('b/'):
                file = file[2:]
            added.setdefault(file, [])
            continue
        if raw.startswith('--- ') or raw.startswith('diff ') or raw.startswith('index '):
            continue
        m = HUNK.match(raw)
        if m:
            new_line = int(m.group(1))
            continue
        if file is None:
            continue
        if raw.startswith('+'):
            added[file].append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith('-'):
            continue
        else:
            new_line += 1
    total = 0
    for f, lines in added.items():
        if not lines:
            continue
        total += len(lines)
        # rebuild a sparse text so line numbers hold
        top = max(n for n, _ in lines)
        buf = [''] * top
        for n, text in lines:
            buf[n - 1] = text
        out.extend(scan_lines('\n'.join(buf), f, os.path.splitext(f)[1].lower(),
                              only_lines={n for n, _ in lines}))
    if total == 0:
        out.append(_finding('diff.no_added_lines', '-', 0))
    return out, {'files': len(added), 'added_lines': total}


# ── the review ───────────────────────────────────────────────────────

def score(findings):
    """0-100 and a letter. Only open findings count."""
    pen = 0
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.get('verdict') in ('fixed', 'ignored'):
            continue
        counts[f['severity']] += 1
        pen += WEIGHTS[f['severity']]
    s = max(0, 100 - pen)
    grade = 'A' if s >= 90 else 'B' if s >= 75 else 'C' if s >= 60 else 'D' if s >= 40 else 'F'
    return {'score': s, 'grade': grade, 'counts': counts}


def _sort(findings):
    findings.sort(key=lambda f: (SEVERITIES.index(f['severity']), f['file'], f.get('line') or 0))
    return findings


def review_dir(path):
    """A module (or any directory): protocol checks + every source file."""
    path = os.path.abspath(path)
    findings, cfg = check_protocol(path)
    files = 0
    for fp in iter_files(path):
        files += 1
        findings.extend(scan_file(fp, os.path.relpath(fp, path)))
    return _sort(findings), {'kind': 'module' if cfg else 'dir', 'files': files,
                             'name': cfg.get('name') if cfg else None}


def review_file(path):
    path = os.path.abspath(path)
    return _sort(scan_file(path, os.path.basename(path))), {'kind': 'file', 'files': 1}


def review_code(code, filename='snippet.py'):
    return _sort(scan_text(code, filename)), {'kind': 'code', 'files': 1}


def review_diff(diff):
    findings, meta = scan_diff(diff)
    meta['kind'] = 'diff'
    return _sort(findings), meta
