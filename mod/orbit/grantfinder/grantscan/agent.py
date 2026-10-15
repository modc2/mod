"""The scraping agent — Claude Code, spawned from the claudecode module's spec.

orbit/claudecode's config.json `harness` block is the one place the CLI's
flags live (bin, base args, model/system-prompt flags); this reads it so a
flag change there lands here too. On top of that spec the run is fenced:

    --tools WebSearch,WebFetch   the only tools it has: no Bash, no file edits
    --strict-mcp-config          none of the host's MCP servers
    --json-schema                the answer is a validated {grants:[...]} object
    --max-budget-usd             a per-run spend cap (it is the host's account)
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path

CLAUDECODE = Path(__file__).resolve().parents[2] / 'claudecode' / 'config.json'
DEFAULT_SPEC = {
    'bin': 'claude',
    'args': ['--print', '--verbose', '--output-format', 'stream-json', '--dangerously-skip-permissions'],
    'model_flag': '--model',
    'system_prompt_flag': '--append-system-prompt',
}
TOOLS = 'WebSearch,WebFetch'

GRANT = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string', 'description': 'program name, e.g. "NGI Zero Commons Fund"'},
        'funder': {'type': 'string', 'description': 'who pays'},
        'url': {'type': 'string', 'description': "the program's OFFICIAL page (not a news article)"},
        'apply_url': {'type': 'string'},
        'summary': {'type': 'string', 'description': '1-3 sentences: what it funds'},
        'eligibility': {'type': 'string'},
        'ecosystem': {'type': 'string', 'description': 'chain / language / community, or ""'},
        'amount_text': {'type': 'string', 'description': 'as the funder states it, e.g. "up to EUR 50k"'},
        'amount_min_usd': {'type': 'number'},
        'amount_max_usd': {'type': 'number', 'description': 'approx USD, per grant'},
        'deadline': {'type': 'string', 'description': 'YYYY-MM-DD next deadline, or "" if rolling/unknown'},
        'status': {'type': 'string', 'enum': ['open', 'rolling', 'upcoming', 'closed', 'unknown']},
        'categories': {'type': 'array', 'items': {'type': 'string', 'enum': ['open-source', 'crypto']}},
        'tags': {'type': 'array', 'items': {'type': 'string'},
                 'description': 'lowercase kebab tags: topics, ecosystems, mechanism (retroactive, '
                                'quadratic, bounty, fellowship, milestone)'},
        'evidence_url': {'type': 'string', 'description': 'the page you actually read the details on'},
    },
    'required': ['name', 'url', 'summary', 'status', 'categories', 'tags'],
}
SCHEMA = {'type': 'object', 'properties': {'grants': {'type': 'array', 'items': GRANT}},
          'required': ['grants']}

SYSTEM = """You are grantfinder's research agent. You find grant programs on the live web and
return them as structured records. Rules:
- Use WebSearch to discover programs and WebFetch to read their OFFICIAL pages. Never answer
  from memory alone: every record must come from a page you fetched or a search result you saw
  this run. Set evidence_url to that page.
- url is the program's own page (funder's site), never a news article, listicle or aggregator.
  Aggregators and "awesome" lists are good for DISCOVERY: read them, then go to the source.
- One record per distinct program/round (a foundation with three separate programs = three).
- status: open (accepting now, deadline known), rolling (accepting, no deadline), upcoming,
  closed (ended, no next round announced), unknown. Today is {today}; a past deadline is not open.
- Amounts: copy the funder's wording into amount_text; convert to approximate USD numbers.
- Be exhaustive within your lane: aim for every real program in it, typically 15-40."""


def spec() -> dict:
    """claudecode's harness spec, or compiled defaults when that module is missing."""
    try:
        s = json.loads(CLAUDECODE.read_text()).get('harness') or {}
    except Exception:
        s = {}
    return {k: s.get(k, v) for k, v in DEFAULT_SPEC.items()}


def available() -> dict:
    s = spec()
    p = shutil.which(s['bin'])
    return {'available': bool(p), 'bin': p or s['bin'], 'spec_from': str(CLAUDECODE) if CLAUDECODE.exists() else 'defaults'}


def command(prompt: str, system: str, model: str = 'sonnet', budget: float = 1.5) -> list:
    s = spec()
    return ([s['bin']] + list(s['args']) +
            [s['model_flag'], model, '--tools', TOOLS, '--strict-mcp-config',
             '--max-budget-usd', str(budget), '--json-schema', json.dumps(SCHEMA),
             s['system_prompt_flag'], system, prompt])


def run(prompt: str, model: str = 'sonnet', budget: float = 1.5, timeout: int = 900,
        on_step=None) -> dict:
    """One agent run -> {grants, cost, turns, steps, error}. Blocks until done."""
    import datetime
    if not available()['available']:
        raise RuntimeError('claude CLI not on PATH — npm install -g @anthropic-ai/claude-code')
    system = SYSTEM.replace('{today}', datetime.date.today().isoformat())
    cwd = tempfile.mkdtemp(prefix='grantfinder-')   # it has no file tools; somewhere empty to stand
    proc = subprocess.Popen(command(prompt, system, model, budget), cwd=cwd,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, env=dict(os.environ))
    errs = deque(maxlen=20)
    threading.Thread(target=lambda: [errs.append(l.rstrip()) for l in proc.stderr], daemon=True).start()
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    steps, result = [], None
    try:
        for line in proc.stdout:
            if not line.startswith('{'):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get('type') == 'assistant':
                for b in ev.get('message', {}).get('content', []):
                    if b.get('type') == 'tool_use' and b.get('name') in ('WebSearch', 'WebFetch'):
                        inp = b.get('input') or {}
                        st = {'tool': b['name'], 'arg': inp.get('query') or inp.get('url') or ''}
                        steps.append(st)
                        if on_step:
                            on_step(st)
            elif ev.get('type') == 'result':
                result = ev
    finally:
        killer.cancel()
        code = proc.wait()
        shutil.rmtree(cwd, ignore_errors=True)
    if not result:
        return {'grants': [], 'cost': 0, 'turns': 0, 'steps': steps,
                'error': f'agent exited {code} with no result: ' + ' | '.join(list(errs)[-3:])}
    out = result.get('structured_output') or {}
    if not out and isinstance(result.get('result'), str):
        try:
            out = json.loads(result['result'])
        except json.JSONDecodeError:
            out = {}
    err = None
    if result.get('is_error') or result.get('subtype') not in (None, 'success'):
        err = f"{result.get('subtype')}: {str(result.get('result') or '')[:300]}"
    return {'grants': out.get('grants') or [], 'cost': float(result.get('total_cost_usd') or 0),
            'turns': int(result.get('num_turns') or 0), 'steps': steps, 'error': err}


def lane_prompt(lane: dict) -> str:
    return (f"Lane: {lane['title']} (category: {lane['category']}).\n"
            f"Find every grant program in this lane. Start from these, then search for more:\n"
            f"{lane['brief']}\n"
            f"Every record's categories must include \"{lane['category']}\" (add the other one too "
            f"if it also applies). Check each program's official page for current status, "
            f"amounts and deadline before you return it.")


def verify_prompt(g: dict) -> str:
    return (f"Re-check ONE grant program against its official page and return exactly one record "
            f"with current details (status, deadline, amounts, apply_url). Keep the same url unless "
            f"the program moved; if it moved, use the new official url.\n"
            f"Program: {g.get('name')} — {g.get('funder', '')}\nURL: {g.get('url')}")
