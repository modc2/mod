#!/usr/bin/env python3
"""plinyville mcp — the elder-plinius mirror as MCP tools.

A dozen read-mostly tools over the same `Ville` core the API and the app use, so an
agent, a browser and the CLI can never see different answers. The point is that
an agent can *read* the corpus — list the repos, walk a repo's tree, pull a file
or a README, grep across everything — instead of scraping GitHub itself.

`pv_exhibit` is the one that matters for safety review: it reports what the
hosted plinyworld page would do if it were live (the clipboard payload, the
typosquatted domains, the mechanism) by reading the preserved-but-unrun script.
No tool here serves or executes that payload.

There are three servers in here, all built from the same handlers:

    handle()      the core corpus tools (pv_*)         POST /mcp
    handle_all()  those + one tool per repo            POST /mcp/all
    handle_repo() one repo, no `name` argument         POST /m/<repo>/mcp

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --all               # stdio, every repo as its own tool
    python3 mcp.py --http --port 50592 # Streamable HTTP — POST /mcp

The module's own API server mounts `handle()` at /mcp too, so the tools and the
REST routes can never drift apart.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

from market import Market  # noqa: E402
from plinyville import VERSION, GitHubError, Ville  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    "A live mirror of github.com/elder-plinius — the prompt-injection and "
    "jailbreak-research corpus (L1B3RT4S, CL4R1T4S, GLOSSOPETRAE …) — plus one "
    "hosted red-team exhibit. Start with pv_repos to see what exists (it is "
    "cached; pass refresh=true to re-pull), then pv_readme for what a repo "
    "claims to be, pv_tree to walk it and pv_file to read one file. pv_search "
    "greps the code across every repo when you do not know which one holds the "
    "thing. pv_exhibit describes the clipboard-hijack (pastejacking) PoC this "
    "module hosts in DEFANGED form: what string the live page writes to a "
    "visitor's clipboard and which typosquatted domains it appends — read from "
    "the preserved payload, which is never served as executable script. "
    "pv_status says how fresh all of this is: a cron job rescans once a day, and "
    "the tool reports the last scan, what changed and the module's own CID. "
    "pv_types says what sort of thing each repo IS (jailbreak set, leaked "
    "system prompt, red-team tool, browser app, writing, empty) with the "
    "evidence for each call, and every listing tool takes `type=` to show one "
    "sort only — that is the tool to start from when the question is 'which of "
    "these are jailbreaks'. "
    "pv_market lists the MARKET — every repo as its own mod with an app, an api "
    "and an MCP server at /m/<repo>; pv_install archives one into the store so "
    "you can read it offline. pv_run is the ARCADE: which of these repos are "
    "browser apps rather than prose, where each starts, and what its scripts "
    "reach for before you run it.  Read-only except pv_update and pv_install, which "
    "only write this module's own mirror and store."
)


def _ville(a):
    return Ville(state_path=(a or {}).pop('state_path', None))


def _market(a):
    return Market(state_path=(a or {}).pop('state_path', None))


def _kinds(a=None):
    """The taxonomy, over the same market this server reads."""
    from kinds import Kinds
    from run import Runner
    mk = _market(a if a is not None else {})
    return Kinds(mk, Runner(mk))


# ── the scope fence ──
#
# A server started with PLINYVILLE_SCOPE=<repo,repo,…> may only read those
# repos. chat.py sets it when a question is filtered to a type ("jailbreaks
# only"), and the point is that the limit lives HERE, on the tool, rather than
# in a sentence in a system prompt an agent can reason its way past.

SCOPE = tuple(x for x in re.split(r'[,\s]+', os.environ.get('PLINYVILLE_SCOPE', ''))
              if x)
_SCOPE_LOWER = {s.lower() for s in SCOPE}


def _scoped(name):
    """`name`, or a refusal that says what the scope is."""
    if SCOPE and str(name).lower() not in _SCOPE_LOWER:
        raise ValueError(
            f'{name} is out of scope for this server. In scope ({len(SCOPE)}): '
            + ', '.join(SCOPE[:40]) + (' …' if len(SCOPE) > 40 else ''))
    return name


def _scope_keep(rows, key='name', out=None):
    """Drop the rows outside the scope — and, when `out` is given, say how many
    went. A filtered-to-nothing list looks exactly like a corpus that never had
    the thing in it, and an agent told "0 results" will report that the repo
    does not exist. It does; it is simply not this question's to read."""
    if not SCOPE:
        return rows
    kept = [r for r in rows
            if str((r.get(key) if isinstance(r, dict) else r) or '').lower()
            in _SCOPE_LOWER]
    if out is not None and len(kept) != len(rows):
        out['hidden_by_scope'] = len(rows) - len(kept)
        out['scope_note'] = ('%d more matched but are outside this connection\'s '
                             'scope — they exist, they are simply not yours to read '
                             'here. Say "out of scope", never "not found".'
                             % (len(rows) - len(kept)))
    return kept


def _typed(rows, want, key='name'):
    """Keep the rows whose repo carries every type in `want` (AND)."""
    if not want:
        return rows
    k = _kinds()
    names = k.filter([str((r.get(key) if isinstance(r, dict) else r) or '')
                      for r in rows], want)
    keep = {n.lower() for n in names}
    return [r for r in rows
            if str((r.get(key) if isinstance(r, dict) else r) or '').lower() in keep]


# ── tools ──

def _t_info(a):
    out = _ville(a).info()
    if SCOPE:
        out['scope'] = list(SCOPE)
        out['scope_note'] = ('this connection is fenced to these repos; every '
                             'other one answers "out of scope"')
    return out


def _t_repos(a):
    out = _ville(a).repos(search=a.get('search'), n=a.get('limit') or 500,
                          refresh=bool(a.get('refresh')))
    rows = _typed(_scope_keep(out.get('repos') or [], out=out), a.get('type'))
    out['repos'] = rows
    out['count'] = len(rows)
    if a.get('type'):
        out['type'] = a['type']
    if SCOPE:
        out['scope'] = list(SCOPE)
    return out


def _t_repo(a):
    return _ville(a).repo(_scoped(a['name']))


# Reads go through the MARKET, not straight to GitHub: when a repo has been
# archived into the store the answer comes from disk, and only an unarchived
# repo (or a file outside the 60-file bundle) spends a REST call. Anonymous
# GitHub is 60 requests an hour for the whole box, and an agent three files
# into a question is exactly who used to hit that wall.

def _t_readme(a):
    return _market(a).repo_readme(_scoped(a['name']))


def _t_tree(a):
    name, ref = _scoped(a['name']), a.get('ref')
    if ref:                       # a specific commit is not what the archive holds
        return _ville(a).tree(name, path=a.get('path') or '', ref=ref)
    return _market(a).repo_tree(name, path=a.get('path') or '')


def _t_file(a):
    return _market(a).repo_file(_scoped(a['name']), a['path'], ref=a.get('ref'))


def _search_the_store(mk, query, n):
    """Grep every archived repo. What we fall back to when GitHub says 403 —
    a code search that stops working at the rate wall is a code search that
    stops working exactly when somebody is using it."""
    hits = []
    for name in sorted(mk.installed()):
        if SCOPE and name.lower() not in _SCOPE_LOWER:
            continue
        try:
            got = mk.repo_search(name, query, n=n)
        except (ValueError, GitHubError):
            continue
        for h in got.get('hits') or []:
            hits.append({'repo': name, 'path': h['path'], 'line': h.get('line'),
                         'text': h.get('text')})
            if len(hits) >= n:
                return hits
    return hits


def _t_search(a):
    mk = _market(dict(a))
    try:
        out = _ville(a).search(a['query'], n=a.get('limit') or 50)
    except GitHubError as e:
        if e.status not in (401, 403, 429):
            raise
        hits = _search_the_store(mk, a['query'], int(a.get('limit') or 50))
        out = {'query': a['query'], 'hits': hits, 'count': len(hits),
               'source': 'store', 'github_error': str(e),
               'note': "GitHub code search is rate-limited right now, so this is a "
                       "grep over the repos archived into the store instead — it "
                       "covers their text files, not every branch of every repo."}
    if SCOPE or a.get('type'):
        hits = _typed(_scope_keep(out.get('hits') or out.get('results') or [],
                                  'repo', out=out), a.get('type'), 'repo')
        key = 'hits' if 'hits' in out else 'results'
        out[key] = hits
        out['count'] = len(hits)
        if SCOPE:
            out['scope'] = list(SCOPE)
    return out


def _t_types(a):
    """The taxonomy — or, with `repo`, why one repo is filed where it is."""
    return _kinds(a).catalog(repo=a.get('repo') or a.get('name'),
                             refresh=bool(a.get('refresh')))


def _t_exhibit(a):
    return _ville(a).exhibit()


def _t_update(a):
    return _ville(a).update()


def _t_status(a):
    from scan import Scanner
    v = _ville(a)
    return Scanner(v, Market(v)).status()


def _t_market(a):
    from run import Runner
    mk = _market(a)
    cat = _kinds().join(Runner(mk).join(
        mk.catalog(search=a.get('search'), refresh=bool(a.get('refresh')))))
    mods = _typed(_scope_keep(cat.get('mods') or [], out=cat), a.get('type'))
    cat['mods'] = mods
    cat['count'] = len(mods)
    return cat


def _t_run(a):
    """Which of these repos are apps, and what one of them is before you run it."""
    from run import Runner
    r = Runner(_market(a))
    if a.get('name'):
        return r.manifest(_scoped(a['name']), clone=True)
    cat = r.catalog(refresh=bool(a.get('refresh')))
    mods = _typed(_scope_keep(cat.get('mods') or [], 'repo', out=cat),
                  a.get('type'), 'repo')
    cat['mods'] = mods
    cat['runnable'] = len(mods)
    return cat


def _archive(mk, name, refresh=False):
    """Archive one repo into the store. Cloning first: git is not charged against
    the 60 requests an hour the REST API gives an anonymous caller, so a tool call
    that has to archive a repo before it can grep it does not die at a rate wall.
    The REST archiver stays as the fallback for a host with no git."""
    try:
        from clone import Cloner
        return Cloner(mk).archive(name, refresh=bool(refresh))
    except Exception as e:                        # noqa: BLE001 — no git, no disk, …
        last = e
    try:
        return mk.install(name, refresh=bool(refresh))
    except GitHubError as e:
        raise GitHubError(f'{e} (cloning it failed first: {last})', e.status) from None


def _t_install(a):
    return _archive(_market(a), _scoped(a['name']), refresh=bool(a.get('refresh')))


def _str(desc, **kw):
    return dict({'type': 'string', 'description': desc}, **kw)


TOOLS = {
    'pv_info': {
        'description': 'What this mirror is, which GitHub user it tracks, how many '
                       'repos are cached and when it last updated.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_info,
    },
    'pv_repos': {
        'description': "Every public repo under elder-plinius, newest push first, from "
                       "the local cache. `search` filters on name, description and "
                       "topics; `refresh` re-pulls from GitHub first. This is the map — "
                       "start here.",
        'inputSchema': {'type': 'object', 'properties': {
            'search': _str('filter on name, description or topic'),
            'limit': {'type': 'integer', 'description': 'max repos to return (default 500)'},
            'refresh': {'type': 'boolean', 'description': 're-pull from GitHub first'},
            'type': _str('keep only repos of this type — jailbreak, system-prompt, redteam, app, tool, writing, exhibit, empty. Comma-separate for AND ("jailbreak,app" = a jailbreak you can run). pv_types lists them'),
        }},
        'handler': _t_repos,
    },
    'pv_repo': {
        'description': "One repo's live details straight from GitHub — stars, language, "
                       'topics, homepage, default branch, last push.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name, e.g. L1B3RT4S'),
        }, 'required': ['name']},
        'handler': _t_repo,
    },
    'pv_readme': {
        'description': "A repo's README as decoded markdown — what the repo says it is, "
                       'in its own words.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name'),
        }, 'required': ['name']},
        'handler': _t_readme,
    },
    'pv_tree': {
        'description': 'List one directory inside a repo (path omitted = the root). '
                       'Directories sort first. Walk this before guessing file paths.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name'),
            'path': _str('directory inside the repo (default: root)'),
            'ref': _str('branch, tag or commit sha (default: the default branch)'),
        }, 'required': ['name']},
        'handler': _t_tree,
    },
    'pv_file': {
        'description': 'Read one file out of a repo as text. Binary files and anything '
                       'over 400 KB are described rather than returned.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name'),
            'path': _str('file path inside the repo, e.g. ANTHROPIC.mkd'),
            'ref': _str('branch, tag or commit sha'),
        }, 'required': ['name', 'path']},
        'handler': _t_file,
    },
    'pv_search': {
        'description': "GitHub code search scoped to elder-plinius's repos. Use it when "
                       'you know the string but not which repo holds it. Returns '
                       'repo/path/url hits, not file contents — follow up with pv_file.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('code search query'),
            'limit': {'type': 'integer', 'description': 'max hits (default 50, cap 100)'},
        }, 'required': ['query']},
        'handler': _t_search,
    },
    'pv_exhibit': {
        'description': 'What the hosted plinyworld page actually does: the clipboard-'
                       'hijack (pastejacking) mechanism, the exact string the live PoC '
                       "writes to a visitor's clipboard, and the typosquatted phishing "
                       'domains it appends — read out of the preserved upstream script, '
                       'which this module never serves as executable JavaScript. Use '
                       'this to study or review the exhibit without visiting it.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_exhibit,
    },
    'pv_update': {
        'description': 'Re-pull the repo list from GitHub and refresh the pinned '
                       'plinyworld upstream snapshot. The only tool here with a side '
                       'effect, and it only writes this mirror\'s own cache.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_update,
    },
    'pv_types': {
        'description': 'What sort of thing each repo IS: jailbreak prompt sets, leaked '
                       'system prompts, red-team tooling, browser apps, tools, writing, '
                       'the defanged exhibit, empty repos. Returns the taxonomy with '
                       'live counts; with `repo`, returns why that one repo is filed '
                       'where it is — the word that matched, and whether it was found '
                       'in the name, the description, a topic, the README or a '
                       'filename. Pass a type id to pv_repos, pv_market or pv_run as '
                       '`type` to see only that sort. Start here when the question is '
                       '"which of these are jailbreaks".',
        'inputSchema': {'type': 'object', 'properties': {
            'repo': _str('one repo — its types and the evidence for each'),
            'refresh': {'type': 'boolean', 'description': 're-classify from scratch'},
        }},
        'handler': _t_types,
    },
    'pv_status': {
        'description': 'How fresh this mirror is. A cron job scans once a day — this '
                       'reports when it last ran, whether that makes the corpus up to '
                       'date or stale, which repos were added, dropped or moved, and '
                       "this module's own registered CID. Check it before trusting a "
                       'listing to be current.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'pv_market': {
        'description': 'The plinyville MARKET: every elder-plinius repo as its own mod, '
                       'each with an app, an api and an MCP server at /m/<repo>. Shows '
                       'which mods have been archived into the store and their content '
                       "ids. `search` filters; `refresh` re-pulls the repo list first.",
        'inputSchema': {'type': 'object', 'properties': {
            'search': _str('filter on name, description or topic'),
            'type': _str('keep only repos of this type — jailbreak, system-prompt, redteam, app, tool, writing, exhibit, empty. Comma-separate for AND ("jailbreak,app" = a jailbreak you can run). pv_types lists them'),
            'refresh': {'type': 'boolean', 'description': 're-pull the repo list first'},
        }},
        'handler': _t_market,
    },
    'pv_run': {
        'description': 'The ARCADE: which elder-plinius repos are browser apps rather '
                       'than prose, and where each one starts. With `name`, the full '
                       "picture for one repo — whether it runs, its entry points, the "
                       'URL that serves it sandboxed, and an audit of what its own '
                       'scripts reach for (clipboard, network hosts, storage, a key '
                       'left in the source). A repo that cannot run says why: source '
                       'that needs a build, a front end for a local service, a corpus '
                       'of prompts. Nothing executes on the server — these are pages '
                       'served to a browser with an opaque origin.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name, e.g. ST3GG — omit for the whole arcade'),
            'type': _str('keep only repos of this type — jailbreak, system-prompt, redteam, app, tool, writing, exhibit, empty. Comma-separate for AND ("jailbreak,app" = a jailbreak you can run). pv_types lists them'),
            'refresh': {'type': 'boolean', 'description': 're-scan the clones first'},
        }},
        'handler': _t_run,
    },
    'pv_install': {
        'description': 'Archive one repo into the store mod as a market mod: its recursive '
                       'tree, README and readable files, content-addressed. Idempotent — '
                       'pass refresh=true to re-archive. This is what turns a repo into a '
                       'mod you can then read offline at /m/<repo>.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('repo name, e.g. L1B3RT4S'),
            'refresh': {'type': 'boolean', 'description': 're-archive even if present'},
        }, 'required': ['name']},
        'handler': _t_install,
    },
}


# ── the ALL server: every elder-plinius repo as its own tool, one endpoint ───
#
# The core registry above is *about* the corpus (list repos, then pass a name).
# The ALL server turns the corpus inside out: one tool per repo, named after the
# repo, so a client's tool list *is* the market. An agent that wants L1B3RT4S
# calls pv_l1b3rt4s — no lookup step, and the tool description carries what the
# repo is. Same handlers underneath, so nothing can drift.

ALL_INSTRUCTIONS = (
    "Every repo under github.com/elder-plinius as its own tool, plus the pv_* "
    "tools that describe the corpus as a whole. Each pv_<repo> tool reads that "
    "one repo: op=readme (default) for what it is, op=tree to list a directory "
    "(path=), op=file to read one file (path=), op=search to grep it (query=), "
    "op=info for its manifest, op=run for whether it is an app you can run. Reads come from the local store archive when the "
    "repo has been installed and live from GitHub otherwise; op=search archives "
    "the repo first if it has to. Start from a repo tool if you know the name — "
    "pv_repos/pv_market list them, pv_search greps every repo at once via "
    "GitHub code search. pv_exhibit describes the DEFANGED clipboard-hijack PoC "
    "this module hosts, read out of a payload that is never served as script."
)

REPO_OPS = ('readme', 'tree', 'file', 'search', 'info', 'install', 'run')


def _repo_handler(mk, name):
    """One repo's whole surface behind a single `op` switch."""
    def run(a):
        op = str(a.get('op') or 'readme').strip().lower()
        if op in ('', 'default'):
            op = 'readme'
        if op == 'readme':
            return mk.repo_readme(name)
        if op == 'info':
            return mk.repo_info(name)
        if op == 'tree':
            return mk.repo_tree(name, path=a.get('path') or '')
        if op == 'file':
            if not a.get('path'):
                raise ValueError('op=file needs path= — use op=tree to find one')
            return mk.repo_file(name, a['path'], ref=a.get('ref'))
        if op == 'search':
            if not a.get('query'):
                raise ValueError('op=search needs query=')
            if not mk.is_installed(name):
                _archive(mk, name)        # grep is offline: archive on demand
            return mk.repo_search(name, a['query'], n=a.get('limit') or 50)
        if op == 'install':
            return _archive(mk, name, refresh=bool(a.get('refresh')))
        if op == 'run':
            from run import Runner
            return Runner(mk).manifest(name, clone=True)
        raise ValueError(f'unknown op {op!r} — one of {", ".join(REPO_OPS)}')
    return run


def _repo_tool(mk, repo, installed):
    """The tool entry for one repo in the ALL registry."""
    name = repo['name']
    desc = (repo.get('description') or '').strip()
    bits = [f'elder-plinius/{name}']
    if repo.get('language'):
        bits.append(repo['language'])
    if repo.get('stars'):
        bits.append(f"★{repo['stars']}")
    bits.append('archived offline' if installed else 'live from github')
    return {
        'description': (f'{desc + " — " if desc else ""}{", ".join(bits)}. '
                        'op=readme (default) | tree (path=) | file (path=) | '
                        'search (query=) | info | install | run (can it run in a '
                        'browser, from where, and what it touches).'),
        'inputSchema': {'type': 'object', 'properties': {
            'op': {'type': 'string', 'enum': list(REPO_OPS),
                   'description': 'what to read (default: readme)'},
            'path': _str('directory for op=tree, file path for op=file'),
            'query': _str('substring to grep for, with op=search'),
            'limit': {'type': 'integer', 'description': 'max search hits (default 50)'},
            'ref': _str('branch, tag or commit sha, with op=file'),
            'refresh': {'type': 'boolean', 'description': 're-archive, with op=install'},
        }},
        'handler': _repo_handler(mk, name),
    }


def all_tools(refresh=False, installed_only=False, state_path=None) -> dict:
    """The aggregate registry: the core pv_* tools + one tool per repo.

    Built from the cached repo list, so listing tools costs no network call."""
    mk = Market(state_path=state_path)
    try:
        repos = mk.ville.repos(n=500, refresh=refresh)['repos']
    except GitHubError:
        repos = []                       # rate-limited with a cold cache: core only
    inst = mk.installed()
    reg = dict(TOOLS)
    for r in sorted(repos, key=lambda x: str(x.get('name', '')).lower()):
        name = r.get('name')
        if not name or (installed_only and name not in inst):
            continue
        key = f'pv_{mk._slug(name)}'
        if key in reg:                   # never shadow a core tool
            key += '_repo'
        reg[key] = _repo_tool(mk, r, name in inst)
    return reg


def handle_all(body, refresh=False, state_path=None):
    """JSON-RPC for the ALL server — one endpoint, every repo."""
    return handle(body, tools=all_tools(refresh=refresh, state_path=state_path),
                  server_name='plinyville-all', instructions=ALL_INSTRUCTIONS)


# ── per-repo MCP servers: one MCP backend per market mod ─────────────────────
#
# /m/<repo>/mcp exposes the same corpus scoped to a single repo, so each mod is
# a first-class MCP server whose tools need no `name` argument.

def repo_tools(name: str) -> dict:
    """The tool registry for one mod's MCP server, bound to `name`."""
    mk = Market()
    slug = mk._slug(name)

    def info(a):
        return mk.repo_info(name)

    def readme(a):
        return mk.repo_readme(name)

    def tree(a):
        return mk.repo_tree(name, path=a.get('path') or '')

    def file(a):
        return mk.repo_file(name, a['path'], ref=a.get('ref'))

    def search(a):
        return mk.repo_search(name, a['query'], n=a.get('limit') or 50)

    def install(a):
        return _archive(mk, name, refresh=bool(a.get('refresh')))

    def run_(a):
        from run import Runner
        return Runner(mk).manifest(name, clone=True)

    return {
        f'{slug}_run': {
            'description': f'Can {name} run in a browser, and what does it reach for? '
                           'Its entry points, the sandboxed URL that serves it, and an '
                           'audit of the clipboard/network/storage its own scripts use '
                           '— or why it cannot run (source that needs a build, a corpus '
                           'of prompts, a front end for a local service).',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': run_,
        },
        f'{slug}_info': {
            'description': f'What the {name} mod is — its manifest, wiring and whether '
                           'it is archived into the store.',
            'inputSchema': {'type': 'object', 'properties': {}}, 'handler': info},
        f'{slug}_readme': {
            'description': f'{name}\'s README as markdown (archived copy if installed, '
                           'else live from GitHub).',
            'inputSchema': {'type': 'object', 'properties': {}}, 'handler': readme},
        f'{slug}_tree': {
            'description': f'List a directory inside {name} (path omitted = root).',
            'inputSchema': {'type': 'object', 'properties': {
                'path': _str('directory inside the repo (default: root)')}},
            'handler': tree},
        f'{slug}_file': {
            'description': f'Read one file out of {name} as text.',
            'inputSchema': {'type': 'object', 'properties': {
                'path': _str('file path inside the repo'),
                'ref': _str('branch, tag or commit sha')}, 'required': ['path']},
            'handler': file},
        f'{slug}_search': {
            'description': f'Grep {name}\'s archived files offline (install it first).',
            'inputSchema': {'type': 'object', 'properties': {
                'query': _str('substring to grep for'),
                'limit': {'type': 'integer', 'description': 'max hits (default 50)'}},
                'required': ['query']},
            'handler': search},
        f'{slug}_install': {
            'description': f'Archive {name} into the store so it can be read offline.',
            'inputSchema': {'type': 'object', 'properties': {
                'refresh': {'type': 'boolean', 'description': 're-archive even if present'}}},
            'handler': install},
    }


# ── JSON-RPC 2.0 ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, tools=None):
    """Run one tool. Raises with a readable message."""
    reg = tools if tools is not None else TOOLS
    tool = reg.get(name)
    if not tool:
        raise ValueError(f'unknown tool: {name} — have {", ".join(reg)}')
    return tool['handler'](dict(args or {}))


def _call(id_, params, tools=None):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args, tools=tools)
    except KeyError as e:
        # A tool failure is a *successful* JSON-RPC response carrying isError,
        # per the MCP spec, so the model reads the hint and retries.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: missing argument {e}'}],
                             'isError': True})
    except (GitHubError, ValueError) as e:
        return _result(id_, {'content': [{'type': 'text', 'text': f'{name}: {e}'}],
                             'isError': True})
    except Exception as e:                      # noqa: BLE001
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, tools=None, server_name='plinyville', instructions=INSTRUCTIONS):
    """One JSON-RPC message in, one response out (None for notifications).

    `tools` defaults to the module-wide registry; pass a per-repo registry (from
    `repo_tools`) to serve one market mod's MCP backend from the same code."""
    reg = tools if tools is not None else TOOLS
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': server_name, 'version': version()},
            'instructions': instructions,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list(reg)})
    if method == 'tools/call':
        return _call(id_, params, tools=reg)
    return _error(id_, -32601, f'method not found: {method}')


def handle_repo(name, body):
    """JSON-RPC for one market mod's MCP server, scoped to `name`."""
    return handle(body, tools=repo_tools(name), server_name=f'plinyville/{name}',
                  instructions=(f'The {name} mod: elder-plinius/{name}, archived into '
                                'the plinyville store as a self-contained MCP server. '
                                'Its tools read this one repo — info, readme, tree, file '
                                'and an offline grep. install first if search is empty.'))


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or VERSION
    except Exception:                           # noqa: BLE001
        return VERSION


def tool_list(tools=None):
    reg = tools if tools is not None else TOOLS
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in reg.items()]


# ── transports ──

def serve_stdio(all_repos=False):
    run = handle_all if all_repos else handle
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:                       # noqa: BLE001
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = run(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PLINYVILLE_API_PORT', 50592)))
    else:
        serve_stdio(all_repos='--all' in argv)
