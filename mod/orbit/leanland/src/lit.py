"""
The literature side: papers as markdown files with front matter.

Why files and not a database: a paper entry is mostly prose — what the paper
claims, which equation you actually want, what you decided not to believe — and
prose belongs in a diff. `lit/<key>.md` is the citation key the library's
`@[source ...]` attributes point at, so the join between a formula and its
provenance is a filename.
"""
from __future__ import annotations

import os
import re
import urllib.request

try:
    import yaml
except ImportError:                                     # pragma: no cover
    yaml = None

ARXIV_API = 'http://export.arxiv.org/api/query?id_list={}'
FM = re.compile(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', re.S)


def split_front_matter(text: str) -> tuple[dict, str]:
    mt = FM.match(text)
    if not mt:
        return {}, text
    head, body = mt.group(1), mt.group(2)
    if yaml is not None:
        return yaml.safe_load(head) or {}, body
    meta = {}                                            # good enough for flat keys
    for line in head.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            meta[k.strip()] = v.strip().strip('"\'')
    return meta, body


def dump_front_matter(meta: dict, body: str) -> str:
    if yaml is not None:
        head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    else:
        head = '\n'.join(f'{k}: {v}' for k, v in meta.items())
    return f'---\n{head}\n---\n\n{body.strip()}\n'


class Paper(dict):
    """A literature entry. `notes` is the markdown body."""

    @property
    def key(self) -> str:
        return self['key']

    def cite(self) -> str:
        bits = [self.get('title', self.key)]
        if self.get('authors'):
            bits.append(self['authors'])
        if self.get('year'):
            bits.append(str(self['year']))
        return ', '.join(bits)


class Lit:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, key: str) -> str:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', key or ''):
            raise ValueError(f'bad citation key {key!r}')
        return os.path.join(self.root, f'{key}.md')

    def keys(self) -> list[str]:
        return sorted(f[:-3] for f in os.listdir(self.root) if f.endswith('.md'))

    def get(self, key: str) -> Paper:
        with open(self.path(key)) as f:
            meta, body = split_front_matter(f.read())
        return Paper({'key': key, **meta, 'notes': body.strip()})

    def all(self) -> dict[str, Paper]:
        return {k: self.get(k) for k in self.keys()}

    def add(self, key: str, title: str = '', authors: str = '', year=None,
            url: str = '', tags=None, notes: str = '', **extra) -> Paper:
        meta = {'title': title, 'authors': authors, 'year': year, 'url': url,
                'tags': list(tags or []), **extra}
        meta = {k: v for k, v in meta.items() if v not in (None, '', [])}
        with open(self.path(key), 'w') as f:
            f.write(dump_front_matter(meta, notes))
        return self.get(key)

    def note(self, key: str, text: str) -> Paper:
        """Append to a paper's notes — where a reading session accumulates."""
        p = self.get(key)
        meta = {k: v for k, v in p.items() if k not in ('key', 'notes')}
        with open(self.path(key), 'w') as f:
            f.write(dump_front_matter(meta, (p['notes'] + '\n\n' + text.strip()).strip()))
        return self.get(key)

    def rm(self, key: str) -> dict:
        os.remove(self.path(key))
        return {'removed': key}

    def search(self, q: str) -> list[Paper]:
        q = (q or '').lower()
        out = []
        for k in self.keys():
            p = self.get(k)
            hay = ' '.join(str(v) for v in p.values()).lower()
            if q in hay:
                out.append(p)
        return out

    def add_arxiv(self, arxiv_id: str, key: str = None, notes: str = '') -> Paper:
        """Pull title/authors/abstract from arXiv. Needs network; says so if not."""
        try:
            with urllib.request.urlopen(ARXIV_API.format(arxiv_id), timeout=20) as r:
                xml = r.read().decode()
        except Exception as e:
            raise RuntimeError(f'could not reach arXiv ({e}); add the entry by hand '
                               f'with lit/add') from e
        def tag(name, default=''):
            mt = re.search(rf'<{name}>(.*?)</{name}>', xml, re.S)
            return re.sub(r'\s+', ' ', mt.group(1)).strip() if mt else default
        entry = xml.split('<entry>', 1)[-1]
        authors = ', '.join(re.findall(r'<name>(.*?)</name>', entry))
        title = tag('title')
        published = tag('published')
        key = key or (re.sub(r'[^a-z]', '', authors.split(',')[0].split()[-1].lower())
                      + (published[:4] if published else ''))
        body = notes or ('## Abstract\n\n' + tag('summary'))
        return self.add(key, title=title, authors=authors,
                        year=int(published[:4]) if published[:4].isdigit() else None,
                        url=f'https://arxiv.org/abs/{arxiv_id}', arxiv=arxiv_id, notes=body)
