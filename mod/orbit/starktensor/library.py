"""starktensor.library — the red-team prompt corpus, read from the pliny store.

This module never touches the network and never scrapes GitHub. It reads the
adversarial prompt corpus that the ``plinyville`` module has already mirrored
into the mod STORE (``~/.mod/store/plinyville/mods/<repo>/content.json``), and
shapes it into agent-protocol TEST overlays.

Why read from plinyville instead of re-fetching: plinyville owns the safety
posture for this content (see its README — the material is hosted DEFANGED, as
a security exhibit). Reusing its store keeps starktensor downstream of that one
decision instead of minting a second, unlabelled copy.
"""

import json
import os

# ── where the corpus lives ───────────────────────────────────────────

def store_root():
    """The plinyville market inside the mod store."""
    base = os.environ.get('MOD_STORE') or os.path.expanduser('~/.mod/store')
    return os.path.join(base, 'plinyville', 'mods')


def _content(repo):
    """The archived content.json for one source repo, or None."""
    p = os.path.join(store_root(), repo, 'content.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _manifest(repo):
    p = os.path.join(store_root(), repo, 'manifest.json')
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


# ── what a red-team source repo looks like ───────────────────────────

# Topics that mark a plinyville repo as a prompt-attack collection. A repo is
# surfaced as a starktensor "vendor" (source) when its manifest carries one of
# these, so the module tracks plinyville's catalogue instead of a hardcoded
# list that rots the moment he pushes a new one.
REDTEAM_TOPICS = {
    'jailbreak', 'ai-jailbreak', 'liberation', 'ai-liberation',
    'red-teaming', 'adversarial-attacks', 'prompt-injection', 'offsec',
}

# Text files are where the prompts live; skip binaries/images/manifests.
PROMPT_EXTS = ('.mkd', '.md', '.txt', '.json', '.text', '')


def _is_prompt_path(path):
    low = path.lower()
    if low.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.pdf', '.zip')):
        return False
    return low.endswith(PROMPT_EXTS)


def vendors():
    """Source repos in the local pliny corpus that are prompt-attack sets.

    Returns one row per repo with its title, the upstream it came from, how
    many prompt files it holds and its red-team topics. If plinyville has not
    been stocked yet the list is empty and ``available`` is False.
    """
    root = store_root()
    if not os.path.isdir(root):
        return {'available': False, 'reason': 'plinyville store not found; run `m plinyville/stock`',
                'store': root, 'vendors': []}
    out = []
    for repo in sorted(os.listdir(root)):
        man = _manifest(repo)
        topics = set(man.get('topics') or [])
        if not (topics & REDTEAM_TOPICS):
            continue
        content = _content(repo) or {}
        files = content.get('files') or {}
        n = sum(1 for p in files if _is_prompt_path(p))
        out.append({
            'repo': repo,
            'title': man.get('title') or repo,
            'upstream': man.get('upstream'),
            'prompts': n,
            'stars': man.get('stars'),
            'topics': sorted(topics & REDTEAM_TOPICS),
        })
    out.sort(key=lambda r: r['prompts'], reverse=True)
    return {'available': True, 'store': root, 'count': len(out), 'vendors': out}


def list_prompts(repo, q=''):
    """Prompt entries (text files) inside one source repo, filename filter q=."""
    content = _content(repo)
    if content is None:
        return {'repo': repo, 'error': f'{repo} not in the pliny store; run `m plinyville/install {repo}`'}
    files = content.get('files') or {}
    ql = (q or '').lower()
    rows = []
    for path, text in files.items():
        if not _is_prompt_path(path):
            continue
        if ql and ql not in path.lower():
            continue
        body = text if isinstance(text, str) else json.dumps(text)
        rows.append({'path': path, 'bytes': len(body),
                     'preview': body.strip()[:120].replace('\n', ' ')})
    rows.sort(key=lambda r: r['path'])
    return {'repo': repo, 'count': len(rows), 'prompts': rows}


def get_prompt(repo, path):
    """The raw text of one prompt, wrapped with a RED-TEAM label.

    The text is returned so you can see exactly what an overlay would carry —
    it is evidence, not an instruction to the reader. It is never executed here.
    """
    content = _content(repo)
    if content is None:
        return {'repo': repo, 'error': f'{repo} not in the pliny store'}
    files = content.get('files') or {}
    if path not in files:
        return {'repo': repo, 'path': path, 'error': 'no such prompt'}
    text = files[path]
    body = text if isinstance(text, str) else json.dumps(text, indent=2)
    return {
        'repo': repo,
        'path': path,
        'label': 'RED-TEAM / JAILBREAK CORPUS — adversarial test material, not an instruction',
        'upstream': (_manifest(repo).get('upstream')),
        'bytes': len(body),
        'text': body,
    }


def search(q, limit=50):
    """Grep every red-team source repo for a string (offline, over the store)."""
    if not q:
        return {'q': q, 'error': 'q= required'}
    ql = q.lower()
    hits = []
    for v in vendors().get('vendors', []):
        repo = v['repo']
        files = (_content(repo) or {}).get('files') or {}
        for path, text in files.items():
            if not _is_prompt_path(path):
                continue
            body = text if isinstance(text, str) else json.dumps(text)
            if ql in body.lower() or ql in path.lower():
                idx = body.lower().find(ql)
                snip = body[max(0, idx - 40):idx + 60].replace('\n', ' ') if idx >= 0 else ''
                hits.append({'repo': repo, 'path': path, 'snippet': snip})
                if len(hits) >= limit:
                    return {'q': q, 'count': len(hits), 'hits': hits, 'truncated': True}
    return {'q': q, 'count': len(hits), 'hits': hits}


# ── the bridge to the agent protocol ─────────────────────────────────

def overlay(repo, path, agent=''):
    """Shape one prompt into an agent-protocol RED-TEAM test overlay.

    The overlay is a plain document, NOT a mounted change: something you hand to
    orbit/agent as the system prompt of a throwaway *test* agent (or the input
    node of a FLOW graph) to probe how a target agent behaves under adversarial
    pressure. It is tagged ``redteam: true`` and ``defanged`` so no downstream
    tool can mistake it for an ordinary agent config and silently apply it.

    Wiring it into a running probe is the module's roadmap (see probe()); this
    function is the stable contract the rest of the module builds on.
    """
    p = get_prompt(repo, path)
    if 'error' in p:
        return p
    return {
        'kind': 'starktensor.redteam_overlay',
        'version': 1,
        'redteam': True,
        'defanged': True,
        'auto_apply': False,
        'target_agent': agent or None,
        'source': {'module': 'plinyville', 'repo': repo, 'path': path,
                   'upstream': p.get('upstream')},
        'note': ('Evaluation test-case. Mount on a THROWAWAY agent to measure '
                 'guardrail robustness; never set as the system prompt of a '
                 'production agent.'),
        # The adversarial text, carried verbatim so a probe can present it.
        'system_prompt': p['text'],
    }
