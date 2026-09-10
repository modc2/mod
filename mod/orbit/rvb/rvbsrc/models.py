"""models — the thing under attack, and the thing that judges it.

One function, `complete(messages, system=..., model=...)`, over four backends.
Which one runs is decided by the model string, so an attack, a defense and a
round are all portable: they name a model, not a provider.

    claude:haiku            the local Claude Code CLI, headless. No API key.
    openrouter:<slug>       BYOK, ~/.mod/openrouter/key or OPENROUTER_API_KEY
    anthropic:<model>       ANTHROPIC_API_KEY
    openai:<model>          OPENAI_API_KEY
    mock:<behaviour>        deterministic, offline, no network

WHY MOCK EXISTS
    A scoring harness that cannot be tested without spending money on a
    frontier model is a scoring harness nobody tests. `mock` is a target whose
    behaviour is written down — mock:compliant answers anything, mock:strict
    refuses anything, mock:naive falls for roleplay framing and refuses plain
    requests — so the arena's arithmetic can be checked against a target whose
    correct score is known in advance.

WHY THE CLI IS THE DEFAULT
    It needs no key, it is already authenticated on this box, and the game is
    playable the moment the module is installed. It is slower than an API call
    (~5s), so a round with more than a few dozen matches should run against an
    API backend or with a larger `parallel`.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

DEFAULT = os.environ.get('RVB_MODEL', 'claude:haiku')
JUDGE_MODEL = os.environ.get('RVB_JUDGE_MODEL', DEFAULT)
TIMEOUT = int(os.environ.get('RVB_MODEL_TIMEOUT', 120))
MAX_TOKENS = int(os.environ.get('RVB_MAX_TOKENS', 1024))


class ModelError(Exception):
    """The target could not be reached or refused to run at all.

    Distinct from the target refusing the *request* — that is a result, not an
    error, and it is the whole point of the game.
    """


def split(model=None):
    model = str(model or DEFAULT).strip()
    provider, _, name = model.partition(':')
    if not name:
        provider, name = ('claude', provider) if provider in (
            'haiku', 'sonnet', 'opus') else (provider, '')
    return provider.lower(), name


def providers():
    """Which backends can actually run right now, and why not if they cannot."""
    out = {}
    cli = _which('claude')
    out['claude'] = {'ready': bool(cli), 'how': cli or 'claude CLI not on PATH',
                     'keyless': True}
    for name, env, files in (
            ('openrouter', 'OPENROUTER_API_KEY', ['~/.mod/openrouter/key']),
            ('anthropic', 'ANTHROPIC_API_KEY', ['~/.mod/rvb/anthropic.key']),
            ('openai', 'OPENAI_API_KEY', ['~/.mod/rvb/openai.key'])):
        key = _key(env, files)
        out[name] = {'ready': bool(key), 'keyless': False,
                     'how': f'{env} is set' if key else
                            f'set {env} or write {files[0]}'}
    out['mock'] = {'ready': True, 'keyless': True,
                   'how': 'offline, deterministic — for testing the harness'}
    return out


def _which(binary):
    from shutil import which
    return which(binary)


def _key(env, files):
    val = os.environ.get(env)
    if val:
        return val.strip()
    for f in files:
        try:
            with open(os.path.expanduser(f)) as fh:
                v = fh.read().strip()
                if v:
                    return v
        except Exception:
            continue
    return None


def complete(messages, system=None, model=None, max_tokens=None, timeout=None):
    """Send a conversation to the target. Returns {text, model, provider, ...}.

    `messages` is [{role, content}] with roles user/assistant. An assistant
    message in the last position is a prefill — the target continues it. Some
    defenses use that on purpose, and so do some attacks.
    """
    provider, name = split(model)
    max_tokens = int(max_tokens or MAX_TOKENS)
    timeout = int(timeout or TIMEOUT)
    fn = {'claude': _claude, 'openrouter': _openrouter, 'anthropic': _anthropic,
          'openai': _openai, 'mock': _mock}.get(provider)
    if fn is None:
        raise ModelError(f'no backend {provider!r} — one of '
                         f'{", ".join(providers())}. Models are "provider:name".')
    text = fn(messages, system, name, max_tokens, timeout)
    return {'text': text, 'model': f'{provider}:{name}' if name else provider,
            'provider': provider, 'chars': len(text)}


# ── backends ─────────────────────────────────────────────────────

def _flatten(messages):
    """The CLI takes one prompt, not a conversation.

    Multi-turn attacks are real — a crescendo builds consent over four turns —
    so the turns are labelled and kept in order rather than dropped. The labels
    are the only honest way to say "this is a transcript" to a single-prompt
    interface; they are not a defensive measure and a defense should not rely
    on them.
    """
    if len(messages) == 1 and messages[0].get('role') == 'user':
        return messages[0].get('content', '')
    lines = []
    for m in messages:
        role = 'User' if m.get('role') == 'user' else 'Assistant'
        lines.append(f'{role}: {m.get("content", "")}')
    if messages and messages[-1].get('role') == 'user':
        lines.append('Assistant:')
    return '\n\n'.join(lines)


def _claude(messages, system, name, max_tokens, timeout):
    cli = _which('claude')
    if not cli:
        raise ModelError('the claude CLI is not on PATH — use openrouter:, '
                         'anthropic:, openai: or mock:')
    cmd = [cli, '--print', '--tools', '', '--model', name or 'haiku']
    if system:
        # --system-prompt REPLACES the default. The target must be the model
        # plus the defense and nothing else, or the harness would be scoring
        # Claude Code's own system prompt.
        cmd += ['--system-prompt', system]
    cmd.append(_flatten(messages))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=os.path.expanduser('~'),
                           env={**os.environ, 'CLAUDE_CODE_DISABLE_TELEMETRY': '1'})
    except subprocess.TimeoutExpired:
        raise ModelError(f'claude CLI did not answer within {timeout}s')
    if p.returncode != 0:
        raise ModelError(f'claude CLI exited {p.returncode}: '
                         f'{(p.stderr or p.stdout or "").strip()[:400]}')
    return (p.stdout or '').strip()


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'content-type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:400]
        raise ModelError(f'{url} returned {e.code}: {body}')
    except Exception as e:
        raise ModelError(f'{url}: {type(e).__name__}: {e}')


def _chat_payload(messages, system, name, max_tokens):
    msgs = ([{'role': 'system', 'content': system}] if system else []) + \
        [{'role': m.get('role', 'user'), 'content': m.get('content', '')}
         for m in messages]
    return {'model': name, 'messages': msgs, 'max_tokens': max_tokens}


def _openrouter(messages, system, name, max_tokens, timeout):
    key = _key('OPENROUTER_API_KEY', ['~/.mod/openrouter/key'])
    if not key:
        raise ModelError('no OpenRouter key — set OPENROUTER_API_KEY or write '
                         '~/.mod/openrouter/key')
    d = _post('https://openrouter.ai/api/v1/chat/completions',
              _chat_payload(messages, system, name or 'openai/gpt-4o-mini',
                            max_tokens),
              {'authorization': f'Bearer {key}',
               'x-title': 'rvb red-vs-blue'}, timeout)
    return _pick_chat(d)


def _openai(messages, system, name, max_tokens, timeout):
    key = _key('OPENAI_API_KEY', ['~/.mod/rvb/openai.key'])
    if not key:
        raise ModelError('no OpenAI key — set OPENAI_API_KEY')
    d = _post('https://api.openai.com/v1/chat/completions',
              _chat_payload(messages, system, name or 'gpt-4o-mini', max_tokens),
              {'authorization': f'Bearer {key}'}, timeout)
    return _pick_chat(d)


def _pick_chat(d):
    try:
        return (d['choices'][0]['message'].get('content') or '').strip()
    except Exception:
        raise ModelError(f'unreadable completion: {json.dumps(d)[:300]}')


def _anthropic(messages, system, name, max_tokens, timeout):
    key = _key('ANTHROPIC_API_KEY', ['~/.mod/rvb/anthropic.key'])
    if not key:
        raise ModelError('no Anthropic key — set ANTHROPIC_API_KEY')
    payload = {'model': name or 'claude-haiku-4-5-20251001',
               'max_tokens': max_tokens,
               'messages': [{'role': m.get('role', 'user'),
                             'content': m.get('content', '')} for m in messages]}
    if system:
        payload['system'] = system
    d = _post('https://api.anthropic.com/v1/messages', payload,
              {'x-api-key': key, 'anthropic-version': '2023-06-01'}, timeout)
    try:
        return ''.join(b.get('text', '') for b in d['content']).strip()
    except Exception:
        raise ModelError(f'unreadable completion: {json.dumps(d)[:300]}')


# ── the offline target ───────────────────────────────────────────

MOCK_REFUSAL = ("I can't help with that. If you're researching this "
                "defensively I'm happy to talk about mitigations instead.")

# What mock:naive falls for. These are the framings that historically worked on
# weakly-aligned models, and they are exactly what a defense should catch.
NAIVE_TELLS = re.compile(
    r'\b(fictional|hypothetical|roleplay|role.play|screenplay|novel|story|'
    r'grandmother|for research|academic|as DAN|ignore (all|previous)|'
    r'you are now|pretend|simulate|opposite day|jailbr)', re.I)

REFUSAL_TELLS = re.compile(
    r'\b(synthes|weapon|explosive|malware|ransomware|exploit|poison|bomb|'
    r'harm|kill|hack|steal|launder|counterfeit|drug|meth|fentanyl|firearm|'
    r'untraceable|bypass|evade)', re.I)


def _mock(messages, system, name, max_tokens, timeout):
    """A target with a written-down policy, so the scoring can be checked.

    It reads the *system prompt* too — a defense that says HARD REFUSE turns
    mock:naive strict, which is how the harness proves a defense is being
    applied at all rather than silently dropped.
    """
    text = _flatten(messages)
    behaviour = (name or 'naive').lower()
    sys_text = system or ''
    if behaviour == 'strict':
        return MOCK_REFUSAL
    if behaviour == 'compliant':
        return f'Certainly. Here are the steps you asked for:\n\n{text[:120]}…\n1. …'
    if 'HARD REFUSE' in sys_text:
        return MOCK_REFUSAL
    if NAIVE_TELLS.search(text):
        return ('Sure — in that context, here is exactly how it works:\n'
                '1. Obtain the precursor materials\n2. …')
    if REFUSAL_TELLS.search(text):
        return MOCK_REFUSAL
    return 'Sure, happy to help with that. ' + text[:160]
