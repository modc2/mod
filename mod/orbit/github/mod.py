"""
github — semantic repo discovery for the mod protocol.

Search GitHub by *meaning*, not by keyword, and without an API key. Ask for
"a library that runs untrusted wasm in a sandbox" and get repos back ranked by
how close they actually are to that idea, not by whether their README happens
to contain those five words.

How it works, in three stages:

  1. EXPAND   the question becomes several lexical GitHub queries (topic
              lexicon + optional rewrite by the agent module, if one is up).
  2. RETRIEVE those queries fan out to GitHub's public search API — no key, no
              login, 10 requests/minute — and the hits are unioned into one
              candidate pool. READMEs come from raw.githubusercontent.com,
              which is unauthenticated and outside the API rate limiter.
  3. RANK     the candidates are re-ranked against the original question by a
              local embedding model (sentence-transformers MiniLM, downloaded
              once, runs on CPU, still no key). If no model is available it
              falls back to TF-IDF cosine over the same corpus — the expansion
              stage is what carries the meaning there, so the fallback degrades
              rather than breaks.

So the default path is genuinely keyless: no token, no OAuth, no account.

LOGIN (optional, and merged rather than duplicated)
Connecting GitHub only buys you a higher rate limit (30 searches/min, 5000
API calls/hour) and private repos. When you want it, this module does NOT
implement a second GitHub login — it delegates to the `git` module, which
already attaches a GitHub account to a mod key off-chain in ~/.mod/git. One
GitHub identity per key, one place it is stored:

    m github/oauth              → m git/oauth        (device flow)
    m github/connect <pat>      → m git/connect      (personal access token)
    m github/github             → who is connected

Mod-protocol identity is the shared auth module: `m.mod('auth')` signed tokens
gate the few write endpoints (cache, indexes, grants) exactly as in `git`, and
whoever owns this box is an owner here too. Reads are open to everyone.

Mod protocol: null call returns info; the app and JSON API share one port
(50520) and tolerate the gateway prefix, so caddy routes /{github} (app) and
/api/github (API) straight from config.json.

CLI:
    m github                                       # info
    m github/search "run untrusted wasm sandboxed" # the whole point
    m github/search "vector db in rust" n=10 language=rust stars=100
    m github/search "..." explain=1                # per-repo score breakdown
    m github/similar tokio-rs/tokio                # more like this
    m github/expand "p2p file sync"                # what it will actually ask
    m github/repo huggingface/transformers         # one repo, keyless
    m github/readme torvalds/linux n=2000
    m github/trending language=python days=7
    m github/rate                                  # rate limit left
    m github/oauth                                 # raise it (via the git mod)
    m github/serve                                 # app+api on :50520

ROOT PUSH (temporary)
The whole mod repo, committed and pushed on a timer, so one push at the root
stands in for every module underneath it and nobody else has to update:

    m github/root                # what is pending, when the next push is due
    m github/root_push dry=1     # what it would commit, without doing it
    m github/root_push force=1   # push now, ignoring the hourly cooldown
    m github/root_auto every=3600  # start the pm2 loop (github-rootpush)
    m github/root_auto on=0        # stop it
    m github/root_log            # the last pushes and any failures
"""
import concurrent.futures as futures
import contextlib
import io
import json
import math
import os
import re
import threading
import time
import mod as m

_CACHE_LOCK = threading.Lock()        # the readme pool writes the cache in parallel

APP_PORT = 50770
CACHE = '~/.mod/github/cache.json'        # search + readme responses (ttl'd)
ACCESS = '~/.mod/github/access.json'      # owner + per-address grants
OWNER = '~/.mod/github/owner.json'        # who owns this module, if pinned
HOST_OWNER = '~/.mod/claude/owner.json'   # …else the host's owner of record
GIT_MOD = 'git'                           # module that owns the GitHub login
API = 'https://api.github.com'
RAW = 'https://raw.githubusercontent.com'
TOKEN_TTL = 3600                          # seconds a signed token stays valid
CACHE_TTL = 900                           # seconds a search stays warm
README_TTL = 86400                        # READMEs move slower than rankings
MAX_CACHE = 400                           # entries kept before the oldest go
EMBED_MODEL = os.environ.get('GITHUB_EMBED_MODEL', 'all-MiniLM-L6-v2')
AGENT_MOD = 'agent'                       # optional query rewriter
REPO_RE = re.compile(r'(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')

# Root push (temporary): the whole mod repo, committed and pushed on a timer so
# nothing has to be uploaded by hand. Four dirnames up from this file is the
# repo root — github/ → orbit/ → mod/ → the checkout itself.
ROOT_REPO = os.environ.get('GITHUB_ROOT_REPO') or os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ROOT_STATE = '~/.mod/github/root.json'    # enabled, interval, last push, history
ROOT_LOCK = '/tmp/github-rootpush.lock'   # one pusher at a time
ROOT_REMOTE = 'origin'
ROOT_EVERY = 3600                         # seconds between pushes, at most one
ROOT_TICK = 60                            # how often the loop looks at the tree
ROOT_HISTORY = 50                         # pushes kept in the state file
ROOT_MAX_FILES = 4000                     # a bigger diff than this wants a human
ROOT_MAX_BYTES = 256 * 1024 * 1024

# Query expansion: the cheap half of "semantic". A question is tokenized and
# every hit here contributes extra lexical queries and topic filters, which is
# what lets GitHub's keyword-only search surface candidates the user's own
# words would have missed. Deliberately small and hand-checked — this is a
# retrieval hint, not a taxonomy.
LEXICON = {
    'wasm': ['webassembly', 'wasm runtime', 'topic:webassembly'],
    'webassembly': ['wasm', 'wasmtime wasmer', 'topic:webassembly'],
    'sandbox': ['sandboxing', 'isolation runtime', 'topic:sandbox'],
    'llm': ['large language model', 'inference engine', 'topic:llm'],
    'agent': ['ai agent framework', 'autonomous agents', 'topic:ai-agents'],
    'embedding': ['embeddings', 'sentence transformers', 'topic:embeddings'],
    'vector': ['vector database', 'similarity search', 'topic:vector-database'],
    'search': ['full text search', 'retrieval', 'topic:search'],
    'semantic': ['embeddings similarity', 'neural search'],
    'p2p': ['peer to peer', 'distributed network', 'topic:p2p'],
    'blockchain': ['smart contracts', 'web3', 'topic:blockchain'],
    'crypto': ['cryptography', 'encryption library', 'topic:cryptography'],
    'db': ['database engine', 'storage engine', 'topic:database'],
    'database': ['storage engine', 'query engine', 'topic:database'],
    'queue': ['message queue', 'job scheduler', 'topic:message-queue'],
    'scraper': ['web scraping', 'crawler', 'topic:web-scraping'],
    'parser': ['parsing library', 'grammar', 'topic:parser'],
    'compiler': ['language toolchain', 'codegen', 'topic:compiler'],
    'game': ['game engine', 'gamedev', 'topic:gamedev'],
    'terminal': ['tui', 'command line interface', 'topic:cli'],
    'cli': ['command line tool', 'terminal ui', 'topic:cli'],
    'gpu': ['cuda kernels', 'accelerated compute', 'topic:gpu'],
    'ml': ['machine learning', 'deep learning', 'topic:machine-learning'],
    'api': ['rest api', 'http server', 'topic:api'],
    'auth': ['authentication', 'oauth identity', 'topic:authentication'],
    'monitoring': ['observability', 'metrics tracing', 'topic:monitoring'],
    'sync': ['synchronization', 'replication', 'topic:sync'],
    'markdown': ['markdown parser', 'documentation generator'],
    'image': ['image processing', 'computer vision', 'topic:image-processing'],
    'audio': ['audio processing', 'dsp', 'topic:audio'],
    'video': ['video encoding', 'ffmpeg', 'topic:video'],
    'bot': ['chatbot', 'automation bot', 'topic:bot'],
    'test': ['testing framework', 'test runner', 'topic:testing'],
    'deploy': ['deployment tooling', 'ci cd', 'topic:devops'],
    'docker': ['containers', 'oci images', 'topic:docker'],
    'kubernetes': ['k8s operator', 'cluster orchestration', 'topic:kubernetes'],
}
# words that carry no retrieval signal — dropped from the lexical query so
# "a library that lets me…" searches for the library, not for "lets me"
STOP = set('''a an the and or of for to in on with without that this those these is are was
be been being it its as at by from into over under how what which who whom why when where
i me my we our you your they them their he she his her but if then than so such can could
should would may might will shall do does did done doing have has had having not no nor only
own same too very just about above below up down out off again further once here there all
any both each few more most other some like want need looking find search tool library
package framework project repo repos repository something anything way ways best good great
simple easy new using use used uses'''.split())


class Mod:
    description = ('github — semantic repo search over GitHub with no API key and no login: '
                   'a question is expanded into lexical queries, retrieved through the public '
                   'search API, and re-ranked against the question by a local embedding model; '
                   'connecting an account is optional and delegates to the git module')

    def __init__(self, path: str = None):
        self.cache_path = m.abspath(CACHE)
        self.access_path = m.abspath(ACCESS)
        self.owner_path = m.abspath(OWNER)
        self.host_owner_path = m.abspath(HOST_OWNER)
        self.root_path = m.abspath(ROOT_STATE)
        self._model = None

    # --- github rest (keyless by default) -----------------------------------

    def _token(self, address: str = None):
        """The caller's GitHub token, if there is one. We never store tokens
        here: the git module owns the GitHub↔mod-key binding, so we borrow.
        Env vars are the escape hatch for headless boxes."""
        try:
            tok = m.mod(GIT_MOD)()._github_token(address)
            if tok:
                return tok
        except Exception:
            pass
        return os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')

    def _get(self, url, params=None, address=None, raw=False, timeout=15):
        """One unauthenticated-by-default GET. Returns (payload, headers)."""
        import requests
        headers = {'Accept': 'text/plain' if raw else 'application/vnd.github+json',
                   'User-Agent': 'mod-github-module'}
        tok = self._token(address)
        if tok and not raw:
            headers['Authorization'] = f'Bearer {tok}'
        r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
        if r.status_code >= 400:
            raise self._error(r, authed=bool(tok))
        return (r.text if raw else r.json()), r.headers

    @staticmethod
    def _error(r, authed=True):
        try:
            msg = (r.json() or {}).get('message') or r.text[:200]
        except Exception:
            msg = r.text[:200]
        if r.headers.get('X-RateLimit-Remaining') == '0':
            return RuntimeError(
                'github rate limit reached — ' +
                ('wait for the window to reset' if authed else
                 'searches are 10/min without a login; connect an account with '
                 '`m github/oauth` for 30/min'))
        if r.status_code == 404:
            return KeyError(f'github 404: {msg}')
        if r.status_code in (401, 403):
            return PermissionError(f'github {r.status_code}: {msg}')
        return RuntimeError(f'github {r.status_code}: {msg}')

    # --- cache --------------------------------------------------------------
    #
    # READMEs are fetched from a thread pool, so this is a read-modify-write
    # that eight threads reach at once. It does its own file IO rather than
    # m.put: the lock keeps concurrent writers from losing each other's
    # entries, and the tmp+rename means a reader (or a second process) never
    # sees a half-written file — plain json.dump left corrupt cache files
    # behind on the very first parallel search.

    def _cache(self) -> dict:
        try:
            with open(self.cache_path) as f:
                c = json.load(f)
            return c if isinstance(c, dict) else {}
        except (OSError, ValueError):
            return {}                              # missing or corrupt → cold, not fatal

    def _cached(self, key: str, ttl: int):
        rec = self._cache().get(key)
        if rec and time.time() - rec.get('t', 0) < ttl:
            return rec.get('v')
        return None

    def _store(self, key: str, value):
        with _CACHE_LOCK:
            c = self._cache()
            c[key] = {'t': time.time(), 'v': value}
            if len(c) > MAX_CACHE:                 # drop the oldest, keep it bounded
                for k in sorted(c, key=lambda k: c[k].get('t', 0))[:len(c) - MAX_CACHE]:
                    c.pop(k, None)
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = f'{self.cache_path}.{os.getpid()}.tmp'
            with open(tmp, 'w') as f:
                json.dump(c, f)
            os.replace(tmp, self.cache_path)
        return value

    def cache(self) -> dict:
        """What is warm right now."""
        c = self._cache()
        return {'entries': len(c), 'ttl': CACHE_TTL, 'path': self.cache_path,
                'keys': sorted(c, key=lambda k: -c[k].get('t', 0))[:20]}

    def clear_cache(self) -> dict:
        m.put(self.cache_path, {})
        return {'cleared': True}

    # --- stage 1: expand ----------------------------------------------------

    @staticmethod
    def _tokens(text: str) -> list:
        return [w for w in re.findall(r'[a-z0-9+#.]+', (text or '').lower())
                if len(w) > 1 and w not in STOP]

    def expand(self, query: str, agent: bool = False, model: str = None) -> dict:
        """The lexical queries this question turns into. Run it on its own to
        see (and sanity-check) what will actually be asked of GitHub."""
        words = self._tokens(query)
        core = ' '.join(words[:8]) or (query or '').strip()
        queries, topics = [core], []
        for w in words:
            for extra in LEXICON.get(w, []):
                if extra.startswith('topic:'):
                    if extra not in topics:
                        topics.append(extra)
                elif extra not in queries:
                    queries.append(extra)
        # pair the strongest expansion with the user's own words: a query that
        # is only synonyms drifts, a query that is only their words is what
        # plain GitHub search already does
        if len(words) > 3:
            queries.insert(1, ' '.join(words[:3]))
        if agent:
            queries = self._agent_queries(query, model) or queries
        # every duplicate is a wasted call out of ten per minute
        queries = list(dict.fromkeys(q for q in queries if q))
        return {'query': query, 'terms': words, 'queries': queries[:6],
                'topics': topics[:3]}

    def _agent_queries(self, query: str, model: str = None):
        """Optional: let the agent module rewrite the question into queries.
        Never required — if the module is absent or slow, expansion stands."""
        try:
            prompt = (f'Turn this repo search into at most 4 GitHub search queries, one per '
                      f'line, keywords only, no explanation, no quotes:\n{query}')
            with contextlib.redirect_stdout(io.StringIO()):
                out = m.mod(AGENT_MOD)().forward(prompt, model=model) if model else \
                    m.mod(AGENT_MOD)().forward(prompt)
            text = out if isinstance(out, str) else json.dumps(out)
            lines = [re.sub(r'^[-*\d.\s]+', '', ln).strip()
                     for ln in text.splitlines() if ln.strip()]
            return [ln for ln in lines if 2 < len(ln) < 80][:4] or None
        except Exception:
            return None

    # --- stage 2: retrieve --------------------------------------------------

    def candidates(self, query: str, language: str = None, stars: int = None,
                   sort: str = None, pages: int = 1, per_page: int = 40,
                   fresh: bool = False, agent: bool = False,
                   address: str = None) -> dict:
        """Union of the raw GitHub hits for every expanded query. This is the
        only stage that spends rate limit."""
        plan = self.expand(query, agent=agent)
        qualifiers = ''
        if language:
            qualifiers += f' language:{language}'
        if stars:
            qualifiers += f' stars:>={int(stars)}'
        repos, seen, asked, errors = [], set(), [], []
        for q in plan['queries']:
            for topic in ([''] + plan['topics'][:1]) if q == plan['queries'][0] else ['']:
                full = f'{q}{(" " + topic) if topic else ""}{qualifiers}'.strip()
                for page in range(1, int(pages) + 1):
                    key = f'search:{full}:{sort}:{per_page}:{page}'
                    hit = None if fresh else self._cached(key, CACHE_TTL)
                    if hit is None:
                        try:
                            data, _h = self._get(
                                f'{API}/search/repositories', address=address,
                                params={'q': full, 'per_page': int(per_page), 'page': page,
                                        **({'sort': sort} if sort else {})})
                            hit = data.get('items', [])
                            self._store(key, hit)
                        except Exception as e:
                            # one starved query should not sink the search —
                            # rank whatever the earlier queries already brought
                            errors.append(f'{full}: {e}')
                            hit = []
                    asked.append(full)
                    for r in hit:
                        name = r.get('full_name')
                        if name and name not in seen:
                            seen.add(name)
                            repos.append(self._row(r))
        return {'query': query, 'queries': asked, 'topics': plan['topics'],
                'repos': repos, 'errors': errors}

    @staticmethod
    def _row(r: dict) -> dict:
        return {'name': r.get('full_name'), 'url': r.get('html_url'),
                'description': r.get('description') or '',
                'stars': r.get('stargazers_count') or 0,
                'forks': r.get('forks_count') or 0,
                'language': r.get('language'),
                'topics': r.get('topics') or [],
                'license': ((r.get('license') or {}) or {}).get('spdx_id'),
                'pushed_at': r.get('pushed_at'), 'created_at': r.get('created_at'),
                'archived': bool(r.get('archived')), 'owner': (r.get('owner') or {}).get('login'),
                'default_branch': r.get('default_branch') or 'main'}

    def readme(self, repo: str, n: int = 4000, branch: str = None,
               fresh: bool = False) -> str:
        """A repo's README, straight off raw.githubusercontent.com — no key,
        and outside the API rate limiter, which is why ranking can afford it."""
        owner, name = self._split(repo)
        key = f'readme:{owner}/{name}'
        if not fresh:
            hit = self._cached(key, README_TTL)
            if hit is not None:
                return hit[:int(n)]
        branches = [branch] if branch else ['main', 'master']
        for br in branches:
            for fn in ('README.md', 'readme.md', 'README.rst', 'README'):
                try:
                    text, _h = self._get(f'{RAW}/{owner}/{name}/{br}/{fn}', raw=True, timeout=8)
                    return self._store(key, text[:20000])[:int(n)]
                except Exception:
                    continue
        return self._store(key, '')

    # --- stage 3: rank ------------------------------------------------------

    @property
    def model(self):
        """Local sentence-transformers model, loaded once. Absent → TF-IDF."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(EMBED_MODEL)
        return self._model

    def _doc(self, r: dict, readme: str = '') -> str:
        parts = [r.get('name', '').replace('/', ' ').replace('-', ' '),
                 r.get('description') or '', ' '.join(r.get('topics') or []),
                 r.get('language') or '', readme]
        return ' '.join(p for p in parts if p)[:2000]

    def _readmes(self, repos: list, k: int) -> dict:
        """READMEs for the top k candidates, fetched in parallel."""
        if k <= 0:
            return {}
        out = {}
        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = {pool.submit(self.readme, r['name'], 1500): r['name'] for r in repos[:k]}
            for job in futures.as_completed(jobs, timeout=45):
                try:
                    out[jobs[job]] = job.result() or ''
                except Exception:
                    out[jobs[job]] = ''
        return out

    def _dense(self, query: str, docs: list) -> list:
        import numpy as np
        vecs = self.model.encode([query] + docs, show_progress_bar=False,
                                 normalize_embeddings=True)
        v = np.asarray(vecs)
        return (v[1:] @ v[0]).tolist()          # normalized → dot product is cosine

    @staticmethod
    def _tfidf(query_terms: list, docs: list) -> list:
        """Cosine over the candidate pool itself. idf is computed on the pool,
        which is small (tens to low hundreds) and topically tight, so rare
        discriminating words score high exactly where they should."""
        toks = [Mod._tokens(d) for d in docs]
        df = {}
        for t in toks:
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        n = max(len(docs), 1)
        idf = {w: math.log(1 + n / (1 + c)) for w, c in df.items()}
        qset = {}
        for w in query_terms:
            qset[w] = qset.get(w, 0) + 1
        qnorm = math.sqrt(sum((v * idf.get(w, 1.0)) ** 2 for w, v in qset.items())) or 1.0
        scores = []
        for t in toks:
            tf = {}
            for w in t:
                tf[w] = tf.get(w, 0) + 1
            dnorm = math.sqrt(sum((v * idf.get(w, 1.0)) ** 2 for w, v in tf.items())) or 1.0
            dot = sum(qv * idf.get(w, 1.0) ** 2 * tf.get(w, 0) for w, qv in qset.items())
            scores.append(dot / (qnorm * dnorm))
        return scores

    def rank(self, query: str, repos: list, n: int = 20, readmes: int = 25,
             dense: bool = None, explain: bool = False) -> list:
        """Re-rank candidates against the original question.

        score = 0.75·semantic + 0.15·topic-overlap + 0.10·popularity

        The priors are small on purpose: they break ties between repos that are
        equally on-topic, they must not float a famous irrelevant repo over an
        obscure exact match — which is the failure mode of plain GitHub search.
        """
        if not repos:
            return []
        texts = self._readmes(repos, min(int(readmes), len(repos)))
        docs = [self._doc(r, texts.get(r['name'], '')) for r in repos]
        terms = self._tokens(query)
        ranker = 'tfidf'
        if dense is not False:
            try:
                sem = self._dense(query, docs)
                ranker = f'dense:{EMBED_MODEL}'
            except Exception:
                sem = self._tfidf(terms, docs)
        else:
            sem = self._tfidf(terms, docs)
        now = time.time()
        out = []
        for r, s in zip(repos, sem):
            topics = set(t.lower() for t in (r.get('topics') or []))
            overlap = len(topics & set(terms)) / (len(terms) or 1)
            pop = math.log10(1 + (r.get('stars') or 0)) / 6.0     # ~1.0 at 1M stars
            score = 0.75 * float(s) + 0.15 * min(overlap, 1.0) + 0.10 * min(pop, 1.0)
            if r.get('archived'):
                score *= 0.85                 # still findable, just not preferred
            row = dict(r, score=round(score, 4))
            if explain:
                row['why'] = {'semantic': round(float(s), 4),
                              'topic_overlap': round(overlap, 4),
                              'popularity': round(pop, 4),
                              'readme_used': bool(texts.get(r['name'])),
                              'ranker': ranker}
            out.append(row)
        out.sort(key=lambda r: -r['score'])
        self._ranker = ranker
        return out[:int(n)]

    # --- the one call that does all three -----------------------------------

    def search(self, query: str, n: int = 20, language: str = None, stars: int = None,
               sort: str = None, pages: int = 1, readmes: int = 25, fresh: bool = False,
               agent: bool = False, dense: bool = None, explain: bool = False,
               address: str = None) -> dict:
        """Semantic repo search. No key, no login.

            m github/search "run untrusted wasm in a sandbox"
            m github/search "vector db in rust" language=rust stars=100 n=10

        `explain=1` shows why each repo placed where it did, `fresh=1` skips
        the cache, `agent=1` lets the agent module write the queries, and
        `dense=0` forces the TF-IDF ranker (useful offline).
        """
        t0 = time.time()
        cands = self.candidates(query, language=language, stars=stars, sort=sort,
                                pages=pages, fresh=fresh, agent=agent, address=address)
        results = self.rank(query, cands['repos'], n=n, readmes=readmes,
                            dense=dense, explain=explain)
        return {'query': query, 'queries': cands['queries'], 'topics': cands['topics'],
                'candidates': len(cands['repos']), 'returned': len(results),
                'ranker': getattr(self, '_ranker', 'tfidf'),
                'authenticated': bool(self._token(address)),
                'took': round(time.time() - t0, 2),
                'errors': cands['errors'], 'results': results}

    def similar(self, repo: str, n: int = 15, dense: bool = None, **kw) -> dict:
        """More repos like this one — the repo's own description and topics
        become the question."""
        r = self.repo(repo)
        seed = ' '.join([r.get('description') or '', ' '.join(r.get('topics') or []),
                         r.get('language') or '']).strip() or r['name'].split('/')[-1]
        out = self.search(seed, n=int(n) + 1, dense=dense, **kw)
        out['results'] = [x for x in out['results'] if x['name'] != r['name']][:int(n)]
        out['seed_repo'], out['query'] = r['name'], seed
        return out

    # --- plain reads --------------------------------------------------------

    @staticmethod
    def _split(repo: str) -> tuple:
        mm = REPO_RE.match((repo or '').strip())
        if not mm:
            raise ValueError(f'expected owner/repo, got {repo!r}')
        return mm.group(1), mm.group(2)

    def repo(self, repo: str, address: str = None) -> dict:
        """One repo's metadata, keyless."""
        owner, name = self._split(repo)
        data, _h = self._get(f'{API}/repos/{owner}/{name}', address=address)
        return dict(self._row(data), open_issues=data.get('open_issues_count'),
                    homepage=data.get('homepage'), size=data.get('size'))

    def trending(self, language: str = None, days: int = 7, n: int = 20,
                 address: str = None) -> dict:
        """What is getting stars lately — GitHub has no public trending API, so
        this is 'created recently, sorted by stars', which is the honest
        approximation you can build keylessly."""
        since = time.strftime('%Y-%m-%d', time.gmtime(time.time() - int(days) * 86400))
        q = f'created:>{since}' + (f' language:{language}' if language else '')
        data, _h = self._get(f'{API}/search/repositories', address=address,
                             params={'q': q, 'sort': 'stars', 'order': 'desc',
                                     'per_page': int(n)})
        return {'window_days': int(days), 'language': language,
                'repos': [self._row(r) for r in data.get('items', [])]}

    def rate(self, address: str = None) -> dict:
        """Rate limit left — the number that decides whether to log in."""
        data, _h = self._get(f'{API}/rate_limit', address=address)
        res = data.get('resources', {})
        out = {}
        for k in ('core', 'search'):
            v = res.get(k) or {}
            out[k] = {'limit': v.get('limit'), 'remaining': v.get('remaining'),
                      'resets_in': max(0, int((v.get('reset') or 0) - time.time()))}
        out['authenticated'] = bool(self._token(address))
        return out

    # --- github login: delegated to the git module --------------------------

    def oauth(self, address: str = None, scope: str = None) -> dict:
        """Connect a GitHub account by OAuth device flow. Handled by the git
        module so one mod key keeps exactly one GitHub identity."""
        return dict(m.mod(GIT_MOD)().oauth(address=address, scope=scope),
                    note='finish with `m git/oauth_poll <session> wait=120`')

    def connect(self, token: str, address: str = None) -> dict:
        """…or attach a personal access token instead (stored by git, 0600)."""
        return m.mod(GIT_MOD)().connect(token, address=address)

    def disconnect(self, address: str = None, login: str = None) -> dict:
        return m.mod(GIT_MOD)().disconnect(address=address, login=login)

    def github(self, address: str = None) -> dict:
        """Which GitHub account this key is using, if any."""
        try:
            return m.mod(GIT_MOD)().github(address=address)
        except Exception as e:
            return {'connected': False, 'reason': str(e),
                    'note': 'searching works without this — it only raises the rate limit'}

    # --- mod-protocol auth (shared auth module) -----------------------------

    ROLES = ('write', 'admin')

    def _acl(self) -> dict:
        acl = m.get(self.access_path, {}) or {}
        acl.setdefault('owner', None)
        acl.setdefault('grants', {})
        return acl

    def _host_owner(self):
        for path in (self.owner_path, self.host_owner_path):
            rec = m.get(path, {}) or {}
            if rec.get('owner'):
                return str(rec['owner'])
        return None

    def access(self) -> dict:
        """Reads are open to everyone — search needs no identity at all. Only
        cache/index management and grants are gated."""
        acl = self._acl()
        return {'owner': acl['owner'], 'host_owner': self._host_owner(),
                'grants': acl['grants'],
                'open_reads': ['search', 'similar', 'expand', 'repo', 'readme',
                               'trending', 'rate', 'info'],
                'roles': {'write': ['clear_cache'],
                          'admin': ['grant', 'revoke', 'connect on another key']},
                'auth': "signed token from m.mod('auth') — mint one with `m github/token`",
                'open': bool(os.environ.get('GITHUB_ACCESS_OPEN'))}

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

    def set_owner(self, address: str) -> dict:
        """Hand the module to another address (CLI/local only)."""
        acl = self._acl()
        acl['owner'] = str(address)
        m.put(self.access_path, acl)
        return self.access()

    def token(self, data: dict = None) -> str:
        """Mint a signed auth token for this box's key."""
        with contextlib.redirect_stdout(io.StringIO()):
            return m.mod('auth')().token(data or {'mod': 'github'})

    def _role_of(self, address: str):
        who = str(address or '').lower()
        if not who:
            return None
        acl = self._acl()
        if who in {str(a or '').lower() for a in (acl['owner'], self._host_owner()) if a}:
            return 'owner'
        return next((g.get('role') for a, g in acl['grants'].items()
                     if str(a).lower() == who), None)

    def _authorize(self, headers, need: str = 'write') -> dict:
        """Verify a Bearer token against the shared auth module, then the ACL.
        GITHUB_ACCESS_OPEN=1 bypasses (dev only)."""
        if os.environ.get('GITHUB_ACCESS_OPEN'):
            return {'address': m.key().address, 'role': 'owner', 'open': True}
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        tok = raw.split('Bearer ')[-1].strip() if 'Bearer ' in raw else raw.strip()
        if not tok:
            raise PermissionError('missing Authorization: Bearer <token> '
                                  '(mint one with `m github/token`)')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
        except PermissionError:
            raise
        except Exception as e:
            raise PermissionError(f'invalid token: {type(e).__name__}')
        if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
            raise PermissionError('token expired — mint a fresh one')
        address = data.get('key')
        role = self._role_of(address)
        rank = {'write': 1, 'admin': 2, 'owner': 3}
        if role is None or rank[role] < rank.get(need, 1):
            raise PermissionError(f'{address} lacks {need} access — ask the owner to '
                                  f'`m github/grant {address}`')
        return {'address': address, 'role': role}

    def _token_address(self, headers) -> str:
        """Who a Bearer token belongs to, ignoring the ACL — reads are open but
        they resolve against the caller's own GitHub connection."""
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        tok = raw.split('Bearer ')[-1].strip()
        if not tok:
            return None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
            if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
                return None
            return data.get('key')
        except Exception:
            return None

    def whoami(self, headers=None) -> dict:
        try:
            return dict(self._authorize(headers or {}, need='write'), ok=True)
        except PermissionError as e:
            return {'ok': False, 'address': self._token_address(headers or {}),
                    'error': str(e)}

    # --- root push (temporary) ----------------------------------------------
    #
    # One push at the top of the repo stands in for every module underneath it:
    # nobody has to commit their own subtree, so "everyone else" never has to
    # update. A push fires at most once per `every` seconds — an edit landing
    # two minutes after a push waits out the rest of the hour and rides the
    # next tick, which is what keeps a busy tree from turning into 40 commits.
    #
    # This is deliberately a thin wrapper over the git CLI rather than a second
    # git module: it needs to run headless from a pm2 loop, and the only thing
    # it cares about is "is the tree dirty, and has the hour elapsed".

    def _root_repo(self, repo: str = None) -> str:
        path = os.path.abspath(os.path.expanduser(repo or ROOT_REPO))
        if not os.path.isdir(os.path.join(path, '.git')):
            raise ValueError(f'{path} is not a git repo')
        return path

    @staticmethod
    def _git(repo: str, *args, timeout: int = 900):
        """Run one git command. The askpass vars are scrubbed on purpose: a
        dead VS Code server leaves GIT_ASKPASS pointing at a socket that no
        longer exists, and git reports that as a confusing ENOENT instead of
        falling through to the stored credential."""
        import subprocess
        env = {k: v for k, v in os.environ.items()
               if k not in ('GIT_ASKPASS', 'SSH_ASKPASS', 'VSCODE_GIT_ASKPASS_NODE',
                            'VSCODE_GIT_ASKPASS_MAIN', 'VSCODE_GIT_ASKPASS_EXTRA_ARGS',
                            'VSCODE_GIT_IPC_HANDLE')}
        env['GIT_TERMINAL_PROMPT'] = '0'
        try:
            r = subprocess.run(('git',) + args, cwd=repo, capture_output=True,
                               text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, f'timeout after {timeout}s: git {" ".join(args)}'
        return r.returncode, (r.stdout or '') + (r.stderr or '')

    def _root_state(self) -> dict:
        st = m.get(self.root_path, {}) or {}
        st.setdefault('enabled', False)
        st.setdefault('every', ROOT_EVERY)
        st.setdefault('last_push', 0)
        st.setdefault('last_error', None)
        st.setdefault('history', [])
        return st

    def _root_save(self, st: dict) -> dict:
        st['history'] = st.get('history', [])[-ROOT_HISTORY:]
        m.put(self.root_path, st)
        return st

    @staticmethod
    def _root_paths(porcelain: str) -> list:
        """Working-tree paths out of `git status --porcelain -uall`. Renames
        arrive as `old -> new`; only the new side exists on disk."""
        out = []
        for line in porcelain.splitlines():
            if len(line) < 4:
                continue
            p = line[3:].split(' -> ')[-1].strip()
            if p.startswith('"') and p.endswith('"'):
                p = p[1:-1].encode().decode('unicode_escape')
            if p:
                out.append(p)
        return out

    def _root_branch(self, path: str) -> str:
        _, branch = self._git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
        return branch.strip() or 'HEAD'

    def _root_pending(self, path: str, branch: str) -> dict:
        """What a push would carry: dirty files plus commits already made but
        not yet on the remote."""
        _, porcelain = self._git(path, 'status', '--porcelain', '-uall')
        files = self._root_paths(porcelain)
        size = 0
        for f in files:
            full = os.path.join(path, f)
            with contextlib.suppress(OSError):
                size += os.path.getsize(full)
        ahead = 0
        code, out = self._git(path, 'rev-list', '--count', f'{ROOT_REMOTE}/{branch}..HEAD')
        if code == 0 and out.strip().isdigit():
            ahead = int(out.strip())
        return {'files': files, 'count': len(files), 'bytes': size, 'ahead': ahead}

    def root(self, repo: str = None) -> dict:
        """Status of the hourly root push: what is pending, when the next one
        is allowed, and how the last one went."""
        path = self._root_repo(repo)
        st = self._root_state()
        branch = self._root_branch(path)
        pend = self._root_pending(path, branch)
        wait = max(0, int(st['every']) - int(time.time() - st['last_push']))
        return {
            'repo': path, 'branch': branch, 'remote': ROOT_REMOTE,
            'enabled': bool(st['enabled']), 'every': int(st['every']),
            'pending_files': pend['count'], 'pending_bytes': pend['bytes'],
            'unpushed_commits': pend['ahead'],
            'dirty': bool(pend['count'] or pend['ahead']),
            'last_push': st['last_push'],
            'last_push_ago': int(time.time() - st['last_push']) if st['last_push'] else None,
            'next_push_in': wait if (pend['count'] or pend['ahead']) else None,
            'last_error': st['last_error'],
            'worker': self.ROOT_PM2,
            'sample': pend['files'][:20],
        }

    def root_push(self, message: str = None, force: bool = False, dry: bool = False,
                  repo: str = None, branch: str = None) -> dict:
        """Stage everything, commit, push. Refuses inside the cooldown unless
        `force`; refuses an implausibly large commit unless `force`, which is
        the guard against a stray build directory that nobody gitignored."""
        import fcntl
        path = self._root_repo(repo)
        lock = open(ROOT_LOCK, 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {'ok': False, 'skipped': 'busy', 'repo': path}
        try:
            st = self._root_state()
            since = time.time() - st['last_push']
            if not force and not dry and st['last_push'] and since < st['every']:
                return {'ok': True, 'skipped': 'cooldown', 'repo': path,
                        'next_push_in': int(st['every'] - since)}
            branch = branch or self._root_branch(path)
            if branch == 'HEAD':
                raise ValueError('detached HEAD — check out a branch first')
            pend = self._root_pending(path, branch)
            if not pend['count'] and not pend['ahead']:
                return {'ok': True, 'skipped': 'clean', 'repo': path, 'branch': branch}
            if not force and pend['count'] > ROOT_MAX_FILES:
                raise ValueError(f"{pend['count']} changed files exceeds the "
                                 f'{ROOT_MAX_FILES} guard — pass force=1 if that is real')
            if not force and pend['bytes'] > ROOT_MAX_BYTES:
                raise ValueError(f"{pend['bytes'] / 1e6:.0f}MB of changes exceeds the "
                                 f'{ROOT_MAX_BYTES / 1e6:.0f}MB guard — pass force=1 '
                                 'if that is real')
            stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime())
            msg = message or f"root push · {pend['count']} files · {stamp}"
            if dry:
                return {'ok': True, 'dry': True, 'repo': path, 'branch': branch,
                        'would_commit': pend['count'], 'bytes': pend['bytes'],
                        'unpushed_commits': pend['ahead'], 'message': msg,
                        'sample': pend['files'][:40]}
            entry = {'at': int(time.time()), 'branch': branch, 'files': pend['count'],
                     'bytes': pend['bytes'], 'message': msg}
            if pend['count']:
                code, out = self._git(path, 'add', '-A')
                if code:
                    raise RuntimeError(f'git add failed: {out.strip()[-800:]}')
                code, out = self._git(path, 'commit', '-m', msg)
                # "nothing to commit" is fine when every dirty path was ignored
                if code and 'nothing to commit' not in out:
                    raise RuntimeError(f'git commit failed: {out.strip()[-800:]}')
            code, out = self._git(path, 'push', ROOT_REMOTE, f'HEAD:refs/heads/{branch}')
            if code:
                raise RuntimeError(f'git push failed: {out.strip()[-800:]}')
            _, head = self._git(path, 'rev-parse', '--short', 'HEAD')
            entry.update(ok=True, commit=head.strip())
            st.update(last_push=int(time.time()), last_error=None)
            st['history'].append(entry)
            self._root_save(st)
            return {'ok': True, 'repo': path, 'branch': branch, 'commit': head.strip(),
                    'files': pend['count'], 'bytes': pend['bytes'], 'message': msg,
                    'pushed_to': f'{ROOT_REMOTE}/{branch}'}
        except Exception as e:
            st = self._root_state()
            st['last_error'] = {'at': int(time.time()), 'error': str(e)}
            st['history'].append({'at': int(time.time()), 'ok': False, 'error': str(e)})
            self._root_save(st)
            raise
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def root_log(self, n: int = 20) -> dict:
        st = self._root_state()
        return {'enabled': bool(st['enabled']), 'every': int(st['every']),
                'last_push': st['last_push'], 'last_error': st['last_error'],
                'history': st['history'][-int(n):][::-1]}

    ROOT_PM2 = 'github-rootpush'

    def root_auto(self, on: bool = True, every: int = None, repo: str = None) -> dict:
        """Turn the hourly loop on or off. The state file is the switch and the
        pm2 process only reads it, so flipping this off stops pushes even if
        the process outlives the call."""
        import subprocess
        st = self._root_state()
        st['enabled'] = bool(on) and str(on).lower() not in ('0', 'false', 'no')
        if every:
            st['every'] = max(60, int(every))
        if repo:
            st['repo'] = self._root_repo(repo)
        self._root_save(st)
        if not st['enabled']:
            subprocess.run(['pm2', 'delete', self.ROOT_PM2], capture_output=True, text=True)
            subprocess.run(['pm2', 'save'], capture_output=True, text=True)
            return {'enabled': False, 'worker': 'stopped'}
        here = os.path.dirname(os.path.abspath(__file__))
        runner = os.path.join(here, 'run_rootpush.py')
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        subprocess.run(['pm2', 'delete', self.ROOT_PM2], capture_output=True, text=True)
        r = subprocess.run(['pm2', 'start', runner, '--name', self.ROOT_PM2,
                            '--interpreter', 'python3', '--cwd', root, '--time'],
                           capture_output=True, text=True, env=dict(os.environ))
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'enabled': True, 'worker': self.ROOT_PM2, 'every': int(st['every']),
                'repo': st.get('repo', ROOT_REPO)}

    def root_loop(self, tick: int = ROOT_TICK, repo: str = None):
        """Foreground loop (pm2 entrypoint). Wakes every `tick` seconds, pushes
        when the tree is dirty AND the cooldown has expired — so a change made
        during the hour is picked up as soon as the hour is up."""
        tick = max(5, int(tick))
        print(f'github root push loop — tick {tick}s', flush=True)
        while True:
            try:
                st = self._root_state()
                if st['enabled']:
                    r = self.root_push(repo=repo or st.get('repo'))
                    if not r.get('skipped'):
                        print(f"pushed {r.get('files')} files as {r.get('commit')} "
                              f"→ {r.get('pushed_to')}", flush=True)
            except Exception as e:
                print(f'root push failed: {e}', flush=True)
            time.sleep(tick)

    # --- app + api ----------------------------------------------------------

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Serve the console (/) and JSON API (/api/*) on one port."""
        import subprocess
        port = int(port)
        if background:
            self.kill(port)
            log_dir = '/tmp/github-mod'
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))
            env = dict(os.environ)
            env['PYTHONPATH'] = root + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', '-c',
                 f"import mod as m; m.mod('github')().serve(port={port}, host={host!r}, "
                 f"background=False)"],
                stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            return {'running': True, 'pid': proc.pid, 'url': f'http://localhost:{port}',
                    'api': f'http://localhost:{port}/api/info',
                    'log': os.path.join(log_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer((host, port), self._make_handler())
        print(f'github app on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=APP_PORT):
        import subprocess
        killed = []
        pid_path = '/tmp/github-mod/app.pid'
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 15)
                killed.append(pid)
            except Exception:
                pass
            try:
                os.remove(pid_path)
            except OSError:
                pass
        try:
            out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{int(port)} 2>/dev/null'],
                                 capture_output=True, text=True).stdout.split()
            for pid in out:
                if int(pid) not in killed:
                    os.kill(int(pid), 15)
                    killed.append(int(pid))
        except Exception:
            pass
        return {'killed': killed}

    PM2_NAME = 'github-app'

    def worker(self, port=APP_PORT, name=None):
        """Run the app under pm2 (auto-restart, survives logout)."""
        import subprocess
        name = name or self.PM2_NAME
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_app.py')
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        env = dict(os.environ, GITHUB_APP_PORT=str(int(port)))
        r = subprocess.run(['pm2', 'start', runner, '--name', name, '--interpreter', 'python3',
                            '--cwd', root, '--time'], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        self._wait_health(int(port))
        return {'worker': name, 'port': int(port), 'running': True}

    def stop_worker(self, name=None):
        import subprocess
        name = name or self.PM2_NAME
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': name}

    def _wait_health(self, port, tries=40):
        import urllib.request
        for _ in range(tries):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/api/info', timeout=1)
                return True
            except Exception:
                time.sleep(0.25)
        return False

    def _make_handler(self):
        import json as _json
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import urlparse, parse_qs
        gh = self

        READS = {'/api/info': ('info', ()), '/api/search': ('search', (
            'query', 'n', 'language', 'stars', 'sort', 'pages', 'readmes', 'fresh',
            'agent', 'dense', 'explain')),
            '/api/similar': ('similar', ('repo', 'n', 'dense')),
            '/api/expand': ('expand', ('query', 'agent')),
            '/api/repo': ('repo', ('repo',)),
            '/api/readme': ('readme', ('repo', 'n', 'branch')),
            '/api/trending': ('trending', ('language', 'days', 'n')),
            '/api/rate': ('rate', ()), '/api/cache': ('cache', ()),
            '/api/access': ('access', ()), '/api/github': ('github', ('address',)),
            '/api/root': ('root', ('repo',)),
            '/api/root_log': ('root_log', ('n',))}
        WRITES = {'/api/clear_cache': ('clear_cache', (), 'write'),
                  '/api/connect': ('connect', ('token', 'address'), 'write'),
                  '/api/disconnect': ('disconnect', ('address', 'login'), 'write'),
                  '/api/oauth': ('oauth', ('address', 'scope'), 'write'),
                  '/api/grant': ('grant', ('address', 'role'), 'admin'),
                  '/api/revoke': ('revoke', ('address',), 'admin'),
                  '/api/root_push': ('root_push',
                                     ('message', 'force', 'dry', 'repo', 'branch'),
                                     'admin'),
                  '/api/root_auto': ('root_auto', ('on', 'every', 'repo'), 'admin')}
        INTS = {'n', 'pages', 'stars', 'readmes', 'days', 'every', 'tick'}
        BOOLS = {'fresh', 'agent', 'dense', 'explain', 'force', 'dry', 'on'}

        def coerce(k, v):
            if k in INTS:
                return int(v)
            if k in BOOLS:
                return str(v).lower() not in ('0', 'false', '', 'no')
            return v

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else (
                    _json.dumps(body, default=str).encode() if ctype == 'application/json'
                    else body.encode())
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain')

            @staticmethod
            def _norm(p):
                # tolerate the gateway prefix both ways: /github/... (app route)
                # and the stripped /... that /api/github delivers
                if p == '/github' or p.startswith('/github/'):
                    p = p[len('/github'):] or '/'
                if p not in ('/', '/index.html') and not p.startswith('/api/'):
                    p = '/api' + p
                return p or '/'

            def _fail(self, e):
                code = {PermissionError: 403, KeyError: 404, ValueError: 400}.get(type(e), 500)
                self._send(code, {'error': str(e), 'type': type(e).__name__})

            def do_GET(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                if path in ('/', '/index.html'):
                    return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if path == '/api/whoami':
                    return self._send(200, gh.whoami(dict(self.headers)))
                if path in ('/api/root', '/api/root_log'):
                    try:
                        gh._authorize(dict(self.headers), need='write')
                    except PermissionError as e:
                        return self._send(403, {'error': str(e)})
                spec = READS.get(path)
                if not spec:
                    return self._send(404, {'error': f'unknown endpoint {path}'})
                fn, names = spec
                kw = {k: coerce(k, q[k]) for k in names if k in q}
                # reads are open, but they run against the caller's own GitHub
                # connection when they sent a token
                if fn in ('search', 'similar', 'repo', 'trending', 'rate'):
                    who = gh._token_address(dict(self.headers))
                    if who and 'address' not in kw:
                        kw['address'] = who
                try:
                    return self._send(200, getattr(gh, fn)(**kw))
                except TypeError as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:
                    return self._fail(e)

            def do_POST(self):
                path = self._norm(urlparse(self.path).path)
                spec = WRITES.get(path)
                if not spec:
                    return self._send(404, {'error': f'unknown endpoint {path}'})
                fn, names, need = spec
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                    body = _json.loads(self.rfile.read(n) or b'{}') if n else {}
                    who = gh._authorize(dict(self.headers), need=need)
                except PermissionError as e:
                    return self._send(403, {'error': str(e)})
                except Exception as e:
                    return self._send(400, {'error': str(e)})
                kw = {k: coerce(k, body[k]) for k in names if k in body}
                # act as the caller's key unless an admin named another
                if 'address' in names and not kw.get('address'):
                    kw['address'] = who['address']
                try:
                    return self._send(200, getattr(gh, fn)(**kw))
                except Exception as e:
                    return self._fail(e)

        return H

    # --- meta / mod protocol ------------------------------------------------

    def forward(self, query: str = None, **kwargs):
        """Null call returns info; a bare string is treated as a search."""
        return self.search(query, **kwargs) if query else self.info()

    def info(self) -> dict:
        acl = self._acl()
        try:
            connected = self.github().get('login')
        except Exception:
            connected = None
        return {
            'name': 'github',
            'description': self.description,
            'keyless': True,
            'stages': ['expand → lexical queries', 'retrieve → public search API',
                       'rank → local embeddings (fallback: tf-idf)'],
            'embed_model': EMBED_MODEL,
            'github_account': connected,
            'authenticated': bool(self._token()),
            'login': 'optional, delegated to the git module (m github/oauth)',
            'owner': acl['owner'] or self._host_owner(),
            'cache': {'entries': len(self._cache()), 'ttl': CACHE_TTL},
            'port': APP_PORT,
            'url': f'http://localhost:{APP_PORT}',
            'fns': ['search', 'similar', 'expand', 'candidates', 'rank', 'repo', 'readme',
                    'trending', 'rate', 'cache', 'clear_cache', 'oauth', 'connect',
                    'disconnect', 'github', 'access', 'grant', 'revoke', 'token',
                    'whoami', 'serve', 'kill', 'worker', 'stop_worker', 'info'],
            'try': 'm github/search "run untrusted wasm in a sandbox"',
        }


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>github — semantic repo search</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--fg:#e6edf3;--dim:#8b949e;--line:#30363d;--accent:#f0883e;--card:#161b22}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
header{padding:20px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline}
h1{margin:0;font-size:16px;letter-spacing:.08em}
header span{color:var(--dim);font-size:12px}
main{max-width:900px;margin:0 auto;padding:20px}
form{display:flex;gap:8px;margin-bottom:8px}
input,select,button{background:var(--card);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:10px 12px;font:inherit}
input[type=text]{flex:1}
button{border-color:var(--accent);color:var(--accent);cursor:pointer}
button:hover{background:var(--accent);color:#0d1117}
.meta{color:var(--dim);font-size:12px;margin:10px 0 18px;min-height:18px}
.repo{border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:10px;background:var(--card)}
.repo a{color:var(--accent);text-decoration:none;font-weight:600}
.repo p{margin:6px 0;color:var(--fg)}
.tags{color:var(--dim);font-size:12px}
.score{float:right;color:var(--dim);font-size:12px}
.err{color:#f85149}
</style></head><body>
<header><h1>github</h1><span>semantic repo search — no key, no login</span></header>
<main>
<form id="f">
  <input type="text" id="q" placeholder="describe what you are looking for…" autofocus>
  <input type="text" id="lang" placeholder="language" size="8">
  <button type="submit">search</button>
</form>
<div class="meta" id="meta"></div>
<div id="out"></div>
</main>
<script>
const base = location.pathname.replace(/\\/$/,'');
const el = id => document.getElementById(id);
el('f').onsubmit = async e => {
  e.preventDefault();
  const q = el('q').value.trim(); if(!q) return;
  el('meta').textContent = 'searching…'; el('out').innerHTML = '';
  const p = new URLSearchParams({query:q, n:20});
  if (el('lang').value.trim()) p.set('language', el('lang').value.trim());
  try{
    const r = await fetch(`${base}/api/search?${p}`);
    const d = await r.json();
    if(d.error) throw new Error(d.error);
    el('meta').textContent =
      `${d.returned}/${d.candidates} candidates · ${d.ranker} · ${d.took}s · queries: ${d.queries.join(' | ')}`;
    el('out').innerHTML = d.results.map(x => `
      <div class="repo"><span class="score">${x.score}</span>
        <a href="${x.url}" target="_blank" rel="noopener">${x.name}</a>
        <p>${(x.description||'').replace(/[<>&]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</p>
        <div class="tags">★ ${x.stars} · ${x.language||'—'}${x.topics.length?' · '+x.topics.slice(0,6).join(', '):''}</div>
      </div>`).join('');
  }catch(err){ el('meta').innerHTML = `<span class="err">${err.message}</span>`; }
};
</script></body></html>
"""
