"""
agent - autonomous coding agent with a tool registry

Usage:
    import mod as m
    agent = m.mod('agent')()
    agent.forward('run', query='fix the bug in main.py')
    agent.forward('tools')
    agent.forward('serve')
    agent.forward('status')
"""
import os
import ast
import json
import re
import subprocess
import signal
import threading
import time
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import mod as m
    print = m.print
except ImportError:
    m = None

from .agents.mod import Agents, REQUIRES as AGENT_REQUIRES
from .memory.mod import Memory
from .memory.registry import Memories, DEFAULT as DEFAULT_MEMORY
from .library.mod import Library
from .toolbox.mod import Toolboxes
from .tools.mod import Tools
from .credits import Credits
from .billing import Meter
from .liquid import BROWSER, CATALOG, BrowserModel, LiquidModel
from .prompt import render as render_prompt
from .steps import (THINK as THINK_BLOCK, normalize as normalize_step,
                    parse as parse_calls)
from .vaults.mod import Vaults
from .privacy.mod import Privacy, SealError
from .discover.mod import Discover
from .harness.mod import Harness, DEFAULT_TIMEOUT as HARNESS_TIMEOUT
from .arena.mod import Arena, Scheduler
from .identity import Identity


# ── path sandboxing ────────────────────────────────────────────────

WRITE_TOOLS = ('write', 'edit', 'patch')

# Tools that only look. Running one twice with the same params inside one run
# is the model going in a circle, so the second call is answered from the
# first one's result (see run_plan). Everything else — bash, git, the fleet —
# may change the workspace, so its repeats are real calls.
READONLY_TOOLS = ('read', 'tree', 'glob', 'grep', 'symbols', 'context',
                  'diff', 'recall')

# ── repeat-call guard ──────────────────────────────────────────────

# How many times one identical (tool, params) call may fail before the loop
# stops running it. Two, so a flaky network gets its retry — but the model
# can't sit there re-fetching a 403 URL twenty times waiting for a different
# answer. Successful calls are never blocked: re-reading a file after an
# edit is the same call with a legitimately different result.
MAX_IDENTICAL_FAILURES = 2

# ── circling guard ─────────────────────────────────────────────────

# How many steps in a row may be calls the run had already made before the
# loop gives up on tool use and goes to write the answer, and the temperature
# the step after a repeat is sampled at. A greedy decode re-derives the same
# call from the same task forever; sampling is what gives it another branch.
MAX_REPEAT_STEPS = 3
REPEAT_TEMPERATURE = 0.7

# How many steps in a row may use the same tool — with different params, so
# it is not a repeat — before the loop says so. Advisory only: reading six
# files in a row is exactly what a good run looks like.
SAME_TOOL_STREAK = 3

# ── a run that ends on a promise ────────────────────────────────────
#
# The most expensive failure in this loop isn't a wrong answer, it's a run
# that never happened: the model writes "I'll read the config and fix the
# port" as its finish summary and stops, having called nothing. The caller
# reads a plan, believes the work was done, and the task quietly didn't
# happen. Two things have to be true to call it that — no tool ever ran, and
# the sign-off announces work rather than reporting it — because an answer
# that legitimately needed no tools ("what does this module do?") looks the
# same from the outside except for how it is written.
#
# Tools that are the agent talking to itself. None of them is doing the task.
NON_WORK_TOOLS = {'finish', 'response', 'error', 'invalid', 'think', 'todo'}

# "I'll", "let me", "I'm going to", "next I will", "the plan is to" — future
# tense aimed at the task itself.
PROMISE_RE = re.compile(
    r"\b(i'?ll\b|i will\b|i am going to\b|i'?m going to\b|let me\b|let'?s\b"
    r"|going to (?:start|begin|check|look|read|run|open|create|write|update|fix)"
    r"|first,? i\b|next,? i\b|the plan is\b)",
    re.I)

# …and the version with no verb in it at all. "Sure!" is only a promise while
# it is the whole message — said at the top of a real answer it is manners,
# which is why this one is length-bound and the one above is not.
ACK_RE = re.compile(r"^(sure|ok(ay)?|got it|on it|will do|absolutely|of course|"
                    r"happy to|no problem)\b", re.I)
MAX_ACK_CHARS = 200

DO_THE_WORK_HINT = (
    "You ended without doing anything: no tool has run in this task, and your "
    "last message describes work rather than reporting it. Do it now — call "
    "the tools the task needs, one step at a time — and only finish once the "
    "work is actually done, with the summary saying what you did and what "
    "changed. If the task genuinely needs no tools, answer it outright, "
    "without saying you are about to."
)

# ── what one step is allowed to cost a local model ──────────────────

# A step is one small JSON object, but the default cap is sized for a hosted
# model writing a long finish summary. On CPU weights that difference is the
# whole run: a model that doesn't stop cleanly generates to the cap, and 8192
# tokens on this box is ten minutes for one step. Capped only for the models
# that need it (Agent.compact_prompt); the answer gets a little more room
# because it is the one turn that is prose.
LOCAL_STEP_TOKENS = 512
LOCAL_ANSWER_TOKENS = 768


def _call_sig(name: str, params: dict) -> str:
    """Stable identity of a tool call — same tool, same params, same string."""
    return name + '|' + json.dumps(params or {}, sort_keys=True, default=str)


def _step_failed(step: dict) -> bool:
    """True if a step didn't do what it was asked.

    A tool can fail two ways: it raises (the loop records `error`), or it
    returns a result that reports its own failure — `fetch` answering 403
    hands back {'success': False, ...} and never raises. Both are failures;
    only counting the first left the loop blind to a repeating dead end.
    """
    if step.get('error'):
        return True
    result = step.get('result')
    if isinstance(result, dict):
        return result.get('success') is False or bool(result.get('error'))
    return False


def check_path_allowed(file_path: str, allowed_paths: list) -> bool:
    """Return True if path is within allowed paths, or if allowed_paths is None (unrestricted)."""
    if allowed_paths is None:
        return True
    resolved = str(Path(file_path).expanduser().resolve())
    return any(resolved.startswith(str(Path(ap).resolve())) for ap in allowed_paths)


# ── step-JSON repair ───────────────────────────────────────────────

def _strip_fence(s: str) -> str:
    """Drop a ```json … ``` wrapper if the model added one (open fence too —
    a truncated answer never gets to write the closing ticks)."""
    if not s.startswith('```'):
        return s
    s = s[3:]
    if s[:4].lower() == 'json':
        s = s[4:]
    end = s.rfind('```')
    return (s[:end] if end >= 0 else s).strip()


def _close_json(s: str) -> str:
    """Close a JSON object the model left open, in one string-aware pass.

    Handles the two ways a step arrives broken: a trailing comma before a
    closer, and a response the token limit cut off mid-object. Braces, brackets
    and commas inside string values are left alone — a blunt regex here would
    rewrite the model's own text (a command like `ls a, ]` lost its comma).
    """
    out = []            # rebuilt characters
    stack = []          # closers owed, innermost last
    in_str = esc = False
    str_at = 0          # where the open string started in out
    str_is_key = False  # …and whether it sits in key position
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str, str_at = True, len(out)
            prev = next((c for c in reversed(out) if not c.isspace()), '')
            str_is_key = prev in ('{', ',')
            out.append(ch)
        elif ch in '{[':
            stack.append('}' if ch == '{' else ']')
            out.append(ch)
        elif ch in '}]':
            _rstrip(out, ',')
            if stack:
                stack.pop()
            out.append(ch)
        else:
            out.append(ch)
    if in_str:
        if str_is_key:
            del out[str_at:]        # half-written key — no value can follow it
        else:
            out.append('"')         # half-written value — take what arrived
    tail = _rstrip(out, ',:')
    while tail == ':':              # the key it belonged to is now orphaned
        _pop_string(out)
        tail = _rstrip(out, ',:')
    out.extend(reversed(stack))
    return ''.join(out)


def _rstrip(out: list, chars: str) -> str:
    """Pop trailing whitespace and one char from `chars` off `out`, in place.
    Returns the popped char, or '' if there was none."""
    while out and out[-1].isspace():
        out.pop()
    if out and out[-1] in chars:
        return out.pop()
    return ''


def _pop_string(out: list):
    """Pop a trailing "…" token off `out`, in place."""
    if not (out and out[-1] == '"'):
        return
    out.pop()
    while out:
        if out.pop() == '"' and (not out or out[-1] != '\\'):
            return


class Agent:
    """
    World-class coding agent. 23 built-in tools for autonomous software
    engineering, plus custom shell tools and the whole fleet of mods.

    Built-ins: bash, read, write, edit, glob, grep, search, task,
               fetch, patch, think, git, test, lint, symbols, diff,
               tree, todo, context, debug, refactor, websurf, claudecode

    Agent loop: query -> context gather -> LLM -> parse plan -> execute -> reflect -> repeat

    Toolboxes snap on as bundles: agent.snap('code') limits the live tool
    set (and the LLM tool schema) to the union of snapped boxes.

    Usage:
        agent = Agent()
        agent.forward("read main.py and fix the bug")
        agent.tools.ls()
        agent.tools.run("bash", command="ls")
        agent.snap("explore"); agent.snap("code")   # snap toolboxes on
        agent.run("fix the bug", toolbox="core")    # per-run snap
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
- The finish summary IS your reply — it is the only text the user reads. Write it
  to them directly: answer what they asked, say what you changed and why. Never
  write a third-person status report ("Responded to the user's greeting").
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
        # LFM providers — the liquidai module's three runtimes (see liquid.py).
        # They map to themselves because they are built here, not fetched as
        # a model module: one runs weights on this box, one relays to Liquid's
        # cloud, one waits on the console's tab.
        'liquidai': 'liquidai',
        'liquidai-cloud': 'liquidai-cloud',
        'browser': 'browser',
    }

    # providers built in this module rather than resolved through m.mod()
    LOCAL_PROVIDERS = {
        'liquidai': lambda: LiquidModel(runtime='server'),
        'liquidai-cloud': lambda: LiquidModel(runtime='cloud'),
        'browser': BrowserModel,
    }

    # which liquidai runtime each local provider's model list comes from
    LOCAL_RUNTIMES = {'liquidai': 'server', 'liquidai-cloud': 'cloud',
                      'browser': 'browser'}

    # the order default_provider() tries. Weights on this box first — no key,
    # no bill, no tab required. `browser` is deliberately not in here: it can
    # only generate while the console is open, so it is a choice, not a default.
    LOCAL_FIRST = ('liquidai', 'liquidai-cloud')
    # …and where it lands when nothing local is serving
    HOSTED_FALLBACK = ('openrouter', 'venice')

    # one line each for a UI that has to explain a provider with no key field.
    # 'no key' is not the same claim for all three: the cloud runtime does take
    # a key, it just isn't ours to hold.
    LOCAL_HINTS = {
        'liquidai': 'runs on this box — no key, never billed',
        'liquidai-cloud': "Liquid's cloud, on the key set in the liquidai module",
        'browser': 'runs in your own tab — no key, never billed',
    }

    DEFAULT_MODELS = {
        'model.openrouter': 'anthropic/claude-opus-5',
        'openrouter': 'anthropic/claude-opus-5',
        'venice': 'deepseek-v3.2',
        # A run is tool calls *and* the answer that ends it, so the default is
        # the generalist rather than the tool-calling fine-tune beside it:
        # measured on this box LFM2-1.2B-Tool makes the cleaner call and then
        # hands back a shell snippet where the answer should be, while the
        # instruct build answers in words every time. Both are in the list,
        # along with LFM2.5-2.6B, which is better than either and minutes per
        # step on CPU.
        'liquidai': 'LiquidAI/LFM2.5-1.2B-Instruct',
        'liquidai-cloud': 'lfm-2.5-8b-a1b',
        'browser': 'LiquidAI/LFM2.5-350M-ONNX',
    }

    # curated model choices per provider for the UI selector (free-text still allowed)
    MODELS = {
        'openrouter': [
            'anthropic/claude-opus-5',
            'anthropic/claude-sonnet-5',
            'anthropic/claude-haiku-4.5',
            'openai/gpt-5.2',
            'openai/gpt-5.1-codex',
            'google/gemini-3.1-pro-preview',
            'google/gemini-3.5-flash',
            'deepseek/deepseek-v4-pro',
            'qwen/qwen3-coder',
        ],
        'venice': [
            'claude-opus-5',
            'claude-sonnet-5',
            'zai-org-glm-5-2',
            'qwen3-coder-480b-a35b-instruct-turbo',
            'kimi-k2-7-code',
            'deepseek-v4-pro',
            'deepseek-v3.2',
            'venice-uncensored-1-2',
            'llama-3.3-70b',
        ],
        # LFM repos, in case liquidai isn't serving its catalog — provider_models()
        # replaces these with the live list whenever it can reach the module.
        # These are repo ids, not the catalog's bare model ids: a repo is what
        # the server loads and what a tab downloads.
        'liquidai': [
            'LiquidAI/LFM2.5-1.2B-Instruct',
            'LiquidAI/LFM2.5-2.6B',
            'LiquidAI/LFM2-1.2B-Tool',
            'LiquidAI/LFM2.5-1.2B-Thinking',
            'LiquidAI/LFM2.5-350M',
            'LiquidAI/LFM2.5-230M',
            'LiquidAI/LFM2.5-VL-1.6B',
        ],
        'liquidai-cloud': [
            'lfm-2.5-8b-a1b',
            'lfm-2.5-1.2b',
        ],
        'browser': [
            'LiquidAI/LFM2.5-350M-ONNX',
            'LiquidAI/LFM2.5-230M-ONNX',
            'LiquidAI/LFM2.5-1.2B-Instruct-ONNX',
            'LiquidAI/LFM2.5-1.2B-Thinking-ONNX',
            'LiquidAI/LFM2.5-VL-450M-ONNX',
        ],
    }

    # Models that read the compact prompt (prompt.py). Everything running on
    # LFM weights is small by definition, and everything else that names its
    # own size in the billions announces itself: `LFM2.5-1.2B`, `-350M`,
    # `qwen3-4b`. A dash or a word boundary has to come first, so `gemma-4-31b`
    # and `llama-3.3-70b` — big models — are not caught by the `1b`/`0b` inside
    # them. Being wrong here costs prompt quality, never correctness: the
    # compact prompt says the same things in fewer tokens.
    SMALL_MODEL_RE = re.compile(r'(?:\b|-)(?:\d{1,3}m|[0-4](?:\.\d)?b)\b', re.I)

    # FREE MODE ranking. The agent loop needs a model that can hold a long
    # transcript and emit well-formed step anchors, which rules out most of
    # the tiny free tiers — so prefer the big ones, biggest first.
    FREE_MODEL_PREFERENCE = ('nemotron-3-ultra', 'gpt-oss', 'gemma-4-31b',
                             'gemma-4', 'nemotron-3-super', 'ling-3')

    anchors = {
        'plan': ['<PLAN>', '</PLAN>'],
        'tool': ['<STEP>', '</STEP>'],
    }

    output_format = """
        Reply with ONE step and nothing else — no commentary around it:
        <STEP>{"tool": "TOOL_NAME", "params": {"ARG": "VALUE"}}</STEP>
        Use a tool name from the list above, its own parameter names, and
        valid JSON. Call one tool per reply and wait for its result.
        When the work is done — or the question needed no tools at all —
        reply with the step that ends the run:
        <STEP>{"tool": "finish", "params": {"summary": "your answer, written to the user"}}</STEP>
        The summary is shown to the user as your response — write the actual
        answer there, not a description of what you did.
    """

    # ── per-run state ────────────────────────────────────────────────
    # Behind the API this object is one singleton shared by every thread:
    # the console's runs, MCP calls and the arena scheduler all run on it at
    # once. What a run sets for itself — where its steps go, which paths it
    # may write, which directory it is in, which images it carries — has to
    # live on the thread, not on the object. Held on the object, the arena
    # match that started a moment after a console run took over the
    # console's step callback, and the console watched a free-model 429
    # from a match it never ran while its own Opus run on Venice was fine
    # (and, the other way round, an arena match could inherit the owner's
    # unsandboxed write paths). Each of these still reads and writes as a
    # plain attribute; the storage is threading.local, created lazily so an
    # Agent built without __init__ (tests) has one too.
    def _tl(self) -> threading.local:
        local = self.__dict__.get('_local')
        if local is None:
            local = self.__dict__['_local'] = threading.local()
        return local

    def _run_local(name, default=None):
        def get(self):
            return getattr(self._tl(), name, default() if callable(default) else default)

        def set_(self, value):
            setattr(self._tl(), name, value)
        return property(get, set_)

    _on_step = _run_local('on_step')
    _on_usage = _run_local('on_usage')
    _on_live = _run_local('on_live')
    _images = _run_local('images', list)
    _allowed_paths = _run_local('allowed_paths')
    _path = _run_local('path')
    _failed_calls = _run_local('failed_calls')
    _done_calls = _run_local('done_calls')
    del _run_local

    def _clear_run_state(self) -> None:
        """Forget this thread's run. Worker threads are reused, and a snap()
        on a thread that last ran a sandboxed guest would otherwise still
        think it is sandboxed."""
        for name in ('on_step', 'on_usage', 'on_live', 'images', 'allowed_paths',
                     'path', 'failed_calls', 'done_calls'):
            try:
                delattr(self._tl(), name)
            except AttributeError:
                pass

    def _explain_rate_limit(self, short: str, model: str, err: str) -> str:
        """A provider's 429 in plain words, with what to do about it.

        The raw body is a dict of headers and a `limit_source`, which reads
        as nonsense next to a balance pill showing money: the credit is real,
        it just isn't what a free model draws on. Say which door closed —
        the free-model quota, shared by everything on the key — when it
        reopens, and the two ways out. Anything that isn't a 429 comes back
        untouched.
        """
        low = err.lower()
        if '429' not in err and 'rate limit' not in low:
            return err
        reset = ''
        found = re.search(r"X-RateLimit-Reset['\"]?\s*:\s*['\"]?(\d{10,13})", err)
        if found:
            ts = int(found.group(1))
            ts = ts / 1000 if ts > 10 ** 11 else ts
            reset = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
        if 'free-models-per-day' in err or ':free' in (model or ''):
            return (f"rate limited: {short}'s free-model quota for today is used up, "
                    f"so {model} can't answer"
                    + (f" — resets {reset}" if reset else " — resets at 00:00 UTC")
                    + ". Switch to a paid model (turn 'Spend credits' on), pick "
                    "another provider, or wait for the reset.")
        return (f"rate limited by {short} on {model}"
                + (f" — resets {reset}" if reset else "")
                + ". Try again shortly, or switch model or provider.")

    def __init__(self,
                 # unset: the provider is resolved on first use, and resolves
                 # local (default_provider). Naming one here still pins it.
                 model: str = None,
                 provider: str = None,
                 memory: str = DEFAULT_MEMORY,
                 goal: str = None,
                 tools: list = None,
                 **kwargs):
        self.agents = Agents()
        # images attached to the current run (data URLs or http urls)
        self._images: List[str] = []
        # ── the sub-components this box is made of ──
        # An agent is not one object: it is a prompt, a model, a toolbox, a
        # tool registry and a memory module, held together here. Each one is
        # its own mod with its own registry, and each is swappable — which is
        # what makes an agent something you compose rather than something you
        # fork. parts() is the whole box in one view.
        #
        # the whole tool surface in one registry: the tools shipped here, the
        # shell tools added from the console, and every mod in the fleet
        self.tools = Tools()
        # toolboxes: named tool bundles that snap onto this agent
        self.toolboxes = Toolboxes(tools=self.tools)
        self._snapped: List[str] = []
        # memory: one of the pluggable memory modules (memory/registry.py).
        # A dotted name ('agent.memory') still resolves through the framework,
        # so an agent can be built with a memory that lives in another mod.
        self.memories = Memories()
        # per-thread overrides of the components a single run may swap — see
        # the `memory` property and _bind_memory
        self._local = threading.local()
        self.memory = self.memories.make(memory)
        # the tools that act on those sub-components (recall, remember,
        # toolbox) need the live box, not a second copy of it
        self.tools.bind(self)
        # session keys: provider shortname -> API key decrypted from the vault,
        # held IN MEMORY ONLY (never written to disk in plaintext)
        self._session_keys: Dict[str, str] = {}
        # resolve provider: shorthand ('venice', 'openrouter') or full module path
        provider = provider or model
        # one live client per provider path, built on demand — a run holds its
        # own so concurrent runs never trade clients (see _client)
        self._clients: Dict[str, Any] = {}
        # why a provider has no client, when it isn't a missing key (see run)
        self._client_why: Dict[str, str] = {}
        # Named none, the provider is resolved on first use rather than here:
        # default_provider() probes the local runtime, and a module must not
        # do that while it is still being constructed (see _client).
        self._provider = self.PROVIDERS.get(provider, provider) if provider else None
        self.model = self._client() if self._provider else None
        # prices each model call from the provider's live catalog, so a guest's
        # credits pay for the provider spend their run actually creates
        self.meter = Meter()
        # arena matches run keyless, so this object IS their standing: it can
        # only be held by code in this process — JSON off the wire can't forge
        # object identity — which is what lets the harness gate trust it
        self._arena_pass = object()
        if goal:
            self.goal = goal
        self._tool_names = tools  # optional filter

    # ── the memory a run thinks with ─────────────────────────────────
    #
    # Behind the API this module is one Mod for the whole host, and working
    # memory — the dict the prompt is built from — used to be one dict on it.
    # Two runs at once then wrote the same scratchpad: the console's question
    # was compiled with an arena match's task, tools and history, and came
    # back answering the arena's task. (That is not hypothetical; it is what
    # this comment was written from.)
    #
    # So each run binds its own memory instance to its own thread — the same
    # pattern the meter uses for its tally (billing.py) and the browser bridge
    # for its session (liquid.py). The durable layers are files, so a per-run
    # instance still reads and writes the same episodes, facts and turns; only
    # the scratchpad is private. Unbound, every reader gets the module's own.

    @property
    def memory(self):
        return getattr(getattr(self, '_local', None), 'memory', None) or self._memory

    @memory.setter
    def memory(self, value):
        """Setting it sets the module's default — that is what a caller doing
        `agent.memory = …` outside a run means."""
        self._memory = value

    def _bind_memory(self, memory=None) -> None:
        """Give this thread its own memory for the run about to start.

        Named one is used as-is (an agent built with `ephemeral`, a caller's
        override). Otherwise the run gets a fresh instance of the module's own
        kind: same durable stores, its own scratchpad.
        """
        if memory is None:
            try:
                memory = self.memories.make(
                    self.memories.name_of(self._memory), fresh=True)
            except Exception:
                return          # no registry to make one from: share, as before
        self._local.memory = memory

    def _unbind_memory(self) -> None:
        if hasattr(getattr(self, '_local', None), 'memory'):
            del self._local.memory

    def _provider_short(self, provider_path: str = None) -> str:
        """Map a provider module path back to its shortname ('model.openrouter' -> 'openrouter')."""
        provider_path = provider_path or self._provider or self.default_provider()
        for short, path in self.PROVIDERS.items():
            if path == provider_path:
                return short
        return provider_path

    def _make_model(self, provider: str = None):
        """Build a live model client for one provider path.

        A vault-unlocked session key takes priority over the provider's own
        stored/env keys. Returns None instead of raising when no key is
        configured yet — runs then fail with a clear 'no model' error rather
        than the whole module failing to construct.
        """
        provider = provider or self._provider
        # LFM providers are built here and need no key at all: the weights run
        # on this box, in the visitor's tab, or on Liquid's own cloud key
        local = self.LOCAL_PROVIDERS.get(provider)
        if local:
            return local()
        if not m:
            return None
        session_key = self._session_keys.get(self._provider_short(provider))
        try:
            client = m.mod(provider)(api_key=session_key) if session_key \
                else m.mod(provider)()
        except Exception as e:
            print(f"Model init failed for {provider}: {e}")
            return None
        # A provider is any module path, so it is easy to name one that isn't a
        # model module. Its forward() is then the mod-protocol dispatcher, which
        # reads the run's context as a function name and answers
        # "unknown fn: {'query': …, 'tools': …}" — a wall of text that names
        # neither the provider nor the mistake. Model clients price their own
        # calls for the meter; nothing else has model2info.
        if not hasattr(client, 'model2info'):
            self._client_why[provider] = (
                f"'{provider}' is not a model provider — it's a module with no "
                f"model2info(), so it has no models to run. Pick openrouter, "
                f"venice, or an LFM provider in the Builder (model node).")
            print(f"[agent] {self._client_why[provider]}")
            return None
        self._client_why.pop(provider, None)
        return client

    def _client(self, provider: str = None):
        """The cached client for a provider — this is what a run must call.

        Behind the API this module is one process-wide singleton, so a run that
        reads `self.model` reads whatever provider the *last* request selected.
        Two overlapping runs on different providers then swap clients mid-loop
        and one provider gets the other's model id ("No model matching
        'nvidia/...:free' found on Venice"). Resolving per run kills that race.
        """
        if provider:
            path = self.PROVIDERS.get(provider, provider)
        else:
            # nobody has picked one yet: local, and remembered from here on
            self._provider = self._provider or \
                self.PROVIDERS.get(self.default_provider(), self.default_provider())
            path = self._provider
        if self._clients.get(path) is None:
            self._clients[path] = self._make_model(path)
        return self._clients[path]

    def has_model(self, provider: str = None) -> bool:
        """Can a run on this provider reach a model? Defaults to the module's
        provider; callers with a request in hand should pass its own."""
        return self._client(provider) is not None

    def free_model(self, provider: str = None) -> Optional[str]:
        """The best zero-cost model a provider offers, or None.

        Ranked by FREE_MODEL_PREFERENCE — the provider's own free list is in
        catalog order, and its first entry is regularly a tiny or endpointless
        model that can't drive the loop. Provider-scoped: a free id is only
        valid on the catalog it came from.
        """
        client = self._client(provider)
        try:
            free = client.free_models() if hasattr(client, 'free_models') else []
        except Exception as e:
            print(f"Free model lookup failed: {e}")
            return None
        for pref in self.FREE_MODEL_PREFERENCE:
            for mid in free:
                if pref in mid:
                    return mid
        return free[0] if free else None

    def default_provider(self) -> str:
        """The provider a caller who named none runs on.

        Local first, and not as a preference: the alternative spends real
        money on somebody's key for a question that a model on this box can
        often answer. So a run defaults to the LFM weights running here
        (liquidai), and only falls back to a hosted provider when nothing
        local is actually serving — a default that costs nothing is only a
        good default while it works.

        Anyone who wants Opus asks for Opus: the console's picker, the
        agent's own saved model, and the `provider` argument all still win.
        Cached briefly because /providers, /params and every run ask.
        """
        cached = getattr(self, '_default_provider', None)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        pick = None
        for short in self.LOCAL_FIRST:
            try:
                if self._client(short) is not None and self.provider_models(short):
                    pick = short
                    break
            except Exception:
                continue
        if not pick:
            # nothing local is serving — the hosted provider whose key is
            # actually configured, since a default that can't run is no default
            def ready(short: str) -> bool:
                try:                      # key_info is Mod's; Agent alone has none
                    return bool(self.key_info(short).get('configured'))
                except Exception:
                    return self._client(short) is not None
            pick = next((p for p in self.HOSTED_FALLBACK if ready(p)), 'openrouter')
        self._default_provider = (time.time(), pick)
        return pick

    def is_free_provider(self, provider: str = None) -> bool:
        """True when a run on this provider can't cost the module anything.

        The LFM providers burn this box's CPU, the visitor's own GPU, or the
        operator's Liquid key — never the OpenRouter/Venice credit a guest's
        deposit funds. So they skip the credit ceiling and the bill, and a
        guest with an empty balance can still run one.
        """
        return bool(getattr(self._client(provider), 'is_free', False))

    def _model_for(self, provider: str, model: str = None) -> str:
        """The model a run should actually use on `provider`.

        Switching an agent's provider leaves its saved model behind, and a
        model id from the provider you left means nothing to the one you
        arrived at — a venice slug reaching the liquidai runtime dies inside
        HuggingFace's loader ("not a valid model identifier … hf auth login"),
        which reads like a broken module rather than a stale setting.

        So: a model that demonstrably belongs to *another* provider is treated
        as leftover state and replaced by this provider's default. Anything
        unrecognised is still passed through untouched — every provider here
        takes free-text model ids, and guessing against a curated list would
        block the model that shipped this morning.
        """
        # MODELS is keyed by shortname; `provider` arrives as a module path
        # ('model.openrouter'). Looking it up unmapped made every openrouter
        # model look like it belonged to somebody else — so each one but the
        # default was silently swapped out for claude-opus-5.
        short = self._provider_short(provider)
        if not model:
            return self.DEFAULT_MODELS.get(short, 'anthropic/claude-opus-5')
        if model in set(self.MODELS.get(short, [])):
            return model
        elsewhere = {name for prov, names in self.MODELS.items()
                     if prov != short for name in names}
        if model in elsewhere:
            swapped = self.DEFAULT_MODELS.get(short, model)
            print(f"[agent] {model!r} is not a {short} model — running {swapped!r}")
            return swapped
        return model

    def provider_models(self, provider: str) -> List[str]:
        """The models a provider offers the console, live where there is one.

        The LFM providers read their list off the liquidai catalog so a model
        Liquid shipped this morning is selectable this afternoon; everything
        else is the curated MODELS list.
        """
        runtime = self.LOCAL_RUNTIMES.get(provider)
        if runtime:
            try:
                live = CATALOG.cloud_models() if runtime == 'cloud' \
                    else CATALOG.repos(runtime)
            except Exception as e:
                print(f"Model list lookup failed for {provider}: {e}")
                live = []
            if live:
                return live
        return self.MODELS.get(provider, [])

    def set_provider(self, provider: str):
        """Switch the module's default LLM provider. Use 'openrouter', 'venice',
        or any module path. A run passes its provider to run() instead — that
        one is per-run and leaves this default alone."""
        self._provider = self.PROVIDERS.get(provider, provider)
        self.model = self._client()
        return {'provider': self._provider}

    # ── tool interface ───────────────────────────────────────────────

    def tool(self, name: str):
        """Get a tool — the instance for a built-in, the entry for the rest."""
        return self.tools.get(name)

    def run_tool(self, name: str, **params):
        """Run a tool by name — built-in, custom shell tool, or fleet module."""
        return self.tools.run(name, **params)

    def all_tools(self, mods: bool = False) -> List[str]:
        """Every callable name: built-ins, custom tools, and — asked for —
        the fleet, which stays out of the default loadout."""
        return self.tools.ls(mods=mods)

    def tool_schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Get LLM-friendly schemas for the agent's tools.

        Built-ins, custom tools and fleet modules land in one schema — the LLM
        sees a single tool list and can't tell which was deployed, which was
        typed into the console and which is another module.

        Priority: explicit names > constructor tool filter > snapped
        toolboxes > every non-fleet tool.
        """
        return self.tools.schema(names or self.active_tools())

    # ── tool aggregator (discover → library) ─────────────────────────

    def scan_tools(self, q: str = "", sources: List[str] = None,
                   limit: int = 30, kind: str = None, fresh: bool = False) -> Dict:
        """Scan every public registry at once for installable tool documents.

        One query fans out to GitHub, the official skills catalog, npm, the MCP
        registry, Glama and curated lists; duplicates across platforms merge
        into one result. A dead registry yields an error, not a failed scan.
        """
        return self.discover.search(q, sources, limit, kind, fresh)

    def tool_install(self, id: str, path: str = None, key=None) -> Dict:
        """Install a scanned result into the library as an external tool doc.

        The fetched document is instruction markdown, never executable code:
        it joins the library as a document the agent can be handed, so an
        install can't run anything on its own. The installer owns it, so this
        needs a sign-in like every other library create.
        """
        if not id:
            raise ValueError("id required — scan first, then install by result id")
        # refuse before the network fetch — an anonymous install can't be stored
        self.identity.require_signed_in(key, f"install tool: {id}")
        doc = self.discover.tool_doc(id, path)
        return self.library.tool_add(
            name=doc["name"], body=doc["body"], description=doc.get("description", ""),
            tags=doc.get("tags"), source=doc.get("source", ""), url=doc.get("url", ""),
            origin_id=doc.get("origin_id") or id, license=doc.get("license"), key=key)

    # ── toolboxes (snap-on tool bundles) ─────────────────────────────

    def snap(self, name: str) -> Dict[str, Any]:
        """Snap a toolbox onto the agent. Active tools become the union
        of everything snapped on (order preserved, first-snap first)."""
        if not self.toolboxes.exists(name):
            raise KeyError(f"toolbox not found: {name}. Available: {self.toolboxes.ls()}")
        if name not in self._snapped:
            self._snapped.append(name)
        self._tool_names = None   # a box is a fresh loadout, not a refinement
        return self.snapped()

    def unsnap(self, name: str = None) -> Dict[str, Any]:
        """Detach one toolbox, or all of them (back to the full tool set)."""
        if name is None:
            self._snapped = []
        elif name in self._snapped:
            self._snapped.remove(name)
        self._tool_names = None
        return self.snapped()

    def select(self, names: List[str] = None) -> Dict[str, Any]:
        """Pin the loadout to an exact tool list.

        Toolboxes are the presets; this is the refinement — flip one tool on
        or off and the live set becomes exactly what's listed, regardless of
        what's snapped. This is also how a fleet module joins a run: name
        `mod.git` here and it's in the loadout. An empty list (or None) hands
        control back to the snapped boxes, since an agent with no tools has
        nothing to run.
        """
        if not names:
            self._tool_names = None
            return self.snapped()
        unknown = [n for n in names if not self.tools.exists(n)]
        if unknown:
            raise ValueError(f"unknown tools: {unknown}")
        self._tool_names = list(dict.fromkeys(names))
        return self.snapped()

    def snapped(self) -> Dict[str, Any]:
        """Current snap state: which boxes are on and the resulting tool set."""
        active = self.active_tools()
        return {
            'snapped': list(self._snapped),
            'tools': active if active is not None else self.all_tools(),
            'filtered': active is not None,
            # what decided the set — the console labels the loadout with this
            'source': 'selection' if self._tool_names else
                      'toolboxes' if self._snapped else 'all',
        }

    def active_tools(self) -> Optional[List[str]]:
        """The agent's live tool filter: constructor filter, else the union of
        snapped toolboxes, else None (= every tool but the fleet)."""
        if self._tool_names:
            return self._tool_names
        if self._snapped:
            return self.toolboxes.resolve(self._snapped)
        return None

    def use_toolbox(self, name: str) -> Dict[str, Any]:
        """Snap a toolbox on from inside a run — what the `toolbox` tool calls.

        A run starts with the loadout it was given, and that is usually the
        right one; but an agent that discovers halfway through that it needs
        version control shouldn't have to fail and be re-run with a bigger
        box. So it can ask for one, and the tools appear in its schema on the
        next step.

        This deliberately edits the run's working memory rather than the
        module's loadout: `select()` is module-wide state, and a run that
        widened it would leave every later run wider. Sandboxed runs can't
        pull the fleet in this way — the same rule run_plan enforces, applied
        before the model is even told those tools exist.
        """
        box = self.toolboxes.get(name)          # KeyError if there is no such box
        sandboxed = getattr(self, '_allowed_paths', None) is not None
        add = [t for t in box.tools if self.tools.exists(t)]
        blocked = []
        if sandboxed:
            blocked = [t for t in add if self.tools.kind(t) not in ('builtin', 'custom')]
            add = [t for t in add if t not in blocked]
        have = self.memory.get('tools') or {}
        if not isinstance(have, dict):          # a caller passed a bare list
            have = self.tool_schema(list(have))
        added = [t for t in add if t not in have]
        if added:
            self.memory.add('tools', {**have, **self.tool_schema(add)})
        if name not in self._snapped:
            self._snapped.append(name)
        return {
            'toolbox': name,
            'description': box.description,
            'added': added,
            'tools': sorted(set(have) | set(add)),
            **({'blocked': blocked,
                'note': 'fleet tools are host-only and were left out of this box'}
               if blocked else {}),
        }

    # ── the box: every sub-component in one view ─────────────────────

    def parts(self) -> Dict[str, Any]:
        """What this agent is made of, component by component.

        One call answers "what is in the box" for the console, the builder and
        anyone auditing a run: which memory module is attached, which tools it
        can reach, which bundles exist, which model it will call. Each entry
        names the sub-registry it came from, so a UI can offer the swap.
        """
        active = self.active_tools()
        return {
            # the box is one node; these are the integrations its template
            # requires wired in before it runs
            'requires': list(AGENT_REQUIRES),
            'model': {
                'provider': self._provider_short(),
                'model': self.DEFAULT_MODELS.get(self._provider_short()),
                'ready': self.has_model(),
                'options': list(self.PROVIDERS.keys()),
            },
            'memory': {
                'module': self.memories.name_of(self.memory),
                'options': self.memories.items(),
                'state': self.memory.status() if hasattr(self.memory, 'status')
                         else self.memory.summary(),
            },
            'toolbox': {
                'snapped': list(self._snapped),
                'boxes': self.toolboxes.ls(),
                'source': self.snapped()['source'],
            },
            'tools': {
                'active': active if active is not None else self.all_tools(),
                'filtered': active is not None,
                'total': len(self.all_tools()),
                'fleet': True,
            },
            'prompt': {'goal': self.goal},
        }

    # ── memory ───────────────────────────────────────────────────────

    def init_memory(self, **kwargs):
        """Start a run's working memory from nothing.

        Working memory is the prompt being built, and behind the API this
        module is one process-wide singleton — so whatever the last run put in
        that dict was still there for the next one. A run that carried library
        notes, an attached tool document or a stale hint left them in the
        prompt of every run after it, on somebody else's question. The durable
        layers (episodes, facts, dialogue) are separate stores and are not
        touched: this clears the scratchpad, not the memory.
        """
        self.memory.clear()
        kwargs.setdefault('goal', self.goal)      # a run may carry its own
        kwargs['output_format'] = self.output_format
        for k, v in kwargs.items():
            self.memory.add(k, v)
            if m and k.startswith('fork') and v is not None:
                self.memory.add(f'fork({k})', m.fn('select_files')(path=m.dp(v), query=kwargs.get('query', '')))

    # ── main loop ────────────────────────────────────────────────────

    def run(self,
            query: str = 'help me with this',
            *extra_text,
            goal: str = None,
            memory=None,
            model: Optional[str] = None,
            provider: str = None,
            path: str = None,
            temperature: float = 0.0,
            # per-step output cap: one step is a single small JSON plan; a huge
            # cap makes providers pre-reserve credits and 402 on low balances
            max_tokens: int = 8192,
            steps: int = 25,
            tools: list = None,
            toolbox=None,
            mod: str = None,
            safety: bool = False,
            save: bool = False,
            key: str = None,
            allowed_paths: list = None,
            free: bool = False,
            on_step=None,
            on_usage=None,
            on_live=None,
            images: list = None,
            budget=None,
            session: str = None,
            # named so it stays out of **kwargs, which is compiled into the
            # prompt — the agent's name is for the memory record, not context
            agent_type: str = None,
            **kwargs) -> List[Dict[str, Any]]:
        """Run the agent loop: query -> LLM -> parse step -> execute tool -> repeat.

        Args:
            goal: the system prompt for this run only — an agent's persona, or
                  a prompt the caller picked. The module's own `goal` is the
                  default and is never overwritten by a run (see _bind_memory
                  for why that matters on a shared module).
            memory: a memory module instance for this run only, bound to this
                  thread. Defaults to the module's.
            model: model name on the provider (e.g. 'anthropic/claude-opus-5' for openrouter,
                   'deepseek-v3.2' for venice). Defaults to provider's default model.
            provider: LLM provider — 'openrouter', 'venice', or any module path. Switches at runtime.
            toolbox: toolbox name (or list of names) to snap on for this run —
                     the tool set becomes the union of those boxes. An explicit
                     `tools` list wins over `toolbox`.
            allowed_paths: list of allowed write paths, or None for unrestricted (owner).
                           Non-owners are restricted to their portal directory.
            on_step: optional callable invoked with each executed step dict as the
                     loop progresses — used by the API to stream live progress.
            on_usage: optional callable invoked after each model call with what
                     that call cost on the provider key — {call, model, tokens,
                     cost, total}. A run's price is the sum of its calls, and
                     waiting for the end to say so hides it while it is being
                     spent.
            on_live: optional callable invoked with ephemeral progress the
                     watcher renders and drops — {'event': 'token', 'text'}
                     as the model's output streams in, {'event': 'tool_start',
                     'tool', 'params', 'i', 'n'} the moment a call begins,
                     {'event': 'model_start', 'step', 'model'} when a call
                     goes out. Nothing here is recorded; the step dicts on
                     on_step remain the run's record.
            budget: optional callable given the run's metered provider cost so far;
                    returning False stops the loop. A paying guest's credits are
                    finite, and a charge clamped to their balance would leave the
                    module holding the overrun.
            images: image URLs (http or data:) the user attached to the query —
                    sent to the model as a leading multimodal turn.
            session: the console conversation this run belongs to. Passing one
                    makes the run a remembered exchange: the memory module
                    compiles this caller's earlier turns into the prompt, and
                    files this turn away when the run ends. A run with no
                    session (an arena match, a tool call) is not conversation,
                    and leaves the dialogue layer untouched.
        """
        # everything provider-shaped is run-local: the module is shared by every
        # concurrent run, so nothing here may read or write self.model
        self._bind_memory(memory)
        provider = provider or self._provider or self.default_provider()
        prov = self.PROVIDERS.get(provider, provider)
        short = self._provider_short(prov)
        client = self._client(prov)
        if client is None:
            raise RuntimeError(self._client_why.get(prov) or (
                f"No API key available for provider '{short}'. "
                f"Add a key — or unlock your encrypted key — in the Builder (model node)."))
        self._on_step = on_step
        self._on_usage = on_usage
        self._on_live = on_live
        self._images = [i for i in (images or []) if isinstance(i, str) and i.strip()][:8]
        model = self._model_for(prov, model)
        # FREE MODE resolves the model here rather than letting the provider
        # grab whatever sorts first: the pick is a deliberate, capable one, and
        # the run ledger records the model that actually ran. A provider that
        # can't cost anything is already free, so it keeps the model asked for.
        if free and getattr(client, 'is_free', False):
            free = False
        if free:
            picked = self.free_model(prov)
            if not picked:
                # the provider has no zero-cost catalog — running the requested
                # paid model would bill the module for a run it never charges
                raise RuntimeError(
                    f"FREE MODE has no zero-cost models on '{short}'. "
                    f"Switch provider, or turn FREE MODE off to run {model}.")
            model, free = picked, False
        self._allowed_paths = allowed_paths
        query = query + ' ' + ' '.join(extra_text) if extra_text else query
        path = path or (m.dp(mod) if m and mod else os.getcwd())
        # …and kept on the box, because it is where the run's relative paths
        # resolve from (see _resolve_paths), not just something to print
        self._path = path
        # per-run toolbox snap: explicit tools list wins, then toolbox union
        if not tools and toolbox:
            tools = self.toolboxes.resolve(toolbox)
        # memory recall: the caller's earlier turns and the durable facts past
        # runs left behind ride into the prompt. Scoped to whoever is asking —
        # the address when they are signed in, the console session when they
        # are not — so one visitor's conversation never surfaces in another's.
        who = None
        if session:
            try:
                who = self.identity.addr(key)
            except Exception:
                who = None
        # who the run is for, kept on the box so the recall tool scopes its
        # retrieval the same way the compiled prompt was scoped
        self._session, self._who = session, who
        recalled = None
        if hasattr(self.memory, 'compile'):
            recalled = self.memory.compile(query, session=session, who=who) or None
        self.init_memory(
            query=query,
            goal=goal or self.goal,
            tools=self.tool_schema(tools),
            path=path,
            steps=steps,
            **({'recalled': recalled} if recalled else {}),
            **({'attachments': f'{len(self._images)} image(s) attached to this task — '
                               f'they are in the conversation above, look at them'} if self._images else {}),
            **kwargs
        )
        history = []
        consecutive_errors = 0
        # spent once, on a run that ended by describing the task (see below)
        nudged = False
        # consecutive steps that were calls the run had already made, and the
        # temperature the next step is sampled at (raised to break a loop)
        repeats, step_temp = 0, temperature
        # …and the looser version of the same failure: one tool, over and over
        prev_tool, streak = None, 0
        # how the prompt is rendered for this run's model (see prompt.py), and
        # how much it may write per step (see LOCAL_STEP_TOKENS)
        compact = self.compact_prompt(short, model)
        step_tokens = min(max_tokens, LOCAL_STEP_TOKENS) if compact else max_tokens
        # per-run tally of identical calls that failed, so the loop can stop
        # replaying a dead end (see MAX_IDENTICAL_FAILURES) — and of the
        # read-only ones that worked, so it can stop replaying those too
        self._failed_calls: Dict[str, Dict[str, Any]] = {}
        self._done_calls: Dict[str, Any] = {}
        # start this thread's cost tally — whoever bills the run reads it back
        # with meter.take() once forward() returns
        self.meter.open(provider=short, model=model)
        for step_i in range(steps):
            # ── spend ceiling ──
            # Checked BEFORE the call, not after. An account with no credits
            # used to burn a whole model call on the module's key and only then
            # be told its "credit balance was spent" — nothing had been spent,
            # there was never anything to spend. The wording splits the two
            # cases, because they need different things from the user.
            if budget and not budget(self.meter.peek()):
                # step 0 means the account could never afford this run; later
                # means it afforded some of it. (Cost is not the tell — an
                # unpriced model tallies 0.0 all the way through.)
                step = {'tool': 'error', 'params': {}, 'error': (
                    'credit balance spent — top up to keep going' if step_i else
                    'no account credits — runs on the host key are billed to your '
                    'credit balance (not the provider key in the header). '
                    'Add credits, or pick a free model, to keep going.')}
                self._emit_step(step)
                history.append([step])
                print('Agent stopped: out of credits')
                break
            self.memory.update({'step': step_i, 'pwd': path})
            # inject recovery hint after repeated errors
            if consecutive_errors >= 3:
                self.memory.add('hint', 'Multiple errors in a row. Use think to reflect on what is going wrong and try a different approach.')
                consecutive_errors = 0
            try:
                context = self.context(compact=compact)
                # a hint is for the step it was written for. Left in place it
                # accumulates — a run that recovered on step 3 was still being
                # told about step 2's malformed JSON on step 20.
                self.memory.rm('hint')
                # say the call went out before it comes back — the watcher's
                # "thinking…" starts when the model does, not when it answers
                self._emit_live({'event': 'model_start', 'step': step_i, 'model': model})
                output = self.meter.watch(
                    client.forward(
                        context,
                        stream=True,
                        model=model,
                        max_tokens=step_tokens,
                        temperature=step_temp,
                        free=free,
                        **({'history': self._image_turn()} if self._images else {}),
                    ),
                    model_obj=client, provider=short,
                    model=model, prompt=context,
                )
                plan = self.plan(output, safety=safety)
            except Exception as e:
                # parens, not brackets: the log printer treats [x/y] as markup
                # and drops it, and the line read "Model error :"
                print(f"Model error ({short}/{model}): {e}")
                raw = err = str(e)
                # providers raise their own missing-key errors at call time —
                # point the user at the Builder, where keys are entered
                if 'api key' in err.lower() or 'api_key' in err.lower():
                    err = f"{err} — enter your {short} API key in the Builder (model node)."
                err = self._explain_rate_limit(short, model, err)
                plan = [{'tool': 'error', 'params': {}, 'error': err,
                         **({'detail': raw[:600]} if err != raw else {})}]
                self._emit_step(plan[-1])
            # what that call cost, while the run is still going — a failed call
            # burned tokens too, so this is outside the try
            self._emit_usage(step_i)
            history.append(plan)
            self.memory.add('history', history)
            if plan and plan[-1]['tool'].lower() in ('finish', 'response'):
                # …unless it signed off on a promise. A model that answers
                # "sure, I'll read the file and fix it" ends the run on step 0
                # with nothing done, and the caller reads an answer that only
                # describes the task. Say so once and let it go do the work.
                if not nudged and self._promised_without_doing(history):
                    nudged = True
                    self.memory.rm('hint')
                    self.memory.add('hint', DO_THE_WORK_HINT)
                    print('Agent promised without doing anything — pushing back')
                    continue
                print('Agent finished')
                break
            if plan and plan[-1]['tool'].lower() == 'error':
                print('Agent stopped: model error')
                break
            # track consecutive errors for recovery
            if plan and any(_step_failed(s) for s in plan):
                consecutive_errors += 1
            else:
                consecutive_errors = 0
            # ── a run going in circles ──
            # Every step was a call the run had already made. A hint says so,
            # but a small model at temperature 0 re-derives the same call from
            # the same task and loops until the budget is gone, so: sample the
            # next step instead of taking the argmax, and if that doesn't break
            # it either, stop and go write the answer. Nothing is lost — a
            # repeat produces no new information by definition.
            if plan and all(s.get('repeat') for s in plan):
                repeats += 1
                step_temp = max(temperature, REPEAT_TEMPERATURE)
                if repeats >= MAX_REPEAT_STEPS:
                    print('Agent stopped: repeating itself')
                    break
            else:
                repeats, step_temp = 0, temperature
            # …and the softer version of the same thing: the same tool over and
            # over with slightly different params ("let me list the files
            # first", four times). Reading six files in a row is legitimate, so
            # this only says so — it doesn't stop the run.
            last = plan[-1].get('tool') if plan else None
            streak = streak + 1 if last and last == prev_tool else 1
            prev_tool = last
            if streak >= SAME_TOOL_STREAK:
                self.memory.add('hint', (
                    f"You have used {last} {streak} times in a row. If you already "
                    f"have what you need, do something else with it — a different "
                    f"tool, or finish and write the answer."))
        # a run that used tools must still end with words: if the loop stopped
        # without a finish summary or response text (steps ran out, or finish
        # came back empty), make one last tools-off call for the actual answer
        if history and not self._has_answer(history) \
                and not any(s.get('tool') == 'error' for s in history[-1]):
            answer = self._force_answer(client=client, short=short, model=model,
                                        max_tokens=max_tokens,
                                        temperature=temperature, free=free)
            self._emit_usage(len(history) - 1)
            if answer:
                history[-1] = history[-1] + [answer]
        # the exchange goes to the memory module, which is where the next run
        # reads it back from — a failed run is still a turn that happened
        if session:
            self._remember_exchange(query, history, session=session, who=who,
                                    agent=agent_type)
        if save and m and mod:
            return m.fn('api/reg')(mod=mod, key=key, comment=query)
        return history[-1] if history else []

    def _remember_exchange(self, query: str, history: List[list], session: str,
                           who: str = None, agent: str = None) -> None:
        """File one finished user↔agent turn in the memory subsystem.

        Never raises: memory is context, and a memory write that failed is not
        a reason to lose the answer the user is waiting on.
        """
        try:
            answer = self._answer_text(history)
            if answer:
                self.memory.exchange(query, answer, session=session,
                                     who=who, agent=agent)
        except Exception as e:
            print(f"Memory write failed: {e}")

    @staticmethod
    def _answer_text(history: List[list]) -> str:
        """The text the user actually read — the last finish summary or
        response in the run, which is the agent's half of the exchange."""
        for plan in reversed(history or []):
            for s in reversed(plan or []):
                if not isinstance(s, dict):
                    continue
                if s.get('tool') == 'finish':
                    text = str((s.get('params') or {}).get('summary') or '').strip()
                    if text:
                        return text
                if s.get('tool') == 'response':
                    text = str(s.get('result') or '').strip()
                    if text:
                        return text
        return ''

    # ── plan parsing & execution ─────────────────────────────────────

    def _image_turn(self) -> List[Dict[str, Any]]:
        """The run's images as a leading user turn, OpenAI content-part shaped.

        Both providers hand `history` straight to an OpenAI-compatible client,
        so this is the only place images enter the conversation — the per-step
        prompt itself stays plain text. Needs a vision-capable model.
        """
        return [{
            'role': 'user',
            'content': [{'type': 'text', 'text': 'Images the user attached to this task:'}]
                       + [{'type': 'image_url', 'image_url': {'url': u}} for u in self._images],
        }]

    @classmethod
    def _promised_without_doing(cls, history: List[list]) -> bool:
        """True when the run is about to end having only said what it would do.

        Requires both halves: not one tool call in the whole run, and a
        sign-off written in the future tense. Either alone is legitimate —
        a question answered from knowledge calls nothing, and a run that read
        six files may well close by naming what it would do next.
        """
        for plan in history or []:
            for s in plan or []:
                if isinstance(s, dict) and str(s.get('tool') or '').lower() not in NON_WORK_TOOLS:
                    return False
        text = cls._answer_text(history)
        if not text:
            return False
        # only the opening matters: a long report that mentions "I'll" in its
        # last paragraph is a report, not a promise
        text = text.strip()
        return bool(PROMISE_RE.search(text[:400])
                    or (len(text) <= MAX_ACK_CHARS and ACK_RE.match(text)))

    @staticmethod
    def _has_answer(history: List[list]) -> bool:
        """True once the run produced text for the user — a non-empty finish
        summary or a response step. finish with a blank summary doesn't count."""
        for plan in history:
            for s in plan:
                if not isinstance(s, dict):
                    continue
                if s.get('tool') == 'finish' and str((s.get('params') or {}).get('summary') or '').strip():
                    return True
                if s.get('tool') == 'response' and str(s.get('result') or '').strip():
                    return True
        return False

    def _force_answer(self, client, short, model, max_tokens, temperature, free) -> Optional[dict]:
        """One last tools-off model call that turns the run's history into the
        answer the user reads. Returns a response step, or None if it fails.

        Takes the run's own client — see _client for why self.model is wrong here."""
        self.memory.rm('hint')
        compact = self.compact_prompt(short, model)
        if compact:
            max_tokens = min(max_tokens, LOCAL_ANSWER_TOKENS)
        try:
            # the answer prompt withholds the tools and the step format: shown
            # them again, a small model writes one more call instead of the
            # answer, and the user reads nothing at all
            context = self.context(compact=compact, answer=True)
            out = self.meter.watch(
                client.forward(
                    context,
                    stream=True,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    free=free,
                    **({'history': self._image_turn()} if self._images else {}),
                ),
                model_obj=client, provider=short,
                model=model, prompt=context,
            )
            steps, raw = self.parse_steps(out)
            text = ''
            for s in steps:  # honor a finish step if the model insisted on one
                if s.get('tool') == 'finish':
                    text = str((s.get('params') or {}).get('summary') or '').strip()
                    if text:
                        break
            if not text:
                text = self._strip_anchors(raw)
            if not text:
                # asked for prose, it wrote another tool call and nothing else.
                # Say so in the loop's own voice rather than handing the user an
                # empty bubble — the trail is the honest answer to what happened.
                text = self._trail_summary()
            if not text:
                return None
            step = {'tool': 'response', 'params': {}, 'result': text}
            self._emit_step(step)
            return step
        except Exception as e:
            print(f"Final-answer error: {e}")
            return None

    def _trail_summary(self) -> str:
        """What the run did, in one line, when the model wrote no answer.

        This is the module talking, not the model, so it says only what it can
        see: the steps that ran and the last thing one of them returned.
        """
        trail = [e for e in (self.memory.get('history') or []) for e in
                 (e if isinstance(e, list) else [e]) if isinstance(e, dict)]
        if not trail:
            return ''
        names = [s.get('tool') for s in trail if s.get('tool')]
        last = next((s.get('result') for s in reversed(trail) if s.get('result')), '')
        line = (f"The run ended without writing an answer — its model kept calling "
                f"tools instead. It took {len(names)} step(s): {', '.join(names[-8:])}.")
        if last:
            line += f"\n\nThe last result was:\n{str(last)[:800]}"
        return line

    def _emit_usage(self, step_i: int):
        """Hand the live callback what the model call for this step cost.

        Reads the meter's last call (and clears it), so a step that made no
        call — or a run nobody is metering — emits nothing. Never raises: a
        price is not worth losing a run over.
        """
        cb = getattr(self, '_on_usage', None)
        if not cb:
            return
        try:
            call = self.meter.last()
            if call:
                cb({'step': step_i, **call})
        except Exception:
            pass

    def _emit_step(self, step):
        """Notify the live-progress callback (if any) and record the step as an
        episode in durable memory. Never lets either kill the loop."""
        if hasattr(self.memory, 'observe'):
            try:
                self.memory.observe(step)
            except Exception:
                pass
        cb = getattr(self, '_on_step', None)
        if cb:
            try:
                cb(step)
            except Exception:
                pass

    def _emit_live(self, ev: dict):
        """Hand the live-events callback something ephemeral — a chunk of the
        model's output as it streams, a tool call the moment it starts. This
        is progress a watcher renders and throws away: nothing here lands in
        memory or the run's history, and it must never kill the loop."""
        cb = getattr(self, '_on_live', None)
        if cb:
            try:
                cb(ev)
            except Exception:
                pass

    def _strip_anchors(self, text: str) -> str:
        """Drop plan/step scaffolding from text meant for the user's eyes.

        Reasoning blocks go with it: a thinking model's scratchpad is the
        working, not the answer, and it opens mid-sentence in the second
        person ("The user wants me to…"), which reads as the agent talking
        about the reader rather than to them.
        """
        import re
        text = re.sub(r'<STEP>.*?</STEP>', '', text, flags=re.S)
        text = THINK_BLOCK.sub('', text)
        text = re.sub(r'<\|[^|]{0,40}\|>', '', text)   # tool_call_start & co
        for tag in (*self.anchors['plan'], *self.anchors['tool']):
            text = text.replace(tag, '')
        return text.strip()

    # ── the prompt, and the calls that come back ─────────────────────

    def compact_prompt(self, provider: str = None, model: str = None) -> bool:
        """Does this run's model get the compact prompt? (see prompt.py)

        Every LFM provider is small compute by definition — weights on this
        box, or in a tab — and anything else that names a size under 5B is
        telling us the same thing.
        """
        if self._provider_short(provider) in self.LOCAL_PROVIDERS:
            return True
        return bool(model and self.SMALL_MODEL_RE.search(model))

    def context(self, compact: bool = False, answer: bool = False) -> str:
        """Working memory as the text the model actually reads.

        This used to be `str(memory.get())` — a Python dict repr, tool schemas
        and all. Frontier models read through that; a 1.2B model answers the
        shape it recognises instead, which is chat, and the run ends in a
        paragraph rather than a tool call. prompt.py renders the same state as
        sections, and a shorter prompt is a cheaper prompt on every provider.

        `answer` is the run's closing prompt — no tools, no step format, just
        the history and the instruction to write the user their answer.
        """
        text = render_prompt(self.memory.get(), compact=compact, answer=answer)
        import os as _os
        if _os.environ.get('AGENT_PROMPT_DUMP'):
            with open(_os.environ['AGENT_PROMPT_DUMP'], 'a') as _f:
                _f.write('\n===== PROMPT =====\n' + text + '\n')
        return text

    def _schemas(self) -> Optional[Dict[str, Dict]]:
        """The tool schemas this run was compiled with — what a returned call
        is checked against. None when the loadout isn't a schema dict."""
        tools = self.memory.get('tools')
        return tools if isinstance(tools, dict) and tools else None

    def _fix_step(self, raw: dict) -> Optional[dict]:
        """One parsed step mapped onto tools this registry actually has.

        A model that calls `read_file(path=…)` means `read(file_path=…)`, and
        failing that call teaches it nothing — it was a correct decision typed
        in another harness's dialect. steps.normalize does the mapping; a name
        that resolves to nothing at all is passed through untouched so the
        registry's own "tool not found" reaches the model as the error.
        """
        fixed = normalize_step(raw, schemas=self._schemas())
        if fixed:
            extra = {k: v for k, v in raw.items()
                     if k not in ('tool', 'params', 'name', 'arguments')}
            return {**extra, **fixed}
        return raw if isinstance(raw.get('tool'), str) else None

    def plan(self, output: str, safety: bool = False) -> list:
        """Parse LLM output into steps and execute them."""
        steps, raw_text = self.parse_steps(output)
        if not steps and raw_text.strip():
            # no anchored step: read the whole response for a call written in
            # some other convention — a fenced JSON block, <tool_call>, a
            # pythonic `[bash(command="ls")]`. Models trained by another
            # harness reach for its format under pressure, and the decision in
            # there is as good as an anchored one (see steps.py).
            steps = [s for s in (self._fix_step(c) for c in
                                 parse_calls(raw_text, schemas=self._schemas()))
                     if s]
        if not steps and raw_text.strip():
            if self.anchors['tool'][0] in raw_text:
                # the model tried to call a tool and the step didn't parse —
                # a broken call, not an answer. Tell it so and let the loop
                # retry, rather than dumping the raw anchors on the user.
                self.memory.add('hint', 'Your last step could not be parsed. Emit exactly '
                                        'one <STEP>{"tool": ..., "params": {...}}</STEP> with '
                                        'strictly valid JSON and balanced braces.')
                step = {'tool': 'invalid', 'params': {},
                        'error': 'malformed step JSON — retrying'}
                self._emit_step(step)
                return [step]
            # LLM responded with plain text and no tool calls — that's the answer
            step = {'tool': 'response', 'params': {}, 'result': self._strip_anchors(raw_text)}
            self._emit_step(step)
            return [step]
        steps = self.run_plan(steps, safety=safety)
        return steps

    def parse_steps(self, output) -> tuple:
        """Consume LLM output — a string or a stream of chunks — into steps.

        One linear pass: each chunk is printed as it lands and searched for the
        next </STEP> from where the previous search stopped, and text up to a
        closed step is dropped from the buffer. The old scan re-tested the whole
        response for both anchors on every single character, which burned
        seconds of CPU on a long answer.

        Returns:
            (plan, raw_text) — plan is list of step dicts, raw_text is full LLM output
        """
        open_tag, close_tag = self.anchors['tool'][0], self.anchors['tool'][1]
        chunks = []   # every chunk, joined once at the end
        buf = ''      # text since the last </STEP>, still to be matched
        scan = 0      # where the next </STEP> search starts inside buf
        plan = []
        for chunk in ((output,) if isinstance(output, str) else output):
            if not chunk:
                continue
            chunks.append(chunk)
            print(chunk, end='')
            self._emit_live({'event': 'token', 'text': chunk})
            buf += chunk
            while True:
                end = buf.find(close_tag, scan)
                if end < 0:
                    # only a tail shorter than the anchor can still begin one
                    scan = max(0, len(buf) - len(close_tag) + 1)
                    break
                start = buf.rfind(open_tag, 0, end)
                if start >= 0:
                    step = self._step_from_json(buf[start + len(open_tag):end])
                    if step:
                        plan.append(step)
                buf = buf[end + len(close_tag):]
                scan = 0
        return plan, ''.join(chunks)

    def _extract_step(self, text: str) -> Optional[dict]:
        """Extract a single step from anchored text (…<STEP>{json}</STEP>…)."""
        open_tag, close_tag = self.anchors['tool'][0], self.anchors['tool'][1]
        end = text.find(close_tag)
        if end < 0:
            return None
        start = text.rfind(open_tag, 0, end)  # nearest open — skips a stray <STEP>
        if start < 0:
            return None
        return self._step_from_json(text[start + len(open_tag):end])

    def _step_from_json(self, raw: str) -> Optional[dict]:
        """Turn one step's JSON payload into a runnable step, or None.

        Models get the shape slightly wrong all the time: no params at all,
        params handed back as a JSON string, a trailing comma, a code fence, an
        answer the token limit cut in half. Anything recoverable is recovered
        here; only a payload with no usable tool name is dropped.
        """
        raw = raw.strip()
        if not raw:
            return None
        print(f"STEP: {raw}")
        try:
            step = json.loads(raw)
        except ValueError:
            step = self._repair_json(raw)
        if not isinstance(step, dict):
            return None
        tool = step.get('tool') or step.get('name')
        if not isinstance(tool, str) or not tool.strip():
            return None      # run_plan calls .lower() on this — it must be a name
        step['tool'] = tool.strip()
        params = step.get('params')
        if isinstance(params, str):              # double-encoded params
            # a string that isn't JSON at all is kept: for a one-parameter
            # tool it is that parameter's value, which normalize places
            step['params'] = self._repair_json(params) or params
        # an anchored step is still written in whatever dialect the model
        # knows: map its name and parameters onto this registry's before it
        # is run (see _fix_step)
        step = self._fix_step(step) or step
        if not isinstance(step.get('params'), dict):
            step['params'] = {}
        return step

    @staticmethod
    def _first_object(s: str) -> Optional[str]:
        """The first brace-balanced {...} in s, ignoring braces inside strings."""
        start = s.find('{')
        if start < 0:
            return None
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            c = s[i]
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
                    return s[start:i + 1]
        return None

    def _repair_json(self, raw: str) -> Optional[dict]:
        """Best-effort repair of slightly-malformed step JSON from the model.

        Tries an optional `fix_json` tool if one is installed, then falls back
        to dependency-free fixes: strip code fences, keep the first balanced
        object, close what a truncated answer left open (dropping trailing
        commas and half-written keys), and finally read it as a Python literal —
        some models answer in single quotes. Returns None if it still can't
        parse — the step is then skipped, not crashed on.
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
        s = _strip_fence(raw.strip())
        # keep only the first balanced {...} — models routinely close the object
        # one brace too many (or trail commentary after it); rindex would swallow
        # the extra and fail, so cut where the nesting actually returns to zero.
        # No balanced object at all means the answer was cut off mid-JSON: take
        # everything from the opening brace and let _close_json finish it.
        obj = self._first_object(s)
        if obj is None:
            start = s.find('{')
            if start < 0:
                return None
            obj = s[start:]
        for candidate in (obj, _close_json(obj)):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                try:
                    parsed = ast.literal_eval(candidate)
                except Exception:
                    continue
            if isinstance(parsed, dict):
                return parsed
        return None

    # params whose value is a filesystem path, and so is meant relative to the
    # run's own directory rather than to whatever directory this server was
    # started in. `cwd` is here too: bash inherits the run's directory unless
    # the model says otherwise.
    PATH_PARAMS = ('file_path', 'path', 'cwd', 'log_file', 'file', 'file_a', 'file_b')

    def _resolve_paths(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Anchor a step's paths to the run's working directory.

        The model is told the directory the run is in and writes `a.txt`
        meaning the file in it — but a tool resolves that against the *server
        process's* cwd, which is somewhere else entirely, so the read failed
        (or, worse, quietly read a different a.txt). Small models write
        relative paths constantly, so this is most of the difference between a
        local run that works and one that doesn't.

        The same reasoning fills in a `path` the model left out: `tree()` with
        no argument means "here", and here is the run's directory, not the
        directory this server happened to start in.
        """
        base = getattr(self, '_path', None)
        if not base or not isinstance(params, dict):
            return params
        out = dict(params)
        for key in self.PATH_PARAMS:
            value = out.get(key)
            if isinstance(value, str) and value and not os.path.isabs(value) \
                    and not value.startswith('~'):
                out[key] = os.path.normpath(os.path.join(base, value))
        schema = (self._schemas() or {}).get(name) or {}
        for key in ('path', 'cwd'):
            if key in (schema.get('params') or {}) and not out.get(key):
                out[key] = base
        return out

    def run_plan(self, plan: List[Dict[str, Any]], safety: bool = False) -> List[Dict[str, Any]]:
        """Execute parsed steps using tools. Enforces path sandboxing via _allowed_paths."""
        if safety and plan:
            confirm = input("Execute plan? (y/n): ")
            if confirm.lower() != 'y':
                raise Exception("Aborted by user")
        allowed = getattr(self, '_allowed_paths', None)
        failed = getattr(self, '_failed_calls', None)
        if failed is None:
            failed = self._failed_calls = {}
        done = getattr(self, '_done_calls', None)
        if done is None:
            done = self._done_calls = {}
        for i, step in enumerate(plan):
            name = step['tool'].lower()
            params = self._resolve_paths(name, step.get('params', {}))
            step['params'] = params
            if name in ('finish', 'review'):
                print(f"[{i+1}/{len(plan)}] {name}")
                self._emit_step(step)
                break

            # ── repeat-call guard ──
            # The same call that already failed twice gets its old result back
            # instead of running again. The model was burning whole runs
            # re-fetching a URL that answered 403 every time; the error text is
            # the nudge to change approach or answer with what it has.
            sig = _call_sig(name, params)
            seen = failed.get(sig)
            if seen and seen['n'] >= MAX_IDENTICAL_FAILURES:
                plan[i]['result'] = seen['result']
                plan[i]['error'] = (
                    f"repeat call blocked: {name} with these exact params already "
                    f"failed {seen['n']} times with the result above. Retrying it "
                    f"unchanged will fail again — change the params, try a "
                    f"different tool, or finish with what you already know.")
                print(f"[{i+1}/{len(plan)}] {name} -> blocked (repeat of a failed call)")
                self._emit_step(plan[i])
                continue

            # ── the same guard for a call that *worked* ──
            # A small model that gets a good result often answers the next
            # prompt with the same call again, and again, until the step budget
            # is gone — it read the task, not the trail. The cached result comes
            # straight back with a line saying so, which is the only signal that
            # reliably breaks the loop. Read-only tools only, and the cache is
            # dropped the moment anything writes (see below), so re-reading a
            # file after an edit still really re-reads it.
            cached = done.get(sig)
            if cached is not None:
                plan[i]['result'] = cached
                plan[i]['repeat'] = True
                plan[i]['note'] = (
                    f"you already ran {name} with exactly these params earlier in "
                    f"this run — that is its result above, not a new one. Use it: "
                    f"take the next step, or finish and write the answer.")
                # …and again where the model is looking hardest. The history
                # note alone does not turn a small model around; a direct
                # instruction in the hint slot does.
                self.memory.add('hint', (
                    f"You have already called {name} with those exact parameters "
                    f"and its result is in the history above. Do NOT call it "
                    f"again. Your next step must be a different tool, or finish "
                    f"with the answer written out of what you already have."))
                print(f"[{i+1}/{len(plan)}] {name} -> cached (identical earlier call)")
                self._emit_step(plan[i])
                continue

            # ── sandboxing: a portal run is not the host ──
            if allowed is not None:
                if self.tools.kind(name) not in ('builtin', 'custom'):
                    # a fleet module runs host code against host state — the
                    # same trust level as writing a custom tool, so host-only.
                    # Bare module names fall in here too, prefix or not.
                    plan[i]['error'] = (
                        f"Permission denied: {name} calls another module on the host. "
                        f"Fleet tools are host-only.")
                    print(f"[{i+1}/{len(plan)}] {name} -> blocked (fleet)")
                    self._emit_step(plan[i])
                    continue
                if name in WRITE_TOOLS:
                    fp = params.get('file_path', '')
                    if fp and not check_path_allowed(fp, allowed):
                        plan[i]['error'] = f"Permission denied: cannot write to {fp}. Restricted to {allowed}"
                        print(f"[{i+1}/{len(plan)}] {name} -> blocked (path)")
                        self._emit_step(plan[i])
                        continue
                if self.tools.is_shell(name):
                    # custom tools are shell too — force cwd into the portal
                    params['cwd'] = allowed[0]
                if name == 'git':
                    params['cwd'] = params.get('cwd') or allowed[0]

            # announce the call before it runs, not after it returns — a slow
            # bash command is exactly when a watcher wants to see what's up
            self._emit_live({'event': 'tool_start', 'tool': name,
                             'params': params, 'i': i + 1, 'n': len(plan)})
            try:
                # the registry knows all three kinds; a bare module name the
                # model reached for without the prefix still resolves via m.tool
                if self.tools.exists(name):
                    result = self.tools.run(name, **params)
                elif m:
                    result = m.tool(name)(**params)
                else:
                    result = {"error": f"unknown tool: {name}"}
                plan[i]['result'] = result
                print(f"[{i+1}/{len(plan)}] {name} -> done")
            except Exception as e:
                plan[i]['error'] = str(e)
                print(f"[{i+1}/{len(plan)}] {name} -> error: {e}")
            if _step_failed(plan[i]):
                failed[sig] = {'n': (seen['n'] + 1) if seen else 1,
                               'result': plan[i].get('result') or plan[i].get('error')}
            elif name in READONLY_TOOLS:
                done[sig] = plan[i].get('result')
            if name not in READONLY_TOOLS:
                # something ran that could have changed the workspace, so every
                # cached look at it is now stale
                done.clear()
            self._emit_step(plan[i])
        return plan


# backwards compat
Dev = Agent


class Mod(Agent):
    description = "Autonomous coding agent. Built-in tools, custom shell tools, and the whole fleet."

    # addresses holding the owner's standing beside the owner. Loaded from
    # ~/.mod/agent/owner.json in __init__; the empty default keeps every
    # owner gate working on a Mod built without one.
    _co_owners = ()

    # the agent a run lands on when the caller named none — the Claude Code
    # CLI on this host. It's a harness agent, so it only holds for the owner
    # with the CLI installed; everyone else falls back to the native loop
    # (see default_agent).
    DEFAULT_AGENT = 'claude-code'
    FALLBACK_AGENT = 'default'

    # harness -> the console module whose own sign-in also vouches for a
    # caller. The CLI drivers (claudecode, codexcli) carry no identity of
    # their own, so the console that fronts the same agent answers for them:
    # whoever the claude module's auth calls owner may run Claude Code here,
    # the codex module's owner may run Codex, and so on. Identity is the
    # fleet's one auth module (m.mod('auth')) end to end — the token this
    # module verifies is the same token those consoles verify — so there is
    # no second ACL to keep in sync.
    HARNESS_AUTH = {
        'claude': 'claude',
        'claudemod': 'claude',
        'codex': 'codex',
        'buildmod': 'build',
        'chainmod': 'chain',
    }

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
        self.auth = m.mod('auth')() if m else None
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
        # co-owners: addresses the owner has handed the same standing to.
        # They pass every is_owner() gate and, because a co-owner's credit
        # account is aliased to the owner's, they run on the owner's credits
        # rather than a balance of their own. Private auth state, so the list
        # lives off-tree in ~/.mod/agent/owner.json (env override for a
        # containerised deploy) and never in the committed config.
        self._co_owners = self._load_co_owners()
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
            self._vault_dir = state_dir / 'vault'
            # per-address preferences (which agent your runs land on) —
            # private state, same treatment as the ACL
            self._prefs_path = state_dir / 'prefs.json'
        except Exception:
            self._acl_path = self.module_dir / '.acl.json'
            self._vault_dir = self.module_dir / '.vault'
            self._prefs_path = self.module_dir / '.prefs.json'
        self._acl = self._load_acl()
        # a remembered unlock survives restarts — resume it before anything
        # asks for a model, so the key is live without a passphrase prompt
        self._vault_resume()

        # prepaid credit ledger — guests top up USDT/USDC and spend the
        # credits to run on the module's public provider key. Ledger state
        # is private, off-tree, next to the ACL.
        self.credits = Credits(self._acl_path.parent, deposit_address=self._owner)
        # a co-owner spends the owner's balance, not one of their own
        self._sync_credit_aliases()
        # the ledger owns the pricing knobs; the meter just applies them
        self.meter.multiplier = self.credits.cost_multiplier

        # ownership — the host (module owner) owns everything unowned, so
        # shipped agents and seeded prompts answer to them. Agents and the
        # library share this one resolver.
        self.identity = Identity(host=self._owner,
                                 resolve=lambda k: self._resolve_address(k, verified=True),
                                 is_host=self.is_owner)
        self.agents.bind(self.identity)

        # unified library (prompts / tool docs / memory / agent market)
        # user collections persist off-tree under ~/.mod/agent/library/
        self.library = Library(tools=self.tools, agents=self.agents,
                               identity=self.identity)

        # per-address key-value vaults (public + private entries), persisted
        # through the mod store module under ~/.mod/agent/vaults/
        self.vaults = Vaults()

        # module visibility for the whole fleet. Public by default and
        # readable by anyone — a module you can't read is a module you can't
        # trust — with an owner switch that seals a module into ciphertext
        # before it can reach a public remote. State: ~/.mod/agent/privacy/
        self.privacy = Privacy()

        # internet-wide tool aggregator: scans GitHub, npm, the MCP
        # registry, Glama and curated lists for installable tool documents
        self.discover = Discover()

        # external agent CLIs (claude code, codex) an agent can hand its run to
        self.harness = Harness()

        # the arena: every agent on the same tasks, one ranked board. Its
        # scheduler is started by the API (see arena/mod.py) — importing the
        # module must never kick off runs on somebody's provider key.
        self.arena = Arena(runner=self.arena_run, agents=self.agents)

        self._public_actions = {'status', 'health', 'schema',
                                'agents', 'agent', 'chains', 'harnesses', 'agent_cids',
                                'agent_load', 'library', 'prompts', 'prompt_add',
                                'prompt_rm', 'memory', 'memory_add', 'memory_rm',
                                'upload', 'library_import', 'formats',
                                'discover', 'discover_sources', 'discover_detail',
                                'discover_doc', 'tool_install', 'installed_tools',
                                'tool_import', 'tool_uninstall',
                                'toolboxes', 'toolbox', 'snapped', 'tools', 'tool',
                                'mods',
                                # what the agent is made of, and the memory
                                # modules one can be built with
                                'parts', 'memories', 'mcp',
                                'recall', 'retrieve', 'episodes', 'facts',
                                'memory_state',
                                # self-scoped: you get your own turns, nobody else's
                                'exchanges',
                                # the board is public — a ranking nobody can
                                # read is not a ranking
                                'arena', 'arena_tasks', 'arena_matches',
                                'arena_card', 'arena_status',
                                # the same matches read by model: what each one
                                # scored, how fast it was, what it burned
                                'arena_models', 'arena_model', 'arena_task_board',
                                'key_info', 'balance',
                                'credits', 'credit_deposit', 'credit_price',
                                # vaults self-scope to the caller's verified
                                # address (Vaults raises without a sign-in)
                                'vaults', 'vaults_get', 'vaults_set',
                                'vaults_add', 'vaults_rm', 'vaults_key_rm',
                                'vaults_public',
                                # auditing the fleet is the whole point of a
                                # public module — no sign-in, no key
                                'modules', 'module_tree', 'module_file',
                                # arena tasks: listed by anyone, but writing one
                                # takes a sign-in and editing one takes owning
                                # it — each of these enforces that itself, and
                                # a draft additionally answers to run policy
                                'arena_task_draft', 'arena_task_add', 'arena_task_rm',
                                # the openarena schema: the board next door is
                                # public too, and each write here enforces its
                                # own sign-in / authorship
                                'openarena', 'openarena_task', 'openarena_sources',
                                'openarena_task_add', 'openarena_task_rm',
                                'openarena_preview', 'openarena_import'}
        self._admin_actions = {'run', 'plan', 'serve', 'kill',
                               'test', 'grant', 'revoke', 'acl',
                               'agent_save', 'agent_install', 'set_key',
                               'unlock', 'lock', 'vault_rm',
                               'toolbox_add', 'toolbox_rm', 'snap', 'unsnap', 'select',
                               'tool_add', 'tool_rm', 'tool_run',
                               'remember', 'forget', 'memory_serve', 'memory_kill',
                               # a round spends real steps on a provider key
                               'arena_run', 'arena_qualify', 'arena_config',
                               'arena_scheduler',
                               # ...and a gauntlet spends them on a named model,
                               # which is the one place the board runs paid ones
                               'arena_gauntlet',
                               # our agent on openarena's board: they run it on
                               # our key, so the host decides
                               'openarena_enter',
                               'credit_grant', 'treasury', 'credit_topup',
                               'credit_verify', 'credit_withdraw', 'credit_config',
                               # visibility + sealing are owner-only: they
                               # decide what the world can read (see below,
                               # each one calls require_owner itself)
                               'module_visibility', 'modules_visibility',
                               'module_seal', 'module_unseal', 'module_restore',
                               'privacy_key'}

    # ── permissions (Claude module interface) ────────────────────────────

    @property
    def _owner_file(self) -> Path:
        return Path.home() / '.mod' / 'agent' / 'owner.json'

    def _load_owner_file(self):
        """Read owner from ~/.mod/agent/owner.json (claude-style, import-independent)."""
        try:
            p = self._owner_file
            if p.exists():
                with open(p) as f:
                    return json.load(f).get('owner')
        except Exception:
            pass
        return None

    def _load_co_owners(self) -> list:
        """Addresses that hold the owner's standing beside the owner.

        Read from ~/.mod/agent/owner.json (`co_owners`) and, for deploys with
        no writable home, AGENT_CO_OWNERS as a comma-separated list. The
        primary owner is never in this list — they are `_owner`.
        """
        found = []
        try:
            p = self._owner_file
            if p.exists():
                with open(p) as f:
                    data = json.load(f)
                found += list(data.get('co_owners') or data.get('owners') or [])
        except Exception:
            pass
        found += [a for a in (os.environ.get('AGENT_CO_OWNERS') or '').split(',') if a.strip()]
        out = []
        for a in found:
            a = str(a).strip().lower()
            if a.startswith('0x') and len(a) == 42 and a != self._owner and a not in out:
                out.append(a)
        return out

    def _save_co_owners(self):
        """Persist the co-owner list next to the owner, off-tree."""
        p = self._owner_file
        data = {}
        try:
            if p.exists():
                with open(p) as f:
                    data = json.load(f) or {}
        except Exception:
            data = {}
        data['owner'] = data.get('owner') or self._owner
        data['co_owners'] = list(self._co_owners)
        data.pop('owners', None)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)

    def _sync_credit_aliases(self):
        """Point every co-owner's credit account at the owner's."""
        credits = getattr(self, 'credits', None)
        if not credits or not self._owner:
            return
        credits.aliases = {a: self._owner for a in self._co_owners}

    def owners(self, op: str = 'list', address: str = None, key: str = None) -> dict:
        """List / add / remove co-owners. The primary owner alone may change it.

        A co-owner passes every owner gate and shares the owner's credit
        account, so adding one is handing over the module — hence it is the
        one thing a co-owner cannot do themselves.
        """
        op = (op or 'list').lower()
        if op in ('list', ''):
            self.require_owner(key, 'owners')
            return {'owner': self._owner, 'co_owners': list(self._co_owners)}
        # mutations: primary owner only
        addr = self._resolve_address(key, verified=True)
        if not self._owner or (addr or '').lower() != self._owner:
            raise PermissionError(
                "Permission denied: only the primary owner can add or remove co-owners.")
        target = str(address or '').strip().lower()
        if not (target.startswith('0x') and len(target) == 42):
            raise ValueError('a 0x address is required')
        if op == 'add':
            if target == self._owner:
                raise ValueError('that address is already the owner')
            if target not in self._co_owners:
                self._co_owners = list(self._co_owners) + [target]
        elif op in ('rm', 'remove', 'delete'):
            self._co_owners = [a for a in self._co_owners if a != target]
        else:
            raise ValueError(f'unknown op: {op}')
        self._save_co_owners()
        self._sync_credit_aliases()
        return {'owner': self._owner, 'co_owners': list(self._co_owners),
                'op': op, 'address': target}

    def _resolve_address(self, key=None, verified: bool = False) -> str:
        """Resolve a key/address/token to an address string.

        With verified=True only identities we can actually check are honored:
        a local key object, or a signed protocol-auth token. A bare address is
        a *claim*, not proof — trusting it would let anyone pass the owner's
        address and hold owner rights — so it counts only when no verifier is
        configured at all (local/dev runs without the auth mod).
        """
        if key is None:
            return self.key.address if self.key else ''
        if hasattr(key, 'address'):
            return key.address
        key_str = str(key)
        if self.auth:
            try:
                return self.auth.verify(key_str)['key']
            except Exception:
                pass
            # a plain string is an unverified claim once a verifier exists
            return '' if verified else key_str
        return key_str

    def is_owner(self, key=None) -> bool:
        """Check if key/address/token belongs to the owner or a co-owner."""
        if not self._owner:
            return True
        addr = self._resolve_address(key, verified=True)
        if not addr:
            return False
        addr = addr.lower()
        return addr == self._owner.lower() or addr in getattr(self, '_co_owners', ())

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
        - `run` is also open to any signed-in caller with a positive
          credit balance (prepaid use of the module's public key)
        """
        if self.is_owner(key):
            return True
        if action in self._public_actions:
            return True
        # check ACL grants — a grant is only honored for a verified caller,
        # otherwise anyone could name a granted address and inherit it
        addr = self._resolve_address(key, verified=True).lower()
        if addr in self._acl:
            grant = self._acl[addr]
            allowed = grant.get('actions', [])
            if '*' in allowed or action in allowed:
                return True
        # paid access: credits buy runs on the public key
        credits = getattr(self, 'credits', None)
        if action == 'run' and credits and credits.balance(addr) > 0:
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
            actions: list of actions to grant (default: ['run', 'tool_run'])
                     use ['*'] for full admin access
            key: caller key (must be owner)
        """
        self.require_owner(key, 'grant')
        addr = address.lower()
        actions = actions or ['run', 'tool_run']
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

    # ── module visibility (public audit / private seal) ──────────────
    #
    # Reading a public module is open (see the `modules`, `module_tree` and
    # `module_file` actions). Everything that CHANGES what the world can read
    # is owner-only, and says so here rather than trusting the caller.

    def module_visibility(self, name: str, visibility: str,
                          passphrase: str = None, key=None) -> dict:
        """Flip one module public/private. Private seals it. Owner only."""
        self.require_owner(key, 'module_visibility')
        return self.privacy.set(name, visibility, passphrase)

    def modules_visibility(self, visibility: str, passphrase: str = None,
                           key=None) -> dict:
        """Flip the whole fleet, and the default new modules inherit. Owner only."""
        self.require_owner(key, 'modules_visibility')
        return self.privacy.set_all(visibility, passphrase)

    def module_seal(self, name: str, passphrase: str = None, key=None) -> dict:
        """Re-seal a private module after editing it. Owner only."""
        self.require_owner(key, 'module_seal')
        return self.privacy.seal(name, passphrase)

    def module_unseal(self, name: str, key=None) -> dict:
        """Drop a module's blob and put its tree back under git. Owner only."""
        self.require_owner(key, 'module_unseal')
        return self.privacy.unseal(name)

    def module_restore(self, name: str, passphrase: str = None,
                       force: bool = False, key=None) -> dict:
        """Unpack a sealed blob back into source — the clone side. Owner only."""
        self.require_owner(key, 'module_restore')
        return self.privacy.restore(name, passphrase, force)

    def privacy_key(self, op: str = 'state', passphrase: str = None,
                    current: str = None, key_b64: str = None, key=None) -> dict:
        """The fleet key: state / export / import / passphrase. Owner only.

        Export exists because the key is the only thing that opens a sealed
        push — lose it and the blob is noise. It never leaves this method
        without an owner signature.
        """
        self.require_owner(key, 'privacy_key')
        if op == 'export':
            return {'key': self.privacy.key_export(passphrase),
                    'warning': 'anyone holding this opens every sealed module'}
        if op == 'import':
            return self.privacy.key_import(key_b64 or '', passphrase)
        if op == 'passphrase':
            return self.privacy.key_passphrase(passphrase, current)
        return self.privacy.key_state()

    # ── credits (prepaid public-key usage) ───────────────────────────

    def credits_info(self, key: str = None) -> dict:
        """Deposit/pricing info + the caller's own credit account."""
        addr = self._resolve_address(key) if key else ''
        if not (isinstance(addr, str) and addr.startswith('0x') and len(addr) == 42):
            addr = None
        return self.credits.info(addr, owner=bool(key) and self.is_owner(key))

    def credit_deposit(self, tx_hash: str, network: str = 'base',
                       provider: str = None) -> dict:
        """Verify a USDT/USDC/ETH deposit tx and credit the on-chain sender.
        `provider` earmarks it for the openrouter or venice key."""
        return self.credits.verify_deposit(tx_hash, network, provider)

    def credit_price(self, network: str = 'base') -> dict:
        """ETH/USD a native deposit on `network` is credited at (Chainlink)."""
        return self.credits.eth_usd(network)

    def credit_grant(self, address: str, amount: float, note: str = '',
                     key: str = None) -> dict:
        """Top up or deduct any account (± amount). Owner only.

        This is the owner's side of the ledger: they already pay the
        providers directly, so they never buy credits for themselves —
        they hand them to whoever should be able to run, and take them
        back the same way. A deduction stops at zero.
        """
        self.require_owner(key, 'credit_grant')
        amount = float(amount)
        kind = 'grant' if amount >= 0 else 'debit'
        verb = 'granted' if amount >= 0 else 'deducted'
        return self.credits.credit(address, amount, kind=kind,
                                   note=note or f'{verb} by {self._owner}')

    def charge_run(self, address: str, usage: dict, note: str = '') -> dict:
        """Bill a finished guest run from its metered cost.

        A priced run is charged provider cost × (1 + fee_rate); a run the
        meter couldn't price (unknown model, harness CLI) falls back to the
        flat per-step price so nothing runs for free by accident.
        """
        usage = usage or {}
        if usage.get('priced') and usage.get('calls'):
            out = self.credits.charge_usage(address, usage.get('cost', 0.0), note=note,
                                            model=usage.get('model'),
                                            steps=usage.get('steps', 0))
        else:
            out = self.credits.charge_steps(address, usage.get('steps', 0),
                                            note=note, model=usage.get('model'))
        out['usage'] = {k: usage.get(k) for k in
                        ('model', 'provider', 'calls', 'prompt_tokens',
                         'completion_tokens', 'priced')}
        return out

    # ── treasury (guest deposits ↔ provider credits) ─────────────────

    def provider_funding(self) -> dict:
        """Live balance + lifetime usage on each provider key we bill against."""
        out = {}
        for name in ('openrouter', 'venice'):
            try:
                bal = self.balance(name)
            except Exception as e:
                out[name] = {'error': str(e)}
                continue
            out[name] = {'balance': bal.get('balance'),
                         'usage': bal.get('total_usage'),
                         # credits ever bought on the key — only OpenRouter
                         # reports it, and it is what makes a top-up exact
                         'purchased': bal.get('total_credits'),
                         'configured': bal.get('configured', False),
                         'key_source': bal.get('source')}
            if bal.get('error'):
                out[name]['error'] = bal['error']
        return out

    def credits_treasury(self, key: str = None, live: bool = True) -> dict:
        """The funding picture: deposits in, provider credits out, margin kept.

        Owner only — it is the module's whole book. `live=False` skips the
        provider balance calls when only the ledger side is wanted.
        """
        self.require_owner(key, 'credits_treasury')
        return self.credits.treasury(self.provider_funding() if live else None)

    def credit_topup(self, provider: str, amount: float, ref: str = '',
                     note: str = '', key: str = None) -> dict:
        """Record credits bought at a provider out of the deposit float. Owner only."""
        self.require_owner(key, 'credit_topup')
        return self.credits.record_topup(provider, amount, ref=ref, note=note)

    def credit_topup_verify(self, provider: str = 'openrouter', key: str = None) -> dict:
        """Book a top-up by reading it off the provider key. Owner only.

        Neither provider sells credits over an API (OpenRouter's Coinbase
        endpoint answers 410 Gone, Venice never had one), so the money is
        always sent on the provider's own page — `credits.PROVIDER_TOPUP`
        holds the link the console opens. This closes the loop: it re-reads
        the key and books whatever arrived, so the books record what landed
        instead of an amount typed from memory.
        """
        self.require_owner(key, 'credit_topup_verify')
        provider = (provider or 'openrouter').strip().lower()
        bal = self.balance(provider)
        live = {'balance': bal.get('balance'), 'purchased': bal.get('total_credits'),
                'error': bal.get('error')}
        out = self.credits.verify_topup(provider, live)
        out['balance'] = bal.get('balance')
        return out

    def credit_withdraw(self, amount: float, note: str = '', key: str = None) -> dict:
        """Take earned margin out of the float. Owner only."""
        self.require_owner(key, 'credit_withdraw')
        return self.credits.record_withdrawal(amount, note=note)

    def credit_config(self, key: str = None, **kwargs) -> dict:
        """Set the margin and pricing knobs (fee_rate, price_per_step,
        cost_multiplier, deposit_address). Owner only."""
        self.require_owner(key, 'credit_config')
        cfg = self.credits.set_config(**kwargs)
        self.meter.multiplier = self.credits.cost_multiplier
        return cfg

    # ── provider api keys, encrypted vault & balance ─────────────────

    KEY_STORES = {
        # provider -> (keys file, env var, required key prefix)
        # the keys file matches each model module's own store
        'openrouter': (Path.home() / '.mod' / 'model' / 'openrouter' / 'apikeys.json',
                       'OPENROUTER_API_KEY', 'sk-'),
        'venice': (Path.home() / '.mod' / 'model' / 'venice' / 'apikeys.json',
                   'VENICE_API_KEY', ''),
    }

    def _provider_keys(self, provider: str) -> list:
        """Active API keys for a provider.

        Priority: vault-unlocked session key > env var > plaintext store.
        """
        session_key = self._session_keys.get(provider)
        if session_key:
            return [session_key]
        store = self.KEY_STORES.get(provider)
        if not store:
            return []
        path, env, _ = store
        if os.environ.get(env):
            return [os.environ[env]]
        try:
            if path.exists():
                with open(path) as f:
                    keys = json.load(f)
                return keys if isinstance(keys, list) else [keys]
        except Exception:
            pass
        return []

    @staticmethod
    def _mask_key(k: str) -> str:
        return f"{k[:10]}…{k[-4:]}" if len(k) > 16 else '•••'

    def key_info(self, provider: str = 'openrouter') -> dict:
        """Masked view of the active API key + encrypted-vault state for a provider."""
        if provider in self.LOCAL_PROVIDERS:
            # nothing to hold a key for: 'configured' means "a run can start",
            # and on these it always can (a cloud key lives in liquidai's own
            # vault, which is that module's business, not ours)
            return {'provider': provider, 'configured': True, 'key': None,
                    'supported': False, 'keyless': True, 'encrypted': False,
                    'unlocked': False, 'hint': None, 'source': 'liquidai',
                    'remembered': False, 'remember_expires': None}
        keys = self._provider_keys(provider)
        vault = self._vault_read(provider)
        unlocked = provider in self._session_keys
        source = ('session' if unlocked else
                  'env' if provider in self.KEY_STORES and os.environ.get(self.KEY_STORES[provider][1]) else
                  'store' if keys else None)
        return {
            'provider': provider,
            'configured': bool(keys),
            'key': self._mask_key(keys[0]) if keys else None,
            'supported': provider in self.KEY_STORES,
            'encrypted': vault is not None,
            'unlocked': unlocked,
            'hint': vault.get('hint') if vault else None,
            'source': source,
            'remembered': self._remember_read(provider) is not None,
            'remember_expires': (self._remember_read(provider) or {}).get('expires'),
        }

    def _validate_key(self, api_key: str, provider: str):
        store = self.KEY_STORES.get(provider)
        if not store:
            raise ValueError(f"key management not supported for provider: {provider}")
        prefix = store[2]
        if len(api_key) < 8:
            raise ValueError("invalid API key (too short)")
        if prefix and not api_key.startswith(prefix):
            raise ValueError(f"invalid {provider} API key (must start with '{prefix}')")

    def set_api_key(self, api_key: str, provider: str = 'openrouter',
                    passphrase: str = None, remember: bool = True) -> dict:
        """Set your own API key for a provider.

        Without a passphrase: replaces the provider's plaintext key store
        (legacy behavior, shared with the model module).

        With a passphrase: the key is written ONLY as an encrypted vault file
        (AES-256-GCM, key derived from the passphrase) that the server cannot
        read without the passphrase — and immediately unlocked in memory for
        this session. The shared plaintext store is left untouched.
        `remember` keeps that unlock alive across restarts (see _remember).
        """
        api_key = (api_key or '').strip()
        self._validate_key(api_key, provider)
        if passphrase:
            return self.vault_save(provider, api_key, passphrase, remember=remember)
        path = self.KEY_STORES[provider][0]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump([api_key], f)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        self._refresh_model(provider)
        return {'provider': provider, 'key': self._mask_key(api_key),
                'configured': True, 'encrypted': False}

    def _refresh_model(self, provider: str):
        """Drop the cached client for a provider whose key just changed, so the
        next run builds one with it."""
        path = self.PROVIDERS.get(provider, provider)
        self._clients.pop(path, None)
        if path == self._provider:
            self.model = self._client()

    # ── encrypted vault ──────────────────────────────────────────────
    #
    # One file per provider under ~/.mod/agent/vault/. The API key is sealed
    # with AES-256-GCM under a PBKDF2-HMAC-SHA256 key derived from a user
    # passphrase the server never stores. Decrypted keys live only in
    # self._session_keys (RAM) until vault_lock() or restart.

    VAULT_KDF_ITERATIONS = 600_000
    # how long a remembered unlock survives on this server (0 = forever)
    REMEMBER_DAYS = 30

    def _vault_path(self, provider: str) -> Path:
        return self._vault_dir / f'{provider}.key.enc'

    def _vault_read(self, provider: str) -> Optional[dict]:
        try:
            p = self._vault_path(provider)
            if p.exists():
                with open(p) as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    @staticmethod
    def _derive_vault_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
        import hashlib
        return hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, iterations, dklen=32)

    # ── stay unlocked (device seal) ──────────────────────────────────
    #
    # Retyping the passphrase after every API restart is the thing that makes
    # the vault miserable to live with, so an unlock can be REMEMBERED on this
    # server: the decrypted key is re-sealed under a random device key kept
    # 0600 at ~/.mod/agent/vault/.device.key and resumed at startup. The
    # passphrase still guards the portable vault file — the device seal only
    # holds while that key file exists and its TTL is unexpired, and locking
    # (or deleting the vault) wipes it.

    def _device_key(self) -> bytes:
        """Random per-server key sealing remembered unlocks. Created on demand."""
        p = self._vault_dir / '.device.key'
        try:
            raw = p.read_bytes()
            if len(raw) == 32:
                return raw
        except Exception:
            pass
        raw = os.urandom(32)
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        with open(p, 'wb') as f:
            f.write(raw)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return raw

    def _remember_path(self, provider: str) -> Path:
        return self._vault_dir / f'{provider}.session.enc'

    def _remember_read(self, provider: str) -> Optional[dict]:
        """The remembered-unlock blob, or None if absent/expired (expired = deleted)."""
        import time
        try:
            p = self._remember_path(provider)
            if not p.exists():
                return None
            with open(p) as f:
                blob = json.load(f)
            if blob.get('expires') and blob['expires'] < time.time():
                p.unlink()
                return None
            return blob
        except Exception:
            return None

    def _remember(self, provider: str, api_key: str) -> Optional[float]:
        """Seal an unlocked key under the device key. Returns the expiry, if any."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import base64, time
        nonce = os.urandom(12)
        ct = AESGCM(self._device_key()).encrypt(
            nonce, api_key.encode(), b'agent-device-v1:' + provider.encode())
        expires = time.time() + self.REMEMBER_DAYS * 86400 if self.REMEMBER_DAYS else 0
        p = self._remember_path(provider)
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        with open(p, 'w') as f:
            json.dump({'v': 1, 'provider': provider,
                       'nonce': base64.b64encode(nonce).decode(),
                       'ct': base64.b64encode(ct).decode(),
                       'expires': expires}, f)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return expires or None

    def _forget(self, provider: str) -> bool:
        """Drop the remembered unlock — the passphrase is needed again."""
        p = self._remember_path(provider)
        if p.exists():
            p.unlink()
            return True
        return False

    def _vault_resume(self):
        """Restore remembered unlocks at startup. A blob we can't open is stale
        (device key rotated) — delete it rather than leaving a dead lock icon."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import base64
        for provider in self.KEY_STORES:
            blob = self._remember_read(provider)
            if not blob:
                continue
            try:
                self._session_keys[provider] = AESGCM(self._device_key()).decrypt(
                    base64.b64decode(blob['nonce']), base64.b64decode(blob['ct']),
                    b'agent-device-v1:' + provider.encode()).decode()
            except Exception:
                self._forget(provider)
        if self._session_keys:
            self._clients.clear()
            self.model = self._client()

    def vault_save(self, provider: str, api_key: str, passphrase: str,
                   remember: bool = True) -> dict:
        """Encrypt an API key with the user's passphrase and persist the sealed blob."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import base64, time
        if len(passphrase) < 4:
            raise ValueError("passphrase too short (min 4 characters)")
        salt, nonce = os.urandom(16), os.urandom(12)
        kek = self._derive_vault_key(passphrase, salt, self.VAULT_KDF_ITERATIONS)
        ct = AESGCM(kek).encrypt(nonce, api_key.encode(),
                                 b'agent-vault-v1:' + provider.encode())
        blob = {
            'v': 1,
            'provider': provider,
            'kdf': 'pbkdf2-sha256',
            'iterations': self.VAULT_KDF_ITERATIONS,
            'cipher': 'aes-256-gcm',
            'salt': base64.b64encode(salt).decode(),
            'nonce': base64.b64encode(nonce).decode(),
            'ct': base64.b64encode(ct).decode(),
            'hint': self._mask_key(api_key),
            'created': time.time(),
        }
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        p = self._vault_path(provider)
        with open(p, 'w') as f:
            json.dump(blob, f, indent=2)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        # the user just typed the key — make it live for this session right away
        self._session_keys[provider] = api_key
        expires = None
        if remember:
            expires = self._remember(provider, api_key)
        else:
            self._forget(provider)
        self._refresh_model(provider)
        return {'provider': provider, 'key': self._mask_key(api_key),
                'configured': True, 'encrypted': True, 'unlocked': True,
                'remembered': bool(remember), 'remember_expires': expires}

    def vault_unlock(self, provider: str = 'openrouter', passphrase: str = '',
                     remember: bool = True) -> dict:
        """Decrypt the vaulted key into memory. Remembered by default, so the
        passphrase is asked for once rather than after every restart."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        import base64
        blob = self._vault_read(provider)
        if not blob:
            raise ValueError(f"no encrypted key stored for {provider}")
        kek = self._derive_vault_key(passphrase, base64.b64decode(blob['salt']),
                                     int(blob.get('iterations', self.VAULT_KDF_ITERATIONS)))
        try:
            api_key = AESGCM(kek).decrypt(
                base64.b64decode(blob['nonce']), base64.b64decode(blob['ct']),
                b'agent-vault-v1:' + provider.encode()).decode()
        except InvalidTag:
            raise PermissionError("wrong passphrase")
        self._session_keys[provider] = api_key
        expires = None
        if remember:
            expires = self._remember(provider, api_key)
        else:
            self._forget(provider)
        self._refresh_model(provider)
        return {'provider': provider, 'unlocked': True, 'encrypted': True,
                'key': self._mask_key(api_key),
                'remembered': bool(remember), 'remember_expires': expires}

    def vault_lock(self, provider: str = 'openrouter') -> dict:
        """Drop the decrypted key from memory and forget any remembered unlock —
        locking has to mean the passphrase is needed again. Sealed file stays."""
        was = self._session_keys.pop(provider, None) is not None
        forgot = self._forget(provider)
        self._refresh_model(provider)
        return {'provider': provider, 'unlocked': False,
                'encrypted': self._vault_read(provider) is not None,
                'was_unlocked': was or forgot, 'remembered': False}

    def vault_rm(self, provider: str = 'openrouter') -> dict:
        """Delete the encrypted key file and forget the session key."""
        self._session_keys.pop(provider, None)
        self._forget(provider)
        p = self._vault_path(provider)
        existed = p.exists()
        if existed:
            p.unlink()
        self._refresh_model(provider)
        return {'provider': provider, 'removed': existed}

    # ── balance ──────────────────────────────────────────────────────

    def balance(self, provider: str = 'openrouter') -> dict:
        """Remaining credit on the active API key (openrouter /credits, venice rate_limits)."""
        info = self.key_info(provider)
        if info.get('keyless'):
            return {**info, 'balance': None,
                    'note': 'no key, no bill — this provider runs on local or '
                            'browser compute'}
        keys = self._provider_keys(provider)
        if not keys:
            return {**info, 'error': 'no API key configured'}
        try:
            import requests as req
            headers = {'Authorization': f'Bearer {keys[0]}'}
            if provider == 'openrouter':
                r = req.get('https://openrouter.ai/api/v1/credits', headers=headers, timeout=10)
                if r.status_code != 200:
                    return {**info, 'error': r.json().get('error', {}).get('message', f'HTTP {r.status_code}')}
                data = r.json().get('data', {})
                credits = float(data.get('total_credits', 0))
                usage = float(data.get('total_usage', 0))
                return {**info, 'balance': round(credits - usage, 4),
                        'total_credits': credits, 'total_usage': round(usage, 4)}
            if provider == 'venice':
                r = req.get('https://api.venice.ai/api/v1/api_keys/rate_limits',
                            headers=headers, timeout=10)
                if r.status_code != 200:
                    return {**info, 'error': f'HTTP {r.status_code}'}
                data = r.json().get('data', {})
                balances = data.get('balances', {}) or {}
                usd = balances.get('USD')
                if usd is not None:
                    return {**info, 'balance': round(float(usd), 4), 'balances': balances}
                # inference-credit accounts (VCU/DIEM) have no USD figure
                return {**info, 'balances': balances}
            return {**info, 'error': f'balance not supported for {provider}'}
        except Exception as e:
            return {**info, 'error': str(e)}

    # ── mod protocol entry point ──────────────────────────────────────

    def forward(self, action=None, key=None, **kwargs):
        """CLI entry point: agent <action> [args]

        Actions:
          Public (anyone):
            status, health, schema, agents, agent, chains, harnesses,
            toolboxes, toolbox, snapped, tools, tool, mods,
            recall, episodes, facts, exchanges, memory_state,
            arena, arena_tasks, arena_matches, arena_card, arena_status,
            arena_models, arena_model, arena_task_board,
            openarena, openarena_task, openarena_sources,
            credits, credit_price (network=),
            credit_deposit (tx_hash=, network=base|ethereum, provider=openrouter|venice)
                        - credit a USDT/USDC/ETH transfer to the deposit address

          Signed-in (self-scoped to the caller's verified address):
            vaults      - List your key-value vaults
            vaults_get  - Read a vault (name=, reveal= to unseal private values)
            vaults_set  - Upsert an entry (name=, entry=, value=, private=)
            vaults_add  - Create an empty vault (name=)
            vaults_rm   - Delete a vault (name=)
            vaults_key_rm - Remove one entry (name=, entry=)
            vaults_public - Public entries of any vault (address=, name=)

          Admin (owner + granted users):
            run         - Run the agent loop (toolbox= snaps a bundle for the run)
            snap        - Snap a toolbox onto the agent (name=)
            unsnap      - Detach a toolbox (name=) or all (no args)
            select      - Pin the loadout to an exact list (tools=[...], none = boxes)
            toolbox_add - Create a custom toolbox (name=, tools=[...])
            toolbox_rm  - Remove a custom toolbox (name=)
            tool_add    - Create a custom tool (name=, command=, params=)
            tool_rm     - Remove a custom tool (name=)
            tool_run    - Execute a custom tool (name=, params={...})
            remember    - Store a durable memory fact (name=, content=)
            forget      - Remove a fact (id=)
            memory_serve- Start the memory service as its own process (:50119)
            memory_kill - Stop the memory service
            arena_task_draft - Draft a task with the task-builder agent
                               (description=, schema=agent|openarena)
            arena_task_add   - Store a hand-written arena task (spec=, slug=)
            arena_task_rm    - Remove one of your tasks (slug=)
            openarena        - The openarena bridge: is it up, what it holds
            openarena_task   - One openarena task in full (slug=)
            openarena_sources- Benchmarks it can pull off the web
            openarena_task_add - Upload a task in the openarena schema (spec=)
            openarena_task_rm  - Delete one you wrote there (slug=)
            openarena_preview  - Convert a benchmark, keep nothing (source=, limit=)
            openarena_import   - ...and keep it (source=, limit=, offset=)
            arena_run   - Play a match (agent=, task=) or a whole round
            arena_gauntlet - Rank models against each other: one agent, one
                              task set, N models (models=, agent=, tasks=)
            arena_qualify - Score a newcomer against the incumbents (agent=)
            arena_config  - Set the board's knobs (enabled=, free=, period_hours=…)
            arena_scheduler - Start/stop the background board process (on=)
            openarena_enter - Enter one of our agents on openarena's own board
                              (agent=, name=, model=, steps=, free=)
            plan        - Parse and execute a single LLM output
            tool_run    - Run a single tool (built-in, custom, or mod.<module>)
            serve       - Start API + app
            kill        - Stop services
            test        - Run tests

          Owner only:
            grant       - Grant access to an address (address=, actions=)
            revoke      - Revoke access from an address (address=)
            acl         - View current access control list
            treasury    - Deposits in, provider credits out, margin kept (live=)
            credit_topup- Record credits bought at a provider (provider=, amount=, ref=)
            credit_verify - Book a top-up read off the provider key (provider=)
            credit_withdraw - Take earned margin out of the float (amount=)
            credit_config   - Set fee_rate / price_per_step / cost_multiplier
        """
        kwargs['key'] = key
        actions = {
            # public
            'status': lambda: self.status(),
            'health': lambda: self.health(),
            'schema': lambda: self.tool_schema(kwargs.get('names')),
            'agents': lambda: self.agents.forward(kwargs.get('name'), **kwargs),
            'agent': lambda: self.agents.forward(kwargs.get('name') or self.default_agent(key)),
            'chains': lambda: self.agents.chains(),
            # external agent CLIs an agent can hand its run to, + what's installed here
            'harnesses': lambda: self.harness.forward(kwargs.get('name')),
            'agent_cids': lambda: self.agents.forward(action='cids'),
            'agent_load': lambda: self.agents.load(kwargs.get('cid', ''), shares=kwargs.get('shares')),
            'library': lambda: self.library.items(q=kwargs.get('q'), kind=kwargs.get('kind'), tag=kwargs.get('tag')),
            # bring your own: a file (json / markdown+front matter) or a CID
            'upload': lambda: self.library.upload(kwargs.get('text', ''), kwargs.get('filename'),
                                                  kwargs.get('kind'), key=key),
            'library_import': lambda: self.library.import_cid(kwargs.get('cid', ''),
                                                              kwargs.get('kind'), key=key),
            'formats': lambda: self.library.formats(),
            'prompts': lambda: {'prompts': self.library.prompts()},
            'prompt_add': lambda: self.library.prompt_add(kwargs.get('name', ''), kwargs.get('text', ''), kwargs.get('description', ''), kwargs.get('tags'), kwargs.get('id'), key=key),
            'prompt_rm': lambda: self.library.prompt_rm(kwargs.get('id', ''), key=key),
            'memory': lambda: {'memory': self.library.notes()},
            'memory_add': lambda: self.library.note_add(kwargs.get('name', ''), kwargs.get('content', ''), kwargs.get('tags'), kwargs.get('id'), key=key),
            'memory_rm': lambda: self.library.note_rm(kwargs.get('id', ''), key=key),
            # tool aggregator: scan the internet, install what you find
            'discover': lambda: self.discover.search(kwargs.get('q', ''), kwargs.get('sources'),
                                                     int(kwargs.get('limit', 30)),
                                                     kwargs.get('kind'), bool(kwargs.get('fresh'))),
            'discover_sources': lambda: {'sources': self.discover.sources(),
                                         'token': bool(self.discover.token())},
            'discover_detail': lambda: self.discover.detail(kwargs.get('id', '')),
            'discover_doc': lambda: self.discover.tool_doc(kwargs.get('id', ''), kwargs.get('path')),
            'tool_install': lambda: self.tool_install(kwargs.get('id', ''), kwargs.get('path'), key=key),
            'installed_tools': lambda: {'tools': self.library.installed_tools()},
            'tool_import': lambda: self.library.tool_import(kwargs.get('cid', ''), key=key),
            'tool_uninstall': lambda: self.library.tool_rm(kwargs.get('id', ''), key=key),
            # toolboxes (snap-on tool bundles)
            'toolboxes': lambda: {'toolboxes': self.toolboxes.items(), 'snapped': self._snapped},
            'toolbox': lambda: self.toolboxes.get(kwargs.get('name', '')).to_dict(),
            'snapped': lambda: self.snapped(),
            # the whole tool surface: built-ins, custom shell tools, the fleet
            'tools': lambda: self.tools.forward(mods=kwargs.get('mods'), q=kwargs.get('q', '')),
            'tool': lambda: self.tools.get(kwargs.get('name', '')),
            'mods': lambda: self.tools.mods.forward(q=kwargs.get('q', ''),
                                                    limit=kwargs.get('limit')),
            # the agent box and every sub-component in it
            'parts': lambda: self.parts(),
            # the same API, spoken as Model Context Protocol (src/mcp.py)
            'mcp': lambda: self.mcp(tools=bool(kwargs.get('tools'))),
            # the memory modules an agent can be built with
            'memories': lambda: self.memories.forward(kwargs.get('name')),
            # memory subsystem (working/episodic/semantic layers, own process)
            'memory_state': lambda: self.memory.forward('status') if hasattr(self.memory, 'status') else self.memory.summary(),
            'recall': lambda: self.memory.recall(kwargs.get('query', kwargs.get('q', '')), kwargs.get('k', 5)),
            # retrieval across every layer at once, scoped to the caller —
            # the same call the recall tool makes from inside a run
            'retrieve': lambda: {
                'query': kwargs.get('query', kwargs.get('q', '')),
                'module': self.memories.name_of(self.memory),
                'hits': self.memory.retrieve(
                    kwargs.get('query', kwargs.get('q', '')),
                    k=int(kwargs.get('k', 5)),
                    layers=kwargs.get('layers'),
                    session=kwargs.get('session'),
                    min_score=kwargs.get('min_score'),
                    who=self.identity.addr(key))},
            'episodes': lambda: self.memory.episodes(kwargs.get('n', 50), kwargs.get('session')),
            # the caller's own conversation history, as the memory module has it
            'exchanges': lambda: {'exchanges': self.memory.history(
                int(kwargs.get('n', 20)), kwargs.get('session'),
                self.identity.addr(key))},
            'facts': lambda: self.memory.facts(),
            # arena: every agent on the same tasks, one ranked board
            'arena': lambda: self.arena.forward(),
            'arena_tasks': lambda: self.arena.forward('tasks'),
            'arena_matches': lambda: self.arena.forward('matches', limit=kwargs.get('limit', 50),
                                                        agent=kwargs.get('agent'),
                                                        task=kwargs.get('task')),
            'arena_card': lambda: self.arena.forward('card', agent=kwargs.get('agent', '')),
            'arena_status': lambda: self.arena.forward('status'),
            # the same board keyed on the model — and the catalog a gauntlet
            # can pick from, so the console never has to guess an id
            'arena_models': lambda: {**self.arena.forward('models'),
                                     'catalog': self.arena_model_options()},
            'arena_model': lambda: self.arena.forward('model',
                                                      model=kwargs.get('model', '')),
            'arena_task_board': lambda: self.arena.forward('task_board'),
            # hand-written tasks: draft one with the task-builder agent, store
            # it under your address, remove your own
            'arena_task_draft': lambda: self.arena_task_draft(
                kwargs.get('description', ''), model=kwargs.get('model'),
                provider=kwargs.get('provider'), free=bool(kwargs.get('free')),
                steps=kwargs.get('steps', 4), key=key),
            'arena_task_add': lambda: self.arena_task_add(
                kwargs.get('spec') or {}, slug=kwargs.get('slug'), key=key),
            'arena_task_rm': lambda: self.arena_task_rm(kwargs.get('slug', ''), key=key),
            # the openarena schema: a statement plus graded cases, stored and
            # judged next door (see arena/openarena.py)
            'openarena': lambda: self.arena.forward('openarena'),
            'openarena_task': lambda: self.arena_oa_task(kwargs.get('slug', '')),
            'openarena_sources': lambda: self.arena.forward('oa_sources'),
            'openarena_task_add': lambda: self.arena_oa_task_add(
                kwargs.get('spec') or {}, key=key),
            'openarena_task_rm': lambda: self.arena_oa_task_rm(
                kwargs.get('slug', ''), key=key),
            'openarena_preview': lambda: self.arena_oa_import(
                kwargs.get('source', 'humaneval'), preview=True, key=key,
                **{k: v for k, v in kwargs.items()
                   if k not in ('source', 'preview', 'key')}),
            'openarena_import': lambda: self.arena_oa_import(
                kwargs.get('source', 'humaneval'), preview=bool(kwargs.get('preview')),
                key=key, **{k: v for k, v in kwargs.items()
                            if k not in ('source', 'preview', 'key')}),
            'key_info': lambda: self.key_info(kwargs.get('provider', 'openrouter')),
            'balance': lambda: self.balance(kwargs.get('provider', 'openrouter')),
            # credits (prepaid public-key usage)
            'credits': lambda: self.credits_info(key),
            'credit_deposit': lambda: self.credit_deposit(kwargs.get('tx_hash', ''),
                                                          kwargs.get('network', 'base'),
                                                          kwargs.get('provider')),
            'credit_price': lambda: self.credit_price(kwargs.get('network', 'base')),
            'credit_grant': lambda: self.credit_grant(kwargs.get('address', ''),
                                                      kwargs.get('amount', 0),
                                                      kwargs.get('note', ''), key),
            # treasury: guest deposits fund the provider keys, we keep the margin
            'treasury': lambda: self.credits_treasury(key, live=kwargs.get('live', True)),
            'credit_topup': lambda: self.credit_topup(kwargs.get('provider', ''),
                                                      kwargs.get('amount', 0),
                                                      kwargs.get('ref', ''),
                                                      kwargs.get('note', ''), key),
            'credit_verify': lambda: self.credit_topup_verify(
                kwargs.get('provider', 'openrouter'), key),
            'credit_withdraw': lambda: self.credit_withdraw(kwargs.get('amount', 0),
                                                            kwargs.get('note', ''), key),
            'credit_config': lambda: self.credit_config(
                key,
                **{k: kwargs[k] for k in ('fee_rate', 'price_per_step',
                                          'cost_multiplier', 'deposit_address')
                   if kwargs.get(k) is not None}),
            # vaults (per-address KV stores via the store module; self-scoped
            # to the caller's verified address — sign-in required)
            'vaults': lambda: {'vaults': self.vaults.ls(self._resolve_address(key))},
            'vaults_get': lambda: self.vaults.get(self._resolve_address(key),
                                                  kwargs.get('name', ''),
                                                  reveal=bool(kwargs.get('reveal', False))),
            'vaults_set': lambda: self.vaults.set(self._resolve_address(key),
                                                  kwargs.get('name', ''),
                                                  kwargs.get('entry', ''),
                                                  kwargs.get('value', ''),
                                                  private=bool(kwargs.get('private', True))),
            'vaults_add': lambda: self.vaults.create(self._resolve_address(key),
                                                     kwargs.get('name', '')),
            'vaults_rm': lambda: self.vaults.rm(self._resolve_address(key),
                                                kwargs.get('name', '')),
            'vaults_key_rm': lambda: self.vaults.entry_rm(self._resolve_address(key),
                                                          kwargs.get('name', ''),
                                                          kwargs.get('entry', '')),
            'vaults_public': lambda: self.vaults.public(kwargs.get('address', ''),
                                                        kwargs.get('name', '')),
            # module visibility — the audit side is open to anyone
            'modules': lambda: self.privacy.ls(kwargs.get('q', '')),
            'module_tree': lambda: self.privacy.tree(kwargs.get('name', '')),
            'module_file': lambda: self.privacy.read(kwargs.get('name', ''),
                                                     kwargs.get('path', '')),
            # …and the switches are the owner's alone
            'module_visibility': lambda: self.module_visibility(
                kwargs.get('name', ''), kwargs.get('visibility', ''),
                kwargs.get('passphrase'), key=key),
            'modules_visibility': lambda: self.modules_visibility(
                kwargs.get('visibility', ''), kwargs.get('passphrase'), key=key),
            'module_seal': lambda: self.module_seal(kwargs.get('name', ''),
                                                    kwargs.get('passphrase'), key=key),
            'module_unseal': lambda: self.module_unseal(kwargs.get('name', ''), key=key),
            'module_restore': lambda: self.module_restore(
                kwargs.get('name', ''), kwargs.get('passphrase'),
                bool(kwargs.get('force')), key=key),
            'privacy_key': lambda: self.privacy_key(
                kwargs.get('op', 'state'), kwargs.get('passphrase'),
                kwargs.get('current'), kwargs.get('key_b64'), key=key),
            # admin (owner + granted)
            'set_key': lambda: self.set_api_key(kwargs.get('api_key', ''),
                                                kwargs.get('provider', 'openrouter'),
                                                kwargs.get('passphrase'),
                                                remember=kwargs.get('remember', True)),
            'unlock': lambda: self.vault_unlock(kwargs.get('provider', 'openrouter'),
                                                kwargs.get('passphrase', ''),
                                                remember=kwargs.get('remember', True)),
            'lock': lambda: self.vault_lock(kwargs.get('provider', 'openrouter')),
            'vault_rm': lambda: self.vault_rm(kwargs.get('provider', 'openrouter')),
            'run': lambda: self._run(**kwargs),
            'plan': lambda: super(Mod, self).plan(kwargs.get('output', ''), safety=kwargs.get('safety', False)),
            'serve': lambda: self.serve(kwargs.get('api_port'), kwargs.get('app_port'), kwargs.get('dev', True)),
            'kill': lambda: self.kill(kwargs.get('service')),
            'test': lambda: self.test(),
            'agent_save': lambda: self.agents.save(**{k: v for k, v in kwargs.items() if k not in ('action',)}),
            'agent_install': lambda: self.agents.load_and_create(cid=kwargs.get('cid', ''), shares=kwargs.get('shares'), key=key),
            # toolbox management + snapping (admin)
            'toolbox_add': lambda: self.toolboxes.add(kwargs.get('name', ''), kwargs.get('tools', []), kwargs.get('description', '')),
            'toolbox_rm': lambda: self.toolboxes.rm(kwargs.get('name', '')),
            # custom tools run shell and fleet tools run other modules, so
            # calling one is admin — unlike library tool documents, which are
            # text and only need a sign-in
            'tool_add': lambda: self.tools.add(kwargs.get('name', ''), kwargs.get('command', ''),
                                               kwargs.get('description', ''), kwargs.get('params'),
                                               kwargs.get('cwd'), kwargs.get('timeout', 60),
                                               owner=self._resolve_address(key, verified=True) or None),
            'tool_rm': lambda: self.tools.rm(kwargs.get('name', '')),
            'tool_run': lambda: self.run_tool(kwargs.get('name', ''), **(kwargs.get('params') or {})),
            'snap': lambda: self.snap(kwargs.get('name', '')),
            'unsnap': lambda: self.unsnap(kwargs.get('name')),
            'select': lambda: self.select(kwargs.get('tools', kwargs.get('names'))),
            # durable memory writes + memory service process (admin)
            'remember': lambda: self.memory.remember(kwargs.get('name', ''), kwargs.get('content', ''), kwargs.get('tags')),
            'forget': lambda: self.memory.forget(kwargs.get('id', '')),
            'memory_serve': lambda: self.memory.serve(kwargs.get('port'), kwargs.get('dev', False)),
            'memory_kill': lambda: self.memory.kill(kwargs.get('port')),
            # arena rounds spend steps on the provider key, so playing is admin
            'arena_run': lambda: self.arena.forward('run', agent=kwargs.get('agent'),
                                                    task=kwargs.get('task'),
                                                    agents=kwargs.get('agents'),
                                                    tasks=kwargs.get('tasks'),
                                                    model=kwargs.get('model'),
                                                    steps=kwargs.get('steps'),
                                                    free=kwargs.get('free'),
                                                    reason=kwargs.get('reason', 'manual')),
            'arena_qualify': lambda: self.arena.forward('qualify', agent=kwargs.get('agent', '')),
            # a gauntlet names its models, so unlike a round it can spend on
            # paid ones — the host's call, and the host's key
            'arena_gauntlet': lambda: self.arena.forward(
                'gauntlet', models=kwargs.get('models') or [],
                agent=kwargs.get('agent'), tasks=kwargs.get('tasks'),
                steps=kwargs.get('steps'), free=bool(kwargs.get('free', False)),
                reason=kwargs.get('reason', 'gauntlet')),
            # openarena calls back into /run to make our entrant play, which
            # spends the host's key — so entering one is the host's call
            'openarena_enter': lambda: self.arena_oa_enter(
                kwargs.get('agent', ''), name=kwargs.get('name'),
                model=kwargs.get('model'), steps=kwargs.get('steps'),
                free=kwargs.get('free'), key=key),
            'arena_config': lambda: self.arena.forward('config', **kwargs),
            'arena_scheduler': lambda: self.arena_scheduler(kwargs.get('on', True)),
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
        goal and tool overrides before running.
        """
        key = kwargs.get('key')
        # the arena's pass is standing, not identity — swap it for None before
        # anything downstream tries to resolve an address out of it. Identity
        # is what makes it trustworthy: it can only arrive from arena_run,
        # never from JSON off the wire.
        # getattr + None guard: test mods are built via __new__ and carry no
        # pass, and a missing pass must never make a keyless run a match
        arena_match = key is not None and key is getattr(self, '_arena_pass', None)
        if arena_match:
            key = kwargs['key'] = None
        # an explicit sandbox wins over key resolution: an arena match has no
        # caller behind it, and it belongs in its own scratch dir either way
        allowed_paths = kwargs.get('allowed_paths') or self.allowed_paths_for(key)

        # resolve agent type from registry — unnamed lands on the default agent
        agent_type = (kwargs.get('agent_type') or kwargs.get('agent')
                      or self.default_agent(key))
        agent_goal = None
        agent_tools = kwargs.get('tools')
        agent_model = kwargs.get('model')
        agent_provider = kwargs.get('provider')
        agent_harness = None
        # the memory module this run thinks with: what the caller asked for,
        # else what the agent was built with, else the default one
        agent_memory = kwargs.get('memory')

        if agent_type and agent_type in self.agents.ls():
            agent_config = self.agents.get(agent_type)
            if agent_config.get('goal'):
                agent_goal = agent_config['goal']
            if agent_config.get('memory') and not agent_memory:
                agent_memory = agent_config['memory']
            # `skills` is the pre-rename key — agent configs saved back then
            # still carry it, so it's read as a fallback
            saved_tools = agent_config.get('tools') or agent_config.get('skills')
            if saved_tools and not kwargs.get('tools'):
                agent_tools = saved_tools
            # the agent's saved model is a default, not an override: a caller
            # that named one — the console's picker, an arena gauntlet ranking
            # six models on the same agent — asked for that model and would
            # otherwise silently get whatever the agent was built with
            if agent_config.get('model') and not agent_model:
                agent_model = agent_config['model']
            agent_harness = agent_config.get('harness')

        # explicit system prompt (library prompt or free text) beats the agent goal
        if kwargs.get('prompt'):
            agent_goal = kwargs['prompt']

        # a harness agent isn't a persona over our loop — the whole run goes to
        # an external CLI, which brings its own tools, model and context
        if agent_harness:
            kw = dict(kwargs)
            kw.pop('model', None)   # a provider model id means nothing to a CLI
            kw.pop('arena_match', None)   # standing is computed here, never a caller knob
            return self._run_harness(agent_harness, goal=agent_goal,
                                     model=agent_config.get('model'),
                                     arena_match=arena_match, **kw)

        # selected library memory notes ride along as run context
        extra = {}
        memory_ids = kwargs.get('memory_ids') or []
        if memory_ids:
            picked = [n for n in self.library.notes() if n.get('id') in memory_ids]
            if picked:
                extra['notes'] = '\n\n'.join(
                    f"[{n['name']}]\n{n.get('content', '')}" for n in picked)

        # installed tool documents ride along the same way — a SKILL.md from
        # the internet is instructions, so handing it to the model IS the way
        # it's used
        tool_ids = kwargs.get('tool_ids') or kwargs.get('skill_ids') or []
        if tool_ids:
            docs = self.library.tool_docs(tool_ids)
            if docs:
                extra['tool_docs'] = '\n\n'.join(
                    f"[tool: {d['name']}]\n{d.get('body', '')}" for d in docs)

        # The agent's prompt and memory module belong to this run, not to the
        # module: behind the API there is one Mod for the whole host, and two
        # runs overlapping on it used to swap these on the object and restore
        # them out of order — leaving a persona's prompt bound to every run
        # after it (the console spent a day answering as an agent called
        # `broski`, whose whole system prompt is the word "broski"). Passed in
        # per run instead; `run` binds them for its own thread.
        run_memory = None
        if agent_memory and agent_memory != self.memories.name_of(self.memory):
            try:
                run_memory = self.memories.make(agent_memory)
            except Exception as e:
                print(f"[agent] memory module {agent_memory!r} unavailable: {e}")
        try:
            return self.run(
                query=kwargs.get('query', 'help me with this'),
                goal=agent_goal,
                memory=run_memory,
                model=agent_model,
                provider=agent_provider,
                path=kwargs.get('path'),
                temperature=kwargs.get('temperature', 0.0),
                max_tokens=kwargs.get('max_tokens', 8192),
                steps=kwargs.get('steps', 25),
                tools=agent_tools,
                toolbox=kwargs.get('toolbox') or kwargs.get('toolboxes'),
                mod=kwargs.get('mod'),
                safety=kwargs.get('safety', False),
                save=kwargs.get('save', False),
                key=kwargs.get('key'),
                allowed_paths=allowed_paths,
                free=kwargs.get('free', False),
                on_step=kwargs.get('on_step'),
                on_usage=kwargs.get('on_usage'),
                on_live=kwargs.get('on_live'),
                images=kwargs.get('images'),
                budget=kwargs.get('budget'),
                session=kwargs.get('session'),
                agent_type=agent_type,
                **extra,
            )
        finally:
            self._unbind_memory()
            self._clear_run_state()

    # ── harness runs (external agent CLIs) ───────────────────────────

    def _harness_trusted(self, harness: str, key=None) -> bool:
        """Whether this caller may hand a run to that harness.

        The host (owner + co-owners) always may. Beyond that the question is
        delegated: the console module behind the harness (HARNESS_AUTH) is
        asked its own is_owner for the address recovered from the caller's
        token — the same gate its own interface enforces — so standing on the
        claude or codex console is standing on its harness here. Verdicts are
        cached briefly because the picker asks on every render and a console's
        owner check may go to disk or chain.
        """
        if self.is_owner(key):
            return True
        peer = self.HARNESS_AUTH.get(harness)
        if not (peer and key and m):
            return False
        try:
            addr = (self._resolve_address(key, verified=True) or '').lower()
        except Exception:   # no verifier wired up — nobody to vouch through
            return False
        if not addr:
            return False
        cache = getattr(self, '_harness_trust', None)
        if cache is None:
            cache = self._harness_trust = {}
        hit = cache.get((peer, addr))
        if hit and time.time() - hit[1] < 60:
            return hit[0]
        try:
            verdict = bool(m.mod(peer)().is_owner(addr))
        except Exception:   # a console that won't load vouches for nobody
            verdict = False
        cache[(peer, addr)] = (verdict, time.time())
        return verdict

    def _runnable_agent(self, name: str, key=None) -> bool:
        """Whether this caller could actually run that agent right now.

        The only thing that can stop them is a harness: the run leaves this
        loop for a CLI on the host's own shell, so it wants a trusted caller
        (the host, or the harnessed console's own owner — _harness_trusted)
        and an installed binary. Everything else is runnable by anyone.
        """
        try:
            harness = self.agents.get(name).get('harness')
        except Exception:      # missing/unloadable — never offer it as a default
            return False
        if not harness:
            return True
        return bool(self._harness_trusted(harness, key)
                    and self.harness.get(harness).available())

    def default_agent(self, key=None) -> str:
        """The agent to run as when the caller picked none.

        A caller's own pick wins: whoever signs in can name the agent their
        runs land on (set_default_agent), and it is remembered per address,
        so the default follows the wallet rather than the browser.

        With no pick on record it is Claude Code — this host's own CLI, with
        its own tools and model. That is a harness run, which is owner-only
        and needs the binary installed, so anyone else (or a host without it)
        gets the native agent.
        """
        pick = self.agent_pref(key)
        # a pick that can no longer be run (a harness gone, an agent deleted)
        # falls through rather than 403-ing every unnamed run
        if pick and self._runnable_agent(pick, key):
            return pick
        try:
            if not self._runnable_agent(self.DEFAULT_AGENT, key):
                return self.FALLBACK_AGENT
            return self.DEFAULT_AGENT
        except Exception:      # default agent missing/unloadable — never block a run
            return self.FALLBACK_AGENT

    # ── the caller's own default agent ───────────────────────────────
    # Which agent an unnamed run lands on is a preference, not a permission,
    # but it is still per-address state, so it lives off-tree in
    # ~/.mod/agent/prefs.json beside the ACL rather than in the module dir.

    def _load_prefs(self) -> dict:
        try:
            if self._prefs_path.exists():
                with open(self._prefs_path) as f:
                    return json.load(f)
        except Exception:      # a corrupt prefs file is not worth a 500
            pass
        return {}

    def _save_prefs(self, prefs: dict):
        with open(self._prefs_path, 'w') as f:
            json.dump(prefs, f, indent=2)

    def _pref_address(self, key=None) -> str:
        """The address a preference is filed under, or '' for anonymous.

        `key=None` is nobody — NOT this server's own key, which is what
        _resolve_address falls back to. A pref bucket shared by every
        anonymous caller is one anonymous caller overwriting the rest.
        """
        if not key:
            return ''
        return (self._resolve_address(key, verified=True) or '').lower()

    def agent_pref(self, key=None) -> Optional[str]:
        """The default agent this caller picked, or None. Anonymous = None.

        Reads never raise: this is consulted on the way into every unnamed
        run, and a module built without an identity (or without the prefs
        path) has no pick rather than a broken run.
        """
        try:
            addr = self._pref_address(key)
            if not addr:
                return None
            return (self._load_prefs().get('default_agent') or {}).get(addr)
        except Exception:
            return None

    def set_default_agent(self, name: Optional[str] = None, key=None) -> dict:
        """Pick the agent unnamed runs land on. Signed-in callers only.

        `name=None` clears the pick and hands the choice back to the module.
        A harness agent can only be picked by someone who could run it — a
        default that 403s every run is worse than no default at all.
        """
        addr = self._pref_address(key)
        if not addr:
            raise PermissionError(
                'sign in to set a default agent — it is remembered per address')
        prefs = self._load_prefs()
        picks = dict(prefs.get('default_agent') or {})
        if name:
            name = str(name).strip()
            try:
                cfg = self.agents.get(name)
            except Exception:
                raise ValueError(f'unknown agent: {name}')
            harness = cfg.get('harness')
            if harness and not self._runnable_agent(name, key):
                raise PermissionError(
                    f"'{name}' hands its run to the {harness} CLI on this "
                    f"host's own shell, so only the host can run it — pick a "
                    f"native agent as your default.")
            picks[addr] = name
        else:
            picks.pop(addr, None)
        prefs['default_agent'] = picks
        self._save_prefs(prefs)
        return {'default': self.default_agent(key), 'pick': picks.get(addr),
                'source': 'you' if picks.get(addr) else 'host', 'address': addr}

    def default_agent_info(self, key=None) -> dict:
        """The default plus where it came from — the caller, or this module."""
        pick = self.agent_pref(key)
        resolved = self.default_agent(key)
        return {'default': resolved,
                'pick': pick,
                # 'you' only when the pick is the one actually in force
                'source': 'you' if pick and pick == resolved else 'host'}

    def harness_for(self, agent_type: str = None) -> Optional[str]:
        """The harness an agent hands its run to, or None for a native run."""
        if not agent_type:
            return None
        try:
            return self.agents.get(agent_type).get('harness')
        except Exception:
            return None

    def _run_harness(self, name: str, goal: str = None, model: str = None,
                     arena_match: bool = False, **kwargs) -> List[Dict[str, Any]]:
        """Hand the run to an external agent CLI and stream back its steps.

        Owner only. The CLIs run with their approval prompts off — nobody is
        at the other end of a server-side run to answer them — so a harness run
        is effectively the host's own shell. Guests stay on this module's loop,
        which is sandboxed to their portal directory.

        The one exception is the board: an arena match (arena_match is set by
        _run alone, off the in-process pass) may play a harness agent once the
        host has opted in with the arena's `harnesses` knob — that knob IS the
        owner's standing consent, and without this door it could never work,
        because a match has no caller to be the owner.
        """
        if arena_match and not self.arena.config().get('harnesses'):
            raise PermissionError(
                f"the arena is not allowed to play harness agents on this "
                f"host — opt in with arena config harnesses=true before "
                f"putting a {name} agent on the board.")
        if not (arena_match or self._harness_trusted(name, kwargs.get('key'))):
            # name the agent that was picked, not the harness behind it — the
            # caller chose "Build Console", and being told about "buildmod"
            # sends them looking for something they never asked for
            agent = kwargs.get('agent_type') or kwargs.get('agent') or name
            raise PermissionError(
                f"'{agent}' hands the run to the {name} CLI on this host's own "
                f"shell, so it is held to this host's owner and the console it "
                f"belongs to. Pick a native agent to run on this module's own "
                f"loop, sandboxed to your directory.")
        path = kwargs.get('path') or (m.dp(kwargs['mod']) if m and kwargs.get('mod')
                                      else os.getcwd())
        # reuse the native step sink: live progress for the console, and the
        # run still lands in the memory subsystem as episodes
        self._on_step = kwargs.get('on_step')
        # runner-specific knobs ride along untouched — which project the chain
        # console's runner opens, say. The caller's key goes too, so a runner
        # that scopes work by identity (whose projects) sees who asked.
        extra = kwargs.get('harness_args')
        extra = dict(extra) if isinstance(extra, dict) else {}
        for reserved in ('query', 'path', 'goal', 'model', 'timeout', 'on_step', 'key'):
            extra.pop(reserved, None)
        steps = self.harness.run(
            name,
            query=kwargs.get('query', 'help me with this'),
            path=path,
            goal=goal,
            model=model,
            timeout=int(kwargs.get('timeout') or HARNESS_TIMEOUT),
            on_step=self._emit_step,
            key=kwargs.get('key'),
            **extra,
        )
        self._meter_harness(name, steps)
        return steps

    def _meter_harness(self, name: str, steps: List[Dict[str, Any]]):
        """Land a harness run's own bill on this thread's meter.

        A CLI run never touches our providers, so without this the meter reads
        zero and every harness run looks free — the arena would score it as
        burning no tokens, and the console's task row would show none. Runners
        that report (claudecode puts the CLI's exact usage on the terminal
        step) get exact numbers; runners that don't still read zero. Never
        raises: accounting must not fail the run it is counting.
        """
        usage = None
        for step in reversed(steps or []):
            if isinstance(step, dict):
                u = (step.get('params') or {}).get('usage')
                if isinstance(u, dict) and u:
                    usage = u
                    break
        if not usage:
            return
        try:
            tally = self.meter.open(provider=usage.get('provider') or f'harness:{name}',
                                    model=usage.get('model') or name)
            tally['calls'] += max(1, int(usage.get('turns') or 0))
            tally['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
            tally['completion_tokens'] += int(usage.get('completion_tokens') or 0)
            # the CLI's own USD figure, not a catalog estimate — priced stays
            # True so downstream reads it as an exact bill
            tally['cost'] += float(usage.get('cost') or 0.0)
        except Exception:
            pass

    # ── arena tasks (hand-written, and drafted by an agent) ──────────

    TASK_BUILDER = 'task-builder'

    def arena_task_add(self, spec: Dict[str, Any], slug: str = None, key=None) -> dict:
        """Store a hand-written arena task under the caller's address.

        Writing a task is creating something the whole board plays, so it takes
        a sign-in; editing one takes being its author (or the host).
        """
        self.identity.require_signed_in(key, operation="write an arena task")
        addr = self.identity.addr(key)
        if slug:
            existing = self.arena.get_custom(slug)
            if existing:
                self.identity.require(owner=existing.get('owner'), key=key,
                                      operation=f"edit task '{slug}'")
        return self.arena.forward('task_add', spec=spec, owner=addr, slug=slug)

    def arena_task_rm(self, slug: str, key=None) -> dict:
        """Drop a hand-written task. Its author or the host."""
        existing = self.arena.get_custom(slug)
        if not existing:
            raise KeyError(f"no such task: {slug}")
        self.identity.require(owner=existing.get('owner'), key=key,
                              operation=f"remove task '{slug}'")
        return self.arena.forward('task_rm', slug=slug)

    # ── arena tasks in the openarena schema ──────────────────────────
    #
    # A statement plus graded test cases, stored in the openarena module and
    # judged by its sandbox. They are not copied here: writing one from this
    # console puts it on openarena's board too, which is the point — one task,
    # one set of hidden cases, one judge, two front doors.

    def arena_oa_task(self, slug: str) -> dict:
        """One openarena task in full, as an entrant may see it — the hidden
        cases keep their names and give up nothing else. Public: reading the
        exam is not cheating on it."""
        from src.arena import openarena as oa
        if not str(slug or '').strip():
            raise ValueError("name the task")
        return oa.get_task(slug, cached=False)

    def arena_oa_task_add(self, spec: Dict[str, Any], key=None) -> dict:
        """Upload a task in the openarena schema, filed under the caller."""
        self.identity.require_signed_in(key, operation="write an openarena task")
        return self.arena.forward('oa_task_add', spec=spec,
                                  author=self.identity.addr(key))

    def arena_oa_task_rm(self, slug: str, key=None) -> dict:
        """Delete an openarena task. Its author, or the host.

        openarena's own API is open — the gate is here, because here is where
        an address is verified. A seeded or benchmark-imported task has no
        address for an author, so only the host can remove one.
        """
        from src.arena import openarena as oa
        try:
            task = oa.get_task(slug, cached=False)
        except Exception as e:
            raise KeyError(f"no such openarena task: {slug} ({e})")
        author = str(task.get('author') or '')
        self.identity.require(owner=author if author.startswith('0x') else None,
                              key=key, operation=f"remove openarena task '{slug}'")
        return self.arena.forward('oa_task_rm', slug=slug)

    def arena_oa_import(self, source: str, preview: bool = False, key=None,
                        **opts) -> dict:
        """Pull a published benchmark in as tasks — HumanEval, MBPP, a
        HuggingFace dataset, a JSON url, a scraped problem page.

        `preview` converts and keeps nothing, which is the call to make first:
        a benchmark nobody looked at becomes tasks nobody read.
        """
        self.identity.require_signed_in(
            key, operation="import a benchmark into the arena")
        action = 'oa_preview' if preview else 'oa_import'
        return self.arena.forward(action, source=source, **opts)

    def arena_oa_enter(self, agent: str, name: str = None, model: str = None,
                       steps: int = None, free: bool = None, key=None) -> dict:
        """Enter one of our agents as a competitor on openarena's own board.

        The other direction of the same bridge: openarena will call this
        module's /run to make it play, which spends the host's provider key —
        so entering an agent is the host's call, not a visitor's.
        """
        self.require_owner(key, 'enter an agent in openarena')
        return self.arena.forward('oa_enter', agent=agent, name=name,
                                  model=model, steps=steps, free=free)

    def arena_task_draft(self, description: str, model: str = None,
                         provider: str = None, free: bool = False,
                         steps: int = 4, schema: str = 'agent', key=None) -> dict:
        """Hand a plain description to the task-builder agent and read a task
        spec back out of its answer.

        `schema` picks which kind of task it drafts: 'agent' scores the trace
        and the files left behind, 'openarena' scores a program against graded
        test cases. Both come back in the shape their own form edits.

        The draft is returned, not saved: a task nobody looked at is exactly the
        kind of thing that quietly makes every round meaningless. The caller
        reviews it in the Builder and saves it themselves.
        """
        self.identity.require_signed_in(key, operation="draft a task")
        # a draft is a model run on somebody's key, so it answers to the same
        # policy a run does: the host, a granted address, or credits on hand
        self.require_allowed(key, 'run')
        description = str(description or '').strip()
        if len(description) < 8:
            raise ValueError("describe the task in a sentence or two first")
        oa_schema = str(schema or 'agent').lower() in ('openarena', 'oa', 'program')
        query = (f"Write an OPENARENA task for this:\n\n{description}\n\n"
                 f"Use the OPENARENA schema — statement, mode, language, tests. "
                 f"Compute every `expect` exactly."
                 if oa_schema else
                 f"Write an arena task for this:\n\n{description}")
        trace = self._run(
            query=query,
            agent_type=self.TASK_BUILDER, model=model, provider=provider,
            steps=max(2, min(int(steps or 4), 8)), free=free, key=key,
            # the agent has no file tools, but a stray write must not land in
            # whatever directory the API happens to be running from
            path=str(Path.home() / '.mod' / 'agent' / 'arena'),
        )
        answer = self._answer_text([trace] if trace and isinstance(trace, list) else [])
        spec = self._parse_task_json(answer, openarena=oa_schema)
        if spec is None:
            return {"error": "the task-builder did not return a task spec — "
                             "try describing the task more concretely",
                    "answer": answer}
        if oa_schema:
            from src.arena import openarena as oa
            try:
                clean = oa.validate(spec)
            except ValueError as e:
                return {"draft": spec, "answer": answer, "invalid": str(e),
                        "schema": "openarena"}
            return {"draft": {**clean, "slug": self.arena.slugify(clean['title'])},
                    "answer": answer, "schema": "openarena"}
        try:
            clean = self.arena.validate_task(spec)
        except ValueError as e:
            # a draft that doesn't validate is still worth showing: the form it
            # fills is editable, and the message says what to fix
            return {"draft": spec, "answer": answer, "invalid": str(e)}
        return {"draft": {**clean, "slug": self.arena.slugify(clean['title'])},
                "answer": answer, "schema": "agent"}

    @staticmethod
    def _parse_task_json(text: str, openarena: bool = False) -> Optional[Dict[str, Any]]:
        """The JSON object out of a model's answer — fenced block first, then
        the outermost braces. None when there isn't one.

        The two schemas are told apart by the field that cannot be missing from
        either: an agent task is a `prompt`, an openarena task is `tests`.
        """
        text = str(text or '')
        blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
        candidates = list(blocks)
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
        for raw in candidates:
            try:
                out = json.loads(raw)
            except Exception:
                continue
            if not isinstance(out, dict):
                continue
            if openarena:
                if out.get('tests'):
                    return out
            elif out.get('prompt'):
                return out
        return None

    # ── arena (one runner, every match) ──────────────────────────────

    def arena_model_options(self) -> List[Dict[str, Any]]:
        """The models a gauntlet can be pointed at, per provider.

        Free ones first and flagged: they are what a board that runs itself on
        a timer is allowed to spend, and the difference between "$0" and "the
        host's credits" is the only thing about this list a console has to say
        out loud. A provider with no key is listed anyway with `ready: false` —
        an empty picker looks like a broken feature, not an unset key.
        """
        out: List[Dict[str, Any]] = []
        order = ('openrouter', 'venice', 'liquidai-cloud', 'liquidai')
        for short in order:
            path = self.PROVIDERS.get(short, short)
            ready = self.has_model(short)
            free_ids = set()
            if ready:
                try:
                    client = self._client(short)
                    if getattr(client, 'is_free', False):
                        free_ids = set(self.provider_models(short))
                    elif hasattr(client, 'free_models'):
                        free_ids = set(client.free_models() or [])
                except Exception as e:
                    print(f"[agent] free model list for {short} failed: {e}")
            ids = list(self.provider_models(short))
            # a free id the catalog offers but the curated list doesn't name is
            # still the most useful thing here — it costs nothing to rank
            for mid in sorted(free_ids):
                if mid not in ids:
                    ids.append(mid)
            for mid in ids:
                out.append({'model': mid, 'provider': short, 'ready': ready,
                            'free': mid in free_ids,
                            'hint': self.LOCAL_HINTS.get(short)})
        # a key that works before one that doesn't, then provider order — the
        # hosted catalogs first, because a gauntlet on this box's own LFM
        # runtime is a hundred repos of noise in a picker
        out.sort(key=lambda o: (not o['ready'], order.index(o['provider']),
                                not o['free'], o['model']))
        return out

    def arena_run(self, prompt: str, agent: str, model: str = None, steps: int = 8,
                  free: bool = True, path: str = None, provider: str = None):
        """Run one arena match and hand back its trace and what it cost.

        There is no caller behind a match — the board runs itself — so the run
        is sandboxed to the match's scratch dir and billed to nobody. The cost
        still has to be read off the meter: an unread tally would be handed to
        whichever run lands on this thread next.
        """
        # every executed step, not just the last plan: run() hands back
        # history[-1], so a run that wrote the file in step 2 and finished in
        # step 5 would be scored on the finish alone
        trace: List[Dict[str, Any]] = []
        try:
            last = self._run(query=prompt, agent_type=agent, model=model, steps=steps,
                             free=free, path=path, provider=provider,
                             allowed_paths=[path] if path else None,
                             # the pass, not a caller key: a match has nobody
                             # behind it, but the harness gate needs to know
                             # the board itself asked (see _run_harness)
                             key=getattr(self, '_arena_pass', None),
                             on_step=trace.append)
        finally:
            try:
                usage = self.meter.take()
            except Exception:
                usage = {}
        return (trace or last), usage

    def arena_scheduler(self, on: bool = True, delay: float = 15.0):
        """Start (or stop) the background process that keeps the board current.

        The API calls this once at boot. It is idempotent — a second call on a
        live scheduler just reports its status, so a reload can't end up with
        two threads racing for the same round.
        """
        sched = self.arena.scheduler or Scheduler(self.arena)
        return sched.start(delay=delay) if on else sched.stop()

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
        memory_status = (self.memory.status() if hasattr(self.memory, 'status')
                         else {'working_keys': self.memory.keys()})
        return {
            'module': 'agent',
            'tools': self.tools.ls(),
            'tool_count': len(self.tools.ls()),
            'agents': self.agents.ls(),
            'agent_count': len(self.agents.ls()),
            'toolboxes': self.toolboxes.ls(),
            'custom_tools': self.tools.custom.ls(),
            'mod_tools': len(self.tools.mods.ls()),
            'snapped': list(self._snapped),
            'model': self.model is not None,
            'memory_keys': self.memory.keys(),
            'memory': memory_status,
            'ports': {
                'api': self.api_port,
                'app': self.app_port,
                'memory': getattr(self.memory, '_port', None),
            },
            'mcp': self.mcp(),
        }

    def mcp(self, tools: bool = False) -> dict:
        """How to connect an MCP client to this module, and what it gets.

        The server is not a second API: src/mcp.py calls the same handlers the
        REST routes call, so the tool list here and the endpoint list there
        cannot describe different modules.
        """
        try:
            from src import mcp as mcp_server
        except Exception as e:
            return {'available': False, 'error': f'{type(e).__name__}: {e}'}
        out = mcp_server.info(f'http://localhost:{self.api_port}')
        if tools:
            out['schema'] = mcp_server.tool_list()
        return out

    def test(self):
        """Test the agent module"""
        results = {'passed': 0, 'failed': 0, 'tests': []}

        # test tools loaded
        try:
            tools = self.tools.ls()
            assert len(tools) > 0, "should have tools"
            results['tests'].append({'name': 'tools_loaded', 'passed': True, 'count': len(tools)})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'tools_loaded', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # module visibility: the crypto round trip and the audit guards, run
        # in a throwaway directory so this never touches the real fleet
        try:
            r = self.privacy.test()
            assert r.get('ok'), r
            results['tests'].append({'name': 'privacy', 'passed': True, **r})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'privacy', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test the fleet is reachable as tools
        try:
            fleet = self.tools.mods.ls()
            assert all(n.startswith('mod.') for n in fleet), "fleet tools carry the mod. prefix"
            results['tests'].append({'name': 'mod_tools', 'passed': True, 'count': len(fleet)})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'mod_tools', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test schema generation
        try:
            schema = self.tool_schema()
            assert 'bash' in schema, "should have the bash tool"
            assert 'read' in schema, "should have the read tool"
            results['tests'].append({'name': 'schema', 'passed': True, 'keys': list(schema.keys())})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'schema', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        # test the bash tool
        try:
            r = self.run_tool('bash', command='echo hello')
            assert r['success'] and 'hello' in r['stdout']
            results['tests'].append({'name': 'bash_tool', 'passed': True})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'bash_tool', 'passed': False, 'error': str(e)})
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

        # test the tool aggregator (offline — no registry is contacted)
        try:
            assert self.discover.test() is True
            results['tests'].append({'name': 'discover', 'passed': True,
                                     'sources': len(self.discover.sources())})
            results['passed'] += 1
        except Exception as e:
            results['tests'].append({'name': 'discover', 'passed': False, 'error': str(e)})
            results['failed'] += 1

        return results
