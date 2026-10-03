#!/usr/bin/env python3
"""plinyville kinds — what each repo *is*, so you can ask for one sort of thing.

Forty-seven repos arrive as one wall of cartridges. Twelve of them are apps you
can press RUN on; fourteen are prompt corpora; a dozen are Python you would have
to clone; three are empty. "L1B3RT4S" and "AutoTemp" are not the same kind of
object at all, and a gallery that lists them side by side with no way to say
*which sort I want* is a pile, not a catalogue.

So every repo gets one or more **types**, and everything that lists repos takes
`type=`:

    GET /repos?type=jailbreak          the liberation prompts
    GET /market?type=app               what the arcade can run
    GET /run?type=jailbreak            (the arcade, filtered the same way)
    GET /types                         the taxonomy, with counts and evidence
    POST /chat {"types": ["jailbreak"]}   what the agent is allowed to read

The classifier is a **heuristic with its receipts showing**. Every type a repo
gets carries the evidence that put it there — the word, and where it was found
(the name, the GitHub description, a topic, the README, a filename). Nothing is
inferred silently: `GET /types?repo=L1B3RT4S` shows the whole reasoning, and a
type nobody can justify is a type nobody should filter on.

Two inputs are not guesses at all and outrank the words:

* what `run.py` decided (`kind: web` → it is an **app**, `exhibit` → **exhibit**,
  `empty` → **empty**, `python` → at least a **tool**),
* `PINNED`, a short list of repos whose README says nothing useful about what
  they are. Each pin says why it is pinned; there are eight, and the API marks
  them `source: pinned` so nobody mistakes a decision for a measurement.
"""
import json
import os
import re
import time

RUN_KIND_TYPES = {
    'web': 'app',
    'exhibit': 'exhibit',
    'empty': 'empty',
    'python': 'tool',
    'notebook': 'tool',
    'source': 'app',            # a page that needs a build is still an app
    'demo': 'app',
    'fragment': 'app',
    'incomplete': 'app',
}

# The taxonomy. `id` is what you pass as ?type=, `label` is what the console
# draws on the pill, `blurb` is the one line under it.
TYPES = [
    {'id': 'jailbreak', 'label': 'JAILBREAK',
     'blurb': 'prompts that unlock a model — liberation sets, god-mode primers, '
              'the DAN family and their descendants'},
    {'id': 'system-prompt', 'label': 'SYS PROMPT',
     'blurb': "a model's own instructions, extracted and kept verbatim"},
    {'id': 'redteam', 'label': 'RED TEAM',
     'blurb': 'tooling that attacks or probes a model on purpose'},
    {'id': 'app', 'label': 'APP',
     'blurb': 'a page — the arcade can run these here, sandboxed'},
    {'id': 'tool', 'label': 'TOOL',
     'blurb': 'code you run yourself: a CLI, a library, an agent'},
    {'id': 'writing', 'label': 'WRITING',
     'blurb': 'essays, lore, poetry, manifestos'},
    {'id': 'exhibit', 'label': 'EXHIBIT',
     'blurb': 'a live attack, hosted here defanged for study'},
    {'id': 'empty', 'label': 'EMPTY',
     'blurb': 'no commits — the repo is a name and nothing else'},
]
TYPE_IDS = [t['id'] for t in TYPES]
LABELS = {t['id']: t['label'] for t in TYPES}

# word -> weight, per type. Matched case-insensitively as whole words against
# the name, the description, the topics, the README and the filenames.
WORDS = {
    'jailbreak': {
        'jailbreak': 5, 'jailbreaks': 5, 'jailbreaking': 5, 'liberation': 5,
        'liberating': 3, 'godmode': 5, 'god mode': 5, 'g0dm0d3': 5,
        'uncensored': 3, 'unrestricted': 3, 'refusal': 3, 'refusals': 3,
        'bypass': 3, 'bypassing': 3, 'guardrail': 3, 'guardrails': 3,
        'dan': 2, 'divergent': 2, 'unlock': 2, 'unlocked': 2,
        'prompt injection': 3, 'obliterate': 2, 'freedom': 1, 'l1b3rt4s': 5,
        'pliny the liberator': 2, 'leetspeak': 1, 'refusal-free': 3,
    },
    'system-prompt': {
        'system prompt': 5, 'system prompts': 5, 'system-prompt': 5,
        'leaked': 4, 'leak': 3, 'leaks': 3, 'extracted': 2, 'verbatim': 2,
        'prompt leak': 5, 'instructions leak': 4, 'cl4r1t4s': 5,
        'transparency': 1, 'living document': 2,
    },
    'redteam': {
        'red team': 5, 'red-team': 5, 'redteam': 5, 'red teaming': 5,
        'adversarial': 4, 'attack': 3, 'attacks': 3, 'exploit': 3,
        'exploits': 3, 'fuzz': 3, 'fuzzing': 3, 'payload': 2, 'payloads': 2,
        'vulnerability': 3, 'vulnerabilities': 3, 'pentest': 4,
        'penetration testing': 4, 'automated testing of llm': 4,
        'steganography': 2, 'obfuscation': 2, 'hijack': 3, 'phishing': 3,
    },
    'tool': {
        'cli': 3, 'pip install': 4, 'npm install': 3, 'library': 2,
        'framework': 2, 'api': 1, 'agent': 2, 'automate': 2, 'automation': 2,
        'requirements.txt': 3, 'setup.py': 3, 'package.json': 1,
        'usage': 1, 'python': 1,
    },
    'writing': {
        'essay': 4, 'essays': 4, 'poem': 4, 'poetry': 4, 'poetic': 3,
        'lore': 3, 'manifesto': 4, 'story': 2, 'stories': 2, 'fiction': 3,
        'novel': 2, 'mythos': 3, 'scripture': 3, 'gospel': 3, 'verse': 2,
    },
    'app': {
        'in your browser': 3, 'web app': 3, 'webapp': 3, 'demo': 1,
        'index.html': 2, 'single page': 2, 'no install': 2,
    },
    'exhibit': {},
    'empty': {},
}
# Where a word was found, and what that is worth. A word in the repo's own name
# is a claim about identity; the same word 400 lines into a README is a mention.
FIELD_WEIGHT = {'name': 3.0, 'description': 2.0, 'topics': 2.0,
                'readme': 1.0, 'files': 1.0}
FIELD_CAP = {'readme': 3, 'files': 3}     # a word repeated is still one signal
SCORE_MIN = 4.0                           # below this it is a mention, not a type
RUN_SCORE = 16.0                          # what run.py measured, not what prose claims

# Repos the words get wrong, and the reason. Each of these was read by hand;
# `source: pinned` on the API says which repos are a decision rather than a
# measurement, and `add`/`drop` say exactly what the pin changed.
PINNED = {
    'L1B3RT4S': {'add': ['jailbreak'],
                 'why': 'the corpus itself — the README is ASCII art, the '
                        'directories are one model per file of liberation prompts'},
    'CL4R1T4S': {'add': ['system-prompt'],
                 'why': 'the system-prompt collection; its own README calls it '
                        'a transparency project, never "system prompt"'},
    'V3R1T4S': {'add': ['jailbreak'],
                'why': 'prompts, in the same house style as L1B3RT4S'},
    'Anomalous-Outputs': {'add': ['jailbreak'],
                          'why': 'model outputs collected from jailbroken sessions'},
    'Misc.-Prompt-Hacks': {'add': ['jailbreak'],
                           'why': 'named for what it is; no README to say it'},
    'BasiliskToken': {'add': ['writing'], 'drop': ['tool'],
                      'why': 'a myth with a token attached, not a program'},
    'elder-plinius.github.io': {'add': ['exhibit', 'redteam'], 'drop': ['app'],
                                'why': 'the pastejacking PoC — hosted defanged, '
                                       'never runnable from here'},
    'Gandalf-Solutions': {'add': ['jailbreak'],
                          'why': "solutions to Lakera's Gandalf levels — "
                                 'prompt attacks, one per level'},
}

INDEX = os.path.expanduser(os.environ.get('PLINYVILLE_KINDS_INDEX',
                                          '~/.mod/pliny/kinds.json'))
CACHE_VERSION = 2


def _words(text: str) -> str:
    return re.sub(r'[^a-z0-9+#. -]+', ' ', (text or '').lower())


class Kinds:
    """What sort of thing is each repo. Cached, and cheap to rebuild."""

    def __init__(self, market=None, runner=None, index_path=None):
        self.market = market
        self.runner = runner
        # The cache file. An instance can hold its own (a test, a second
        # market) without reaching into the module's global and leaving it
        # pointed at a directory that no longer exists.
        self.index_path = index_path or INDEX
        self._mem = None

    # ── classifying one repo ────────────────────────────────────────────────

    def classify(self, card: dict, run: dict = None, readme: str = '',
                 files=()) -> dict:
        """One repo in, {types, primary, why, source} out.

        `card` is a market card (name/description/language/topics), `run` the
        run-index entry, `readme` the archived README and `files` its top-level
        filenames. Everything is optional: a repo with nothing but a name still
        classifies, it just says so."""
        name = card.get('name') or card.get('repo') or ''
        fields = {
            'name': _words(re.sub(r'[-_.]', ' ', name)),
            'description': _words(card.get('description') or ''),
            'topics': _words(' '.join(card.get('topics') or [])),
            'readme': _words((readme or '')[:20_000]),
            'files': _words(' '.join(files)),
        }
        scores, why = {}, {}
        for tid, words in WORDS.items():
            total, ev = 0.0, []
            for word, w in words.items():
                pat = re.compile(r'(?<![a-z0-9])' + re.escape(word) + r'(?![a-z0-9])')
                for field, text in fields.items():
                    n = len(pat.findall(text))
                    if not n:
                        continue
                    n = min(n, FIELD_CAP.get(field, 1))
                    total += w * FIELD_WEIGHT[field] * n
                    ev.append({'word': word, 'in': field, 'hits': n})
            if total:
                scores[tid] = round(total, 1)
                why[tid] = sorted(ev, key=lambda e: -e['hits'])[:5]

        types = {t for t, s in scores.items() if s >= SCORE_MIN}
        source = 'words' if types else 'none'

        # What the runner measured beats what the README says about itself.
        rk = (run or {}).get('kind')
        if rk in RUN_KIND_TYPES:
            t = RUN_KIND_TYPES[rk]
            if t not in types:
                types.add(t)
                why.setdefault(t, []).append(
                    {'word': f'run kind: {rk}', 'in': 'run', 'hits': 1})
            # A measurement outranks a mention. NATURALIS-FUTURA's README says
            # "jailbreak" twice, which is enough to file it under jailbreak, but
            # the thing itself is a page we *watched load* — so what the runner
            # saw scores above a word found in prose and takes `primary`.
            scores[t] = max(scores.get(t, 0), RUN_SCORE)
            source = 'run' if source == 'none' else source

        pin = PINNED.get(name)
        if pin:
            for t in pin.get('add') or []:
                types.add(t)
                scores[t] = max(scores.get(t, 0), 99)
            for t in pin.get('drop') or []:
                types.discard(t)
            source = 'pinned'

        # `empty` is exclusive: a repo with no commits is nothing else.
        if 'empty' in types:
            types = {'empty'}
        if not types:
            types = {'tool'} if (card.get('language')) else set()

        order = sorted(types, key=lambda t: (-scores.get(t, 0), TYPE_IDS.index(t)))
        return {
            'repo': name,
            'types': order,
            'primary': order[0] if order else None,
            'labels': [LABELS[t] for t in order],
            'scores': {t: scores.get(t, 0) for t in order},
            'why': {t: why.get(t, [{'word': (pin or {}).get('why', 'pinned'),
                                    'in': 'pinned', 'hits': 1}]) for t in order},
            'source': source,
            'pinned': (pin or {}).get('why'),
        }

    # ── the whole corpus ────────────────────────────────────────────────────

    def index(self, refresh=False) -> dict:
        """{repo: classification} for every repo in the market, cached on disk."""
        if self._mem is not None and not refresh:
            return self._mem
        cached = {}
        if not refresh:
            try:
                with open(self.index_path, encoding='utf-8') as f:
                    got = json.load(f) or {}
                if got.get('version') == CACHE_VERSION:
                    cached = got.get('repos') or {}
            except (OSError, json.JSONDecodeError):
                cached = {}
        cat = self.market.catalog()
        runs = {}
        if self.runner is not None:
            try:
                runs = self.runner.index()
            except Exception:                                # noqa: BLE001
                runs = {}
        out, changed = {}, False
        for card in cat.get('mods') or []:
            name = card['name']
            stamp = '%s:%s' % (card.get('archived_at') or 0,
                               (runs.get(name) or {}).get('stamp') or '')
            was = cached.get(name)
            if was and was.get('stamp') == stamp and not refresh:
                out[name] = was
                continue
            readme, files = self._corpus(name)
            c = self.classify(card, runs.get(name), readme, files)
            c['stamp'] = stamp
            out[name] = c
            changed = True
        if changed or refresh:
            self._save({'version': CACHE_VERSION, 'updated': time.time(),
                        'repos': out})
        self._mem = out
        return out

    def _corpus(self, name):
        """The README and the top-level filenames, offline if we have them."""
        readme, files = '', []
        try:
            b = self.market.content(name)
            readme = b.get('readme') or ''
            files = [e['path'] for e in (b.get('tree') or [])
                     if '/' not in e['path']][:80]
        except Exception:                                    # noqa: BLE001
            try:
                readme = (self.market.repo_readme(name) or {}).get('markdown') or ''
            except Exception:                                # noqa: BLE001
                readme = ''
        return readme, files

    def _save(self, obj):
        try:
            os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
            tmp = self.index_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(obj, f)
            os.replace(tmp, self.index_path)
        except OSError:
            pass

    # ── the API surface ─────────────────────────────────────────────────────

    def catalog(self, repo=None, refresh=False) -> dict:
        """The taxonomy with live counts — or, with `repo=`, one repo's receipts."""
        idx = self.index(refresh=refresh)
        if repo:
            name = repo
            got = idx.get(name)
            if not got:
                for k in idx:
                    if k.lower() == str(name).lower():
                        got, name = idx[k], k
                        break
            if not got:
                raise ValueError(f'{repo} is not in the market')
            return {**got, 'types_catalog': TYPES}
        counts = {t['id']: 0 for t in TYPES}
        for c in idx.values():
            for t in c.get('types') or []:
                counts[t] = counts.get(t, 0) + 1
        return {
            'types': [{**t, 'count': counts.get(t['id'], 0)} for t in TYPES],
            'repos': len(idx),
            'untyped': sorted(k for k, v in idx.items() if not v.get('types')),
            'pinned': {k: v['why'] for k, v in PINNED.items()},
            'note': 'a heuristic over the name, the description, the topics, the '
                    'README and the filenames, plus what the runner measured. '
                    'GET /types?repo=<name> shows why one repo landed where it did.',
        }

    def types_of(self, name) -> list:
        return (self.index().get(name) or {}).get('types') or []

    def filter(self, names, want) -> list:
        """Names that carry every type in `want` (AND — 'a jailbreak I can run'
        is `type=jailbreak,app`)."""
        want = self.parse(want)
        if not want:
            return list(names)
        idx = self.index()
        out = []
        for n in names:
            have = set((idx.get(n) or {}).get('types') or [])
            if want <= have:
                out.append(n)
        return out

    @staticmethod
    def parse(want) -> set:
        """'jailbreak,app' | ['jailbreak'] -> {'jailbreak','app'}; unknown ids
        raise, because silently returning everything is how a filter lies."""
        if not want:
            return set()
        if isinstance(want, str):
            want = re.split(r'[,\s]+', want)
        out = {str(w).strip().lower() for w in want if str(w).strip()}
        bad = out - set(TYPE_IDS)
        if bad:
            raise ValueError('unknown type(s): ' + ', '.join(sorted(bad))
                             + ' — known types: ' + ', '.join(TYPE_IDS))
        return out

    def join(self, catalog: dict) -> dict:
        """Annotate a market catalog in place with each mod's types."""
        try:
            idx = self.index()
        except Exception:                                    # noqa: BLE001
            return catalog
        for m in catalog.get('mods') or []:
            c = idx.get(m.get('name')) or {}
            m['types'] = c.get('types') or []
            m['type'] = c.get('primary')
            m['type_labels'] = c.get('labels') or []
        return catalog


if __name__ == '__main__':
    import sys
    from market import Market
    from run import Runner
    k = Kinds(Market(), Runner())
    if len(sys.argv) > 1:
        print(json.dumps(k.catalog(repo=sys.argv[1]), indent=2, default=str))
    else:
        idx = k.index(refresh=True)
        for n in sorted(idx):
            print('%-30s %-14s %s' % (n, idx[n].get('primary') or '-',
                                      ','.join(idx[n]['types'][1:]) or ''))
        print(json.dumps(k.catalog()['types'], indent=1))
