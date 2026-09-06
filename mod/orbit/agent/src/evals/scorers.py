"""
scorers - declarative scoring functions for agent evals

Each scorer takes a `trace` (list of step dicts produced by Agent.run / run_plan)
and a `spec` dict from the scenario. It returns:
    {"passed": bool, "score": float, "reason": str}

Scorers are pure functions over the trace + filesystem state, so evals can run
without an LLM judge. New scorers register themselves via SCORERS[name] = fn.
"""
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _flatten(trace: List[Any]) -> List[Dict[str, Any]]:
    """Flatten history-of-plans into a flat list of step dicts."""
    out = []
    for item in trace or []:
        if isinstance(item, list):
            out.extend(s for s in item if isinstance(s, dict))
        elif isinstance(item, dict):
            out.append(item)
    return out


def steps_of(trace) -> List[Dict[str, Any]]:
    """The flattened step list — what the arena counts against a step budget."""
    return _flatten(trace)


def _tools_used(trace) -> List[str]:
    return [s.get('tool', '') for s in _flatten(trace) if s.get('tool')]


# the loop breaks on these without running a tool, so they never carry a
# `result` — the agent's actual answer is in their params
TERMINAL_TOOLS = ('finish', 'review', 'response')


def _result_text(trace) -> str:
    parts = []
    for s in _flatten(trace):
        if s.get('tool') in TERMINAL_TOOLS:
            params = s.get('params') or {}
            parts.extend(str(params[f]) for f in ('summary', 'text', 'answer', 'content')
                         if isinstance(params.get(f), str))
        r = s.get('result')
        if r is None:
            continue
        if isinstance(r, dict):
            for v in r.values():
                if isinstance(v, str):
                    parts.append(v)
        else:
            parts.append(str(r))
    return '\n'.join(parts)


# ── individual scorers ───────────────────────────────────────────────

def tool_used(trace, spec) -> Dict[str, Any]:
    """Pass if any step used the named tool."""
    name = spec.get('tool', spec.get('skill', ''))
    used = _tools_used(trace)
    ok = name in used
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"tool {name!r} {'used' if ok else 'not used'} (used: {used})"}


def tool_not_used(trace, spec) -> Dict[str, Any]:
    """Pass if the named tool was NOT used."""
    name = spec.get('tool', spec.get('skill', ''))
    used = _tools_used(trace)
    ok = name not in used
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"tool {name!r} {'absent' if ok else 'present'} (used: {used})"}


def no_errors(trace, spec) -> Dict[str, Any]:
    """Pass if no step has an `error` field set."""
    errs = [s.get('error') for s in _flatten(trace) if s.get('error')]
    ok = len(errs) == 0
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"{len(errs)} error(s)" + (f": {errs[0]}" if errs else "")}


def finished(trace, spec) -> Dict[str, Any]:
    """Pass if the agent emitted a finish or response step."""
    tools = _tools_used(trace)
    ok = any(t in ('finish', 'response') for t in tools)
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': "finished" if ok else "did not finish"}


def max_steps(trace, spec) -> Dict[str, Any]:
    """Pass if total step count is <= spec['n']."""
    n = int(spec.get('n', 25))
    count = len(_flatten(trace))
    ok = count <= n
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"used {count} step(s), max {n}"}


def contains(trace, spec) -> Dict[str, Any]:
    """Pass if any step result contains the substring."""
    needle = spec.get('text', '')
    text = _result_text(trace)
    ok = needle in text
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"substring {needle!r} {'found' if ok else 'missing'}"}


def regex(trace, spec) -> Dict[str, Any]:
    """Pass if any step result matches the regex."""
    pattern = spec.get('pattern', '')
    text = _result_text(trace)
    ok = bool(re.search(pattern, text))
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"pattern {pattern!r} {'matched' if ok else 'unmatched'}"}


def file_exists(trace, spec) -> Dict[str, Any]:
    """Pass if the file at spec['path'] exists on disk after the run."""
    p = Path(spec.get('path', '')).expanduser()
    ok = p.exists()
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"{p} {'exists' if ok else 'missing'}"}


def file_contains(trace, spec) -> Dict[str, Any]:
    """Pass if the file exists and its contents include the substring."""
    p = Path(spec.get('path', '')).expanduser()
    needle = spec.get('text', '')
    if not p.exists():
        return {'passed': False, 'score': 0.0, 'reason': f"{p} missing"}
    body = p.read_text(errors='replace')
    ok = needle in body
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"file substring {needle!r} {'found' if ok else 'missing'}"}


def file_not_contains(trace, spec) -> Dict[str, Any]:
    """Pass if the file exists and its contents do NOT include the substring.

    The scorer an edit task needs: every other file scorer here is satisfied by
    handing the fixture straight back, so a task that says "improve this file"
    scores full marks for touching nothing. Pass the fixture as `text` and a
    no-op fails.
    """
    p = Path(spec.get('path', '')).expanduser()
    needle = spec.get('text', '')
    if not p.exists():
        return {'passed': False, 'score': 0.0, 'reason': f"{p} missing"}
    ok = needle not in p.read_text(errors='replace')
    label = needle if len(needle) < 60 else f"{needle[:57]}..."
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"file substring {label!r} {'absent' if ok else 'still present'}"}


def file_regex(trace, spec) -> Dict[str, Any]:
    """Pass if the file exists and its contents match the regex.

    The substring check breaks on whitespace an agent is free to choose —
    `a + b` vs `a+b` is the same fix — so a written file is matched by pattern.
    """
    p = Path(spec.get('path', '')).expanduser()
    pattern = spec.get('pattern', '')
    if not p.exists():
        return {'passed': False, 'score': 0.0, 'reason': f"{p} missing"}
    ok = bool(re.search(pattern, p.read_text(errors='replace')))
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"file pattern {pattern!r} {'matched' if ok else 'unmatched'}"}


def step_count_at_least(trace, spec) -> Dict[str, Any]:
    """Pass if at least spec['n'] steps were taken (sanity check)."""
    n = int(spec.get('n', 1))
    count = len(_flatten(trace))
    ok = count >= n
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"{count} step(s), required >= {n}"}


def openarena(trace, spec) -> Dict[str, Any]:
    """Grade the program the run produced against an openarena task.

    The one scorer here that is not a pure function of the trace and the disk:
    it hands the program to the openarena module, whose judge runs it in a
    throwaway sandbox against every graded case — including the hidden ones,
    which is exactly why the grading does not happen in this process.

        {"type": "openarena", "task": "fizzbuzz",
         "path": "solution.py", "language": "python"}

    The program is the file at `path` if the agent wrote one, and otherwise the
    last fenced code block it showed — an agent that answers in chat has still
    answered. `score` is the weighted fraction of cases that passed, so a
    near-miss ranks above a blank, and `passed` means every case.

    A judge that cannot be reached returns `void`: the arena drops that match
    from the rating rather than recording it as a loss the agent never earned.
    """
    from src.arena import openarena as oa

    slug = str(spec.get('task') or spec.get('slug') or '').strip()
    if not slug:
        return {'passed': False, 'score': 0.0, 'void': True,
                'reason': 'openarena scorer needs `task`'}

    lang_hint = ''
    code = ''
    path = spec.get('path') or ''
    if path:
        p = Path(path).expanduser()
        if p.exists():
            code = p.read_text(errors='replace')
    if not code.strip():
        code, lang_hint = oa.code_from_trace(_flatten(trace))
    if not code.strip():
        entry = Path(str(path)).name or 'the entrypoint'
        return {'passed': False, 'score': 0.0,
                'reason': f"no program to grade — nothing written to {entry} "
                          f"and no code block in the reply"}

    language = oa.pick_language(lang_hint, spec.get('language') or '')
    try:
        out = oa.grade(slug, code, language)
    except Exception as e:
        # the judge, not the agent — see the docstring
        return {'passed': False, 'score': 0.0, 'void': True,
                'reason': f"openarena could not grade this: {e}"}

    total = int(out.get('total') or 0)
    passed = int(out.get('passed') or 0)
    score = float(out.get('score') or 0.0)
    return {
        'passed': bool(out.get('solved')),
        'score': max(0.0, min(1.0, score)),
        'reason': f"{passed}/{total} cases pass ({language})",
        # the case-by-case record, hidden cases included by name only
        'cases': [{'name': c.get('name'), 'passed': bool(c.get('passed')),
                   'hidden': bool(c.get('hidden')), 'ms': c.get('ms')}
                  for c in (out.get('cases') or [])],
        'submission': out.get('submission_id'),
    }


def arena(trace, spec) -> Dict[str, Any]:
    """Play the run's answer into a drill at the arena module and read it back.

    The sibling of `openarena`, and the same bargain: the neighbour grades. A
    drill over there is a game class in its own sandbox, and the only way to
    find out whether an answer is right is to play it into a table and look at
    what the drill says afterwards — so that is what this does. No drill's
    answer is computed in this process, and none is stored here.

        {"type": "arena", "game": "invoice", "seed": 7,
         "round": 4, "path": "answer.txt"}

    The answer is the file at `path` if the agent wrote one, and otherwise the
    last thing it said — an agent that answered in chat has still answered, and
    the drills read a number out of a sentence on purpose.

    An arena that cannot be reached returns `void`: the board drops that match
    from the rating rather than recording a loss the agent never earned.
    """
    from src.arena import drills as dr

    game = str(spec.get('game') or '').strip()
    if not game:
        return {'passed': False, 'score': 0.0, 'void': True,
                'reason': 'arena scorer needs `game`'}

    answer = ''
    path = spec.get('path') or ''
    if path:
        f = Path(path).expanduser()
        if f.exists():
            answer = f.read_text(errors='replace').strip()
    if not answer:
        # the last non-empty line the run produced. A drill wants one value,
        # so the tail of the answer is the answer
        lines = [ln.strip() for ln in _result_text(trace).splitlines() if ln.strip()]
        answer = lines[-1] if lines else ''
    if not answer:
        entry = Path(str(path)).name or 'the answer file'
        return {'passed': False, 'score': 0.0,
                'reason': f"nothing to play — no {entry} and nothing said"}

    try:
        out = dr.grade(game, int(spec.get('seed') or dr.DEFAULT_SEED),
                       int(spec.get('round') or 0), answer)
    except Exception as e:
        # the arena, not the agent — see the docstring
        return {'passed': False, 'score': 0.0, 'void': True,
                'reason': f"the arena could not grade this: {e}"}

    right = bool(out.get('correct'))
    # An illegal move is worth saying out loud rather than folding into "wrong":
    # one is an agent that cannot follow the format and the other is an agent
    # that cannot do the work, and the drills are built to tell them apart.
    shape = 'right' if right else ('wrong' if out.get('legal') else 'illegal')
    return {
        'passed': right,
        'score': 1.0 if right else 0.0,
        'reason': f"{game} round {int(spec.get('round') or 0) + 1}: {shape}"
                  + (f" — {out['note']}" if out.get('note') else ''),
        'legal': bool(out.get('legal')),
    }


SCORERS = {
    'tool_used': tool_used,
    'tool_not_used': tool_not_used,
    # pre-rename names, kept so existing eval specs keep scoring
    'skill_used': tool_used,
    'skill_not_used': tool_not_used,
    'no_errors': no_errors,
    'finished': finished,
    'max_steps': max_steps,
    'contains': contains,
    'regex': regex,
    'file_exists': file_exists,
    'file_contains': file_contains,
    'file_not_contains': file_not_contains,
    'file_regex': file_regex,
    'step_count_at_least': step_count_at_least,
    # graded by the openarena module's sandbox, not by this process
    'openarena': openarena,
    # …and this one by the arena module's, playing the answer into a drill
    'arena': arena,
}


def run_scorer(spec: Dict[str, Any], trace: List[Any]) -> Dict[str, Any]:
    """Run a single scorer spec and return a uniform result dict."""
    name = spec.get('type', '')
    fn = SCORERS.get(name)
    if fn is None:
        return {'type': name, 'passed': False, 'score': 0.0,
                'reason': f"unknown scorer: {name!r}"}
    try:
        out = fn(trace, spec)
    except Exception as e:
        return {'type': name, 'passed': False, 'score': 0.0,
                'reason': f"scorer error: {e}"}
    return {'type': name, **out}
