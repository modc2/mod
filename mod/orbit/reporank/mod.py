"""
reporank — scores a repo out of 100, on several dimensions, and says what to fix.

Point it at anything: a module name in the orbit (`agent`), a local path, a
GitHub repo or git URL (cloned on demand), or a localfs CID (restored on
demand). It reads the tree, collects evidence, and hands that evidence to a
panel of voters.

A voter is one ballot on one dimension. Three kinds, all in one registry:

  static  deterministic scoring straight off the evidence — no model, no key,
          always available; this is the floor every dimension falls back to
  agent   the claude agent (the `agent` module) reads the evidence and the code
          and returns a scored ballot with findings and suggestions
  ap      an external Agent Protocol v1 agent — create task, drive steps, mine
          the ballot out of its output; a third party sits on the panel

Dimension score is the confidence-weighted mean of its agent/ap ballots, and
the static score when none of them came back — so a box with no API key still
ranks, and drift between the model and the deterministic floor is reported
rather than hidden. The overall score is the weighted mean across dimensions.

The module is also an Agent Protocol v1 server (/ap/v1/agent/tasks): create a
task with a repo reference as its input and each step runs one voter, so any
Agent Protocol client can drive a ranking and read the artifacts.

Auth is the fleet's: signed tokens from the shared auth module (mod protocol)
and HMAC session tokens under ~/.mod/reporank/server.secret (the build module's
scheme), against one ACL — reads are open, writes need a grant, the host's
owner of record is an owner here too.

CLI:
    m reporank                               # info + protocol card
    m reporank/rank agent                    # rank a module in the orbit
    m reporank/rank modc2/mod                # …a GitHub repo
    m reporank/rank QmXk…                    # …a localfs CID
    m reporank/rank ~/code/thing llm=0       # deterministic only, no model
    m reporank/facts agent                   # just the evidence
    m reporank/suggestions agent n=10        # just the fixes, ranked
    m reporank/compare agent,git,claude      # leaderboard over several
    m reporank/board                         # everything ranked so far
    m reporank/voters                        # the panel
    m reporank/add_voter id=sec2 dimension=security kind=agent model=…
    m reporank/grant 0xADDR role=write       # access
    m reporank/token                         # mint a token for the app/API
    m reporank/serve                         # app + API + /ap/v1 on :50530
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

import mod as m

APP_PORT = 50530

HOME = '~/.mod/reporank'
ACCESS = f'{HOME}/access.json'          # owner + per-address grants
OWNER = f'{HOME}/owner.json'            # who owns this box, if pinned for reporank
HOST_OWNER = '~/.mod/claude/owner.json'  # …else the host's owner of record
SECRET = f'{HOME}/server.secret'        # HMAC key for session tokens (0600)
VOTERS = f'{HOME}/voters.json'          # custom voters + enable/disable overrides
RANKINGS = f'{HOME}/rankings.json'      # cache, keyed by (repo, revision, panel)
AP_TASKS = f'{HOME}/ap_tasks.json'      # Agent Protocol task store
CLONES = f'{HOME}/repos'                # where remote repos get cloned
CIDS = f'{HOME}/cids'                   # where CIDs get materialised

TOKEN_TTL = 3600                        # auth-module token freshness window
SESSION_TTL = 7 * 24 * 3600             # HMAC session token lifetime
AGENT_MOD = 'agent'                     # the module the agent voters run on
AGENT_MODEL = 'anthropic/claude-sonnet-4.5'   # only if the agent has no default
BALLOT_TOKENS = 1400                    # max_tokens per ballot
SAMPLE_BYTES = 28_000                   # code handed to a voter, per ballot
FILE_SAMPLE = 6_000                     # …per file inside that
MAX_FILES = 20_000                      # walk cap
READ_FILES = 250                        # files opened for quality/security scans
READ_BYTES = 400_000                    # …and the byte budget across them
CLONE_DEPTH = 200                       # shallow, but deep enough to judge cadence
AP_MAX_STEPS = 24                       # cap on steps an AP task will run

SKIP_DIRS = {'.git', 'node_modules', 'vendor', 'target', '.next', 'dist', 'build',
             '__pycache__', '.venv', 'venv', '.mypy_cache', '.pytest_cache', '.tox',
             'site-packages', '.cargo', 'coverage', '.turbo', '.gradle', 'Pods'}
CODE_EXT = {'.py', '.rs', '.ts', '.tsx', '.js', '.jsx', '.go', '.rb', '.java', '.c',
            '.h', '.cpp', '.hpp', '.cs', '.php', '.swift', '.kt', '.sh', '.sol', '.lua'}
DOC_EXT = {'.md', '.mdx', '.rst', '.txt', '.adoc'}
GH_RE = re.compile(r'^(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')
CID_RE = re.compile(r'^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$')
URL_RE = re.compile(r'^(https?|git|ssh)://|^git@')

# Every dimension: what it is worth, and what a voter is being asked to judge.
# Weights sum to 100 so a dimension's weight reads as its share of the score.
DIMENSIONS = {
    'docs': {
        'title': 'Documentation', 'weight': 15,
        'brief': 'Can a stranger install, run and extend this repo from what is written '
                 'down? README that says what it is and how to run it, examples that '
                 'would actually work, reference for the API/CLI surface, docs that '
                 'match the code rather than an older version of it.'},
    'structure': {
        'title': 'Structure', 'weight': 10,
        'brief': 'Is the layout legible — one obvious entry point, coherent boundaries '
                 'between the parts, no dead trees or duplicated copies of the same '
                 'thing, files at a size a person can hold in their head.'},
    'tests': {
        'title': 'Tests', 'weight': 15,
        'brief': 'Is behaviour pinned by tests that would fail if it broke? Judge '
                 'whether the risky paths are covered, not whether the count is high; '
                 'fixtures, determinism, and CI that actually runs them.'},
    'quality': {
        'title': 'Code quality', 'weight': 15,
        'brief': 'Readability, naming, error handling, duplication, dead code, and '
                 'whether the comments say why rather than restating what. Penalise '
                 'swallowed exceptions and functions doing five things.'},
    'security': {
        'title': 'Security', 'weight': 15,
        'brief': 'Committed secrets, unsafe eval/exec/shell interpolation, writes with '
                 'no authorization, unvalidated input reaching a subprocess or a query, '
                 'dependency exposure. Judge the actual risk, not the vocabulary.'},
    'activity': {
        'title': 'Activity', 'weight': 10,
        'brief': 'Is it alive — commit cadence, how recent the last real change is, '
                 'whether the history reads as sustained work or a single dump.'},
    'deps': {
        'title': 'Dependencies', 'weight': 10,
        'brief': 'Are dependencies declared, pinned and lockfiled? Penalise vendored '
                 'trees checked into source, a heavyweight pull for one function, and '
                 'anything undeclared that the code imports anyway.'},
    'protocol': {
        'title': 'Mod protocol', 'weight': 10,
        'brief': 'Conformance to the mod protocol: config.json carrying name, a real '
                 'description, fns and ports; an anchor exposing forward() and info(); '
                 'a null call that returns info; declared dependencies; a schema CID. '
                 'A repo that is not a mod scores on general convention instead.'},
}

SEVERITY = {'critical': 4.0, 'high': 3.0, 'medium': 2.0, 'low': 1.0}
GRADES = ((90, 'A'), (80, 'B'), (70, 'C'), (60, 'D'), (0, 'F'))

SECRET_PATTERNS = [
    ('aws-access-key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('github-token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b')),
    ('openai-key', re.compile(r'\bsk-[A-Za-z0-9]{32,}\b')),
    ('anthropic-key', re.compile(r'\bsk-ant-[A-Za-z0-9\-_]{20,}\b')),
    ('private-key-block', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('slack-token', re.compile(r'\bxox[abps]-[A-Za-z0-9-]{10,}\b')),
    ('hardcoded-secret', re.compile(
        r'(?i)\b(api_?key|secret|password|passwd|token|private_?key)\s*[:=]\s*'
        r'["\'][A-Za-z0-9/+_\-]{16,}["\']')),
]
UNSAFE_PATTERNS = [
    ('eval', re.compile(r'\beval\s*\(')),
    ('exec', re.compile(r'\bexec\s*\(')),
    ('shell-true', re.compile(r'shell\s*=\s*True')),
    ('os-system', re.compile(r'\bos\.system\s*\(')),
    ('pickle-load', re.compile(r'\bpickle\.loads?\s*\(')),
    ('yaml-unsafe', re.compile(r'yaml\.load\s*\((?![^)]*Safe)')),
    ('sql-format', re.compile(r'(?i)(select|insert|update|delete)\s+.*["\']\s*[%+]\s*\w')),
    ('verify-off', re.compile(r'verify\s*=\s*False')),
]

BALLOT_SCHEMA = ('{"score": <0-100 integer>, "confidence": <0.0-1.0>, '
                 '"findings": ["<what you actually saw, one line each>"], '
                 '"suggestions": [{"suggestion": "<one concrete change>", '
                 '"severity": "critical|high|medium|low", '
                 '"effort": "small|medium|large", "evidence": "<path or fact>"}]}')

BALLOT_PROMPT = """You are one voter on a panel scoring a code repository. You score \
exactly ONE dimension and nothing else.

DIMENSION: {title}
WHAT YOU JUDGE: {brief}

REPOSITORY: {name} ({kind}: {source})

EVIDENCE (collected deterministically from the tree):
{facts}

A DETERMINISTIC SCORER PUT THIS DIMENSION AT {static}/100. It only counts things; \
it cannot read code. Agree with it or overrule it, but do not anchor on it.

CODE:
{sample}

Score {title} out of 100 for THIS repo as it stands. Calibrate: 50 is an ordinary \
working repo, 80 is genuinely good, 95+ is exemplary and rare. Be specific about what \
you saw — a suggestion that would apply to any repo is worthless. Suggest only changes \
that would raise THIS score, at most 5, most valuable first.

Reply with JSON only, no prose and no code fence:
{schema}"""


class Mod:
    description = ('reporank — ranks any repo (orbit module name, git URL, path or CID) '
                   'out of 100 across eight dimensions with a modular panel of voters: '
                   'deterministic scorers, claude-agent voters, and external Agent '
                   'Protocol agents; returns a ranked list of concrete fixes and serves '
                   'the whole thing over Agent Protocol v1')

    protocol = 'reporank/1.0'
    ROLES = ('write', 'admin')

    def __init__(self, path: str = None):
        self.home = m.abspath(HOME)
        self.access_path = m.abspath(ACCESS)
        self.owner_path = m.abspath(OWNER)
        self.host_owner_path = m.abspath(HOST_OWNER)
        self.secret_path = m.abspath(SECRET)
        self.voters_path = m.abspath(VOTERS)
        self.rankings_path = m.abspath(RANKINGS)
        self.tasks_path = m.abspath(AP_TASKS)
        self.clones = m.abspath(CLONES)
        self.cids = m.abspath(CIDS)
        self._default = path

    # ── resolving what to rank ───────────────────────────────────────────
    #
    # four kinds of reference land here and leave as a directory on disk

    def resolve(self, repo: str = None) -> dict:
        """Turn a reference — an orbit module name, a path, a git URL or
        owner/repo, or a localfs CID — into a checkout on disk. Remote repos
        are cloned once and reused; `pull=1` on rank refreshes them."""
        ref = str(repo or self._default or '.').strip()
        if CID_RE.match(ref):
            return self._from_cid(ref)
        if URL_RE.search(ref) or (GH_RE.match(ref) and '/' in ref and not os.path.exists(
                m.abspath(ref))):
            return self._from_git(ref)
        path = m.abspath(ref)
        if os.path.isdir(path):
            return self._local(ref, path, 'path')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                mod_path = m.dirpath(ref)
        except Exception:
            mod_path = None
        if mod_path and os.path.isdir(mod_path):
            return self._local(ref, mod_path, 'mod')
        raise ValueError(f'{ref!r} is not a module in the orbit, a path, a git URL or a CID')

    def _local(self, ref, path, kind) -> dict:
        return dict({'source': ref, 'kind': kind, 'name': os.path.basename(path.rstrip('/')),
                     'path': path}, **self._git_meta(path))

    def _from_git(self, ref: str) -> dict:
        """Clone (or reuse) a remote repo. Shallow — but deep enough that the
        activity dimension has a history to look at."""
        mm = GH_RE.match(ref)
        url = ref if URL_RE.search(ref) else f'https://github.com/{mm.group(1)}/{mm.group(2)}.git'
        slug = re.sub(r'[^\w.-]+', '_', re.sub(r'^https?://|\.git$', '', url)).strip('_')
        dest = os.path.join(self.clones, slug)
        os.makedirs(self.clones, exist_ok=True)
        if not os.path.isdir(os.path.join(dest, '.git')):
            self._run(['git', 'clone', '--depth', str(CLONE_DEPTH), url, dest],
                      cwd=self.clones, timeout=300)
        name = os.path.basename(dest)
        return dict({'source': ref, 'kind': 'git', 'name': name, 'path': dest, 'url': url},
                    **self._git_meta(dest))

    def _from_cid(self, cid: str) -> dict:
        """Materialise a CID out of localfs. A directory CID lands as a tree; a
        single blob lands as one file in a directory of its own, which still
        ranks (badly, and the suggestions will say why)."""
        dest = os.path.join(self.cids, cid)
        if not os.path.isdir(dest):
            os.makedirs(dest, exist_ok=True)
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('localfs')().get(cid)
            self._materialise(data, dest)
        return {'source': cid, 'kind': 'cid', 'name': cid[:12], 'path': dest, 'cid': cid}

    @staticmethod
    def _materialise(data, dest: str):
        """Write whatever localfs handed back into `dest`. A {path: content}
        map is a tree; anything else is one blob."""
        if isinstance(data, dict) and data and all(isinstance(v, (str, bytes)) for v in data.values()):
            for rel, content in data.items():
                out = os.path.join(dest, str(rel).lstrip('/'))
                os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
                mode = 'wb' if isinstance(content, bytes) else 'w'
                with open(out, mode) as f:
                    f.write(content)
            return
        blob = data if isinstance(data, (str, bytes)) else json.dumps(data, indent=2, default=str)
        mode = 'wb' if isinstance(blob, bytes) else 'w'
        with open(os.path.join(dest, 'content'), mode) as f:
            f.write(blob)

    def _git_meta(self, path: str) -> dict:
        """History for a checkout — or for a subtree of one. An orbit module is
        a directory inside the mod repo rather than a repo of its own, so the
        enclosing checkout is found by walking up, and `subpath` scopes every
        history question to the module's own files."""
        root = path
        while not os.path.isdir(os.path.join(root, '.git')):
            parent = os.path.dirname(root)
            if parent == root:
                return {}
            root = parent
        out = {'git_root': root}
        rel = os.path.relpath(path, root)
        if rel != '.':
            out['subpath'] = rel
        for key, args in (('commit', ['git', 'rev-parse', '--short', 'HEAD']),
                          ('branch', ['git', 'rev-parse', '--abbrev-ref', 'HEAD']),
                          ('url', ['git', 'remote', 'get-url', 'origin'])):
            val = self._run(args, cwd=root, check=False).strip()
            if val:
                out[key] = val
        if 'subpath' in out:
            # HEAD moves when any module in the repo changes; what identifies
            # THIS module's tree is the last commit that touched it
            own = self._run(['git', 'log', '-n', '1', '--format=%h', '--', out['subpath']],
                            cwd=root, check=False).strip()
            if own:
                out['commit'] = own
        return out

    @staticmethod
    def _run(args, cwd=None, timeout=60, check=True) -> str:
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '',
               'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new'}
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        if check and r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or f'{args[0]} failed').strip()[:400])
        return r.stdout

    # ── evidence ─────────────────────────────────────────────────────────

    def facts(self, repo: str = None) -> dict:
        """Everything measurable about a repo, before anyone judges it. This is
        the shared context every voter gets, so a ballot can be argued with."""
        r = repo if isinstance(repo, dict) else self.resolve(repo)
        files = self._walk(r['path'])
        texts = self._read(r['path'], files)
        return {
            'repo': {k: v for k, v in r.items() if k != 'path'},
            'size': self._size_facts(files),
            'docs': self._doc_facts(r['path'], files, texts),
            'tests': self._test_facts(files, texts),
            'quality': self._quality_facts(files, texts),
            'security': self._security_facts(files, texts),
            'activity': self._activity_facts(r),
            'deps': self._dep_facts(r['path'], files),
            'protocol': self._protocol_facts(r['path'], files, texts),
        }

    def _walk(self, root: str) -> list:
        """Every file worth looking at, as (relpath, size, ext). Build output,
        vendored trees and virtualenvs are not the repo's own work, so they are
        skipped — counting them would flatter or punish the wrong thing."""
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith('.egg-info')]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                out.append((rel, size, os.path.splitext(name)[1].lower()))
                if len(out) >= MAX_FILES:
                    return out
        return out

    def _read(self, root: str, files: list) -> dict:
        """Open the source files that matter, biggest first, inside a byte
        budget — the scans below need text, and a repo can be arbitrarily big."""
        code = sorted([f for f in files if f[2] in CODE_EXT and f[1] < 400_000],
                      key=lambda f: -f[1])[:READ_FILES]
        texts, spent = {}, 0
        for rel, size, _ in code:
            if spent >= READ_BYTES:
                break
            try:
                with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as f:
                    body = f.read(80_000)
            except OSError:
                continue
            texts[rel] = body
            spent += len(body)
        return texts

    @staticmethod
    def _size_facts(files) -> dict:
        by_ext = {}
        for _, size, ext in files:
            by_ext[ext or '(none)'] = by_ext.get(ext or '(none)', 0) + 1
        code = [f for f in files if f[2] in CODE_EXT]
        return {'files': len(files), 'code_files': len(code),
                'bytes': sum(f[1] for f in files),
                'by_ext': dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:12])}

    def _doc_facts(self, root, files, texts) -> dict:
        names = {f[0].lower() for f in files}
        readme = next((f[0] for f in files
                       if f[0].lower() in ('readme.md', 'readme.rst', 'readme.txt', 'readme')), None)
        body = ''
        if readme:
            try:
                with open(os.path.join(root, readme), encoding='utf-8', errors='replace') as f:
                    body = f.read(200_000)
            except OSError:
                pass
        docs = [f[0] for f in files if f[2] in DOC_EXT and f[0].lower() != (readme or '').lower()]
        return {'readme': bool(readme), 'readme_lines': body.count('\n'),
                'readme_sections': len(re.findall(r'^#{1,3} ', body, re.M)),
                'readme_examples': len(re.findall(r'^```', body, re.M)) // 2,
                'doc_files': len(docs), 'doc_dir': any(f[0].startswith('docs/') for f in files),
                'skill': 'skill.md' in names, 'license': any(n.startswith('license') for n in names),
                'changelog': any(n.startswith('changelog') for n in names),
                'docstring_files': sum(1 for t in texts.values() if t.lstrip().startswith(('"""', "'''")))}

    @staticmethod
    def _test_facts(files, texts) -> dict:
        tests = [f[0] for f in files if f[2] in CODE_EXT and (
            'test' in f[0].lower().replace('latest', '') or f[0].lower().startswith('spec/'))]
        ci = [f[0] for f in files if f[0].startswith('.github/workflows/')
              or f[0] in ('.gitlab-ci.yml', 'Jenkinsfile', '.circleci/config.yml')]
        cases = sum(len(re.findall(r'^\s*(?:def test_|it\(|test\(|#\[test\])', t, re.M))
                    for rel, t in texts.items() if rel in set(tests))
        code = [f for f in files if f[2] in CODE_EXT]
        return {'test_files': len(tests), 'test_cases': cases, 'ci': ci,
                'ratio': round(len(tests) / max(1, len(code)), 3),
                'examples': [t for t in tests[:10]]}

    @staticmethod
    def _quality_facts(files, texts) -> dict:
        locs = {rel: t.count('\n') + 1 for rel, t in texts.items()}
        long_files = sorted(locs.items(), key=lambda kv: -kv[1])[:5]
        joined = '\n'.join(texts.values())
        comment_lines = len(re.findall(r'^\s*(#|//|/\*|\*)', joined, re.M))
        blank = len(re.findall(r'^\s*$', joined, re.M))
        total = joined.count('\n') + 1
        return {'loc_sampled': total, 'files_sampled': len(texts),
                'avg_file_loc': round(total / max(1, len(texts))),
                'longest_files': [{'file': f, 'loc': n} for f, n in long_files],
                'comment_ratio': round(comment_lines / max(1, total - blank), 3),
                'todo': len(re.findall(r'(?i)\b(todo|fixme|hack|xxx)\b', joined)),
                'bare_except': len(re.findall(r'except\s*:|except Exception:\s*\n\s*pass', joined)),
                'print_debug': len(re.findall(r'^\s*(?:console\.log|print)\(', joined, re.M))}

    @staticmethod
    def _security_facts(files, texts) -> dict:
        names = {f[0].lower() for f in files}
        secrets, unsafe = [], []
        for rel, body in texts.items():
            for kind, pat in SECRET_PATTERNS:
                mm = pat.search(body)
                if mm:
                    secrets.append({'file': rel, 'kind': kind,
                                    'line': body[:mm.start()].count('\n') + 1})
            for kind, pat in UNSAFE_PATTERNS:
                hits = len(pat.findall(body))
                if hits:
                    unsafe.append({'file': rel, 'pattern': kind, 'hits': hits})
        committed_env = sorted(n for n in names
                               if n == '.env' or n.endswith('/.env') or n.endswith('.pem'))
        return {'suspect_secrets': secrets[:20], 'secret_files': len(secrets),
                'unsafe': sorted(unsafe, key=lambda u: -u['hits'])[:20],
                'gitignore': '.gitignore' in names, 'committed_env': committed_env[:10]}

    def _activity_facts(self, r) -> dict:
        """Cadence for this tree. Scoped to the module's own subpath when it
        lives inside a bigger repo — otherwise every module in the mod repo
        would score identically on activity."""
        root = r.get('git_root')
        if not root:
            return {'git': False}
        log = self._run(self._log_args('%at%x09%an', 500, r), cwd=root, check=False)
        rows = [l.split('\t') for l in log.strip().splitlines() if '\t' in l]
        if not rows:
            return {'git': True, 'commits': 0}
        stamps = sorted(int(r[0]) for r in rows)
        now = time.time()
        span_days = max(1, (stamps[-1] - stamps[0]) / 86400)
        return {'git': True, 'commits': len(rows), 'authors': len({row[1] for row in rows}),
                'scope': r.get('subpath') or '(whole repo)',
                'days_since_last': round((now - stamps[-1]) / 86400, 1),
                'span_days': round(span_days, 1),
                'commits_30d': sum(1 for s in stamps if now - s < 30 * 86400),
                'commits_90d': sum(1 for s in stamps if now - s < 90 * 86400),
                'per_week': round(len(rows) / (span_days / 7), 2),
                'shallow': os.path.exists(os.path.join(root, '.git', 'shallow'))}

    @staticmethod
    def _log_args(fmt: str, n: int, r: dict, extra=()) -> list:
        """`git log` for this tree — the pathspec must stay last, after every
        option, so callers pass extra flags rather than appending them."""
        args = ['git', 'log', f'--pretty={fmt}', '-n', str(n), *extra]
        if r.get('subpath'):
            args += ['--', r['subpath']]
        return args

    @staticmethod
    def _dep_facts(root, files) -> dict:
        names = {f[0] for f in files}
        manifests = [n for n in ('requirements.txt', 'pyproject.toml', 'setup.py',
                                 'package.json', 'Cargo.toml', 'go.mod', 'Gemfile')
                     if n in names]
        locks = [n for n in ('poetry.lock', 'package-lock.json', 'yarn.lock', 'Cargo.lock',
                             'go.sum', 'Gemfile.lock', 'uv.lock') if n in names]
        declared = pinned = 0
        if 'requirements.txt' in names:
            try:
                with open(os.path.join(root, 'requirements.txt'), encoding='utf-8',
                          errors='replace') as f:
                    reqs = [l.strip() for l in f if l.strip() and not l.startswith('#')]
                declared, pinned = len(reqs), sum(1 for r in reqs if '==' in r)
            except OSError:
                pass
        if 'package.json' in names:
            try:
                with open(os.path.join(root, 'package.json'), encoding='utf-8',
                          errors='replace') as f:
                    pkg = json.load(f)
                deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
                declared += len(deps)
                pinned += sum(1 for v in deps.values() if re.match(r'^\d', str(v)))
            except (OSError, ValueError):
                pass
        # a mod-protocol module declares its fleet dependencies in config.json
        # rather than a language manifest — that IS its declaration
        mod_deps = []
        if 'config.json' in names:
            try:
                with open(os.path.join(root, 'config.json'), encoding='utf-8',
                          errors='replace') as f:
                    cfg = json.load(f)
                mod_deps = cfg.get('dependencies') or cfg.get('deps') or []
                if mod_deps:
                    manifests = manifests + ['config.json']
            except (OSError, ValueError):
                pass
        vendored = sorted({f[0].split('/')[0] for f in files
                           if f[0].split('/')[0] in ('vendor', 'third_party', 'node_modules')})
        return {'manifests': manifests, 'lockfiles': locks, 'declared': declared,
                'pinned': pinned, 'mod_deps': mod_deps, 'vendored': vendored}

    @staticmethod
    def _protocol_facts(root, files, texts) -> dict:
        names = {f[0] for f in files}
        cfg_path = os.path.join(root, 'config.json')
        cfg = {}
        if 'config.json' in names:
            try:
                with open(cfg_path, encoding='utf-8', errors='replace') as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                cfg = {}
        anchor = next((n for n in ('mod.py', 'src/mod.py', 'agent.py', 'mod.rs', 'mod.ts')
                       if n in names), None)
        body = texts.get(anchor, '')
        if anchor and not body:
            try:
                with open(os.path.join(root, anchor), encoding='utf-8', errors='replace') as f:
                    body = f.read(200_000)
            except OSError:
                body = ''
        return {'is_mod': bool(cfg) or bool(anchor),
                'config': bool(cfg), 'anchor': anchor,
                'name': cfg.get('name'), 'version': cfg.get('version'),
                'description_len': len(str(cfg.get('description') or '')),
                'fns': len(cfg.get('fns') or []), 'schema_cid': bool(cfg.get('schema')),
                'deps': cfg.get('dependencies') or cfg.get('deps') or [],
                'port': cfg.get('port') or cfg.get('app_port'),
                'has_forward': bool(re.search(r'def forward\b|fn forward\b', body)),
                'has_info': bool(re.search(r'def info\b|fn info\b', body)),
                'has_serve': bool(re.search(r'def serve\b|fn serve\b', body))}

    # ── static voters ────────────────────────────────────────────────────
    #
    # one per dimension, deterministic, no model. These are the floor: they
    # always run, they cost nothing, and a dimension whose agent ballots all
    # failed is carried by its static score rather than dropped.

    @staticmethod
    def _band(value, marks) -> int:
        """Score by the highest threshold `value` clears. `marks` is descending
        (threshold, score)."""
        for threshold, score in marks:
            if value >= threshold:
                return score
        return marks[-1][1]

    def _static_docs(self, f) -> tuple:
        d, tips = f['docs'], []
        if not d['readme']:
            # a docs/ tree is not a README — the entry point is still missing —
            # but it is not nothing, so it does not score as an undocumented repo
            written = d['doc_files'] + (4 if d['doc_dir'] else 0)
            return min(35, 5 + 4 * written), [
                self._tip('critical', 'small', 'Add a README that says what this is, how to '
                          'install it and how to run it — it is the first thing every reader '
                          'and every catalog looks for',
                          f"no README; {d['doc_files']} other doc file(s) in the tree")]
        score = self._band(d['readme_lines'], ((150, 60), (60, 48), (25, 36), (5, 20), (0, 8)))
        score += min(12, d['readme_examples'] * 4)
        score += min(10, d['readme_sections'] * 2)
        score += 8 if d['doc_dir'] or d['doc_files'] >= 3 else 0
        score += 5 if d['license'] else 0
        score += 5 if d['skill'] else 0
        if d['readme_examples'] == 0:
            tips.append(self._tip('high', 'small', 'Put a runnable example in the README — '
                                  'install, one command, expected output',
                                  'no fenced code block in the README'))
        if not d['license']:
            tips.append(self._tip('medium', 'small', 'Add a LICENSE — without one nobody can '
                                  'legally reuse this', 'no LICENSE file'))
        if d['readme_lines'] < 40:
            tips.append(self._tip('high', 'small',
                                  f"Expand the README ({d['readme_lines']} lines) to cover "
                                  'setup, usage and the surface it exposes', 'README is thin'))
        return min(100, score), tips

    def _static_structure(self, f) -> tuple:
        q, s, tips = f['quality'], f['size'], []
        score = 60
        longest = q['longest_files'][0]['loc'] if q['longest_files'] else 0
        if longest > 2000:
            score -= 20
            tips.append(self._tip('medium', 'large',
                                  f"Split {q['longest_files'][0]['file']} ({longest} lines) — "
                                  'it is too big to review as one unit',
                                  f'largest sampled file is {longest} lines'))
        elif longest > 1000:
            score -= 8
        if q['avg_file_loc'] and q['avg_file_loc'] < 400:
            score += 12
        if s['code_files'] == 0:
            score -= 30
            tips.append(self._tip('high', 'medium', 'No source files found outside build and '
                                  'vendor directories', 'nothing to rank'))
        if f['protocol']['anchor']:
            score += 10
        if f['docs']['doc_dir']:
            score += 6
        return max(0, min(100, score)), tips

    def _static_tests(self, f) -> tuple:
        t, tips = f['tests'], []
        if t['test_files'] == 0:
            return 5, [self._tip('critical', 'medium', 'Add tests — there is nothing pinning '
                                 'current behaviour', 'no test files found')]
        score = self._band(t['ratio'], ((0.25, 55), (0.15, 46), (0.08, 36), (0.02, 24), (0, 14)))
        score += min(25, t['test_cases'] // 2)
        if t['ci']:
            score += 15
        else:
            tips.append(self._tip('medium', 'small', 'Run the tests in CI — a test nobody runs '
                                  'is a comment', 'no CI workflow found'))
        if t['ratio'] < 0.1:
            tips.append(self._tip('high', 'medium',
                                  f"Raise test coverage: {t['test_files']} test files against "
                                  f"{f['size']['code_files']} source files",
                                  f"test/source ratio {t['ratio']}"))
        return min(100, score), tips

    def _static_quality(self, f) -> tuple:
        q, tips = f['quality'], []
        score = 70
        if q['comment_ratio'] < 0.03:
            score -= 10
            tips.append(self._tip('low', 'medium', 'Comment the non-obvious decisions — the '
                                  'code explains what, not why',
                                  f"comment ratio {q['comment_ratio']}"))
        elif q['comment_ratio'] > 0.35:
            score -= 5
        if q['bare_except']:
            score -= min(20, q['bare_except'] * 4)
            tips.append(self._tip('high', 'small',
                                  f"Replace {q['bare_except']} bare/swallowed exception "
                                  'handlers with the specific errors you expect',
                                  'bare except or except-pass in the source'))
        if q['todo'] > 30:
            score -= 8
            tips.append(self._tip('low', 'medium', f"Work off or file the {q['todo']} "
                                  'TODO/FIXME markers', 'TODO/FIXME count'))
        if q['print_debug'] > 40:
            score -= 6
            tips.append(self._tip('low', 'small',
                                  f"Route the {q['print_debug']} print/console.log calls "
                                  'through a logger', 'debug prints in the source'))
        if q['avg_file_loc'] > 800:
            score -= 10
        return max(0, min(100, score)), tips

    def _static_security(self, f) -> tuple:
        s, tips = f['security'], []
        score = 85
        if s['suspect_secrets']:
            score -= min(60, 25 * len(s['suspect_secrets']))
            first = s['suspect_secrets'][0]
            tips.append(self._tip('critical', 'small',
                                  'Rotate and remove the committed credentials, then move them '
                                  'to ~/.mod/<module>/ off-tree',
                                  f"{first['kind']} at {first['file']}:{first['line']}"))
        if s['committed_env']:
            score -= 20
            tips.append(self._tip('critical', 'small',
                                  f"Untrack {', '.join(s['committed_env'][:3])} and add it to "
                                  '.gitignore', 'env/key file committed'))
        risky = [u for u in s['unsafe'] if u['pattern'] in
                 ('eval', 'exec', 'shell-true', 'os-system', 'sql-format', 'yaml-unsafe')]
        if risky:
            score -= min(25, 5 * len(risky))
            tips.append(self._tip('high', 'medium',
                                  f"Review {len(risky)} dynamic-execution site(s) — "
                                  f"{risky[0]['pattern']} in {risky[0]['file']}",
                                  'unsafe pattern in the source'))
        if not s['gitignore']:
            score -= 8
            tips.append(self._tip('medium', 'small', 'Add a .gitignore so build output and '
                                  'secrets cannot be committed by accident', 'no .gitignore'))
        return max(0, min(100, score)), tips

    def _static_activity(self, f) -> tuple:
        a, tips = f['activity'], []
        if not a.get('git'):
            return 20, [self._tip('medium', 'small', 'Put this under version control — there '
                                  'is no history to judge', 'no .git directory')]
        if not a.get('commits'):
            return 15, [self._tip('medium', 'small', 'Make a first commit', 'empty history')]
        score = self._band(a['days_since_last'], ((365, 10), (180, 25), (90, 40), (30, 60),
                                                  (7, 78), (0, 88)))
        score += min(8, a['authors'] * 2)
        score += 6 if a['commits_90d'] >= 10 else 0
        if a['days_since_last'] > 90:
            tips.append(self._tip('medium', 'small',
                                  f"Last commit was {a['days_since_last']} days ago — say in the "
                                  'README whether this is finished or abandoned',
                                  'stale history'))
        if a.get('shallow'):
            tips.append(self._tip('low', 'small', 'History is a shallow clone here, so cadence '
                                  'is measured over a partial window', 'shallow clone'))
        return max(0, min(100, score)), tips

    def _static_deps(self, f) -> tuple:
        d, tips = f['deps'], []
        if not d['manifests']:
            return 35, [self._tip('high', 'small', 'Declare dependencies in a manifest '
                                  '(requirements.txt, pyproject.toml, package.json…)',
                                  'no dependency manifest')]
        # a module whose only manifest is config.json declares fleet deps, not
        # packages — there is no lockfile to want, so it is not marked down
        fleet_only = d['manifests'] == ['config.json']
        score = 60 + (20 if d['lockfiles'] or fleet_only else 0)
        if d['declared']:
            ratio = d['pinned'] / d['declared']
            score += round(15 * ratio)
            if ratio < 0.5:
                tips.append(self._tip('medium', 'small',
                                      f"Pin versions: {d['pinned']} of {d['declared']} declared "
                                      'dependencies are pinned', 'unpinned dependencies'))
        if not d['lockfiles'] and not fleet_only:
            tips.append(self._tip('medium', 'small', 'Commit a lockfile so builds are '
                                  'reproducible', 'no lockfile'))
        if d['vendored']:
            score -= 10
            tips.append(self._tip('low', 'medium',
                                  f"Vendored tree(s) committed: {', '.join(d['vendored'])} — "
                                  'keep them out of source unless the build needs them',
                                  'vendored dependencies in-tree'))
        return max(0, min(100, score)), tips

    def _static_protocol(self, f) -> tuple:
        p, tips = f['protocol'], []
        if not p['is_mod']:
            # not a mod at all — judge it on the same idea (a declared surface,
            # one entry point) rather than punishing it for not being one
            return 50, [self._tip('low', 'medium', 'Not a mod-protocol module: add a '
                                  'config.json and a mod.py anchor to make it callable from '
                                  'the fleet', 'no config.json or anchor')]
        score = 20
        score += 15 if p['config'] else 0
        score += 15 if p['anchor'] else 0
        score += 10 if p['has_forward'] else 0
        score += 10 if p['has_info'] else 0
        score += 10 if p['fns'] else 0
        score += 10 if p['description_len'] >= 80 else (4 if p['description_len'] else 0)
        score += 10 if p['schema_cid'] else 0
        if not p['has_forward']:
            tips.append(self._tip('high', 'small', 'Add forward() to the anchor — a null call '
                                  'must return info', 'no forward() in the anchor'))
        if not p['fns']:
            tips.append(self._tip('medium', 'small', 'List the callable surface in '
                                  'config.json["fns"] so the fleet can discover it',
                                  'config.json declares no fns'))
        if p['description_len'] < 80:
            tips.append(self._tip('medium', 'small', 'Write a real description in config.json — '
                                  'it is what every catalog and agent reads first',
                                  f"description is {p['description_len']} chars"))
        if not p['schema_cid']:
            tips.append(self._tip('low', 'small', 'Pin a schema CID in config.json',
                                  'no schema field'))
        return max(0, min(100, score)), tips

    @staticmethod
    def _tip(severity, effort, suggestion, evidence) -> dict:
        return {'severity': severity, 'effort': effort, 'suggestion': suggestion,
                'evidence': evidence}

    def _static(self, dimension: str, facts: dict) -> tuple:
        return getattr(self, f'_static_{dimension}')(facts)

    # ── the panel ────────────────────────────────────────────────────────

    def _builtin_voters(self) -> list:
        """One agent ballot per dimension is the shipped panel. Weight 1.0 —
        a second voter on the same dimension splits the vote with this one."""
        return [{'id': f'agent.{d}', 'dimension': d, 'kind': 'agent', 'weight': 1.0,
                 'enabled': True, 'builtin': True} for d in DIMENSIONS]

    def voters(self, dimension: str = None) -> list:
        """The panel: the shipped agent voters plus whatever has been added,
        with the on/off state applied."""
        store = m.get(self.voters_path, {}) or {}
        overrides = store.get('overrides', {}) or {}
        panel = self._builtin_voters() + list(store.get('voters', []) or [])
        for v in panel:
            v.setdefault('weight', 1.0)
            v.setdefault('enabled', True)
            if v['id'] in overrides:
                v.update(overrides[v['id']])
        if dimension:
            panel = [v for v in panel if v['dimension'] == dimension]
        return panel

    def add_voter(self, id: str, dimension: str, kind: str = 'agent', weight: float = 1.0,
                  model: str = None, base: str = None, prompt: str = None,
                  headers: dict = None, steps: int = 6) -> dict:
        """Add a voter to the panel. kind=agent runs on the agent module (pass a
        `model` to pin one), kind=ap drives an external Agent Protocol agent at
        `base`. `prompt` overrides what the voter is asked; it is formatted with
        the same fields as the built-in one."""
        if dimension not in DIMENSIONS:
            raise ValueError(f'dimension must be one of {list(DIMENSIONS)}')
        if kind not in ('agent', 'ap', 'static'):
            raise ValueError('kind must be agent, ap or static')
        if kind == 'ap' and not base:
            raise ValueError('an ap voter needs base= (the Agent Protocol root URL)')
        store = m.get(self.voters_path, {}) or {}
        voters = [v for v in (store.get('voters') or []) if v['id'] != id]
        voters.append({'id': str(id), 'dimension': dimension, 'kind': kind,
                       'weight': float(weight), 'enabled': True, 'model': model,
                       'base': base, 'prompt': prompt, 'headers': headers or {},
                       'steps': int(steps), 'added_at': int(time.time())})
        store['voters'] = voters
        m.put(self.voters_path, store)
        return {'added': id, 'panel': len(self.voters())}

    def remove_voter(self, id: str) -> dict:
        """Drop a custom voter, or switch a built-in one off (they cannot be
        deleted — they come back with the code)."""
        store = m.get(self.voters_path, {}) or {}
        before = len(store.get('voters') or [])
        store['voters'] = [v for v in (store.get('voters') or []) if v['id'] != id]
        if len(store['voters']) == before:
            store.setdefault('overrides', {})[id] = {'enabled': False}
        m.put(self.voters_path, store)
        return {'removed': id, 'panel': len(self.voters())}

    def set_voter(self, id: str, enabled: bool = None, weight: float = None,
                  model: str = None) -> dict:
        """Retune a voter without rewriting it."""
        store = m.get(self.voters_path, {}) or {}
        patch = {k: v for k, v in (('enabled', enabled), ('weight', weight), ('model', model))
                 if v is not None}
        if not patch:
            raise ValueError('pass enabled=, weight= or model=')
        store.setdefault('overrides', {}).setdefault(id, {}).update(patch)
        m.put(self.voters_path, store)
        return {'voter': id, **patch}

    def dimensions(self) -> dict:
        """What is being scored, and what each dimension is worth."""
        return {k: dict(v, voters=len([x for x in self.voters(k) if x['enabled']]))
                for k, v in DIMENSIONS.items()}

    # ── running a ballot ─────────────────────────────────────────────────

    def _sample(self, r: dict, facts: dict, dimension: str, budget=SAMPLE_BYTES) -> str:
        """The code a voter reads. Which files matter depends on the dimension —
        a docs voter wants the README, a tests voter wants the tests — so the
        sample is picked per dimension inside one byte budget."""
        root = r['path']
        files = self._walk(root)
        names = {f[0]: f for f in files}
        picks = []
        if dimension == 'docs':
            picks += [f[0] for f in files if f[2] in DOC_EXT][:8]
        elif dimension == 'tests':
            picks += facts['tests']['examples']
        elif dimension == 'protocol':
            picks += [n for n in ('config.json', facts['protocol']['anchor']) if n in names]
        elif dimension == 'deps':
            picks += facts['deps']['manifests']
        elif dimension == 'security':
            picks += [s['file'] for s in facts['security']['suspect_secrets']]
            picks += [u['file'] for u in facts['security']['unsafe'][:6]]
        elif dimension == 'activity':
            log = self._run(self._log_args('%h %ad %an: %s', 40, r) + ['--date=short'],
                            cwd=r['git_root'], check=False) if r.get('git_root') else ''
            return f"=== git log ({r.get('subpath') or 'repo'}) ===\n{log}" if log else '(no history)'
        # every dimension also sees the biggest source files and the README
        picks += [n for n in ('README.md', 'readme.md') if n in names]
        picks += [f[0] for f in sorted([f for f in files if f[2] in CODE_EXT],
                                       key=lambda f: -f[1])[:8]]
        out, spent, seen = [], 0, set()
        per_file = max(800, min(FILE_SAMPLE, budget // 4))
        for rel in picks:
            if not rel or rel in seen or rel not in names or spent >= budget:
                continue
            seen.add(rel)
            try:
                with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as f:
                    body = f.read(per_file)
            except OSError:
                continue
            out.append(f'=== {rel} ===\n{body}')
            spent += len(body)
        return '\n\n'.join(out)[:budget] or '(no readable source)'

    def _prompt(self, voter, r, facts, dimension, static_score, budget=SAMPLE_BYTES) -> str:
        spec = DIMENSIONS[dimension]
        return (voter.get('prompt') or BALLOT_PROMPT).format(
            title=spec['title'], brief=spec['brief'], name=r['name'], kind=r['kind'],
            source=r['source'], static=static_score, schema=BALLOT_SCHEMA,
            facts=json.dumps({'size': facts['size'], dimension: facts[dimension]},
                             indent=1, default=str)[:max(1200, budget // 4)],
            sample=self._sample(r, facts, dimension, budget))

    def _agent(self):
        """The agent module, built once per instance and only when a ballot
        actually needs a model — info() and the static panel stay cheap."""
        if getattr(self, '_agent_mod', None) is None:
            with contextlib.redirect_stdout(io.StringIO()):
                self._agent_mod = m.mod(AGENT_MOD)()
        return self._agent_mod

    def _ballot_agent(self, voter, prompt, model=None, free=False, attempt=0) -> dict:
        ag = self._agent()
        model = model or voter.get('model') or ag.DEFAULT_MODELS.get(
            getattr(ag, '_provider', None), AGENT_MODEL)
        if free and hasattr(ag.model, 'free_models'):
            # the free tier is a grab bag — a retry moves to the next one along
            # rather than asking the same small model the same thing twice
            pool = ag.model.free_models() or [model]
            model = pool[min(attempt, len(pool) - 1)]
        with contextlib.redirect_stdout(io.StringIO()):
            text = ag.model.forward(prompt, stream=False, model=model,
                                    max_tokens=BALLOT_TOKENS, temperature=0)
        if text is None or not str(text).strip():
            raise RuntimeError(f'{model} returned nothing')
        return dict(self._parse_ballot(text), model=model)

    def _ballot_ap(self, voter, prompt) -> dict:
        """Drive an external Agent Protocol v1 agent: create the task, step it
        until it says it is done, and mine the ballot out of the transcript."""
        base = str(voter['base']).rstrip('/')
        headers = dict(voter.get('headers') or {})
        created = self._http(f'{base}/ap/v1/agent/tasks', {'input': prompt}, headers)
        task_id = created.get('task_id') or created.get('taskId')
        if not task_id:
            raise RuntimeError(f'{base} returned no task_id')
        transcript = ''
        for _ in range(int(voter.get('steps') or 6)):
            step = self._http(f'{base}/ap/v1/agent/tasks/{task_id}/steps', {}, headers)
            for key in ('output', 'additional_output'):
                val = step.get(key)
                if isinstance(val, str):
                    transcript += val + '\n'
                elif val:
                    transcript += json.dumps(val, default=str) + '\n'
            if step.get('is_last') or step.get('status') == 'completed':
                break
        return dict(self._parse_ballot(transcript), task_id=task_id)

    @staticmethod
    def _http(url, body=None, headers=None, timeout=180) -> dict:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(url, data=data, method='POST',
                                     headers={'Content-Type': 'application/json',
                                              **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
        try:
            return json.loads(raw)
        except ValueError:
            raise RuntimeError(f'{url} returned non-JSON: {raw[:200]}')

    @staticmethod
    def _parse_ballot(text) -> dict:
        """Take a model's reply down to the ballot. Fenced, prefixed or
        trailing prose is normal; a reply with no JSON object in it is not."""
        text = text if isinstance(text, str) else json.dumps(text, default=str)
        body = re.sub(r'^```[\w]*\n?|\n?```$', '', text.strip(), flags=re.M).strip()
        start = body.find('{')
        if start < 0:
            raise ValueError(f'no ballot in the reply: {body[:200]}')
        depth, end = 0, None
        for i, ch in enumerate(body[start:], start):
            depth += (ch == '{') - (ch == '}')
            if depth == 0:
                end = i + 1
                break
        parsed = json.loads(body[start:end or len(body)])
        score = float(parsed.get('score', 0))
        tips = []
        for s in (parsed.get('suggestions') or [])[:5]:
            if isinstance(s, str):
                s = {'suggestion': s}
            tips.append({'suggestion': str(s.get('suggestion') or '')[:400],
                         'severity': str(s.get('severity') or 'medium').lower(),
                         'effort': str(s.get('effort') or 'medium').lower(),
                         'evidence': str(s.get('evidence') or '')[:200]})
        return {'score': max(0.0, min(100.0, score)),
                'confidence': max(0.0, min(1.0, float(parsed.get('confidence', 0.7)))),
                'findings': [str(x)[:300] for x in (parsed.get('findings') or [])[:6]],
                'suggestions': [t for t in tips if t['suggestion']]}

    def _cast(self, voter, r, facts, static_scores, model=None, free=False) -> dict:
        """Run one voter and return its ballot, errors included. A voter that
        fails is recorded as a failed ballot rather than raising — one dead
        provider must not take the whole ranking down with it."""
        dim = voter['dimension']
        out = {'voter': voter['id'], 'dimension': dim, 'kind': voter['kind'],
               'weight': float(voter.get('weight', 1.0))}
        t0 = time.time()
        try:
            if voter['kind'] == 'static':
                score, tips = self._static(dim, facts)
                out.update(score=score, confidence=1.0, suggestions=tips, findings=[])
            elif voter['kind'] == 'ap':
                out.update(self._ballot_ap(
                    voter, self._prompt(voter, r, facts, dim, static_scores[dim])))
            else:
                # a small model handed a big prompt answers with nothing or with
                # prose; one retry on a quarter of the sample recovers most of
                # those, and costs a fraction of the first attempt
                out.update(self._retry_agent(voter, r, facts, dim, static_scores[dim],
                                             model, free))
            out['ok'] = True
        except Exception as e:
            out.update(ok=False, error=f'{type(e).__name__}: {e}'[:300], score=None,
                       confidence=0.0, suggestions=[], findings=[])
        out['seconds'] = round(time.time() - t0, 2)
        return out

    def _retry_agent(self, voter, r, facts, dim, static_score, model, free) -> dict:
        failure = None
        for attempt, budget in enumerate((SAMPLE_BYTES, SAMPLE_BYTES // 5)):
            prompt = self._prompt(voter, r, facts, dim, static_score, budget)
            try:
                ballot = self._ballot_agent(voter, prompt, model, free, attempt)
                return dict(ballot, attempts=attempt + 1) if attempt else ballot
            except Exception as e:
                failure = e
        raise failure

    # ── ranking ──────────────────────────────────────────────────────────

    def rank(self, repo: str = None, llm: bool = True, dimensions=None, voters=None,
             model: str = None, free: bool = False, fresh: bool = False,
             workers: int = 4) -> dict:
        """Score a repo out of 100. `repo` is an orbit module name, a path, a
        git URL or owner/repo, or a localfs CID.

        llm=False runs the deterministic panel only — no model, no key, no
        spend. `dimensions` and `voters` narrow the panel; `model` pins the
        model every agent voter uses; free=True runs them on the agent's free
        tier. Results are cached per (repo, revision, panel) — fresh=True
        re-runs the panel."""
        r = self.resolve(repo)
        dims = [d for d in (self._list(dimensions) or list(DIMENSIONS)) if d in DIMENSIONS]
        if not dims:
            raise ValueError(f'no known dimension in {dimensions!r}')
        panel = [v for v in self.voters() if v['enabled'] and v['dimension'] in dims
                 and (llm or v['kind'] == 'static')
                 and (not voters or v['id'] in self._list(voters))]
        key = self._cache_key(r, panel, dims, model, free)
        if not fresh:
            cached = (m.get(self.rankings_path, {}) or {}).get(key)
            if cached:
                return dict(cached, cached=True)

        facts = self.facts(r)
        static_scores, static_tips = {}, {}
        for d in dims:
            static_scores[d], static_tips[d] = self._static(d, facts)

        ballots = []
        if panel:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
                ballots = list(pool.map(
                    lambda v: self._cast(v, r, facts, static_scores, model, free), panel))

        report = self._aggregate(r, dims, facts, static_scores, static_tips, ballots)
        self._remember(key, report)
        return report

    def _aggregate(self, r, dims, facts, static_scores, static_tips, ballots) -> dict:
        """Turn ballots into a score. A dimension is the confidence-weighted
        mean of the ballots that came back; with none, the static score carries
        it — and `by` says which happened, so a number is never mistaken for a
        model's opinion when no model ran."""
        out_dims, suggestions = {}, []
        for d in dims:
            cast = [b for b in ballots if b['dimension'] == d and b.get('ok')]
            weights = [b['weight'] * max(0.05, b['confidence']) for b in cast]
            if cast and sum(weights) > 0:
                score = sum(b['score'] * w for b, w in zip(cast, weights)) / sum(weights)
                by = 'panel'
            else:
                score, by = static_scores[d], 'static'
            out_dims[d] = {
                'title': DIMENSIONS[d]['title'], 'weight': DIMENSIONS[d]['weight'],
                'score': round(score, 1), 'static': static_scores[d], 'by': by,
                'drift': round(score - static_scores[d], 1),
                'ballots': [{k: v for k, v in b.items() if k != 'suggestions'}
                            for b in ballots if b['dimension'] == d],
                'findings': [f for b in cast for f in b.get('findings', [])][:8],
            }
            for tip in [t for b in cast for t in b.get('suggestions', [])] or static_tips[d]:
                suggestions.append(dict(tip, dimension=d, source='panel' if cast else 'static'))

        total_w = sum(DIMENSIONS[d]['weight'] for d in dims)
        score = sum(out_dims[d]['score'] * DIMENSIONS[d]['weight'] for d in dims) / total_w
        failed = [b for b in ballots if not b.get('ok')]
        return {
            'repo': {k: v for k, v in r.items() if k != 'path'},
            'score': round(score, 1), 'grade': next(g for t, g in GRADES if score >= t),
            'dimensions': out_dims,
            'suggestions': self._rank_suggestions(suggestions, out_dims),
            'panel': {'ballots': len(ballots), 'ok': len(ballots) - len(failed),
                      'failed': [{'voter': b['voter'], 'error': b.get('error')} for b in failed],
                      'llm': any(b['kind'] != 'static' for b in ballots)},
            'facts': facts,
            'ranked_at': int(time.time()),
        }

    @staticmethod
    def _rank_suggestions(suggestions, dims) -> list:
        """Order the fixes by what they are worth: how bad it is, how much the
        dimension counts, and how much room that dimension has left to gain."""
        seen, out = set(), []
        for s in suggestions:
            key = re.sub(r'\W+', ' ', s['suggestion'].lower()).strip()[:60]
            if key in seen:
                continue
            seen.add(key)
            d = dims[s['dimension']]
            headroom = max(0.0, (100 - d['score']) / 100)
            s = dict(s, impact=round(SEVERITY.get(s['severity'], 2.0) * d['weight'] * headroom, 1))
            out.append(s)
        return sorted(out, key=lambda s: -s['impact'])

    def suggestions(self, repo: str = None, n: int = 10, **kw) -> list:
        """Just the fixes, most valuable first."""
        return self.rank(repo, **kw)['suggestions'][:int(n)]

    def score(self, repo: str = None, **kw) -> dict:
        """Just the number."""
        rep = self.rank(repo, **kw)
        return {'repo': rep['repo']['source'], 'score': rep['score'], 'grade': rep['grade'],
                'dimensions': {d: v['score'] for d, v in rep['dimensions'].items()}}

    def compare(self, repos, **kw) -> dict:
        """Rank several repos and put them in order."""
        rows = []
        for ref in self._list(repos):
            try:
                rep = self.rank(ref, **kw)
                rows.append({'repo': ref, 'score': rep['score'], 'grade': rep['grade'],
                             'dimensions': {d: v['score'] for d, v in rep['dimensions'].items()},
                             'top_fix': (rep['suggestions'] or [{}])[0].get('suggestion')})
            except Exception as e:
                rows.append({'repo': ref, 'error': f'{type(e).__name__}: {e}'[:200]})
        ranked = sorted([r for r in rows if 'score' in r], key=lambda r: -r['score'])
        for i, row in enumerate(ranked, 1):
            row['rank'] = i
        return {'compared': len(rows), 'board': ranked,
                'failed': [r for r in rows if 'error' in r]}

    def board(self, n: int = 50) -> list:
        """Every repo ranked on this box so far, best first."""
        cache = m.get(self.rankings_path, {}) or {}
        rows = [{'repo': v['repo'].get('source'), 'name': v['repo'].get('name'),
                 'score': v['score'], 'grade': v['grade'], 'ranked_at': v['ranked_at'],
                 'llm': v['panel']['llm']} for v in cache.values() if 'score' in v]
        best = {}
        for row in sorted(rows, key=lambda r: r['ranked_at']):
            best[row['repo']] = row              # newest ranking per repo wins
        return sorted(best.values(), key=lambda r: -r['score'])[:int(n)]

    def purge(self, repo: str = None) -> dict:
        """Drop cached rankings — one repo's, or all of them."""
        cache = m.get(self.rankings_path, {}) or {}
        if repo is None:
            m.put(self.rankings_path, {})
            return {'purged': len(cache)}
        keep = {k: v for k, v in cache.items() if v.get('repo', {}).get('source') != repo}
        m.put(self.rankings_path, keep)
        return {'purged': len(cache) - len(keep), 'repo': repo}

    def _cache_key(self, r, panel, dims, model, free) -> str:
        """A ranking is only reusable for the same tree judged by the same
        panel — so the revision, the voters and the model all key it."""
        # the commit is subpath-scoped, so it moves when this module does; the
        # directory mtime catches an uncommitted edit that adds or removes a
        # file. Anything finer costs a walk — `fresh=1` is the escape hatch
        rev = f"{r.get('commit') or r.get('cid') or ''}:{int(os.path.getmtime(r['path']))}"
        sig = json.dumps({'panel': sorted(f"{v['id']}:{v.get('model') or ''}" for v in panel),
                          'dims': sorted(dims), 'model': model, 'free': bool(free)},
                         sort_keys=True)
        return f"{r['source']}@{rev}#{hashlib.sha256(sig.encode()).hexdigest()[:10]}"

    def _remember(self, key, report):
        cache = m.get(self.rankings_path, {}) or {}
        cache[key] = report
        if len(cache) > 400:
            cache = dict(sorted(cache.items(), key=lambda kv: -kv[1].get('ranked_at', 0))[:400])
        m.put(self.rankings_path, cache)

    @staticmethod
    def _list(value) -> list:
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(',') if v.strip()]
        return [str(v) for v in value]

    # ── Agent Protocol v1 (server side) ──────────────────────────────────
    #
    # a task is a ranking; each step is one voter. Any Agent Protocol client —
    # including the arena's `ap` driver — can drive the whole panel and read
    # the report back as an artifact.

    def ap_create_task(self, input: str = None, additional_input: dict = None) -> dict:
        """POST /ap/v1/agent/tasks — `input` is the repo reference."""
        opts = dict(additional_input or {})
        r = self.resolve(input or opts.get('repo'))
        dims = [d for d in (self._list(opts.get('dimensions')) or list(DIMENSIONS))
                if d in DIMENSIONS]
        panel = [v for v in self.voters() if v['enabled'] and v['dimension'] in dims
                 and (opts.get('llm', True) or v['kind'] == 'static')]
        task_id = hashlib.sha256(f"{r['source']}{time.time()}".encode()).hexdigest()[:24]
        task = {'task_id': task_id, 'input': input or opts.get('repo'),
                'additional_input': opts, 'created_at': int(time.time()),
                'repo': r, 'dims': dims, 'queue': [v['id'] for v in panel][:AP_MAX_STEPS],
                'ballots': [], 'steps': [], 'artifacts': [], 'status': 'created'}
        self._put_task(task)
        return self._task_view(task)

    def ap_step(self, task_id: str, input: str = None) -> dict:
        """POST /ap/v1/agent/tasks/{id}/steps — run the next voter. The last
        step aggregates and files the report as this task's artifact."""
        task = self._get_task(task_id)
        if task['status'] == 'completed':
            raise ValueError(f'task {task_id} is already completed')
        if not task.get('facts'):
            task['facts'] = self.facts(task['repo'])
            task['static'] = {d: self._static(d, task['facts']) for d in task['dims']}
        static_scores = {d: v[0] for d, v in task['static'].items()}

        step_no = len(task['steps']) + 1
        if task['queue']:
            vid = task['queue'].pop(0)
            voter = next((v for v in self.voters() if v['id'] == vid), None)
            ballot = (self._cast(voter, task['repo'], task['facts'], static_scores)
                      if voter else {'voter': vid, 'ok': False, 'error': 'voter is gone'})
            task['ballots'].append(ballot)
            output = json.dumps({k: v for k, v in ballot.items() if k != 'findings'},
                                default=str)
            name = f"vote:{vid}"
        else:
            ballot, output, name = None, '', 'aggregate'

        is_last = not task['queue']
        step = {'step_id': f'{task_id}-{step_no}', 'task_id': task_id, 'name': name,
                'input': input, 'output': output, 'is_last': is_last,
                'status': 'completed', 'created_at': int(time.time())}
        if is_last:
            report = self._aggregate(task['repo'], task['dims'], task['facts'], static_scores,
                                     {d: v[1] for d, v in task['static'].items()},
                                     task['ballots'])
            self._remember(self._cache_key(task['repo'], [], task['dims'], None, False), report)
            task['report'] = report
            task['status'] = 'completed'
            task['artifacts'] = [{'artifact_id': f'{task_id}-report', 'file_name': 'report.json',
                                  'relative_path': None, 'created_at': int(time.time())}]
            step['output'] = json.dumps({'score': report['score'], 'grade': report['grade'],
                                         'dimensions': {d: v['score'] for d, v in
                                                        report['dimensions'].items()},
                                         'suggestions': report['suggestions'][:5]}, default=str)
            step['additional_output'] = report
        else:
            task['status'] = 'running'
        task['steps'].append({k: v for k, v in step.items() if k != 'additional_output'})
        self._put_task(task)
        return step

    def ap_task(self, task_id: str) -> dict:
        return self._task_view(self._get_task(task_id))

    def ap_tasks(self, n: int = 50) -> dict:
        tasks = sorted((self._tasks()).values(), key=lambda t: -t['created_at'])[:int(n)]
        return {'tasks': [self._task_view(t) for t in tasks]}

    def ap_steps(self, task_id: str) -> dict:
        return {'steps': self._get_task(task_id)['steps']}

    def ap_artifacts(self, task_id: str) -> dict:
        task = self._get_task(task_id)
        return {'artifacts': task['artifacts'], 'report': task.get('report')}

    def ap_run(self, repo: str = None, **opts) -> dict:
        """Drive a whole Agent Protocol task to completion in one call — the
        same path a remote client walks, useful for testing it locally."""
        task = self.ap_create_task(repo, opts)
        for _ in range(AP_MAX_STEPS + 1):
            step = self.ap_step(task['task_id'])
            if step['is_last']:
                break
        return self.ap_artifacts(task['task_id'])

    @staticmethod
    def _task_view(task) -> dict:
        return {'task_id': task['task_id'], 'input': task['input'],
                'additional_input': task['additional_input'], 'status': task['status'],
                'artifacts': task['artifacts'], 'steps_run': len(task['steps']),
                'steps_left': len(task['queue']),
                'repo': {k: v for k, v in task['repo'].items() if k != 'path'}}

    def _tasks(self) -> dict:
        return m.get(self.tasks_path, {}) or {}

    def _get_task(self, task_id) -> dict:
        task = self._tasks().get(str(task_id))
        if not task:
            raise ValueError(f'no such task: {task_id}')
        return task

    def _put_task(self, task):
        tasks = self._tasks()
        tasks[task['task_id']] = task
        if len(tasks) > 200:
            tasks = dict(sorted(tasks.items(), key=lambda kv: -kv[1]['created_at'])[:200])
        m.put(self.tasks_path, tasks)

    # ── auth: mod-protocol tokens + build-module sessions, one ACL ───────

    def _acl(self) -> dict:
        acl = m.get(self.access_path, {}) or {}
        if not acl.get('owner'):
            acl = {'owner': m.key().address, 'grants': acl.get('grants', {})}
            m.put(self.access_path, acl)
        acl.setdefault('grants', {})
        return acl

    def _host_owner(self):
        """Whoever owns the mod host this runs on is an owner here too — every
        module records it the same way (~/.mod/<mod>/owner.json), so this reads
        its own file if the box pins one for reporank and the host console's
        otherwise. $REPORANK_OWNER overrides both."""
        env = os.environ.get('REPORANK_OWNER') or os.environ.get('MOD_OWNER')
        if env:
            return env.strip()
        for path in (self.owner_path, self.host_owner_path):
            rec = m.get(path, {}) or {}
            if rec.get('owner'):
                return str(rec['owner'])
        return None

    def access(self) -> dict:
        """Who can do what. Reads are open; changing the panel, the ACL or the
        cache needs a grant."""
        acl = self._acl()
        return {'owner': acl['owner'], 'host_owner': self._host_owner(),
                'grants': acl['grants'],
                'roles': {'write': ['add_voter', 'remove_voter', 'set_voter', 'purge'],
                          'admin': ['grant', 'revoke']},
                'auth': ["signed token from m.mod('auth') — mint one with `m reporank/token` — "
                         'or an HMAC session token from `m reporank/session`'],
                'open': bool(os.environ.get('REPORANK_ACCESS_OPEN'))}

    def grant(self, address: str, role: str = 'write') -> dict:
        if role not in self.ROLES:
            raise ValueError(f'role must be one of {self.ROLES}')
        acl = self._acl()
        acl['grants'][str(address)] = {'role': role, 'granted_at': int(time.time())}
        m.put(self.access_path, acl)
        return self.access()

    def revoke(self, address: str) -> dict:
        acl = self._acl()
        acl['grants'].pop(str(address), None)
        m.put(self.access_path, acl)
        return self.access()

    def set_owner(self, address: str, host: bool = False) -> dict:
        """Hand the module to another address (CLI/local only — not exposed over
        the HTTP API). host=True instead pins the box's owner of record."""
        if host:
            m.put(self.owner_path, {'owner': str(address)})
            return self.access()
        acl = self._acl()
        acl['owner'] = str(address)
        m.put(self.access_path, acl)
        return self.access()

    def token(self, data: dict = None) -> str:
        """Mint a signed auth-module token for this box's key — paste it into
        the app or send it as `Authorization: Bearer <token>`. A wallet signs
        its own instead: base64url of {data, time, key, signature}."""
        with contextlib.redirect_stdout(io.StringIO()):
            return m.mod('auth')().token(data or {'mod': 'reporank'})

    def _secret(self) -> bytes:
        """The HMAC key session tokens are signed with — persisted 0600 under
        ~/.mod/reporank so a restart does not sign every browser out (the build
        module's scheme, same reason)."""
        if os.path.exists(self.secret_path):
            raw = open(self.secret_path, 'rb').read()
            if len(raw) == 32:
                return raw
        os.makedirs(os.path.dirname(self.secret_path), exist_ok=True)
        os.chmod(os.path.dirname(self.secret_path), 0o700)
        secret = os.urandom(32)
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(secret)
        return secret

    def session(self, address: str = None, headers: dict = None) -> dict:
        """Exchange proof of a key for a long-lived session token. Over the API
        the proof is an auth-module Bearer token; locally it is the box's own
        key. The token is `<address>.<expiry>.<hmac>` and is accepted anywhere a
        Bearer token is."""
        if headers:
            address = self._token_address(headers) or address
            if not address:
                raise PermissionError('a session needs a signed auth token to mint from')
        address = str(address or m.key().address)
        exp = int(time.time()) + SESSION_TTL
        body = f'{address}.{exp}'
        sig = hmac.new(self._secret(), body.encode(), hashlib.sha256).hexdigest()
        return {'token': f'{body}.{sig}', 'address': address, 'expires': exp,
                'role': self._role_of(address)}

    def _session_address(self, token: str):
        parts = str(token).rsplit('.', 2)
        if len(parts) != 3:
            return None
        address, exp, sig = parts
        expected = hmac.new(self._secret(), f'{address}.{exp}'.encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected) or int(exp or 0) < time.time():
            return None
        return address

    def _role_of(self, address: str):
        """The module's owner and the host's owner both rank as owner. Compared
        case-insensitively — wallets sign in lowercase, owner records are
        checksummed."""
        who = str(address or '').lower()
        if not who:
            return None
        acl = self._acl()
        if who in {str(a or '').lower() for a in (acl['owner'], self._host_owner())}:
            return 'owner'
        return next((g.get('role') for a, g in acl['grants'].items()
                     if str(a).lower() == who), None)

    @staticmethod
    def _bearer(headers) -> str:
        raw = ((headers or {}).get('Authorization')
               or (headers or {}).get('authorization') or '')
        return raw.split('Bearer ')[-1].strip() if 'Bearer ' in raw else raw.strip()

    def _token_address(self, headers) -> str:
        """The address a token belongs to, ignoring the ACL — a session token
        first (cheap HMAC), then an auth-module signature."""
        tok = self._bearer(headers)
        if not tok:
            return None
        address = self._session_address(tok)
        if address:
            return address
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
            if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
                return None
            return data.get('key')
        except Exception:
            return None

    def _authorize(self, headers, need: str = 'write') -> dict:
        """Verify a Bearer token and enforce the ACL.
        REPORANK_ACCESS_OPEN=1 bypasses (dev only)."""
        if os.environ.get('REPORANK_ACCESS_OPEN'):
            return {'address': m.key().address, 'role': 'owner', 'open': True}
        if not self._bearer(headers):
            raise PermissionError('missing Authorization: Bearer <token> '
                                  '(mint one with `m reporank/token`)')
        address = self._token_address(headers)
        if not address:
            raise PermissionError('invalid or expired token — mint a fresh one')
        role = self._role_of(address)
        rank = {'write': 1, 'admin': 2, 'owner': 3}
        if role is None or rank[role] < rank.get(need, 1):
            raise PermissionError(f'{address} lacks {need} access — ask the owner to '
                                  f'`m reporank/grant {address}`')
        return {'address': address, 'role': role}

    def whoami(self, headers=None) -> dict:
        """Resolve a token to (address, role) — the app uses this to sign in."""
        address = self._token_address(headers or {})
        if not address:
            return {'ok': False, 'error': 'no valid token'}
        role = self._role_of(address)
        return {'ok': bool(role), 'address': address, 'role': role}

    # ── mod protocol ─────────────────────────────────────────────────────

    def forward(self, **kwargs):
        """Null call returns the card."""
        return self.card()

    def card(self) -> dict:
        """What this module is and how to reach it — the discovery surface for
        an agent that has never seen it before."""
        return {
            'protocol': self.protocol, 'name': 'reporank', 'description': self.description,
            'concepts': {
                'repo': 'anything rankable: an orbit module name, a path, a git URL or a CID',
                'dimension': 'one axis of the score, with a fixed weight out of 100',
                'voter': 'one ballot on one dimension — static, claude agent, or Agent Protocol',
                'ballot': 'score + confidence + findings + suggestions from one voter',
            },
            'dimensions': {k: v['weight'] for k, v in DIMENSIONS.items()},
            'endpoints': {
                'rank': 'GET|POST /api/rank {repo, llm?, dimensions?, model?, free?, fresh?}',
                'facts': 'GET /api/facts?repo=',
                'suggestions': 'GET /api/suggestions?repo=&n=',
                'compare': 'POST /api/compare {repos}',
                'board': 'GET /api/board',
                'voters': 'GET /api/voters · POST /api/voters/add|remove|set (write)',
                'access': 'GET /api/access · POST /api/grant|revoke (admin)',
                'agent_protocol': 'POST /ap/v1/agent/tasks · POST /ap/v1/agent/tasks/{id}/steps',
            },
            'auth': "Bearer: signed auth-module token, or an HMAC session token",
            'urls': {'app': f'http://localhost:{APP_PORT}',
                     'api': f'http://localhost:{APP_PORT}/api/info',
                     'ap': f'http://localhost:{APP_PORT}/ap/v1'},
        }

    def info(self) -> dict:
        acl = self._acl()
        panel = self.voters()
        return {
            'name': 'reporank', 'protocol': self.protocol,
            'description': self.description,
            'dimensions': {k: v['weight'] for k, v in DIMENSIONS.items()},
            'panel': {'voters': len(panel), 'enabled': len([v for v in panel if v['enabled']]),
                      'kinds': {k: len([v for v in panel if v['kind'] == k])
                                for k in ('static', 'agent', 'ap')}},
            'ranked': len(m.get(self.rankings_path, {}) or {}),
            'ap_tasks': len(self._tasks()),
            # the agent module is loaded only when a ballot needs it, so info()
            # stays a cheap health check
            'agent': {'mod': AGENT_MOD, 'loaded': getattr(self, '_agent_mod', None) is not None},
            'owner': acl['owner'], 'host_owner': self._host_owner(),
            'grants': len(acl['grants']),
            'port': APP_PORT, 'url': f'http://localhost:{APP_PORT}',
        }

    def schema(self) -> dict:
        return {
            'rank': {'repo': 'str', 'llm': 'bool?', 'dimensions': 'str?', 'voters': 'str?',
                     'model': 'str?', 'free': 'bool?', 'fresh': 'bool?'},
            'score': {'repo': 'str'}, 'facts': {'repo': 'str'},
            'suggestions': {'repo': 'str', 'n': 'int?'},
            'compare': {'repos': 'str|list'}, 'board': {'n': 'int?'},
            'voters': {'dimension': 'str?'},
            'add_voter': {'id': 'str', 'dimension': 'str', 'kind': 'str?', 'weight': 'float?',
                          'model': 'str?', 'base': 'str?', 'prompt': 'str?'},
            'remove_voter': {'id': 'str'},
            'set_voter': {'id': 'str', 'enabled': 'bool?', 'weight': 'float?', 'model': 'str?'},
            'dimensions': {}, 'purge': {'repo': 'str?'},
            'ap_create_task': {'input': 'str', 'additional_input': 'dict?'},
            'ap_step': {'task_id': 'str'}, 'ap_run': {'repo': 'str'},
            'grant': {'address': 'str', 'role': 'str?'}, 'revoke': {'address': 'str'},
            'token': {}, 'session': {'address': 'str?'}, 'access': {},
        }

    def test(self) -> dict:
        """Rank this module with the deterministic panel — no model, no spend."""
        rep = self.rank(os.path.dirname(os.path.abspath(__file__)), llm=False, fresh=True)
        assert 0 <= rep['score'] <= 100, rep['score']
        assert set(rep['dimensions']) == set(DIMENSIONS)
        return {'ok': True, 'score': rep['score'], 'grade': rep['grade'],
                'suggestions': len(rep['suggestions'])}

    # ── serving (app + API + /ap/v1 on one port) ─────────────────────────

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Serve the console (/), the JSON API (/api/*) and Agent Protocol
        (/ap/v1/*) on one port. background=True detaches and returns."""
        port = int(port)
        if background:
            self.kill(port)
            log_dir = '/tmp/reporank-mod'
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))
            env = dict(os.environ)
            env['PYTHONPATH'] = root + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', '-c',
                 f"import mod as m; m.mod('reporank')().serve(port={port}, host={host!r}, "
                 'background=False)'],
                stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            return {'running': True, 'pid': proc.pid, 'url': f'http://localhost:{port}',
                    'api': f'http://localhost:{port}/api/info',
                    'ap': f'http://localhost:{port}/ap/v1/agent/tasks',
                    'log': os.path.join(log_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer((host, port), self._make_handler())
        print(f'reporank on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=APP_PORT):
        killed = []
        pid_path = '/tmp/reporank-mod/app.pid'
        if os.path.exists(pid_path):
            try:
                os.kill(int(open(pid_path).read().strip()), 15)
                killed.append('pidfile')
            except (OSError, ValueError):
                pass
            try:
                os.remove(pid_path)
            except OSError:
                pass
        try:
            out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{int(port)} 2>/dev/null'],
                                 capture_output=True, text=True).stdout.split()
            for pid in out:
                os.kill(int(pid), 15)
                killed.append(int(pid))
        except (OSError, ValueError):
            pass
        return {'killed': killed}

    PM2_NAME = 'reporank-app'

    def worker(self, port=APP_PORT, name=None):
        """Run the app under pm2 (auto-restart, survives logout)."""
        name = name or self.PM2_NAME
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_app.py')
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        env = dict(os.environ, REPORANK_APP_PORT=str(int(port)))
        r = subprocess.run(['pm2', 'start', runner, '--name', name, '--interpreter', 'python3',
                            '--cwd', root, '--time'], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        self._wait_health(int(port))
        return {'worker': name, 'port': int(port), 'running': True}

    def stop_worker(self, name=None):
        name = name or self.PM2_NAME
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': name}

    def status(self, port=APP_PORT) -> dict:
        try:
            with urllib.request.urlopen(f'http://localhost:{int(port)}/api/info', timeout=2) as r:
                return {'running': True, **json.loads(r.read().decode())}
        except Exception as e:
            return {'running': False, 'error': str(e)[:200]}

    def _wait_health(self, port, tries=40):
        for _ in range(tries):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/api/info', timeout=1)
                return True
            except Exception:
                time.sleep(0.25)
        return False

    def _make_handler(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import urlparse, parse_qs
        rr = self

        # POST endpoint → (method, required role or None for open)
        POSTS = {
            '/api/rank': ('rank', None), '/api/compare': ('compare', None),
            '/api/facts': ('facts', None), '/api/suggestions': ('suggestions', None),
            '/api/session': ('session', None),
            '/api/voters/add': ('add_voter', 'write'),
            '/api/voters/remove': ('remove_voter', 'write'),
            '/api/voters/set': ('set_voter', 'write'),
            '/api/purge': ('purge', 'write'),
            '/api/grant': ('grant', 'admin'), '/api/revoke': ('revoke', 'admin'),
        }
        ARGS = {'rank': ('repo', 'llm', 'dimensions', 'voters', 'model', 'free', 'fresh'),
                'compare': ('repos', 'llm', 'dimensions', 'model', 'free', 'fresh'),
                'facts': ('repo',), 'suggestions': ('repo', 'n', 'llm', 'model', 'free'),
                'session': (), 'purge': ('repo',),
                'add_voter': ('id', 'dimension', 'kind', 'weight', 'model', 'base',
                              'prompt', 'headers', 'steps'),
                'remove_voter': ('id',), 'set_voter': ('id', 'enabled', 'weight', 'model'),
                'grant': ('address', 'role'), 'revoke': ('address',)}
        BOOLS = {'llm', 'free', 'fresh', 'enabled'}

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = (body if isinstance(body, bytes)
                        else json.dumps(body, default=str).encode() if ctype == 'application/json'
                        else body.encode())
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain')

            @staticmethod
            def _norm(path: str) -> str:
                """Tolerate the gateway prefix — caddy routes /reporank here."""
                for prefix in ('/reporank/_api', '/reporank/api', '/reporank'):
                    if path == prefix:
                        return '/'
                    if path.startswith(prefix + '/'):
                        rest = path[len(prefix):]
                        return ('/api' + rest[len('/_api'):] if prefix.endswith('_api')
                                else rest)
                return path

            @staticmethod
            def _coerce(name, value):
                if name in BOOLS and isinstance(value, str):
                    return value.lower() not in ('0', 'false', 'no', '')
                return value

            def _call(self, fn_name, params, role):
                if role:
                    rr._authorize(dict(self.headers), need=role)
                fn = getattr(rr, fn_name)
                allowed = ARGS.get(fn_name, ())
                kwargs = {k: self._coerce(k, v) for k, v in params.items() if k in allowed}
                if fn_name == 'session':
                    return fn(headers=dict(self.headers))
                return fn(**kwargs)

            def _guard(self, work):
                try:
                    self._send(200, work())
                except PermissionError as e:
                    self._send(401, {'error': str(e)})
                except (ValueError, KeyError) as e:
                    self._send(400, {'error': f'{type(e).__name__}: {e}'})
                except Exception as e:
                    self._send(500, {'error': f'{type(e).__name__}: {e}'})

            def do_GET(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if path in ('/', '/index.html'):
                    return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                simple = {'/api/info': lambda: rr.info(), '/api/card': lambda: rr.card(),
                          '/api/access': lambda: rr.access(),
                          '/api/dimensions': lambda: rr.dimensions(),
                          '/api/schema': lambda: rr.schema(),
                          '/api/whoami': lambda: rr.whoami(dict(self.headers)),
                          '/api/board': lambda: {'board': rr.board(int(q.get('n', 50)))},
                          '/api/voters': lambda: {'voters': rr.voters(q.get('dimension'))},
                          '/api/facts': lambda: rr.facts(q.get('repo')),
                          '/api/suggestions': lambda: {
                              'suggestions': rr.suggestions(q.get('repo'), int(q.get('n', 10)),
                                                            **({'llm': False} if q.get('llm') in
                                                               ('0', 'false') else {}))},
                          '/api/rank': lambda: self._call('rank', q, None)}
                if path in simple:
                    return self._guard(simple[path])
                if path == '/ap/v1/agent/tasks':
                    return self._guard(lambda: rr.ap_tasks(int(q.get('n', 50))))
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)(/steps|/artifacts)?$', path)
                if mm:
                    tid, tail = mm.group(1), mm.group(2)
                    return self._guard(lambda: rr.ap_steps(tid) if tail == '/steps'
                                       else rr.ap_artifacts(tid) if tail == '/artifacts'
                                       else rr.ap_task(tid))
                self._send(404, {'error': f'no route {path}'})

            def do_POST(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length).decode('utf-8', 'replace') if length else '{}'
                try:
                    body = json.loads(raw or '{}')
                except ValueError:
                    return self._send(400, {'error': 'body must be JSON'})
                if not isinstance(body, dict):
                    return self._send(400, {'error': 'body must be a JSON object'})
                if path in POSTS:
                    fn_name, role = POSTS[path]
                    return self._guard(lambda: self._call(fn_name, body, role))
                if path == '/ap/v1/agent/tasks':
                    return self._guard(lambda: rr.ap_create_task(
                        body.get('input'), body.get('additional_input')))
                mm = re.match(r'^/ap/v1/agent/tasks/([\w-]+)/steps$', path)
                if mm:
                    return self._guard(lambda: rr.ap_step(mm.group(1), body.get('input')))
                self._send(404, {'error': f'no route {path}'})

        return H


INDEX_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>reporank</title>
<style>
:root{--bg:#0b0e14;--panel:#11151f;--line:#1e2533;--fg:#c9d4e5;--dim:#6b7688;--acc:#f0883e;
 --good:#3fb950;--mid:#d29922;--bad:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
header{display:flex;gap:10px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}
h1{font-size:15px;margin:0;letter-spacing:.12em;color:var(--acc)}
.sp{flex:1}
input,button,select{background:#0e121a;color:var(--fg);border:1px solid var(--line);
 border-radius:4px;padding:7px 9px;font:inherit}
input:focus,select:focus{outline:none;border-color:var(--acc)}
button{cursor:pointer}button:hover{border-color:var(--acc);color:var(--acc)}
main{padding:16px;max-width:1100px;margin:0 auto}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row input.ref{flex:1;min-width:260px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px;margin:14px 0}
.score{font-size:52px;font-weight:700;line-height:1}
.grade{font-size:22px;color:var(--dim);margin-left:8px}
.meta{color:var(--dim);margin-top:6px}
.dim{display:grid;grid-template-columns:130px 1fr 74px;gap:10px;align-items:center;margin:7px 0}
.bar{height:9px;background:#0e121a;border:1px solid var(--line);border-radius:5px;overflow:hidden}
.bar i{display:block;height:100%}
.n{text-align:right;color:var(--dim)}
.tips{list-style:none;padding:0;margin:0}
.tips li{border-top:1px solid var(--line);padding:9px 0}
.tag{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:3px;
 padding:1px 6px;margin-right:6px;color:var(--dim)}
.ev{color:var(--dim);font-size:12px}
h2{font-size:12px;letter-spacing:.12em;color:var(--dim);margin:0 0 10px;text-transform:uppercase}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:400;font-size:11px;letter-spacing:.08em}
.err{color:var(--bad)}.muted{color:var(--dim)}
</style></head><body>
<header><h1>REPORANK</h1><span class="muted" id="who">reads are open</span><span class="sp"></span>
<input id="tok" placeholder="Bearer token (m reporank/token)" size="26"><button id="signin">SIGN IN</button></header>
<main>
<div class="row">
 <input class="ref" id="ref" placeholder="module name · owner/repo · git URL · path · CID" value="agent">
 <label class="muted"><input type="checkbox" id="llm" checked> panel</label>
 <label class="muted"><input type="checkbox" id="fresh"> fresh</label>
 <button id="go">RANK</button>
</div>
<div id="out"></div>
<div class="card"><h2>Board</h2><div id="board" class="muted">…</div></div>
<div class="card"><h2>Panel</h2><div id="panel" class="muted">…</div></div>
</main>
<script>
const $=s=>document.querySelector(s), api=(p)=>location.pathname.replace(/\/$/,'')+p;
let tok=localStorage.getItem('reporank_token')||'';
$('#tok').value=tok;
const hdr=()=>tok?{'Authorization':'Bearer '+tok,'Content-Type':'application/json'}
                 :{'Content-Type':'application/json'};
const col=v=>v>=80?'var(--good)':v>=60?'var(--mid)':'var(--bad)';
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function get(p){const r=await fetch(api(p),{headers:hdr()});return r.json()}
async function post(p,b){const r=await fetch(api(p),{method:'POST',headers:hdr(),
  body:JSON.stringify(b)});return r.json()}

$('#signin').onclick=async()=>{tok=$('#tok').value.trim();
  localStorage.setItem('reporank_token',tok);
  const w=await get('/api/whoami');
  $('#who').textContent=w.ok?`${w.role} · ${w.address.slice(0,10)}…`:(w.error||'not signed in');};

$('#go').onclick=async()=>{
  const repo=$('#ref').value.trim(); if(!repo)return;
  $('#out').innerHTML='<div class="card muted">ranking '+esc(repo)+' — the panel takes a moment…</div>';
  const rep=await post('/api/rank',{repo,llm:$('#llm').checked,fresh:$('#fresh').checked});
  if(rep.error){$('#out').innerHTML='<div class="card err">'+esc(rep.error)+'</div>';return}
  render(rep); loadBoard();
};

function render(r){
  const dims=Object.entries(r.dimensions).map(([k,d])=>`<div class="dim">
    <span title="${esc(d.title)}">${esc(d.title)}</span>
    <span class="bar"><i style="width:${d.score}%;background:${col(d.score)}"></i></span>
    <span class="n">${d.score} <span style="opacity:.5">/${d.weight}</span></span></div>`).join('');
  const tips=(r.suggestions||[]).slice(0,12).map(s=>`<li>
    <span class="tag">${esc(s.dimension)}</span><span class="tag">${esc(s.severity)}</span>
    <span class="tag">${esc(s.effort)}</span>${esc(s.suggestion)}
    ${s.evidence?`<div class="ev">${esc(s.evidence)}</div>`:''}</li>`).join('');
  const p=r.panel||{};
  $('#out').innerHTML=`<div class="card">
    <span class="score" style="color:${col(r.score)}">${r.score}</span><span class="grade">${esc(r.grade)}</span>
    <div class="meta">${esc(r.repo.source)} · ${esc(r.repo.kind)}${r.repo.commit?' · '+esc(r.repo.commit):''}
      · ${p.ok||0}/${p.ballots||0} ballots${p.llm?'':' · deterministic only'}${r.cached?' · cached':''}</div>
    <div style="margin-top:14px">${dims}</div>
    ${(p.failed||[]).length?`<div class="ev err">failed: ${p.failed.map(f=>esc(f.voter)).join(', ')}</div>`:''}
  </div>
  <div class="card"><h2>What to fix</h2><ul class="tips">${tips||'<li class="muted">nothing pressing</li>'}</ul></div>`;
}

async function loadBoard(){
  const b=(await get('/api/board')).board||[];
  $('#board').innerHTML=b.length?`<table><tr><th>#</th><th>repo</th><th>score</th><th>grade</th><th>panel</th></tr>
    ${b.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.repo)}</td>
      <td style="color:${col(r.score)}">${r.score}</td><td>${esc(r.grade)}</td>
      <td class="muted">${r.llm?'panel':'static'}</td></tr>`).join('')}</table>`
    :'<span class="muted">nothing ranked yet</span>';
}
async function loadPanel(){
  const v=(await get('/api/voters')).voters||[];
  $('#panel').innerHTML=`<table><tr><th>voter</th><th>dimension</th><th>kind</th><th>weight</th><th>on</th></tr>
    ${v.map(x=>`<tr><td>${esc(x.id)}</td><td>${esc(x.dimension)}</td><td>${esc(x.kind)}</td>
      <td>${x.weight}</td><td>${x.enabled?'yes':'no'}</td></tr>`).join('')}</table>`;
}
loadBoard(); loadPanel(); if(tok)$('#signin').onclick();
</script></body></html>
"""

