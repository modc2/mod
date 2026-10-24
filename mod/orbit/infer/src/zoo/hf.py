"""Every HuggingFace repository that carries an .onnx file.

The biggest zoo by far: HuggingFace tags a repo `onnx` when it finds .onnx
files in it, and `filter=onnx` lists every one of them — onnx-community,
Xenova, onnxmodelzoo, qualcomm, sentence-transformers' `onnx/` folders, and
tens of thousands of individual uploads. One listing call returns 1,000 repos
*with their file names* (`expand[]=siblings`), so the whole crawl is roughly
one request per thousand repos, paged by the cursor in the `Link` header.

What the listing does not say is file sizes. Those are one call per repo
(`?blobs=true`) and are fetched when a row is opened or planted, not for the
whole index.

HuggingFace rate-limits anonymous callers to 500 calls per 5-minute window and
says so in a `ratelimit` header on every answer; `base.http` reads it.
HF_TOKEN is used when set — it raises the limit and opens gated repos — and is
never required.

Sort is `createdAt`, not the default trending score: a crawl ordered by
something that changes while you page through it skips and repeats rows.
"""

import os
import urllib.parse

from .base import (Source, ZooError, assemble, entry, http, is_data_path, is_onnx_archive,
                   is_onnx_path, next_link, unpack)

API = 'https://huggingface.co/api/models'
# Orgs that ship .onnx inside archives HuggingFace does not tag, listed in a
# second pass without the onnx filter. Extend with INFER_ZOO_HF_AUTHORS=a,b.
EXTRA_AUTHORS = ['qualcomm']
EXPAND = ('siblings', 'pipeline_tag', 'downloads', 'likes', 'tags', 'gated',
          'lastModified', 'library_name', 'createdAt')
# HF tags that are noise for a model browser.
_SKIP_TAG = ('region:', 'endpoints_compatible', 'autotrain_compatible', 'onnx',
             'deploy:', 'text-generation-inference', 'has_space')


def _token():
    tok = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if not tok:
        for p in ('~/.cache/huggingface/token', '~/.huggingface/token'):
            try:
                with open(os.path.expanduser(p)) as f:
                    tok = f.read().strip()
                    break
            except OSError:
                pass
    return tok


def _headers():
    tok = _token()
    return {'authorization': f'Bearer {tok}'} if tok else {}


def row(m, source='huggingface'):
    sib = [s['rfilename'] for s in m.get('siblings') or []]
    onnx_files = [p for p in sib if is_onnx_path(p)] or [p for p in sib if is_onnx_archive(p)]
    if not onnx_files:
        return None
    data = [p for p in sib if is_data_path(p)]
    tags = [t for t in m.get('tags') or [] if not t.startswith(_SKIP_TAG)]
    lic = next((t.split(':', 1)[1] for t in tags if t.startswith('license:')), None)
    tags = [t for t in tags if not t.startswith(('license:', 'base_model:', 'dataset:',
                                                 'arxiv:'))][:12]
    rid = m.get('id') or m.get('modelId')
    return entry(
        source, rid, name=rid.split('/', 1)[-1],
        files=[[p, None] for p in onnx_files] + [[p, None] for p in data],
        task=m.get('pipeline_tag'), author=rid.split('/', 1)[0] if '/' in rid else None,
        library=m.get('library_name'), downloads=m.get('downloads'),
        likes=m.get('likes'), gated=m.get('gated') or None, license=lic, tags=tags,
        updated=m.get('lastModified'), created=m.get('createdAt'),
        url=f'https://huggingface.co/{rid}', variants=len(onnx_files))


class HuggingFace(Source):
    name = 'huggingface'
    title = 'HuggingFace Hub — every repo tagged onnx'
    kind = 'remote'
    home = 'https://huggingface.co/models?library=onnx'
    note = ('every public HuggingFace repo with an .onnx in it. Anonymous is '
            '500 calls / 5 min; HF_TOKEN raises that and opens gated repos.')

    def scrape(self, job, state):
        """Pass 0: everything tagged onnx. Then one pass per EXTRA_AUTHORS org,
        unfiltered, keeping repos with an .onnx or an .onnx archive."""
        import json
        authors = EXTRA_AUTHORS + [a.strip() for a in
                                   os.environ.get('INFER_ZOO_HF_AUTHORS', '').split(',')
                                   if a.strip() and a.strip() not in EXTRA_AUTHORS]
        phase, url, seen = state.get('phase', 0), state.get('next'), state.get('seen', 0)
        while phase <= len(authors):
            if not url:
                q = [('limit', '1000'), ('sort', 'createdAt'), ('direction', '-1')]
                q = ([('filter', 'onnx')] if phase == 0
                     else [('author', authors[phase - 1])]) + q
                url = f'{API}?{urllib.parse.urlencode(q + [("expand[]", x) for x in EXPAND])}'
            blob, headers = http(url, raw=True, job=job, headers=_headers(), timeout=180)
            page = json.loads(blob)
            rows = [r for r in (row(m, self.name) for m in page) if r]
            seen += len(page)
            url = next_link(headers)
            job.say(f'{"onnx-tagged" if phase == 0 else authors[phase - 1]}: {seen:,} repos '
                    f'read, {len(rows)} with onnx on this page')
            if not url:
                phase += 1
            yield rows, {'phase': phase, 'next': url, 'seen': seen}, phase > len(authors), None

    def files(self, e, job=None):
        if all(isinstance(s, int) for _, s in e.get('files') or []):
            return e['files']
        d = http(f"{API}/{urllib.parse.quote(e['ref'], safe='/')}?blobs=true",
                 job=job, headers=_headers())
        sizes = {s['rfilename']: (s.get('lfs') or {}).get('size') or s.get('size')
                 for s in d.get('siblings') or []}
        return [[p, sizes.get(p, s)] for p, s in e.get('files') or []]

    def fetch(self, e, path, job=None, max_bytes=None):
        rid = e['ref']
        base = f'https://huggingface.co/{rid}/resolve/main/'
        try:
            blob, _ = http(base + urllib.parse.quote(path), raw=True, limit=max_bytes,
                           job=job, headers=_headers(), timeout=900)
        except ZooError as ex:
            if ex.status in (401, 403):
                raise ZooError(f'{rid} is gated — accept its terms on huggingface.co '
                               f'and set HF_TOKEN', 403, self.name)
            raise
        if is_onnx_archive(path):
            return unpack(blob, path, max_bytes=max_bytes)
        return assemble(blob, lambda p: http(base + urllib.parse.quote(p), raw=True,
                                             limit=max_bytes, job=job, headers=_headers(),
                                             timeout=900)[0],
                        onnx_path=path, max_bytes=max_bytes)
