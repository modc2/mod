"""ONNX files committed to GitHub repos — the ONNX Model Zoo first.

`onnx/models` is the original zoo: ~2,300 .onnx files, the `validated/` set
the ONNX project tested plus the timm / torch-hub / transformers exports under
`Computer_Vision/`, `Natural_Language_Processing/`, `Generative_AI/` and
`Graph_Machine_Learning/`. One git-trees call lists every file in it.

The files are Git LFS objects, so the tree reports the size of the *pointer*
(~133 bytes), not the model. The real size is one small read of that pointer
away, and is fetched when somebody opens the row rather than 2,300 times up
front. Downloads go to media.githubusercontent.com, which serves the LFS body.

More repos: INFER_ZOO_GITHUB="owner/repo@branch,owner/repo" — any public repo
with .onnx files committed to it. GITHUB_TOKEN is used if set (60 → 5,000
calls an hour), never required.
"""

import os
import re

from .base import Source, ZooError, assemble, companions, entry, http, is_onnx_path

DEFAULT_REPOS = ['onnx/models@main']


def _repos():
    extra = [r.strip() for r in os.environ.get('INFER_ZOO_GITHUB', '').split(',') if r.strip()]
    out = []
    for r in DEFAULT_REPOS + extra:
        repo, _, branch = r.partition('@')
        if (repo, branch or 'main') not in out:
            out.append((repo, branch or 'main'))
    return out


def _headers():
    tok = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    return {'authorization': f'Bearer {tok}'} if tok else {}


def _opset(path):
    m = re.search(r'(?:opset|-)(\d{1,2})(?:\.onnx|_)', path, re.I)
    return int(m.group(1)) if m else None


class GitHub(Source):
    name = 'github'
    title = 'ONNX Model Zoo (GitHub) + any repo you add'
    kind = 'remote'
    home = 'https://github.com/onnx/models'
    note = ('the original ONNX Model Zoo — validated models plus the timm, '
            'torch-hub and transformers exports. Add repos with INFER_ZOO_GITHUB.')

    def scrape(self, job, state):
        repos = _repos()
        done = set(state.get('repos_done') or [])
        for i, (repo, branch) in enumerate(repos):
            if f'{repo}@{branch}' in done:
                continue
            job.say(f'listing {repo}@{branch}')
            tree = http(f'https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1',
                        headers=_headers(), job=job)
            items = [t for t in tree.get('tree', []) if t.get('type') == 'blob']
            paths = [t['path'] for t in items]
            rows = []
            for t in items:
                p = t['path']
                if not is_onnx_path(p):
                    continue
                parts = p.split('/')
                area = parts[0]
                name = parts[-1][:-5]
                # validated/<domain>/<task>/<family>/model/<file>.onnx
                task = parts[2] if area == 'validated' and len(parts) > 3 else area
                family = parts[3] if area == 'validated' and len(parts) > 4 else (
                    parts[1] if len(parts) > 2 else None)
                hub = parts[1].rsplit('_', 1)[-1] if area != 'validated' and len(parts) > 2 else None
                files = [[p, None]] + [[c, None] for c in companions(p, paths)]
                rows.append(entry(
                    self.name, f'{repo}@{branch}:{p}', name=name, files=files,
                    task=task.lower().replace('_', '-'), author=repo.split('/')[0],
                    family=family, opset=_opset(p), repo=repo,
                    tags=[x for x in (area, family, hub) if x],
                    url=f'https://github.com/{repo}/blob/{branch}/{p}',
                    license='apache-2.0' if repo == 'onnx/models' else None,
                    lfs=True))
            done.add(f'{repo}@{branch}')
            yield rows, {'repos_done': sorted(done)}, i == len(repos) - 1, None
        if not repos:
            yield [], {}, True, 0

    @staticmethod
    def _split(e):
        repo_branch, path = e['ref'].split(':', 1)
        repo, branch = repo_branch.split('@', 1)
        return repo, branch, path

    def files(self, e, job=None):
        """Read each LFS pointer for the true size."""
        repo, branch, _ = self._split(e)
        out = []
        for p, size in e.get('files') or []:
            if size is None:
                try:
                    blob, _ = http(f'https://raw.githubusercontent.com/{repo}/{branch}/{p}',
                                   raw=True, limit=4096, job=job, headers=_headers())
                    m = re.search(rb'size (\d+)', blob)
                    size = int(m.group(1)) if m else len(blob)
                except ZooError:
                    pass
            out.append([p, size])
        return out

    def fetch(self, e, path, job=None, max_bytes=None):
        repo, branch, main = self._split(e)
        path = path or main
        media = f'https://media.githubusercontent.com/media/{repo}/{branch}/'
        blob, _ = http(media + path, raw=True, limit=max_bytes, job=job,
                       headers=_headers(), timeout=600)
        return assemble(blob, lambda p: http(media + p, raw=True, limit=max_bytes,
                                             job=job, timeout=600)[0],
                        onnx_path=path, max_bytes=max_bytes)
