"""
prompt - what the model actually reads

The agent's working memory is a dict, and for a long time the prompt was that
dict's Python repr: `{'query': ..., 'tools': {'bash': {'params': {'command':
{'type': "<class 'str'>", ...`. A frontier model reads through that. A 1.2B
model running on this box does not — it answers the shape it recognises, which
is chat, and the run ends with a paragraph instead of a tool call.

So the same state is rendered here instead: named sections, one line per tool,
history truncated oldest-first, and the calling convention last where a small
model's attention actually is. Two styles off one renderer:

    full      every tool description, generous history — hosted models
    compact   first sentence per tool, tighter history, a worked example
              of a step — local LFM/small models (Agent.compact_prompt)

Nothing here is provider-specific: a smaller prompt is a cheaper prompt, so
the hosted providers get the same treatment, one tier looser.

Usage:
    from .prompt import render
    render(agent.memory.get())                  # full
    render(agent.memory.get(), compact=True)    # local/small models
"""
import json
from typing import Any, Dict, List

# how much of one tool result the model reads back. Recent steps keep enough
# to work from; older ones only have to say what happened.
RESULT_RECENT = 1200
RESULT_OLD = 220
RESULT_RECENT_COMPACT = 600
RESULT_OLD_COMPACT = 120

# how many past steps stay in the prompt at all, and how many of those are
# "recent". A 25-step run on a 1.2B model would otherwise spend its whole
# context re-reading step 1.
HISTORY_STEPS = 24
HISTORY_RECENT = 4
HISTORY_STEPS_COMPACT = 8
HISTORY_RECENT_COMPACT = 3

# how much of one skill document reaches the prompt. Skills are written to be
# read whole, but four of them at 8k characters is a context spent on being
# taught rather than on working.
SKILL_CHARS = 6000
SKILL_CHARS_COMPACT = 1800

# keys the renderer places itself — anything else in working memory is
# rendered generically under EXTRA, so a run that adds its own context
# (notes, tool_docs, fork(...)) still reaches the model.
PLACED = {'goal', 'output_format', 'query', 'tools', 'path', 'pwd', 'steps',
          'step', 'history', 'hint', 'recalled', 'attachments', 'notes',
          'tool_docs', 'skills'}

# the worked example a small model needs. It is a real call with real params:
# shown an abstract `{"tool": "<tool_name>"}`, small models emit that literally.
EXAMPLE = (
    'Example — one step, nothing else:\n'
    '<STEP>{"tool": "bash", "params": {"command": "ls -la"}}</STEP>\n'
    'Example — the last step of every run, which writes the user\'s answer:\n'
    '<STEP>{"tool": "finish", "params": {"summary": "There are 3 files: a.txt, '
    'b.py and README.md."}}</STEP>'
)

# the whole instruction for the run's last call, where the answer is written
ANSWER = (
    "# WRITE THE ANSWER\n"
    "Tool use is over for this run. Using the task and everything above, write "
    "your answer to the user now, as plain text: what they asked for, what you "
    "found, what you changed. No tools, no <STEP>, no JSON — just the answer, "
    "addressed to them."
)


def _type_name(t: Any) -> str:
    """'<class 'str'>' -> 'str'. Python's repr of a type is not prompt text."""
    s = str(t)
    if s.startswith("<class '") and s.endswith("'>"):
        return s[8:-2]
    for prefix in ('typing.', "<class '"):
        if s.startswith(prefix):
            s = s[len(prefix):].rstrip("'>")
    return s or 'any'


def _first_sentence(text: str, limit: int = 110) -> str:
    """The first sentence of a tool description, for the compact list."""
    text = ' '.join((text or '').split())
    cut = text.find('. ')
    if 0 < cut < limit:
        return text[:cut]
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _signature(name: str, spec: Dict[str, Any]) -> str:
    """`edit(file_path:str, old_string:str, new_string:str, replace_all?:bool)`

    Required params first and unmarked, optional ones with a `?`, which is the
    one distinction the model has to get right to make a call that runs.
    """
    params = spec.get('params') or {}
    required = [f"{p}:{_type_name(d.get('type', 'any'))}"
                for p, d in params.items() if d.get('required')]
    optional = [f"{p}?:{_type_name(d.get('type', 'any'))}"
                for p, d in params.items() if not d.get('required')]
    return f"{name}({', '.join(required + optional)})"


def tool_lines(tools: Dict[str, Any], compact: bool = False) -> List[str]:
    """One line per tool: signature, then what it is for."""
    lines = []
    for name, spec in (tools or {}).items():
        if not isinstance(spec, dict):
            lines.append(f"- {name}")
            continue
        if spec.get('error'):            # a tool that failed to load
            continue
        desc = spec.get('description') or ''
        desc = _first_sentence(desc) if compact else ' '.join(desc.split())
        hints = [f"{p}: {d['hint']}" for p, d in (spec.get('params') or {}).items()
                 if isinstance(d, dict) and d.get('hint')]
        line = f"- {_signature(name, spec)}"
        if desc:
            line += f" — {desc}"
        if hints and not compact:
            line += ''.join(f"\n    {h}" for h in hints)
        lines.append(line)
    return lines


def _result_text(step: Dict[str, Any], limit: int) -> str:
    """What a finished step actually produced, flattened to one blob."""
    if step.get('error'):
        return f"ERROR: {str(step['error'])[:limit]}"
    result = step.get('result')
    if result is None:
        return ''
    if isinstance(result, (dict, list)):
        try:
            text = json.dumps(result, default=str)
        except Exception:
            text = str(result)
    else:
        text = str(result)
    text = text.strip()
    return text[:limit] + (f"… [+{len(text) - limit} chars]" if len(text) > limit else '')


def _flatten(history: List[Any]) -> List[Dict[str, Any]]:
    """The run's steps in order. History is a list of per-iteration plans, and
    each plan is a list of steps — the model wants the flat trail."""
    steps = []
    for plan in history or []:
        for step in (plan if isinstance(plan, list) else [plan]):
            if isinstance(step, dict) and step.get('tool'):
                steps.append(step)
    return steps


def history_lines(history: List[Any], compact: bool = False) -> List[str]:
    """The trail so far, oldest trimmed hardest.

    Older steps are kept as a one-line record that they happened — that is
    what stops the loop repeating them — while the last few keep enough of
    their output to be worked from.
    """
    keep = HISTORY_STEPS_COMPACT if compact else HISTORY_STEPS
    recent = HISTORY_RECENT_COMPACT if compact else HISTORY_RECENT
    long_cap = RESULT_RECENT_COMPACT if compact else RESULT_RECENT
    short_cap = RESULT_OLD_COMPACT if compact else RESULT_OLD
    steps = _flatten(history)
    dropped = max(0, len(steps) - keep)
    steps = steps[-keep:]
    lines = []
    if dropped:
        lines.append(f"[{dropped} earlier step(s) trimmed]")
    for i, step in enumerate(steps):
        n = dropped + i + 1
        try:
            params = json.dumps(step.get('params') or {}, default=str)
        except Exception:
            params = str(step.get('params'))
        if len(params) > 300:
            params = params[:300] + '…'
        lines.append(f"[{n}] {step.get('tool')} {params}")
        body = _result_text(step, long_cap if i >= len(steps) - recent else short_cap)
        if body:
            lines.append(f"    -> {body}")
        # the loop's own word to the model about that step — a blocked repeat,
        # a cached result. It is the nudge that breaks a circling run, so it
        # is never trimmed with the result it belongs to.
        if step.get('note'):
            lines.append(f"    !! {step['note']}")
    return lines


def _extra_lines(state: Dict[str, Any]) -> List[str]:
    """Whatever the caller put in working memory that this file doesn't place."""
    lines = []
    for k, v in (state or {}).items():
        if k in PLACED or v in (None, '', [], {}):
            continue
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        lines.append(f"{k}: {text}")
    return lines


def render(state: Dict[str, Any], compact: bool = False,
           answer: bool = False) -> str:
    """Working memory as the text the model reads.

    Section order is deliberate: the job first, the tools in the middle, the
    calling convention last. Models weight the end of a long prompt heavily,
    and the thing this loop needs them to get right is the step format.

    `answer` renders the other prompt this loop makes: tool use is over and
    the run needs the words the user reads. The tool list and the step format
    are left out entirely — a small model handed them writes one more tool
    call instead of the answer, which is exactly what it was asked not to do.
    """
    state = state or {}
    out: List[str] = []

    goal = state.get('goal')
    if goal:
        out.append(str(goal).strip())

    query = state.get('query')
    if query:
        out.append(f"# TASK\n{str(query).strip()}")

    where = []
    path = state.get('pwd') or state.get('path')
    if path:
        where.append(f"working directory: {path}")
    total = state.get('steps')
    step = state.get('step')
    if total and not answer:
        # step is 0-based in the loop; the model counts from one like a human
        done = int(step or 0)
        left = max(0, int(total) - done - 1)
        where.append(f"step {done + 1} of {total} ({left} left after this one)")
        if left <= 1:
            # the budget is the one deadline the model can't see coming, and a
            # run that hits it with no finish leaves the user reading nothing
            where.append("This is your last chance to answer — call finish now "
                         "with what you already know.")
    if where:
        out.append("# WORKSPACE\n" + '\n'.join(where))

    for key, title in (('attachments', '# ATTACHMENTS'),
                       ('recalled', '# MEMORY'),
                       ('notes', '# NOTES'),
                       ('tool_docs', '# TOOL DOCUMENTS')):
        value = state.get(key)
        if value:
            out.append(f"{title}\n{str(value).strip()}")

    extra = _extra_lines(state)
    if extra:
        out.append("# CONTEXT\n" + '\n'.join(extra))

    # SKILLS before TOOLS on purpose: a skill is the method for using the
    # tools, so the model should read what it was taught before it reads the
    # list of what it can call.
    skills = state.get('skills')
    if skills and not answer:
        block = skill_lines(skills, compact=compact)
        if block:
            out.append(f"# SKILLS ({len(block)})\nMethods you have been taught. "
                       "When one applies to the task, follow it.\n\n" +
                       '\n\n'.join(block))

    tools = state.get('tools')
    if tools and not answer:
        lines = tool_lines(tools, compact=compact)
        out.append(f"# TOOLS ({len(lines)})\n" + '\n'.join(lines) +
                   "\nfinish(summary:str) — end the run; the summary is what the "
                   "user reads, so write the answer there")

    history = history_lines(state.get('history'), compact=compact)
    if history:
        out.append("# WHAT YOU HAVE DONE SO FAR\n" + '\n'.join(history))

    hint = state.get('hint')
    if hint:
        out.append(f"# HINT\n{str(hint).strip()}")

    if answer:
        out.append(ANSWER)
        return '\n\n'.join(out)

    fmt = str(state.get('output_format') or '').strip()
    if fmt:
        block = "# YOUR NEXT STEP\n" + '\n'.join(l.strip() for l in fmt.splitlines())
        if compact:
            block += '\n' + EXAMPLE
        out.append(block)
    return '\n\n'.join(out)


def skill_lines(skills: Any, compact: bool = False) -> List[str]:
    """Skills as prompt text.

    A skill is already markdown written for a model, so it is passed through
    rather than reformatted — the one thing done to it is a cap, because a run
    that loaded four 8k-character skills would spend its whole context being
    taught instead of working. Compact keeps the front of each document, which
    is where a well-written skill puts "when to use this".
    """
    cap = SKILL_CHARS_COMPACT if compact else SKILL_CHARS
    if isinstance(skills, str):
        skills = {'': skills}
    if not isinstance(skills, dict):
        return []
    out = []
    for name, body in skills.items():
        text = str(body or '').strip()
        if not text:
            continue
        if len(text) > cap:
            text = text[:cap].rstrip() + "\n… (truncated — the full skill is in the catalog)"
        out.append((f"## {name}\n{text}" if name else text))
    return out


def size(state: Dict[str, Any], compact: bool = False) -> int:
    """Characters the rendered prompt costs — used by tests and the meter."""
    return len(render(state, compact=compact))
