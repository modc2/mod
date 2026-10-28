"""ModelScope — Alibaba's model hub, every repo whose libraries include onnx.

~7,300 repos, heavy on what HuggingFace is thin on: FunASR speech models
(VAD, ASR, punctuation), Chinese NLP, OCR, and mirrors of onnx-community.
Listed through the same endpoint the site's own search uses
(`PUT /api/v1/dolphin/models`, filtered on `libraries contains onnx`), 100 per
page. File names and sizes are one call per repo
(`/repo/files?Recursive=true`) and are read when a row is opened, not for
the whole index. Downloads are anonymous.
"""

import urllib.parse

from .base import Source, ZooError, assemble, entry, http, is_data_path, is_onnx_path

API = 'https://www.modelscope.cn/api/v1'
PAGE = 100


def _q(rid):
    return urllib.parse.quote(rid, safe='/')


class ModelScope(Source):
    name = 'modelscope'
    title = 'ModelScope — every repo with onnx in its libraries'
    kind = 'remote'
    home = 'https://www.modelscope.cn/models?libraries=onnx'
    note = 'strong on speech (FunASR), OCR and Chinese NLP; anonymous downloads'

    def scrape(self, job, state):
        page = state.get('page', 1)
        total = state.get('total')
        while True:
            body = {'PageSize': PAGE, 'PageNumber': page, 'SortBy': 'Default',
                    'Target': '', 'SingleCriterion': [],
                    'Criterion': [{'category': 'libraries', 'predicate': 'contains',
                                   'values': ['onnx'], 'sub_values': []}]}
            d = http(f'{API}/dolphin/models', method='PUT', body=body, job=job)
            m = ((d or {}).get('Data') or {}).get('Model') or {}
            models = m.get('Models') or []
            total = m.get('TotalCount') or total
            rows = []
            for x in models:
                rid = f"{x.get('Path')}/{x.get('Name')}"
                tasks = x.get('Tasks') or []
                task = tasks[0].get('Name') if tasks and isinstance(tasks[0], dict) else None
                rows.append(entry(
                    self.name, rid, name=x.get('Name'), files=[],
                    task=task, author=x.get('Path'), downloads=x.get('Downloads'),
                    likes=x.get('Stars'), license=x.get('License'),
                    tags=[t for t in (x.get('Libraries') or []) if t != 'onnx'][:8]
                    + [t for t in (x.get('Language') or [])][:4],
                    about=(x.get('Description') or x.get('ChineseName') or '')[:240],
                    updated=x.get('LastUpdatedTime'),
                    url=f'https://www.modelscope.cn/models/{rid}', lazy_files=True))
            done = not models or (total and page * PAGE >= total)
            job.say(f'page {page} · {min(page * PAGE, total or 0):,}/{total or "?"} repos')
            page += 1
            yield rows, {'page': page, 'total': total}, bool(done), total
            if done:
                return

    def files(self, e, job=None):
        if e.get('files'):
            return e['files']
        d = http(f"{API}/models/{_q(e['ref'])}/repo/files?Recursive=true", job=job)
        fs = ((d or {}).get('Data') or {}).get('Files') or []
        return [[f['Path'], f.get('Size')] for f in fs
                if f.get('Type') == 'blob' and (is_onnx_path(f['Path']) or is_data_path(f['Path']))]

    def fetch(self, e, path, job=None, max_bytes=None):
        base = f"https://www.modelscope.cn/models/{_q(e['ref'])}/resolve/master/"
        blob, _ = http(base + urllib.parse.quote(path), raw=True, limit=max_bytes, job=job,
                       timeout=900)
        if blob[:1] == b'{' and b'"Code"' in blob[:200]:
            raise ZooError(f'modelscope refused {path}: {blob[:200]!r}', 404, self.name)
        return assemble(blob, lambda p: http(base + urllib.parse.quote(p), raw=True,
                                             limit=max_bytes, job=job, timeout=900)[0],
                        onnx_path=path, max_bytes=max_bytes)
