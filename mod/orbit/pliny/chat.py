#!/usr/bin/env python3
"""plinyville chat — ask the corpus a question; the Claude agent does the reading.

Forty-seven repos, thirteen thousand files, and a search box that only matches
repo names. "Which of these actually jailbreak Claude, and what does the prompt
look like?" is not a question a `filter` input can answer — it is a question
somebody has to go and *read* the corpus to answer.

So this module hands that job to an agent. `Chat.ask()` runs the **Claude agent**
(the `claude` CLI, headless) against **this module's own MCP server** — the same
`pv_*` tools an agent gets when it connects to POST /mcp — and streams back what
it did: every tool call, the files it opened, and the answer with the repo and
path it came from. Nothing else is wired in:

    claude --print --restricted --tools ""      no Bash, no Read, no WebFetch —
           --mcp-config … --strict-mcp-config   only the pv_* tools, and only
           --allowedTools mcp__pliny__pv_…      the ones listed here

**The type filter is a real fence, not a hint.** `POST /chat {"types":
["jailbreak"]}` sets `PLINYVILLE_SCOPE` on the MCP server the agent talks to,
and that server refuses to read a repo outside the scope (see mcp.py `_scope`).
Asking a jailbreak-scoped chat about ENTHEA gets "out of scope", not an answer —
the fence is on the tool, where the agent cannot talk its way around it.

    POST /chat {"question": "...", "types": ["jailbreak"]}      → the answer
    POST /chat/stream {...}                                     → SSE, live
    GET  /chat                                                  → the card
    GET  /.well-known/agent.json                                → agent/1.0

A conversation continues with `session`: the id every answer comes back with is
the Claude session, and passing it back resumes that context.

The agent costs the host real money, so an anonymous caller gets a small hourly
allowance per IP (`RATE_PER_HOUR`); the module owner's bearer token lifts it.
Everything here reads a corpus this module already serves in the open — the
agent quotes what is already at /m/<repo>/file, it does not generate anything
new — and a question that never called a tool comes back `grounded: false`
instead of pretending it went looking.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

from kinds import LABELS, TYPES  # noqa: E402

CLAUDE = os.environ.get('PLINYVILLE_CLAUDE_BIN') or shutil.which('claude')
MODEL = os.environ.get('PLINYVILLE_CHAT_MODEL', 'sonnet')
MODELS = ('sonnet', 'opus', 'haiku')
TIMEOUT = int(os.environ.get('PLINYVILLE_CHAT_TIMEOUT', 300))
# Two at a time. Each one is a live model session on the host's account; a
# console that lets fifty people press ASK at once is a console that bills for
# fifty sessions.
MAX_CONCURRENT = int(os.environ.get('PLINYVILLE_CHAT_CONCURRENCY', 2))
RATE_PER_HOUR = int(os.environ.get('PLINYVILLE_CHAT_RATE', 12))

# The tools the agent may call. Read-only, all of them: the corpus tools plus
# the two that describe the taxonomy and the arcade. pv_install is deliberately
# absent — an agent should not decide to clone 46 repos mid-answer.
AGENT_TOOLS = ('pv_repos', 'pv_repo', 'pv_readme', 'pv_tree', 'pv_file',
               'pv_search', 'pv_types', 'pv_run', 'pv_market', 'pv_exhibit',
               'pv_info')

SYSTEM = """\
You are the guide to PLINYVILLE — a mirror of github.com/elder-plinius, the
public jailbreak / prompt-injection research corpus (L1B3RT4S, CL4R1T4S,
GLOSSOPETRAE, G0DM0D3 …), served as one mod per repo.

HOW YOU ANSWER
* Only from the pv_* tools. You have no other sources. Never answer from what
  you remember about these repos: they change, and a remembered answer is a
  wrong one. If you did not read it in this session, do not claim it.
* Go and look first, always. pv_repos / pv_types to see what exists, pv_search
  to find the string, pv_tree to walk a repo, pv_file / pv_readme to read. Call
  at least one tool before you answer anything about a repo — the console marks
  an answer with no tool call as ungrounded, and it is right to.
* Never credit a tool you did not call. "per pv_types" when you did not run
  pv_types is a fabricated citation, and worse than no citation at all.
* Cite. Every claim ends with the repo and the path it came from, like
  `L1B3RT4S/ANTHROPIC.mkd`. A claim with no path is a claim you should delete.
* Quote sparingly and exactly — a line or two of the actual prompt text is
  worth more than a paragraph describing it. This corpus is already served
  verbatim by this module at /m/<repo>/file; you are a finding aid for it, not
  a rewriter of it, and you never invent, extend or "improve" a prompt.
* Be short. A researcher asked a question; give the answer, the paths, and stop.
  Markdown, no preamble, no "I'll help you with that".
* If the corpus does not answer it, say so and name what you checked.

WHAT THIS PLACE IS
Every repo here is one of: a jailbreak prompt collection, a leaked system
prompt, red-team tooling, a browser app the visitor can run sandboxed at
/pliny/m/<repo>#run, a tool, writing, the defanged exhibit, or an empty repo.
pv_types gives that taxonomy with counts and its own evidence; when someone
asks for "jailbreaks" they mean the `jailbreak` type, and repos of that type
are the ones to search first.
"""

_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)
_HITS = {}                                    # ip -> [unix times]
_HITS_LOCK = threading.Lock()


class ChatError(RuntimeError):
    """Something stopped the agent before it answered. `status` is the HTTP one."""

    def __init__(self, msg, status=400, **extra):
        super().__init__(msg)
        self.status = status
        self.extra = extra


def available() -> dict:
    """Is there a Claude agent on this box to talk to at all."""
    if not CLAUDE:
        return {'ok': False, 'why': 'the claude CLI is not installed on this host',
                'fix': 'npm install -g @anthropic-ai/claude-code (or m claude/install)'}
    try:
        v = subprocess.run([CLAUDE, '--version'], capture_output=True, text=True,
                           timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return {'ok': False, 'why': f'{CLAUDE} would not run: {e}'}
    return {'ok': True, 'bin': CLAUDE, 'version': v}


class Chat:
    """The corpus, with somebody to read it for you."""

    def __init__(self, market=None, kinds=None, runner=None, ville=None,
                 state_path=None):
        self.market = market
        self.kinds = kinds
        self.runner = runner
        self.ville = ville
        self.state_path = state_path

    # ── the card ────────────────────────────────────────────────────────────

    def card(self, base='/pliny') -> dict:
        """agent/1.0 — what this agent is, what it may read, how to ask it."""
        av = available()
        types = []
        try:
            types = [{'id': t['id'], 'label': t['label'], 'count': c['count'],
                      'blurb': t['blurb']}
                     for t, c in zip(TYPES, self.kinds.catalog()['types'])]
        except Exception:                                    # noqa: BLE001
            types = [{'id': t['id'], 'label': t['label'], 'blurb': t['blurb']}
                     for t in TYPES]
        return {
            'protocol': 'agent/1.0',
            'name': 'pliny-chat',
            'description': 'Ask the elder-plinius corpus a question. The Claude '
                           'agent reads the mirrored repos through this module\'s '
                           'own MCP tools and answers with the repo and path it '
                           'read it in — scoped, if you like, to one type of repo.',
            'agent': {'runtime': 'claude (Claude Code, headless)',
                      'model': MODEL, 'models': list(MODELS),
                      'available': av.get('ok', False),
                      'why_not': av.get('why'), 'fix': av.get('fix'),
                      'tools': list(AGENT_TOOLS),
                      'sandbox': 'built-in tools off (--tools ""), only this '
                                 "module's MCP server (--strict-mcp-config), and "
                                 'only the read-only pv_* tools on it'},
            'scopes': {'param': 'types',
                       'note': 'the scope is enforced on the MCP server the agent '
                               'talks to, not asked of the model: a repo outside '
                               'it answers "out of scope"',
                       'types': types},
            'endpoints': {
                'ask': 'POST /chat {question, types?, repo?, model?, session?}',
                'stream': 'POST /chat/stream — the same, as server-sent events',
                'card': 'GET /chat  ·  GET /.well-known/agent.json',
                'mcp': 'POST /mcp — the same tools, for your own agent',
            },
            'limits': {'per_hour_per_ip': RATE_PER_HOUR,
                       'concurrent': MAX_CONCURRENT, 'timeout_s': TIMEOUT},
            'urls': {'app': f'{base}#chat', 'api': f'/api{base}/chat',
                     'mcp': f'/api{base}/mcp'},
        }

    # ── the fence ───────────────────────────────────────────────────────────

    def scope(self, types=None, repo=None) -> dict:
        """Which repos this question may read, and why that set."""
        want = self.kinds.parse(types) if types else set()
        names = sorted(self.kinds.index()) if (want or repo) else []
        if repo:
            hit = [n for n in (names or []) if n.lower() == str(repo).lower()]
            if not hit:
                raise ChatError(f'{repo} is not a repo in this market', 404)
            allowed = hit
        elif want:
            allowed = self.kinds.filter(names, want)
            if not allowed:
                raise ChatError('no repo in this market is ' + '+'.join(sorted(want)),
                                404)
        else:
            allowed = None                     # the whole corpus
        return {'types': sorted(want), 'repo': repo, 'repos': allowed,
                'labels': [LABELS[t] for t in sorted(want)]}

    # ── asking ──────────────────────────────────────────────────────────────

    def ask(self, question, types=None, repo=None, model=None, session=None,
            timeout=None, ip=None, owner=False, on_event=None) -> dict:
        """Run the agent to completion. `on_event` sees every step as it lands."""
        events = []

        def sink(e):
            events.append(e)
            if on_event:
                on_event(e)

        for e in self.stream(question, types=types, repo=repo, model=model,
                             session=session, timeout=timeout, ip=ip, owner=owner):
            sink(e)
        last = events[-1] if events else {}
        if last.get('type') == 'error':
            raise ChatError(last.get('error') or 'the agent failed',
                            last.get('status') or 502)
        out = dict(last)
        out.pop('type', None)
        out['steps'] = [e for e in events if e['type'] in ('tool', 'text')]
        return out

    def stream(self, question, types=None, repo=None, model=None, session=None,
               timeout=None, ip=None, owner=False):
        """Yield dicts as the agent works: `tool`, `text`, then one `done`.

        Everything that can refuse the question refuses it here, as an `error`
        event, so the HTTP and SSE surfaces never have two ways to fail."""
        question = (question or '').strip()
        if not question:
            yield {'type': 'error', 'status': 400, 'error': 'ask a question'}
            return
        if len(question) > 4000:
            yield {'type': 'error', 'status': 400,
                   'error': 'that question is longer than the answers here are'}
            return
        av = available()
        if not av['ok']:
            yield {'type': 'error', 'status': 503, 'error': av['why'],
                   'fix': av.get('fix')}
            return
        model = str(model or MODEL).lower()
        if model not in MODELS:
            yield {'type': 'error', 'status': 400,
                   'error': f'model must be one of {", ".join(MODELS)}'}
            return
        try:
            sc = self.scope(types, repo)
        except (ChatError, ValueError) as e:
            yield {'type': 'error', 'status': getattr(e, 'status', 400),
                   'error': str(e)}
            return
        if not owner:
            left = _rate(ip)
            if left < 0:
                yield {'type': 'error', 'status': 429,
                       'error': f'{RATE_PER_HOUR} questions an hour from one address '
                                '— the agent runs on the host\'s own account. The '
                                'tools it uses are open at POST /mcp with no limit.'}
                return
        if not _SEM.acquire(blocking=False):
            yield {'type': 'error', 'status': 429,
                   'error': f'{MAX_CONCURRENT} agents are already reading; try again '
                            'in a moment'}
            return
        try:
            yield from self._run(question, sc, model, session, timeout)
        finally:
            _SEM.release()

    # ── the subprocess ──────────────────────────────────────────────────────

    def _cmd(self, question, sc, model, session):
        env_scope = {}
        if sc.get('repos') is not None:
            env_scope['PLINYVILLE_SCOPE'] = ','.join(sc['repos'])
        if self.state_path:
            env_scope['PLINYVILLE_STATE'] = self.state_path
        cfg = {'mcpServers': {'pliny': {
            'command': sys.executable, 'args': [os.path.join(HERE, 'mcp.py')],
            'env': env_scope}}}
        cmd = [CLAUDE, '--print', '--model', model, '--restricted', '--tools', '',
               '--mcp-config', json.dumps(cfg), '--strict-mcp-config',
               '--allowedTools', ','.join('mcp__pliny__' + t for t in AGENT_TOOLS),
               '--append-system-prompt', SYSTEM + self._scope_note(sc),
               '--output-format', 'stream-json', '--verbose']
        if session:
            if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(session)):
                raise ChatError('that is not a session id', 400)
            cmd += ['--resume', str(session)]
        return cmd + ['-p', question]

    @staticmethod
    def _scope_note(sc):
        if sc.get('repos') is None:
            return ('\nSCOPE\nThe whole corpus is open to you.\n')
        what = ('the repo ' + sc['repo']) if sc.get('repo') else (
            'repos of type ' + '+'.join(sc['types']))
        names = sc['repos']
        return ('\nSCOPE\nThis question is fenced to %s. The tools will refuse '
                'anything else — that is the fence, not a preference. In scope '
                '(%d): %s.\nIf the answer needs a repo outside it, say which one '
                'and that the scope excluded it.\n'
                % (what, len(names), ', '.join(names[:60])
                   + (' …' if len(names) > 60 else '')))

    def _run(self, question, sc, model, session, timeout):
        t0 = time.time()
        try:
            cmd = self._cmd(question, sc, model, session)
        except ChatError as e:
            yield {'type': 'error', 'status': e.status, 'error': str(e)}
            return
        yield {'type': 'start', 'model': model, 'scope': sc.get('types'),
               'repo': sc.get('repo'),
               'repos': len(sc['repos']) if sc.get('repos') is not None else None,
               'tools': list(AGENT_TOOLS)}
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True, bufsize=1, cwd='/tmp',
                env=dict(os.environ, CLAUDE_CODE_ENTRYPOINT='pliny-chat'))
        except OSError as e:
            yield {'type': 'error', 'status': 503, 'error': f'could not start the agent: {e}'}
            return
        reads, text, result, err = [], [], None, []
        watchdog = threading.Timer(float(timeout or TIMEOUT), proc.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith('{'):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get('type')
                if kind == 'assistant':
                    for c in (ev.get('message') or {}).get('content') or []:
                        if c.get('type') == 'tool_use':
                            step = {'type': 'tool',
                                    'tool': str(c.get('name') or '').replace(
                                        'mcp__pliny__', ''),
                                    'input': _small(c.get('input') or {})}
                            reads.append({k: v for k, v in step.items() if k != 'type'})
                            yield step
                        elif c.get('type') == 'text' and (c.get('text') or '').strip():
                            text.append(c['text'])
                            yield {'type': 'text', 'text': c['text']}
                elif kind == 'user':
                    for c in (ev.get('message') or {}).get('content') or []:
                        if isinstance(c, dict) and c.get('is_error'):
                            yield {'type': 'refused',
                                   'error': _first_text(c.get('content'))[:300]}
                elif kind == 'result':
                    result = ev
            proc.wait(timeout=15)
            err = (proc.stderr.read() or '')[-2000:]
        except Exception as e:                               # noqa: BLE001
            proc.kill()
            yield {'type': 'error', 'status': 502, 'error': f'{type(e).__name__}: {e}'}
            return
        finally:
            watchdog.cancel()
        if result is None:
            killed = proc.returncode in (-9, 137)
            yield {'type': 'error', 'status': 504 if killed else 502,
                   'error': ('the agent ran past %ss and was stopped'
                             % (timeout or TIMEOUT)) if killed else
                            ('the agent stopped without answering'
                             + (': ' + err.strip().splitlines()[-1] if err.strip() else ''))}
            return
        answer = (result.get('result') or '\n'.join(text)).strip()
        if result.get('is_error') and not answer:
            yield {'type': 'error', 'status': 502,
                   'error': result.get('error') or 'the agent errored'}
            return
        yield {
            'type': 'done',
            'answer': answer,
            'grounded': bool(reads),
            'note': None if reads else ('the agent answered without opening a single '
                                        'file — treat this as its opinion, not as '
                                        'the corpus'),
            'reads': reads,
            'read_count': len(reads),
            'scope': {'types': sc.get('types'), 'repo': sc.get('repo'),
                      'repos': sc.get('repos')},
            'session': result.get('session_id'),
            'model': model,
            'turns': result.get('num_turns'),
            'cost_usd': result.get('total_cost_usd'),
            'duration_ms': int((time.time() - t0) * 1000),
            'agent': 'claude (Claude Code, headless) over this module\'s MCP server',
        }


def _small(obj, cap=300):
    """Tool input, trimmed: the console shows what it looked for, not a blob."""
    out = {}
    for k, v in (obj or {}).items():
        s = v if isinstance(v, (int, float, bool)) else str(v)
        if isinstance(s, str) and len(s) > cap:
            s = s[:cap] + '…'
        out[k] = s
    return out


def _first_text(content):
    if isinstance(content, str):
        return content
    for c in content or []:
        if isinstance(c, dict) and c.get('text'):
            return c['text']
    return ''


def _rate(ip):
    """Questions left this hour for `ip`; -1 when it is out."""
    now = time.time()
    key = ip or '-'
    with _HITS_LOCK:
        hits = [t for t in _HITS.get(key, []) if now - t < 3600]
        if len(hits) >= RATE_PER_HOUR:
            _HITS[key] = hits
            return -1
        hits.append(now)
        _HITS[key] = hits
        if len(_HITS) > 4000:                  # never a memory leak, just a counter
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > 3600]:
                _HITS.pop(k, None)
        return RATE_PER_HOUR - len(hits)


if __name__ == '__main__':
    from kinds import Kinds
    from market import Market
    from run import Runner
    mk = Market()
    c = Chat(mk, Kinds(mk, Runner(mk)), Runner(mk))
    if len(sys.argv) < 2:
        print(json.dumps(c.card(), indent=2))
        raise SystemExit(0)
    q = sys.argv[1]
    ts = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    for e in c.stream(q, types=ts, owner=True):
        if e['type'] == 'tool':
            print('· %s %s' % (e['tool'], json.dumps(e['input'])[:120]), file=sys.stderr)
        elif e['type'] == 'done':
            print(e['answer'])
            print('\n— %d reads · %s · %.1fs' % (e['read_count'], e['session'],
                                                 e['duration_ms'] / 1000),
                  file=sys.stderr)
        elif e['type'] == 'error':
            print('error:', e['error'], file=sys.stderr)
