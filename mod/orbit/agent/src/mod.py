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
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import mod as m
    print = m.print
except ImportError:
    m = None

from .agents.mod import Agents
from .memory.mod import Memory
from .library.mod import Library
from .toolbox.mod import Toolboxes
from .tools.mod import Tools
from .credits import Credits
from .billing import Meter
from .vaults.mod import Vaults
from .discover.mod import Discover
from .harness.mod import Harness, DEFAULT_TIMEOUT as HARNESS_TIMEOUT
from .arena.mod import Arena, Scheduler
from .identity import Identity


# ── path sandboxing ────────────────────────────────────────────────

WRITE_TOOLS = ('write', 'edit', 'patch')

# ── repeat-call guard ──────────────────────────────────────────────

# How many times one identical (tool, params) call may fail before the loop
# stops running it. Two, so a flaky network gets its retry — but the model
# can't sit there re-fetching a 403 URL twenty times waiting for a different
# answer. Successful calls are never blocked: re-reading a file after an
# edit is the same call with a legitimately different result.
MAX_IDENTICAL_FAILURES = 2


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
    }

    DEFAULT_MODELS = {
        'model.openrouter': 'anthropic/claude-opus-5',
        'openrouter': 'anthropic/claude-opus-5',
        'venice': 'deepseek-v3.2',
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
    }

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
        Respond with exactly ONE step per iteration inside anchors.
        The params must be valid JSON.
        <PLAN>
        <STEP>{"tool": "<tool_name>", "params": {...}}</STEP>
        </PLAN>
        When finished:
        <PLAN>
        <STEP>{"tool": "finish", "params": {"summary": "your answer, written to the user"}}</STEP>
        </PLAN>
        The summary is shown to the user as your response — write the actual
        answer there, not a description of what you did.
    """

    def __init__(self,
                 model: str = 'model.openrouter',
                 provider: str = None,
                 memory: str = 'agent.memory',
                 goal: str = None,
                 tools: list = None,
                 **kwargs):
        self.agents = Agents()
        # images attached to the current run (data URLs or http urls)
        self._images: List[str] = []
        # the whole tool surface in one registry: the tools shipped here, the
        # shell tools added from the console, and every mod in the fleet
        self.tools = Tools()
        # toolboxes: named tool bundles that snap onto this agent
        self.toolboxes = Toolboxes(tools=self.tools)
        self._snapped: List[str] = []
        self.memory = m.mod(memory)() if m else Memory()
        # session keys: provider shortname -> API key decrypted from the vault,
        # held IN MEMORY ONLY (never written to disk in plaintext)
        self._session_keys: Dict[str, str] = {}
        # resolve provider: shorthand ('venice', 'openrouter') or full module path
        provider = provider or model
        self._provider = self.PROVIDERS.get(provider, provider)
        self.model = self._make_model()
        # prices each model call from the provider's live catalog, so a guest's
        # credits pay for the provider spend their run actually creates
        self.meter = Meter()
        if goal:
            self.goal = goal
        self._tool_names = tools  # optional filter

    def _provider_short(self, provider_path: str = None) -> str:
        """Map a provider module path back to its shortname ('model.openrouter' -> 'openrouter')."""
        provider_path = provider_path or self._provider
        for short, path in self.PROVIDERS.items():
            if path == provider_path:
                return short
        return provider_path

    def _make_model(self):
        """Build the live model for the active provider.

        A vault-unlocked session key takes priority over the provider's own
        stored/env keys. Returns None instead of raising when no key is
        configured yet — runs then fail with a clear 'no model' error rather
        than the whole module failing to construct.
        """
        if not m:
            return None
        session_key = self._session_keys.get(self._provider_short())
        try:
            if session_key:
                return m.mod(self._provider)(api_key=session_key)
            return m.mod(self._provider)()
        except Exception as e:
            print(f"Model init failed for {self._provider}: {e}")
            return None

    def free_model(self) -> Optional[str]:
        """The best zero-cost model the active provider offers, or None.

        Ranked by FREE_MODEL_PREFERENCE — the provider's own free list is in
        catalog order, and its first entry is regularly a tiny or endpointless
        model that can't drive the loop.
        """
        try:
            free = self.model.free_models() if hasattr(self.model, 'free_models') else []
        except Exception as e:
            print(f"Free model lookup failed: {e}")
            return None
        for pref in self.FREE_MODEL_PREFERENCE:
            for mid in free:
                if pref in mid:
                    return mid
        return free[0] if free else None

    def set_provider(self, provider: str):
        """Switch LLM provider at runtime. Use 'openrouter', 'venice', or any module path."""
        self._provider = self.PROVIDERS.get(provider, provider)
        self.model = self._make_model()
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
            images: list = None,
            budget=None,
            **kwargs) -> List[Dict[str, Any]]:
        """Run the agent loop: query -> LLM -> parse step -> execute tool -> repeat.

        Args:
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
            budget: optional callable given the run's metered provider cost so far;
                    returning False stops the loop. A paying guest's credits are
                    finite, and a charge clamped to their balance would leave the
                    module holding the overrun.
            images: image URLs (http or data:) the user attached to the query —
                    sent to the model as a leading multimodal turn.
        """
        if provider:
            self.set_provider(provider)
        if self.model is None:
            raise RuntimeError(
                f"No API key available for provider '{self._provider_short()}'. "
                f"Add a key — or unlock your encrypted key — in the Builder (model node).")
        self._on_step = on_step
        self._images = [i for i in (images or []) if isinstance(i, str) and i.strip()][:8]
        model = model or self.DEFAULT_MODELS.get(self._provider, 'anthropic/claude-opus-5')
        # FREE MODE resolves the model here rather than letting the provider
        # grab whatever sorts first: the pick is a deliberate, capable one, and
        # the run ledger records the model that actually ran.
        if free:
            picked = self.free_model()
            if picked:
                model, free = picked, False
        self._allowed_paths = allowed_paths
        query = query + ' ' + ' '.join(extra_text) if extra_text else query
        path = path or (m.dp(mod) if m and mod else os.getcwd())
        # per-run toolbox snap: explicit tools list wins, then toolbox union
        if not tools and toolbox:
            tools = self.toolboxes.resolve(toolbox)
        # semantic recall: durable facts from past runs ride into the prompt
        recalled = None
        if hasattr(self.memory, 'compile'):
            recalled = self.memory.compile(query) or None
        self.init_memory(
            query=query,
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
        # per-run tally of identical calls that failed, so the loop can stop
        # replaying a dead end (see MAX_IDENTICAL_FAILURES)
        self._failed_calls: Dict[str, Dict[str, Any]] = {}
        # start this thread's cost tally — whoever bills the run reads it back
        # with meter.take() once forward() returns
        self.meter.open(provider=self._provider_short(), model=model)
        for step_i in range(steps):
            self.memory.update({'step': step_i, 'pwd': path})
            # inject recovery hint after repeated errors
            if consecutive_errors >= 3:
                self.memory.add('hint', 'Multiple errors in a row. Use think to reflect on what is going wrong and try a different approach.')
                consecutive_errors = 0
            try:
                context = str(self.memory.get())
                output = self.meter.watch(
                    self.model.forward(
                        context,
                        stream=True,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        free=free,
                        **({'history': self._image_turn()} if self._images else {}),
                    ),
                    model_obj=self.model, provider=self._provider_short(),
                    model=model, prompt=context,
                )
                plan = self.plan(output, safety=safety)
            except Exception as e:
                print(f"Model error: {e}")
                err = str(e)
                # providers raise their own missing-key errors at call time —
                # point the user at the Builder, where keys are entered
                if 'api key' in err.lower() or 'api_key' in err.lower():
                    err = f"{err} — enter your {self._provider_short()} API key in the Builder (model node)."
                plan = [{'tool': 'error', 'params': {}, 'error': err}]
                self._emit_step(plan[-1])
            history.append(plan)
            self.memory.add('history', history)
            if plan and plan[-1]['tool'].lower() in ('finish', 'response'):
                print('Agent finished')
                break
            if plan and plan[-1]['tool'].lower() == 'error':
                print('Agent stopped: model error')
                break
            if budget and not budget(self.meter.peek()):
                step = {'tool': 'error', 'params': {},
                        'error': 'credit balance spent — top up to keep going'}
                self._emit_step(step)
                history.append([step])
                print('Agent stopped: out of credits')
                break
            # track consecutive errors for recovery
            if plan and any(_step_failed(s) for s in plan):
                consecutive_errors += 1
            else:
                consecutive_errors = 0
        # a run that used tools must still end with words: if the loop stopped
        # without a finish summary or response text (steps ran out, or finish
        # came back empty), make one last tools-off call for the actual answer
        if history and not self._has_answer(history) \
                and not any(s.get('tool') == 'error' for s in history[-1]):
            answer = self._force_answer(model=model, max_tokens=max_tokens,
                                        temperature=temperature, free=free)
            if answer:
                history[-1] = history[-1] + [answer]
        if save and m and mod:
            return m.fn('api/reg')(mod=mod, key=key, comment=query)
        return history[-1] if history else []

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

    def _force_answer(self, model, max_tokens, temperature, free) -> Optional[dict]:
        """One last tools-off model call that turns the run's history into the
        answer the user reads. Returns a response step, or None if it fails."""
        self.memory.add('hint', 'Tool use is over. Using everything in the history '
                                'above, write your final answer to the user now as '
                                'plain text — answer what they asked. No tools, no anchors.')
        try:
            context = str(self.memory.get())
            out = self.meter.watch(
                self.model.forward(
                    context,
                    stream=True,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    free=free,
                    **({'history': self._image_turn()} if self._images else {}),
                ),
                model_obj=self.model, provider=self._provider_short(),
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
                return None
            step = {'tool': 'response', 'params': {}, 'result': text}
            self._emit_step(step)
            return step
        except Exception as e:
            print(f"Final-answer error: {e}")
            return None

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

    def _strip_anchors(self, text: str) -> str:
        """Drop plan/step scaffolding from text meant for the user's eyes."""
        import re
        text = re.sub(r'<STEP>.*?</STEP>', '', text, flags=re.S)
        for tag in (*self.anchors['plan'], *self.anchors['tool']):
            text = text.replace(tag, '')
        return text.strip()

    def plan(self, output: str, safety: bool = False) -> list:
        """Parse LLM output into steps and execute them."""
        steps, raw_text = self.parse_steps(output)
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
        tool = step.get('tool')
        if not isinstance(tool, str) or not tool.strip():
            return None      # run_plan calls .lower() on this — it must be a name
        step['tool'] = tool.strip()
        params = step.get('params')
        if isinstance(params, str):              # double-encoded params
            params = self._repair_json(params)
        step['params'] = params if isinstance(params, dict) else {}
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
        for i, step in enumerate(plan):
            name = step['tool'].lower()
            params = step.get('params', {})
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
            self._emit_step(plan[i])
        return plan


# backwards compat
Dev = Agent


class Mod(Agent):
    description = "Autonomous coding agent. Built-in tools, custom shell tools, and the whole fleet."

    # the agent a run lands on when the caller named none — the Claude Code
    # CLI on this host. It's a harness agent, so it only holds for the owner
    # with the CLI installed; everyone else falls back to the native loop
    # (see default_agent).
    DEFAULT_AGENT = 'claude-code'
    FALLBACK_AGENT = 'default'

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
        except Exception:
            self._acl_path = self.module_dir / '.acl.json'
            self._vault_dir = self.module_dir / '.vault'
        self._acl = self._load_acl()
        # a remembered unlock survives restarts — resume it before anything
        # asks for a model, so the key is live without a passphrase prompt
        self._vault_resume()

        # prepaid credit ledger — guests top up USDT/USDC and spend the
        # credits to run on the module's public provider key. Ledger state
        # is private, off-tree, next to the ACL.
        self.credits = Credits(self._acl_path.parent, deposit_address=self._owner)
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
                                'recall', 'episodes', 'facts', 'memory_state',
                                # the board is public — a ranking nobody can
                                # read is not a ranking
                                'arena', 'arena_tasks', 'arena_matches',
                                'arena_card', 'arena_status',
                                'key_info', 'balance',
                                'credits', 'credit_deposit',
                                # vaults self-scope to the caller's verified
                                # address (Vaults raises without a sign-in)
                                'vaults', 'vaults_get', 'vaults_set',
                                'vaults_add', 'vaults_rm', 'vaults_key_rm',
                                'vaults_public'}
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
                               'credit_grant', 'treasury', 'credit_topup',
                               'credit_withdraw', 'credit_config'}

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
        """Check if key/address/token belongs to the module owner."""
        if not self._owner:
            return True
        addr = self._resolve_address(key, verified=True)
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

    # ── credits (prepaid public-key usage) ───────────────────────────

    def credits_info(self, key: str = None) -> dict:
        """Deposit/pricing info + the caller's own credit account."""
        addr = self._resolve_address(key) if key else ''
        if not (isinstance(addr, str) and addr.startswith('0x') and len(addr) == 42):
            addr = None
        return self.credits.info(addr, owner=bool(key) and self.is_owner(key))

    def credit_deposit(self, tx_hash: str, network: str = 'base') -> dict:
        """Verify a USDT/USDC deposit tx and credit the on-chain sender."""
        return self.credits.verify_deposit(tx_hash, network)

    def credit_grant(self, address: str, amount: float, note: str = '',
                     key: str = None) -> dict:
        """Manually adjust an account's credits (± amount). Owner only."""
        self.require_owner(key, 'credit_grant')
        return self.credits.credit(address, amount, kind='grant',
                                   note=note or f'granted by {self._owner}')

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
        """Rebuild the live model if the changed provider is the active one."""
        if self.PROVIDERS.get(provider, provider) == self._provider:
            self.model = self._make_model()

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
            self.model = self._make_model()

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
        keys = self._provider_keys(provider)
        info = self.key_info(provider)
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
            recall, episodes, facts, memory_state,
            arena, arena_tasks, arena_matches, arena_card, arena_status

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
            arena_run   - Play a match (agent=, task=) or a whole round
            arena_qualify - Score a newcomer against the incumbents (agent=)
            arena_config  - Set the board's knobs (enabled=, free=, period_hours=…)
            arena_scheduler - Start/stop the background board process (on=)
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
            # memory subsystem (working/episodic/semantic layers, own process)
            'memory_state': lambda: self.memory.forward('status') if hasattr(self.memory, 'status') else self.memory.summary(),
            'recall': lambda: self.memory.recall(kwargs.get('query', kwargs.get('q', '')), kwargs.get('k', 5)),
            'episodes': lambda: self.memory.episodes(kwargs.get('n', 50), kwargs.get('session')),
            'facts': lambda: self.memory.facts(),
            # arena: every agent on the same tasks, one ranked board
            'arena': lambda: self.arena.forward(),
            'arena_tasks': lambda: self.arena.forward('tasks'),
            'arena_matches': lambda: self.arena.forward('matches', limit=kwargs.get('limit', 50),
                                                        agent=kwargs.get('agent'),
                                                        task=kwargs.get('task')),
            'arena_card': lambda: self.arena.forward('card', agent=kwargs.get('agent', '')),
            'arena_status': lambda: self.arena.forward('status'),
            'key_info': lambda: self.key_info(kwargs.get('provider', 'openrouter')),
            'balance': lambda: self.balance(kwargs.get('provider', 'openrouter')),
            # credits (prepaid public-key usage)
            'credits': lambda: self.credits_info(key),
            'credit_deposit': lambda: self.credit_deposit(kwargs.get('tx_hash', ''),
                                                          kwargs.get('network', 'base')),
            'credit_grant': lambda: self.credit_grant(kwargs.get('address', ''),
                                                      kwargs.get('amount', 0),
                                                      kwargs.get('note', ''), key),
            # treasury: guest deposits fund the provider keys, we keep the margin
            'treasury': lambda: self.credits_treasury(key, live=kwargs.get('live', True)),
            'credit_topup': lambda: self.credit_topup(kwargs.get('provider', ''),
                                                      kwargs.get('amount', 0),
                                                      kwargs.get('ref', ''),
                                                      kwargs.get('note', ''), key),
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

        if agent_type and agent_type in self.agents.ls():
            agent_config = self.agents.get(agent_type)
            if agent_config.get('goal'):
                agent_goal = agent_config['goal']
            # `skills` is the pre-rename key — agent configs saved back then
            # still carry it, so it's read as a fallback
            saved_tools = agent_config.get('tools') or agent_config.get('skills')
            if saved_tools and not kwargs.get('tools'):
                agent_tools = saved_tools
            if agent_config.get('model'):
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
            return self._run_harness(agent_harness, goal=agent_goal,
                                     model=agent_config.get('model'), **kw)

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
                images=kwargs.get('images'),
                budget=kwargs.get('budget'),
                **extra,
            )
        finally:
            self.goal = original_goal

    # ── harness runs (external agent CLIs) ───────────────────────────

    def default_agent(self, key=None) -> str:
        """The agent to run as when the caller picked none.

        Claude Code by default — this host's own CLI, with its own tools and
        model. That is a harness run, which is owner-only and needs the binary
        installed, so anyone else (or a host without it) gets the native agent.
        """
        try:
            harness = self.agents.get(self.DEFAULT_AGENT).get('harness')
            if harness and not (self.is_owner(key)
                                and self.harness.get(harness).available()):
                return self.FALLBACK_AGENT
            return self.DEFAULT_AGENT
        except Exception:      # default agent missing/unloadable — never block a run
            return self.FALLBACK_AGENT

    def harness_for(self, agent_type: str = None) -> Optional[str]:
        """The harness an agent hands its run to, or None for a native run."""
        if not agent_type:
            return None
        try:
            return self.agents.get(agent_type).get('harness')
        except Exception:
            return None

    def _run_harness(self, name: str, goal: str = None, model: str = None,
                     **kwargs) -> List[Dict[str, Any]]:
        """Hand the run to an external agent CLI and stream back its steps.

        Owner only. The CLIs run with their approval prompts off — nobody is
        at the other end of a server-side run to answer them — so a harness run
        is effectively the host's own shell. Guests stay on this module's loop,
        which is sandboxed to their portal directory.
        """
        if not self.is_owner(kwargs.get('key')):
            raise PermissionError(
                f"the {name} agent runs a coding CLI on this host — owner only. "
                f"Pick a native agent to run on this module's own sandboxed loop.")
        path = kwargs.get('path') or (m.dp(kwargs['mod']) if m and kwargs.get('mod')
                                      else os.getcwd())
        # reuse the native step sink: live progress for the console, and the
        # run still lands in the memory subsystem as episodes
        self._on_step = kwargs.get('on_step')
        return self.harness.run(
            name,
            query=kwargs.get('query', 'help me with this'),
            path=path,
            goal=goal,
            model=model,
            timeout=int(kwargs.get('timeout') or HARNESS_TIMEOUT),
            on_step=self._emit_step,
        )

    # ── arena (one runner, every match) ──────────────────────────────

    def arena_run(self, prompt: str, agent: str, model: str = None, steps: int = 8,
                  free: bool = True, path: str = None):
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
                             free=free, path=path,
                             allowed_paths=[path] if path else None, key=None,
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
        }

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
