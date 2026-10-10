"""The artist store: assets on disk, projects in SQLite, optional ffmpeg render.

One directory (~/.mod/artist, override with ARTIST_DIR) holds everything:
    artist.db      projects + the asset index
    assets/        the media files themselves, named by content hash

An asset's id is a content hash, so importing the same file twice — or two
people trading project packs — never duplicates bytes. A project is a name
plus a timeline: two lanes of clips (video, audio) played in order. The
browser can compile a timeline by itself; render() is the optional server-side
path and simply reports when ffmpeg is not installed.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time

VIDEO_EXT = {'mp4', 'webm', 'mov', 'mkv', 'avi', 'm4v'}
AUDIO_EXT = {'mp3', 'wav', 'ogg', 'oga', 'm4a', 'flac', 'aac', 'opus'}
IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    kind    TEXT NOT NULL,
    ext     TEXT NOT NULL,
    size    INTEGER NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    timeline TEXT NOT NULL,
    created  INTEGER NOT NULL,
    updated  INTEGER NOT NULL
);
"""


def kind_of(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext in VIDEO_EXT:
        return 'video', ext
    if ext in AUDIO_EXT:
        return 'audio', ext
    if ext in IMAGE_EXT:
        return 'image', ext
    return '', ext


class Studio:
    def __init__(self, path=None):
        self.dir = path or os.environ.get('ARTIST_DIR') \
            or os.path.expanduser('~/.mod/artist')
        self.assets_dir = os.path.join(self.dir, 'assets')
        os.makedirs(self.assets_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(self.dir, 'artist.db'),
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ── assets ───────────────────────────────────────────────────

    def add_asset(self, name, data):
        """Save one media file. The id is a hash of the bytes: no duplicates."""
        kind, ext = kind_of(name or '')
        if not kind:
            return {'error': f'unsupported file type: {name!r} — '
                             'video, audio or image only'}
        if not data:
            return {'error': 'empty file'}
        aid = hashlib.sha256(data).hexdigest()[:16]
        path = self.asset_path(aid, ext)
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                f.write(data)
        self.db.execute(
            'INSERT OR IGNORE INTO assets (id,name,kind,ext,size,created) '
            'VALUES (?,?,?,?,?,?)',
            (aid, os.path.basename(name), kind, ext, len(data), int(time.time())))
        self.db.commit()
        return self.get_asset(aid)

    def get_asset(self, aid):
        r = self.db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
        return dict(r) if r else None

    def asset_path(self, aid, ext=None):
        if ext is None:
            a = self.get_asset(aid)
            if not a:
                return None
            ext = a['ext']
        return os.path.join(self.assets_dir, f'{aid}.{ext}')

    def assets(self, kind=''):
        q = 'SELECT * FROM assets'
        args = ()
        if kind:
            q += ' WHERE kind=?'
            args = (kind,)
        rows = self.db.execute(q + ' ORDER BY created DESC', args).fetchall()
        return [dict(r) for r in rows]

    def remove_asset(self, aid):
        a = self.get_asset(aid)
        if not a:
            return {'error': f'no asset {aid}'}
        self.db.execute('DELETE FROM assets WHERE id=?', (aid,))
        self.db.commit()
        p = self.asset_path(aid, a['ext'])
        if p and os.path.exists(p):
            os.remove(p)
        return {'removed': aid}

    # ── projects ─────────────────────────────────────────────────

    def save_project(self, name, timeline, id=None):
        """Create or update a project. The timeline is two lanes of clips:
        {"video": [{"asset": id, "dur": s?}, ...], "audio": [...]}"""
        if isinstance(timeline, str):
            timeline = json.loads(timeline)
        if not isinstance(timeline, dict):
            return {'error': 'timeline must be an object with video/audio lanes'}
        timeline = {'video': list(timeline.get('video') or []),
                    'audio': list(timeline.get('audio') or [])}
        now = int(time.time())
        pid = id or hashlib.sha256(f'{name}{now}'.encode()).hexdigest()[:12]
        row = self.db.execute('SELECT created FROM projects WHERE id=?',
                              (pid,)).fetchone()
        created = row['created'] if row else now
        self.db.execute(
            'INSERT OR REPLACE INTO projects (id,name,timeline,created,updated) '
            'VALUES (?,?,?,?,?)',
            (pid, name or 'untitled', json.dumps(timeline), created, now))
        self.db.commit()
        return self.get_project(pid)

    def get_project(self, pid):
        r = self.db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d['timeline'] = json.loads(d['timeline'])
        return d

    def projects(self):
        rows = self.db.execute(
            'SELECT id,name,created,updated FROM projects '
            'ORDER BY updated DESC').fetchall()
        return [dict(r) for r in rows]

    def remove_project(self, pid):
        if not self.get_project(pid):
            return {'error': f'no project {pid}'}
        self.db.execute('DELETE FROM projects WHERE id=?', (pid,))
        self.db.commit()
        return {'removed': pid}

    def stats(self):
        a = self.db.execute(
            'SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM assets').fetchone()
        p = self.db.execute('SELECT COUNT(*) n FROM projects').fetchone()
        return {'assets': a['n'], 'bytes': a['b'], 'projects': p['n'],
                'ffmpeg': bool(shutil.which('ffmpeg'))}

    # ── optional server-side render ──────────────────────────────

    def render(self, pid, width=1280, height=720, fps=30):
        """Compile a project into one mp4 with ffmpeg. Optional: the browser
        can export a webm on its own; this is the higher-quality path when
        ffmpeg is installed on the host."""
        if not shutil.which('ffmpeg'):
            return {'error': 'ffmpeg is not installed on this host — '
                             'use EXPORT in the console (browser-side webm), '
                             'or install ffmpeg to enable server renders'}
        proj = self.get_project(pid)
        if not proj:
            return {'error': f'no project {pid}'}
        vclips = proj['timeline'].get('video') or []
        aclips = proj['timeline'].get('audio') or []
        if not vclips:
            return {'error': 'nothing on the video lane'}

        inputs, filters, vlabels = [], [], []
        for i, c in enumerate(vclips):
            a = self.get_asset(c.get('asset', ''))
            if not a:
                return {'error': f"missing asset {c.get('asset')}"}
            path = self.asset_path(a['id'], a['ext'])
            if a['kind'] == 'image':
                dur = float(c.get('dur') or 3)
                inputs += ['-loop', '1', '-t', str(dur), '-i', path]
            else:
                inputs += ['-i', path]
            filters.append(
                f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio='
                f'decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
                f'setsar=1,fps={fps}[v{i}]')
            vlabels.append(f'[v{i}]')
        filters.append(''.join(vlabels) + f'concat=n={len(vclips)}:v=1:a=0[vout]')

        maps = ['-map', '[vout]']
        base = len(vclips)
        if aclips:
            alabels = []
            for j, c in enumerate(aclips):
                a = self.get_asset(c.get('asset', ''))
                if not a:
                    return {'error': f"missing asset {c.get('asset')}"}
                inputs += ['-i', self.asset_path(a['id'], a['ext'])]
                alabels.append(f'[{base + j}:a]')
            filters.append(''.join(alabels)
                           + f'concat=n={len(aclips)}:v=0:a=1[aout]')
            maps += ['-map', '[aout]', '-shortest']

        out = os.path.join(self.dir, f'render_{pid}.mp4')
        cmd = (['ffmpeg', '-y', *inputs, '-filter_complex', ';'.join(filters),
                *maps, '-c:v', 'libx264', '-pix_fmt', 'yuv420p'])
        if aclips:
            cmd += ['-c:a', 'aac']
        cmd += [out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            return {'error': 'ffmpeg failed', 'detail': r.stderr[-2000:]}
        return {'ok': True, 'file': out, 'bytes': os.path.getsize(out)}

    # ── sharing ──────────────────────────────────────────────────

    def export_pack(self, pid):
        """A project as plain JSON: the timeline plus every asset it uses,
        base64-inlined, so another artist imports it whole."""
        import base64
        proj = self.get_project(pid)
        if not proj:
            return {'error': f'no project {pid}'}
        ids = {c.get('asset') for lane in ('video', 'audio')
               for c in proj['timeline'].get(lane, []) if c.get('asset')}
        assets = []
        for aid in sorted(ids):
            a = self.get_asset(aid)
            if not a:
                continue
            with open(self.asset_path(aid, a['ext']), 'rb') as f:
                a['data'] = base64.b64encode(f.read()).decode()
            assets.append(a)
        return {'artist_pack': 1, 'name': proj['name'],
                'timeline': proj['timeline'], 'assets': assets}

    def import_pack(self, pack):
        """Merge a pack in. Content-hash ids mean no duplicate bytes."""
        import base64
        if isinstance(pack, str):
            pack = json.loads(pack)
        if not isinstance(pack, dict) or 'timeline' not in pack:
            return {'error': 'not an artist pack'}
        added = 0
        for a in pack.get('assets', []):
            r = self.add_asset(a.get('name', f"{a.get('id','x')}.{a.get('ext','')}"),
                               base64.b64decode(a.get('data', '')))
            if r and 'error' not in r:
                added += 1
        proj = self.save_project(pack.get('name', 'imported'), pack['timeline'])
        return {'ok': True, 'project': proj['id'], 'assets': added}
