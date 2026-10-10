"""Kaggle Models — every model with an instance whose framework is ONNX.

Kaggle groups a model into *instances* (one per framework and variation), and
only some of a model's instances are ONNX, so the row is the instance, not the
model. The listing is public at 50 models a page; there is no framework
filter on it, so the crawl reads the whole catalogue (two passes: a `search=onnx`
pass that finds the obvious ones in a few calls, then everything) and keeps
the ONNX instances. Downloads are an anonymous .tar.gz per instance version;
the .onnx is pulled out of it on plant.
"""

import urllib.parse

from .base import Source, ZooError, _sleep, assemble, entry, http, is_onnx_path, unpack

API = 'https://www.kaggle.com/api/v1'
# (search, sortBy) — see the module docstring for why there are eight.
PASSES = [('onnx', None)] + [(None, s) for s in (
    'hotness', 'downloadCount', 'voteCount', 'notebookCount', 'createTime',
    'publishTime', 'updateTime')]


class Kaggle(Source):
    name = 'kaggle'
    title = 'Kaggle Models — ONNX instances'
    kind = 'remote'
    home = 'https://www.kaggle.com/models?framework=onnx'
    note = 'every Kaggle model instance published as ONNX; anonymous tarball download'

    def scrape(self, job, state):
        pi = state.get('pass', 0)
        token = state.get('token')
        seen = state.get('seen', 0)
        page = state.get('page', 0)
        found = set(state.get('found') or [])
        gaps = list(state.get('gaps') or [])
        while pi < len(PASSES):
            search, order = PASSES[pi]
            q = {'pageSize': 50}
            if search:
                q['search'] = search
            if order:
                q['sortBy'] = order
            if token:
                q['pageToken'] = token
            d = None
            for attempt in range(4):
                try:
                    d = http(f'{API}/models/list?{urllib.parse.urlencode(q)}', job=job,
                             tries=3)
                    break
                except ZooError as ex:
                    # Kaggle also answers a perfectly good page token with a
                    # transient 404 now and then — the same token is 200 a
                    # minute later — so a 404 is retried like a 5xx.
                    if ex.status not in (404, 500, 502):
                        raise
                    if attempt == 3:
                        # Page 1 failing means the listing is down — that is
                        # an error. A token failing later means this ordering
                        # ends here (seen at page 7 of voteCount, and ~117 of
                        # the others); the other orderings overlap it.
                        if page == 0:
                            raise
                        job.say(f'{order or search}: kaggle stops paging at page {page} '
                                f'— next ordering')
                        gaps.append(f'{order or search}@{page}')
                        d = {}
                    else:
                        job.say(f'HTTP {ex.status} on page {page} — retrying in {10 * (attempt + 1)}s')
                        _sleep(10 * (attempt + 1), job)
            models = (d or {}).get('models') or []
            seen += len(models)
            page += 1
            rows = []
            for m in models:
                for i in m.get('instances') or []:
                    if (i.get('framework') or '').lower() != 'onnx':
                        continue
                    ref = f"{m['ref']}/{i.get('framework')}/{i.get('slug')}/{i.get('versionNumber')}"
                    if ref in found:
                        continue
                    found.add(ref)
                    rows.append(entry(
                        self.name, ref, name=f"{m.get('slug')}/{i.get('slug')}",
                        files=[[f"{i.get('slug')}.tar.gz", i.get('totalUncompressedBytes')
                                and int(i['totalUncompressedBytes'])]],
                        author=m['ref'].split('/')[0], likes=m.get('voteCountNullable'),
                        license=i.get('licenseName'), task=None,
                        about=(m.get('subtitle') or '')[:240],
                        tags=['kaggle', i.get('modelInstanceType') or 'instance'],
                        url=i.get('url') or f"https://www.kaggle.com/models/{m['ref']}",
                        archive=True, download=i.get('downloadUrl')))
            token = (d or {}).get('nextPageToken') or None
            job.say(f'pass {pi + 1}/{len(PASSES)} ({order or search}): page {page}, '
                    f'{seen:,} models read, {len(found)} onnx instances')
            if not token or not models:
                pi, token, page = pi + 1, None, 0
            yield rows, {'pass': pi, 'token': token, 'seen': seen, 'page': page,
                         'found': sorted(found), 'gaps': gaps}, pi >= len(PASSES), None

    def files(self, e, job=None):
        return e.get('files') or []

    def fetch(self, e, path, job=None, max_bytes=None):
        blob, _ = http(f"{API}/models/{e['ref']}/download", raw=True, limit=max_bytes,
                       job=job, timeout=900)
        try:
            return unpack(blob, e['ref'], max_bytes=max_bytes,
                          want=path if path and is_onnx_path(path) else None)
        except ZooError as ex:
            if 'neither' in ex.message:        # served bare, not archived
                return assemble(blob, lambda p: b'', max_bytes=max_bytes)
            raise ZooError(ex.message, ex.status, self.name)
