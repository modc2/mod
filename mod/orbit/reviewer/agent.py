"""The deep engine: hand a target to the claudecode agent for a written,
read-only review. Speaks the agent protocol (POST /run/stream, SSE
step/done/error) to orbit/claudecode on loopback, so the CLI spawn spec,
credentials and cost metering stay in one place. If that server is not up,
the static review is all you get and ``available()`` says so.
"""

import json
import os
import urllib.error
import urllib.request

AGENT_URL = os.environ.get('REVIEWER_AGENT_URL', 'http://127.0.0.1:51260')
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 1800

GOAL = """You are a senior code reviewer for a fleet of small composable modules (the mod protocol).
A module is a folder with config.json (name, version, description, anchor, port, fns, api_fns,
endpoints, urls), an anchor mod.py holding `class Mod` whose public methods are the module's
functions, a README.md, tests/, and usually a serve.py + web/ console.

Rules of this review:
- READ ONLY. Do not create, edit, move or delete any file. Do not run anything that changes state.
- Work only inside the path you are given. Use absolute paths.
- Be concrete: every finding names file:line, says what is wrong, why it matters, and the fix.
- Rank by severity: high (breaks, loses data, security), medium (bugs waiting), low (hygiene).
- Finish with a short verdict paragraph and a score 0-100.

Answer in markdown with these sections: ## Summary, ## Findings (a list, most severe first,
each as `file:line` — message — fix), ## Verdict (one paragraph, then `Score: NN/100`).
"""


def available(timeout=2):
    """Is the claudecode agent answering on loopback?"""
    try:
        with urllib.request.urlopen(AGENT_URL.rstrip('/') + '/agents', timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _query(path, static_findings, focus=''):
    lines = [f'Review the module at {path}.']
    if focus:
        lines.append(f'Focus: {focus}')
    if static_findings:
        lines.append('\nA static pass already found these; confirm, dismiss, or go deeper, '
                     'and look for what it cannot see (logic, races, API contract, UX):')
        for f in static_findings[:60]:
            lines.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['rule']}: {f['message']}")
    return '\n'.join(lines)


def review(path, static_findings=None, focus='', model=None, timeout=DEFAULT_TIMEOUT, on_step=None):
    """Returns {ok, text, meta, steps} — or {ok: False, error} if unreachable."""
    timeout = max(30, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    body = json.dumps({'query': _query(path, static_findings or [], focus), 'goal': GOAL,
                       'path': path, 'model': model or None, 'timeout': timeout}).encode()
    req = urllib.request.Request(AGENT_URL.rstrip('/') + '/run/stream', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout + 60)
    except (urllib.error.URLError, OSError) as e:
        return {'ok': False, 'error': f'claudecode agent unreachable at {AGENT_URL}: {e}',
                'text': '', 'meta': {}, 'steps': []}
    steps, done, err = [], None, None
    with resp:
        for raw in resp:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            t = ev.get('type')
            if t == 'step':
                s = ev.get('step') or {}
                steps.append(s)
                if on_step:
                    try:
                        on_step(s)
                    except Exception:  # noqa: BLE001 — a listener never breaks the run
                        pass
            elif t == 'done':
                done = ev
            elif t == 'error':
                err = ev.get('error') or 'agent error'
    last = steps[-1] if steps else {}
    usage = (done or {}).get('usage') or last.get('params', {}).get('usage') or {}
    meta = {'agent': 'orbit/claudecode', 'task_id': (done or {}).get('task_id'),
            'turns': usage.get('turns'), 'cost_usd': usage.get('cost'), 'model': usage.get('model')}
    if err:
        return {'ok': False, 'error': err, 'text': '', 'meta': meta, 'steps': steps}
    if last.get('tool') == 'error':
        return {'ok': False, 'error': last.get('error') or 'agent failed', 'text': '',
                'meta': meta, 'steps': steps}
    text = last.get('params', {}).get('summary') if last.get('tool') == 'finish' else ''
    if not text:
        # fall back to the last text-bearing step
        for s in reversed(steps):
            p = s.get('params') or {}
            cand = p.get('summary') or p.get('text') or (s.get('result') if isinstance(s.get('result'), str) else '')
            if cand:
                text = cand
                break
    return {'ok': done is not None, 'text': text or '', 'meta': meta, 'steps': steps}


def parse_score(text):
    """Pull `Score: NN/100` out of the agent's markdown, if it gave one."""
    import re
    m = re.search(r'[Ss]core:?\s*\**\s*(\d{1,3})\s*/\s*100', text or '')
    if m:
        return max(0, min(100, int(m.group(1))))
    return None
