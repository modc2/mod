"""
vidz — make a film of any form from a prompt. 60 seconds by default.

A script becomes shots (planner), each shot is rendered by a top AI video model
bought per request over x402 — USDC on Base, no account, no API key, the
wallet on this machine signs a gasless EIP-3009 authorization — and the clips
are stitched into one file (ffmpeg if present, playlist otherwise).

Spending is opt-in: `make` without confirm=1 only plans and quotes.

CLI:
    m vidz/wallet create=1                      # local paying wallet (fund it with USDC on Base)
    m vidz/plan "a fox crossing a neon city at night"
    m vidz/quote "a fox crossing a neon city at night" seconds=60
    m vidz/make "a fox crossing a neon city at night"             # dry run: plan + price
    m vidz/make "a fox ..." confirm=1 max_usd=3 model=veo-3.1-lite  # pays, renders, stitches
    m vidz/make "..." shots='["wide city", "close on the fox", "fox at dawn"]' seconds=30
    m vidz/render <project_id> confirm=1        # resume a project where it stopped
    m vidz/projects | m vidz/project <id> | m vidz/assemble <id>
    m vidz/test                                 # offline: fake x402 seller, real signatures

Connected tools (the fleet's editing suite — `m vidz/tools` shows what's up):
    m vidz/music "synthwave chase"              # artlist royalty-free; source=sfx|musica for effects / the crate
    m vidz/footage "city at night"              # artlist stock clips (b-roll)
    m vidz/soundtrack <id> query="synthwave"    # download the pick, mux under the film (ffmpeg) or stage for artist
    m vidz/captions <id>                        # sound2text -> captions.json + captions.srt
    m vidz/edit <id>                            # push clips + soundtrack into the artist studio timeline
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from vidzkit import assemble, fleet, planner, providers, x402  # noqa: E402

HOME = Path(os.environ.get('VIDZ_HOME', Path.home() / '.vidz'))


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, float(t)), 3600)
    m, s = divmod(rem, 60)
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}'


def _srt(segments: list) -> str:
    return '\n'.join(f"{i + 1}\n{_ts(s.get('start', 0))} --> {_ts(s.get('end', 0))}\n{(s.get('text') or '').strip()}\n"
                     for i, s in enumerate(segments))


class Mod:
    description = 'Video maker: prompt -> shots -> AI video models paid per request over x402 (USDC on Base) -> one film; 60s default'

    def __init__(self):
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.defaults = self.config['defaults']
        self.networks = self.config.get('x402', {}).get('networks')
        self.home = HOME
        self.projects_dir = HOME / 'projects'

    def forward(self, prompt: Optional[str] = None, **kwargs):
        return self.make(prompt, **kwargs) if prompt else self.info()

    def info(self) -> dict:
        return {'name': 'vidz', 'description': self.description,
                'defaults': self.defaults, 'providers': self.providers(),
                'tools': sorted(fleet.registry(self.config)),
                'wallet': self.wallet().get('address'), 'ffmpeg': bool(assemble.ffmpeg()),
                'home': str(self.home), 'fns': self.config['fns']}

    # ── providers ────────────────────────────────────────────────────

    def _providers(self, include_disabled=False) -> dict:
        return providers.load(self.config, include_disabled)

    def _provider(self, name: Optional[str]) -> providers.Provider:
        name = name or self.defaults['provider']
        ps = self._providers()
        if name not in ps:
            raise ValueError(f'unknown or disabled provider {name!r}; have {sorted(ps)}')
        return ps[name]

    def providers(self) -> dict:
        return {n: {'enabled': p.spec.get('enabled', True), 'url': p.url, 'max_clip_seconds': p.max_clip,
                    'models': p.models, 'default_model': p.default_model, 'docs': p.spec.get('docs')}
                for n, p in self._providers(include_disabled=True).items()}

    def models(self) -> dict:
        return {n: p.models for n, p in self._providers().items()}

    # ── wallet (key stays on this machine, never in config) ─────────

    def _key(self) -> str:
        key = os.environ.get('VIDZ_PRIVATE_KEY')
        if key:
            return key
        f = self.home / 'wallet.json'
        if f.exists():
            return json.loads(f.read_text())['key']
        raise RuntimeError('no wallet: run `m vidz/wallet create=1` or set VIDZ_PRIVATE_KEY')

    def wallet(self, create: bool = False) -> dict:
        """Address of the paying wallet; create=1 makes one in ~/.vidz/wallet.json (0600)."""
        from eth_account import Account
        try:
            return {'address': Account.from_key(self._key()).address,
                    'source': 'env' if os.environ.get('VIDZ_PRIVATE_KEY') else str(self.home / 'wallet.json'),
                    'fund': 'USDC on Base (no ETH needed: payments are gasless authorizations)'}
        except RuntimeError as e:
            if not create:
                return {'address': None, 'hint': str(e)}
        acct = Account.create()
        self.home.mkdir(parents=True, exist_ok=True)
        f = self.home / 'wallet.json'
        f.write_text(json.dumps({'address': acct.address, 'key': acct.key.hex()}))
        os.chmod(f, 0o600)
        return {'address': acct.address, 'created': str(f),
                'fund': 'send USDC on Base to this address'}

    # ── plan + price ─────────────────────────────────────────────────

    def plan(self, prompt: str = '', seconds: Optional[int] = None, provider: Optional[str] = None,
             shots=None, style: str = '') -> dict:
        p = self._provider(provider)
        if isinstance(shots, str):
            shots = json.loads(shots)
        seconds = int(seconds or self.defaults['seconds'])
        sp = planner.plan(prompt, seconds, p.max_clip, shots=shots, style=style)
        return {'provider': p.name, 'seconds': seconds, 'clips': len(sp), 'shots': sp}

    def quote(self, prompt: str = '', seconds: Optional[int] = None, provider: Optional[str] = None,
              model: str = '', aspect: Optional[str] = None, shots=None, style: str = '') -> dict:
        """Price the whole film by reading each distinct clip's 402 — pays nothing."""
        pl = self.plan(prompt, seconds, provider, shots, style)
        p, aspect = self._provider(pl['provider']), aspect or self.defaults['aspect']
        by_len, total = {}, 0.0
        for s in pl['shots']:
            if s['seconds'] not in by_len:
                by_len[s['seconds']] = p.quote(s['prompt'], s['seconds'], aspect, model, self.networks)
            q = by_len[s['seconds']]
            if 'usd' not in q:
                return {'error': f'{p.name} did not quote', 'detail': q}
            total += q['usd']
        return {'provider': p.name, 'model': model or p.default_model, 'clips': pl['clips'],
                'seconds': pl['seconds'], 'usd': round(total, 4),
                'per_clip': {k: v['usd'] for k, v in by_len.items()},
                'network': next(iter(by_len.values()))['network']}

    # ── projects ─────────────────────────────────────────────────────

    def _pdir(self, pid: str) -> Path:
        d = self.projects_dir / pid
        if not (d / 'project.json').exists():
            raise ValueError(f'no project {pid}')
        return d

    def _load(self, pid: str) -> dict:
        return json.loads((self._pdir(pid) / 'project.json').read_text())

    def _save(self, proj: dict):
        d = self.projects_dir / proj['id']
        d.mkdir(parents=True, exist_ok=True)
        (d / 'project.json').write_text(json.dumps(proj, indent=2))

    def projects(self) -> list:
        if not self.projects_dir.exists():
            return []
        out = []
        for f in sorted(self.projects_dir.glob('*/project.json'), key=lambda f: f.stat().st_mtime, reverse=True):
            p = json.loads(f.read_text())
            out.append({k: p.get(k) for k in ('id', 'title', 'status', 'seconds', 'provider', 'model', 'spent_usd', 'film')})
        return out

    def project(self, pid: str) -> dict:
        return self._load(pid)

    def make(self, prompt: str = '', seconds: Optional[int] = None, provider: Optional[str] = None,
             model: str = '', aspect: Optional[str] = None, shots=None, style: str = '',
             max_usd: Optional[float] = None, confirm: bool = False, title: str = '') -> dict:
        """Plan + quote a film; with confirm=1 also pay, render every shot and stitch."""
        if not prompt and not shots:
            return {'error': 'pass a prompt (or shots=[...])'}
        max_usd = float(max_usd if max_usd is not None else self.defaults['max_usd'])
        pl = self.plan(prompt, seconds, provider, shots, style)
        p = self._provider(pl['provider'])
        proj = {'id': time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6],
                'title': title or (prompt or str(pl['shots'][0]['prompt']))[:60],
                'prompt': prompt, 'style': style, 'seconds': pl['seconds'],
                'aspect': aspect or self.defaults['aspect'], 'provider': p.name,
                'model': model or p.default_model, 'max_usd': max_usd, 'spent_usd': 0.0,
                'status': 'planned', 'created': int(time.time()), 'film': None,
                'shots': [{**s, 'status': 'todo'} for s in pl['shots']]}
        try:
            q = self.quote(prompt, pl['seconds'], p.name, proj['model'], proj['aspect'], shots, style)
        except Exception as e:
            q = {'error': str(e)}
        proj['quote'] = q
        self._save(proj)
        if 'usd' in q and q['usd'] > max_usd:
            return {**self._summary(proj), 'refused': f"quote ${q['usd']} is over max_usd=${max_usd}"}
        if not confirm:
            return {**self._summary(proj), 'next': f"m vidz/render {proj['id']} confirm=1"}
        return self.render(proj['id'], confirm=True)

    def render(self, pid: str, confirm: bool = False, max_usd: Optional[float] = None) -> dict:
        """Render every unfinished shot (resumable), then stitch. Pays only with confirm=1."""
        proj = self._load(pid)
        if not confirm:
            return {**self._summary(proj), 'hint': 'confirm=1 to pay and render'}
        if max_usd is not None:
            proj['max_usd'] = float(max_usd)
        d, p, key = self._pdir(pid), self._provider(proj['provider']), self._key()
        proj['status'] = 'rendering'
        self._save(proj)
        for s in proj['shots']:
            clip = d / f"shot_{s['i']:02d}.mp4"
            if s['status'] == 'done' and clip.exists():
                continue
            budget = proj['max_usd'] - proj['spent_usd']
            try:
                r = p.render(s['prompt'], s['seconds'], proj['aspect'], proj['model'], key, budget,
                             self.defaults['poll_seconds'], self.defaults['timeout_seconds'], self.networks)
            except x402.PaymentError as e:
                r = {'error': str(e)}
            proj['spent_usd'] = round(proj['spent_usd'] + r.get('paid_usd', 0.0), 6)
            s.update({k: r[k] for k in ('video', 'receipt', 'paid_usd', 'status_url', 'error') if r.get(k) is not None})
            if 'bytes' in r:
                clip.write_bytes(r['bytes'])
            elif r.get('video'):
                try:
                    self._download(r['video'], clip)
                except Exception as e:
                    s['error'] = f'download: {e}'
            s['status'] = 'done' if clip.exists() else 'failed'
            self._save(proj)
            if s['status'] == 'failed':
                proj['status'] = 'stopped'
                self._save(proj)
                return {**self._summary(proj), 'stopped_at': s['i'], 'error': s.get('error'),
                        'resume': f'm vidz/render {pid} confirm=1'}
        return self.assemble(pid)

    def assemble(self, pid: str) -> dict:
        proj, d = self._load(pid), self._pdir(pid)
        clips = [d / f"shot_{s['i']:02d}.mp4" for s in proj['shots']]
        res = assemble.stitch(clips, d / 'film.mp4', audio=self._soundtrack_file(d))
        proj['film'] = res.get('film')
        proj['status'] = 'done' if res.get('film') else ('clips' if all(c.exists() for c in clips) else proj['status'])
        self._save(proj)
        return {**self._summary(proj), **res}

    def _download(self, url: str, out: Path):
        import requests
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = out.with_suffix('.part')
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.rename(out)

    def _summary(self, proj: dict) -> dict:
        return {'id': proj['id'], 'status': proj['status'], 'provider': proj['provider'],
                'model': proj['model'], 'seconds': proj['seconds'], 'clips': len(proj['shots']),
                'quote': proj.get('quote'), 'max_usd': proj['max_usd'], 'spent_usd': proj['spent_usd'],
                'dir': str(self.projects_dir / proj['id']), 'film': proj.get('film')}

    # ── connected tools (the fleet's editing suite) ─────────────────

    def tools(self) -> dict:
        """Every connected media tool (artist, artlist, musica, sound2text, voice) and whether it is up."""
        return {'tools': fleet.probe(self.config),
                'flow': 'make → soundtrack (artlist/musica) → captions (sound2text) → edit (artist) → export'}

    def music(self, query: str = '', k: int = 10, source: str = 'artlist') -> dict:
        """Find a soundtrack. source: artlist (royalty-free songs), sfx, or musica (Bandcamp/SoundCloud/YouTube/archive)."""
        k = int(k)
        if source in ('artlist', 'sfx'):
            r = fleet.artlist(self.config, 'music' if source == 'artlist' else 'sfx', query, k)
            return {'source': source, 'total': r.get('total'),
                    'tracks': [{'id': t.get('id'), 'name': t.get('name'), 'artist': t.get('artist'),
                                'duration': t.get('duration'), 'url': t.get('preview_url')}
                               for t in r.get('results') or []]}
        if source == 'musica':
            r = fleet.musica(self.config, 'search', q=query, kind='track', limit=k)
            return {'source': 'musica', 'tracks': [
                {'id': i.get('id'), 'name': i.get('name'), 'artist': i.get('artists'),
                 'platform': i.get('source'), 'duration': i.get('duration_ms'),
                 'track': i.get('track')}
                for i in (r.get('items') or []) if i.get('source') != 'spotify'][:k]}
        return {'error': 'source must be artlist, sfx or musica'}

    def footage(self, query: str = '', k: int = 10) -> dict:
        """Stock footage search (Artlist clips) — b-roll to cut into an edit."""
        r = fleet.artlist(self.config, 'footage', query, k)
        return {'total': r.get('total'), 'clips': r.get('results') or []}

    def _soundtrack_file(self, d: Path) -> Optional[Path]:
        return next(iter(sorted(d.glob('soundtrack.*'))), None)

    def soundtrack(self, pid: str, query: str = '', source: str = 'artlist', id: str = '',
                   url: str = '', platform: str = '', pick: int = 0) -> dict:
        """Put music under a film: search artlist/sfx/musica (or pass url=), save into the project, restitch if ffmpeg."""
        import urllib.parse
        proj, d = self._load(pid), self._pdir(pid)
        pick, chosen = int(pick), {'source': source}
        if not url and source in ('artlist', 'sfx'):
            tracks = self.music(query or proj['title'], k=pick + 1, source=source).get('tracks') or []
            tracks = [t for t in tracks if t.get('url')]
            if not tracks:
                return {'error': f'nothing playable on {source} for {(query or proj["title"])!r}'}
            t = tracks[min(pick, len(tracks) - 1)]
            chosen.update(t)
            url = t['url']
        elif not url and source == 'musica':
            if not (id and platform):
                tracks = self.music(query or proj['title'], k=pick + 1, source='musica').get('tracks') or []
                if not tracks:
                    return {'error': f'nothing streamable on musica for {(query or proj["title"])!r}'}
                t = tracks[min(pick, len(tracks) - 1)]
                chosen.update(t)
                id, platform = t.get('id'), t.get('platform')
            for old in d.glob('soundtrack.*'):
                old.unlink()
            out = d / 'soundtrack.mp3'
            r = fleet.musica_download(self.config, platform, id, out, track=chosen.get('track'))
            if 'error' in r:
                return r
        if url:
            host = urllib.parse.urlparse(url).hostname or ''
            is_artlist = host == 'artlist.io' or host.endswith('.artlist.io')
            # artlist CDN paths are base64 and carry no suffix; its previews are AAC
            ext = Path(urllib.parse.urlparse(url).path).suffix or ('.aac' if is_artlist else '.mp3')
            for old in d.glob('soundtrack.*'):
                old.unlink()
            out = d / f'soundtrack{ext}'
            if is_artlist:
                fleet.artlist_download(url, out)
            else:
                self._download(url, out)
            chosen['url'] = url
        elif source not in ('musica',):
            return {'error': 'pass query=, url= or (source=musica, platform=, id=)'}
        proj['soundtrack'] = {**{k: v for k, v in chosen.items() if v is not None}, 'file': str(out)}
        self._save(proj)
        res = {'id': pid, 'soundtrack': proj['soundtrack']}
        if assemble.ffmpeg() and any(s['status'] == 'done' for s in proj['shots']):
            res.update(self.assemble(pid))
        else:
            res['hint'] = f'no ffmpeg on this host — `m vidz/edit {pid}` opens it in artist with the music on the AUDIO lane'
        return res

    def edit(self, pid: str, name: str = '') -> dict:
        """Open a project in the artist studio: clips on the VIDEO lane, soundtrack on AUDIO; edit + export in the browser."""
        proj, d = self._load(pid), self._pdir(pid)
        video, pushed = [], []
        for s in proj['shots']:
            clip = d / f"shot_{s['i']:02d}.mp4"
            if not clip.exists():
                continue
            a = fleet.artist_add(self.config, f"vidz_{pid}_shot{s['i']:02d}.mp4", clip.read_bytes())
            if not isinstance(a, dict) or a.get('error') or not a.get('id'):
                return {'error': f"artist refused shot {s['i']}: {(a or {}).get('error', 'no response')}"}
            video.append({'asset': a['id']})
            pushed.append(a['id'])
        if not video:
            return {'error': 'no rendered clips on disk — `m vidz/render <id> confirm=1` first'}
        audio = []
        snd = self._soundtrack_file(d)
        if snd:
            a = fleet.artist_add(self.config, f'vidz_{pid}_{snd.name}', snd.read_bytes())
            if isinstance(a, dict) and a.get('id'):
                audio.append({'asset': a['id']})
        ap = fleet.artist_save(self.config, name or f"vidz · {proj['title']}",
                               {'video': video, 'audio': audio}, proj.get('artist_project'))
        if not isinstance(ap, dict) or ap.get('error') or not ap.get('id'):
            return {'error': f"artist did not save the project: {(ap or {}).get('error', 'no response')}"}
        proj['artist_project'] = ap['id']
        self._save(proj)
        return {'id': pid, 'artist_project': ap['id'], 'clips': len(video), 'audio_tracks': len(audio),
                'assets': pushed, 'open': '/artist',
                'hint': 'EXPORT compiles in the browser (webm); RENDER appears when that host has ffmpeg'}

    def captions(self, pid: str, engine: str = '', policy: str = 'fast') -> dict:
        """Transcribe the film with sound2text; writes captions.json and, when it segments, captions.srt."""
        proj, d = self._load(pid), self._pdir(pid)
        target = proj.get('film')
        if not target:
            have = [d / f"shot_{s['i']:02d}.mp4" for s in proj['shots'] if (d / f"shot_{s['i']:02d}.mp4").exists()]
            if not have:
                return {'error': 'nothing rendered yet — `m vidz/render <id> confirm=1` first'}
            target = str(have[0])
        r = fleet.transcribe(self.config, target, engine=engine, policy=policy)
        if not isinstance(r, dict) or r.get('error'):
            return {'error': (r or {}).get('error', 'no response'),
                    'hint': 'sound2text decodes wav/mp3/flac itself; an mp4 needs ffmpeg on its host'}
        (d / 'captions.json').write_text(json.dumps(r, indent=2))
        out = {'id': pid, 'captions': str(d / 'captions.json'),
               'text': r.get('transcript') or r.get('text')}
        segs = r.get('segments') or []
        if segs:
            (d / 'captions.srt').write_text(_srt(segs))
            out['srt'] = str(d / 'captions.srt')
        return out

    # ── self-check ───────────────────────────────────────────────────

    def test(self) -> dict:
        """Offline end-to-end against a local fake x402 seller (v1 and v2)."""
        sys.path.append(str(MODULE_DIR / 'test'))
        import test_vidz
        return test_vidz.run()

    def readme(self) -> str:
        return (MODULE_DIR / 'README.md').read_text()
