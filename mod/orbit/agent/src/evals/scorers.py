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


def _tools_used(trace) -> List[str]:
    return [s.get('tool', '') for s in _flatten(trace) if s.get('tool')]


def _result_text(trace) -> str:
    parts = []
    for s in _flatten(trace):
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

def skill_used(trace, spec) -> Dict[str, Any]:
    """Pass if any step used the named skill."""
    name = spec.get('skill', '')
    used = _tools_used(trace)
    ok = name in used
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"skill {name!r} {'used' if ok else 'not used'} (used: {used})"}


def skill_not_used(trace, spec) -> Dict[str, Any]:
    """Pass if the named skill was NOT used."""
    name = spec.get('skill', '')
    used = _tools_used(trace)
    ok = name not in used
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"skill {name!r} {'absent' if ok else 'present'} (used: {used})"}


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


def step_count_at_least(trace, spec) -> Dict[str, Any]:
    """Pass if at least spec['n'] steps were taken (sanity check)."""
    n = int(spec.get('n', 1))
    count = len(_flatten(trace))
    ok = count >= n
    return {'passed': ok, 'score': 1.0 if ok else 0.0,
            'reason': f"{count} step(s), required >= {n}"}


SCORERS = {
    'skill_used': skill_used,
    'skill_not_used': skill_not_used,
    'no_errors': no_errors,
    'finished': finished,
    'max_steps': max_steps,
    'contains': contains,
    'regex': regex,
    'file_exists': file_exists,
    'file_contains': file_contains,
    'step_count_at_least': step_count_at_least,
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
