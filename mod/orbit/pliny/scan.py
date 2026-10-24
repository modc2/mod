#!/usr/bin/env python3
"""plinyville scan — the daily keeper of the mirror.

One job, run once a day by cron:

  1. re-pull elder-plinius's repo list from GitHub,
  2. re-pull the plinyworld upstream snapshot (index.html + triggers.js + COMMIT),
  3. re-archive any *installed* market mod whose repo has moved since last scan,
  4. re-register the module so the CID it shows is the CID of the code that is
     actually serving.

Every run leaves a receipt in ~/.mod/pliny/scan.json — that receipt is what
the header pill ("up to date · scanned 3h ago") and GET /status read. A scan
that fails is still a receipt: it records the error and the page says so rather
than showing stale data as if it were fresh.

    python3 scan.py --run        # one scan — this is what cron runs
    python3 scan.py --status     # the last receipt, as JSON
    python3 scan.py --cron       # install the daily crontab entry
    python3 scan.py --uncron     # remove it

or through the protocol: `m pliny/scan`, `m pliny/scan_status`,
`m pliny/cron`.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

from market import Market                        # noqa: E402
from plinyville import VERSION, Ville            # noqa: E402

SCAN_STATE = '~/.mod/pliny/scan.json'
LOG = '/tmp/plinyville-scan.log'
INTERVAL_HOURS = 24.0
# A receipt stays "up to date" for one interval plus this much slack, so a scan
# that runs a few minutes late never flips the pill to stale.
GRACE = 1.5
HISTORY = 20
# A nightly scan re-archives at most this many moved repos, so one busy night in
# the corpus cannot turn the job into an hour of cloning; the next scan continues
# where this one stopped, and the receipt says what it skipped.
RESTOCK_CAP = 8
CRON_TAG = '# plinyville daily scan'


def _protocol():
    """Import the *protocol's* `mod` package, not this module's own mod.py.

    Running `python3 scan.py` puts this directory first on sys.path, and it
    holds a mod.py; a plain `import mod` would get that instead of the protocol
    and self-import into a half-built module. Hide the directory for the length
    of the import.
    """
    import contextlib
    import importlib
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != HERE]
    try:
        # The protocol chatters on import ("[localfs] Rust bindings…"); on stdout
        # that would corrupt an MCP stdio stream, so it goes to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            return importlib.import_module('mod')
    finally:
        sys.path[:] = saved


def pin(mkt, name) -> dict:
    """Upgrade a stored mod to a real localfs CID and rewrite the recorded id in
    both its manifest and the market index. Best-effort: the market stays usable
    when localfs is not available, it just keeps the computed hash.

    This lives here rather than in market.py because it is the one operation that
    needs the protocol, and market.py is deliberately protocol-free. mod.py and
    the nightly restock both call it, so an archive pinned by hand and one
    refreshed at 04:17 end up addressed the same way."""
    info = {}
    try:
        bundle = mkt.content(name)
        cid = _protocol().mod('localfs')().put(bundle)
        man = mkt._store_get(f'mods/{name}/manifest') or {}
        man['cid'] = cid
        mkt._store_put(f'mods/{name}/manifest', man)
        idx = mkt._index()
        if name in idx.get('mods', {}):
            idx['mods'][name]['cid'] = cid
            mkt._save_index(idx)
        info['cid'] = cid
    except Exception as e:                                # noqa: BLE001
        info['pin_error'] = str(e)
    return info


class Scanner:
    """The daily scan, its receipts, and the crontab entry that fires it."""

    def __init__(self, ville: Ville = None, market: Market = None,
                 state_path=None, pinner=None):
        self.ville = ville or Ville()
        self.mkt = market or Market(self.ville)
        self.path = os.path.expanduser(state_path or SCAN_STATE)
        # A restocked mod keeps a real localfs CID, exactly as `install` gives it.
        self.pin = pinner or (lambda name: pin(self.mkt, name))

    # ── receipts ────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, st: dict):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, default=str)
        os.replace(tmp, self.path)

    def _record(self, rec: dict) -> dict:
        st = self._load()
        st['last'] = rec
        if rec.get('ok'):
            st['last_ok'] = rec['at']
        if rec.get('cid'):
            st['cid'], st['cid_at'] = rec['cid'], rec['at']
        st['history'] = ([rec] + (st.get('history') or []))[:HISTORY]
        st.setdefault('interval_hours', INTERVAL_HOURS)
        self._save(st)
        return rec

    # ── the scan ────────────────────────────────────────────────────────────

    @staticmethod
    def _pushed(rows) -> dict:
        return {r.get('name'): r.get('pushed_at') for r in (rows or []) if r.get('name')}

    def run(self, restock=True, register=True) -> dict:
        """One pass. Never raises: a failed pass is recorded as a failed receipt,
        because cron has nowhere to raise to and the page has to be able to say
        'the last scan failed' instead of quietly aging."""
        t0 = time.time()
        rec = {'at': t0, 'ok': False, 'version': VERSION,
               'trigger': os.environ.get('PLINYVILLE_SCAN_TRIGGER', 'manual')}
        before = self._pushed(self.ville._load().get('repos'))
        try:
            up = self.ville.update()
        except Exception as e:                            # noqa: BLE001
            # The REST list is the one part of a scan that can be rate-limited
            # away. Re-list off the public page instead (no API budget) and keep
            # going — a scan that can still see the corpus is worth finishing.
            up = self._update_offbudget(rec, e)
            if up is None:
                rec.update(error=f'{type(e).__name__}: {e}',
                           seconds=round(time.time() - t0, 2))
                return self._record(rec)

        after = self._pushed(self.ville._load().get('repos'))
        moved = sorted(n for n in after
                       if n in before and after[n] != before[n])
        rec.update({
            'repos': up.get('repos', len(after)),
            'added': sorted(set(after) - set(before)),
            'removed': sorted(set(before) - set(after)),
            'moved': moved,
            'plinyworld': up.get('plinyworld'),
            'first_scan': not before,
        })
        if restock:
            rec['restocked'], rec['restock_errors'] = self._restock(moved)
        if register:
            try:
                rec['cid'] = self.mint_cid()
            except Exception as e:                        # noqa: BLE001
                rec['cid_error'] = f'{type(e).__name__}: {e}'
                rec['cid'] = self._load().get('cid')
        rec['ok'] = True
        rec['seconds'] = round(time.time() - t0, 2)
        return self._record(rec)

    def _update_offbudget(self, rec, why):
        """The fallback half of a scan whose REST call was refused: re-list the
        repos from the public repositories page, then refresh the plinyworld
        snapshot on its own (it is a raw.githubusercontent fetch, not an API
        call, so it usually survives). Returns an `update()`-shaped dict, or
        None if even this could not be done."""
        try:
            from clone import Cloner
            found = Cloner(self.mkt).discover()
        except Exception as e:                            # noqa: BLE001
            rec['discover_error'] = f'{type(e).__name__}: {e}'
            return None
        rec['rest_error'] = f'{type(why).__name__}: {why}'
        rec['repos_source'] = found.get('source')
        try:
            world = self.ville.refresh_plinyworld()
        except Exception as e:                            # noqa: BLE001
            world = {'error': f'{type(e).__name__}: {e}'}
        return {'repos': found.get('count'), 'plinyworld': world}

    def _archiver(self):
        """How to re-pull one archive. Prefer the clone archiver: git transport is
        not on the 60/hr REST budget, which a nightly job run behind other traffic
        would otherwise find already spent."""
        try:
            from clone import Cloner
            cl = Cloner(self.mkt)
            if shutil.which('git'):
                return (lambda n: cl.archive(n, refresh=True)), 'clone'
        except Exception:                                 # noqa: BLE001
            pass
        return (lambda n: self.mkt.install(n, refresh=True)), 'api'

    def _restock(self, moved) -> tuple:
        """Re-archive installed mods whose repo moved. Only installed ones: the
        market is a catalog of every repo, but only what someone archived is a
        mod with content that can go stale."""
        installed = set(self.mkt.installed())
        todo = [n for n in moved if n in installed][:RESTOCK_CAP]
        archive, via = self._archiver()
        done, errors = [], []
        for name in todo:
            try:
                r = archive(name)
                cid = r.get('cid')
                if self.pin:
                    cid = (self.pin(name) or {}).get('cid') or cid
                done.append({'name': name, 'cid': cid, 'via': via,
                             'files_stored': r.get('files_stored')})
            except Exception as e:                        # noqa: BLE001
                errors.append({'name': name, 'error': f'{type(e).__name__}: {e}'})
        skipped = [n for n in moved if n in installed][RESTOCK_CAP:]
        if skipped:
            errors.append({'skipped': skipped,
                           'why': f'restock cap {RESTOCK_CAP}/scan — next scan continues'})
        return done, errors

    # ── the module's own CID ────────────────────────────────────────────────

    # The protocol addresses a module by its directory, and this one's moved
    # from `plinyville` to `pliny` while every route it serves kept the old
    # name. Ask under the directory name, or the registrar raises
    # ModuleNotFoundError and the scan books a failure it did not have.
    MOD_NAME = os.path.basename(HERE)

    def mint_cid(self, register=True) -> str:
        """Register this module with the registrar and return the CID that comes
        back. That CID addresses this module's own content — the code serving
        the page — which is why the scan re-mints it after every pull."""
        mp = _protocol()
        reg = mp.mod('registry')()
        if not hasattr(reg, 'reg') and not hasattr(reg, 'cid'):
            # `registry` is a contested name on this host: when orbit/registry
            # wins the lookup, the registrar this module wants is not what comes
            # back. Say that, so the receipt reads as a naming problem and not
            # as a mysterious AttributeError from inside the scan.
            raise RuntimeError(
                f'the name `registry` resolves to {type(reg).__module__}, which is '
                'not the registrar (no reg()/cid()) — the module CID cannot be '
                're-minted until that name resolves to core/registry again')
        if register and hasattr(reg, 'reg'):
            info = reg.reg(
                self.MOD_NAME, comment='daily scan — elder-plinius mirror + market')
            cid = info.get('cid')
            if cid:
                return cid
        # the registrar's own read-only lookup — the api mod has no cid()
        return reg.cid(self.MOD_NAME)

    def cid(self, live=False) -> dict:
        """The cached module CID, or one live lookup if we have never seen it.
        The api calls this per request, so the registry is touched at most once
        per process unless live=True."""
        st = self._load()
        if st.get('cid') and not live:
            return {'cid': st['cid'], 'at': st.get('cid_at'), 'source': 'scan'}
        try:
            # live=True means "re-address the code that is serving right now",
            # and registering is the one call that reliably answers that. The
            # lazy per-request path stays read-only and falls back to cache.
            cid = self.mint_cid(register=bool(live))
        except Exception as e:                            # noqa: BLE001
            return {'cid': st.get('cid'), 'at': st.get('cid_at'),
                    'source': 'cache', 'error': f'{type(e).__name__}: {e}'}
        if cid:
            st['cid'], st['cid_at'] = cid, time.time()
            self._save(st)
        return {'cid': cid, 'at': st.get('cid_at'), 'source': 'registry'}

    # ── status: what the header pill reads ──────────────────────────────────

    @staticmethod
    def _ago(sec) -> str:
        if sec is None:
            return 'never'
        sec = max(0, int(sec))
        if sec < 90:
            return f'{sec}s ago'
        if sec < 5400:
            return f'{sec // 60}m ago'
        if sec < 172800:
            return f'{sec // 3600}h ago'
        return f'{sec // 86400}d ago'

    @staticmethod
    def _iso(ts):
        if not ts:
            return None
        import datetime
        return datetime.datetime.fromtimestamp(
            float(ts), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    @staticmethod
    def _next_cron(cron: dict):
        """When the installed daily entry fires next, as a timestamp. Only the
        daily shape this module installs (`M H * * *`) is read; anything else
        falls back to the interval."""
        if not cron.get('installed'):
            return None
        f = str(cron.get('schedule') or '').split()
        if len(f) != 5 or not f[0].isdigit() or not f[1].isdigit():
            return None
        import datetime
        now = datetime.datetime.now()
        nxt = now.replace(hour=int(f[1]), minute=int(f[0]), second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        return nxt.timestamp()

    def status(self, cid=True) -> dict:
        st = self._load()
        last = st.get('last') or {}
        at = last.get('at')
        interval = float(st.get('interval_hours') or INTERVAL_HOURS)
        age = (time.time() - float(at)) if at else None
        fresh = bool(at) and bool(last.get('ok')) and age < interval * 3600 * GRACE

        if not at:
            state, label = 'never', 'never scanned'
        elif not last.get('ok'):
            state, label = 'failed', 'last scan failed'
        elif fresh:
            state, label = 'ok', 'up to date'
        else:
            state, label = 'stale', 'stale'

        changed = {k: last.get(k) or [] for k in ('added', 'removed', 'moved')}
        cron = self.cron_status()
        # When the job is installed, the next scan is when *cron* fires, not
        # last-run + 24h: those differ by however late in the day you scanned.
        nxt = self._next_cron(cron) or ((float(at) + interval * 3600) if at else None)
        out = {
            'state': state,
            'label': label,
            'up_to_date': fresh,
            'last_scan': at,
            'last_scan_iso': self._iso(at),
            'age_seconds': None if age is None else int(age),
            'age': self._ago(age),
            'next_scan': nxt,
            'next_scan_iso': self._iso(nxt),
            'next_scan_source': 'cron' if cron.get('installed') else 'interval',
            'interval_hours': interval,
            'repos': last.get('repos') or len(self.ville._load().get('repos') or []),
            'changed': changed,
            'changes': sum(len(v) for v in changed.values()),
            'restocked': last.get('restocked') or [],
            'seconds': last.get('seconds'),
            'error': last.get('error'),
            'trigger': last.get('trigger'),
            'upstream_commit': (last.get('plinyworld') or {}).get('commit')
            if isinstance(last.get('plinyworld'), dict) else None,
            'cron': cron,
            'history': [{'at': h.get('at'), 'ok': h.get('ok'), 'repos': h.get('repos'),
                         'changes': len(h.get('added') or []) + len(h.get('removed') or [])
                         + len(h.get('moved') or []), 'error': h.get('error')}
                        for h in (st.get('history') or [])[:10]],
        }
        if cid:
            c = self.cid()
            out['cid'] = c.get('cid')
            out['cid_short'] = (c['cid'][:6] + '…' + c['cid'][-4:]) if c.get('cid') else None
            out['cid_at'] = c.get('at')
            out['cid_source'] = c.get('source')
            if c.get('error'):
                out['cid_error'] = c['error']
        return out

    # ── the cron entry ──────────────────────────────────────────────────────

    def cron_line(self, hour=4, minute=17) -> str:
        return (f'{int(minute)} {int(hour)} * * * cd {HERE} && '
                f'PLINYVILLE_SCAN_TRIGGER=cron {sys.executable} {os.path.join(HERE, "scan.py")} '
                f'--run >> {LOG} 2>&1  {CRON_TAG}')

    @staticmethod
    def _crontab() -> list:
        r = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        # An empty crontab exits non-zero with "no crontab for root" — not an error.
        return (r.stdout or '').splitlines() if r.returncode == 0 else []

    @staticmethod
    def _write_crontab(lines):
        body = '\n'.join(lines).rstrip('\n') + '\n'
        r = subprocess.run(['crontab', '-'], input=body, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'crontab install failed: {r.stderr or r.stdout}')

    def cron_status(self) -> dict:
        """Is the daily job actually installed? The page says 'up to date' off a
        receipt; this says whether anything will write the next one."""
        try:
            ours = [ln for ln in self._crontab() if CRON_TAG in ln]
        except FileNotFoundError:
            return {'installed': False, 'error': 'no crontab binary'}
        if not ours:
            return {'installed': False}
        line = ours[0]
        spec = ' '.join(line.split()[:5])
        return {'installed': True, 'schedule': spec, 'line': line, 'log': LOG}

    def cron(self, hour=4, minute=17) -> dict:
        """Install (or move) the daily entry. Idempotent — one tagged line, every
        other crontab entry left exactly where it was."""
        line = self.cron_line(hour, minute)
        keep = [ln for ln in self._crontab() if CRON_TAG not in ln]
        self._write_crontab(keep + [line])
        return {'installed': True, 'schedule': f'{int(minute)} {int(hour)} * * *',
                'line': line, 'log': LOG, 'other_entries': len(keep),
                'note': 'cron carries almost no environment, so the token comes off disk '
                        '(~/.mod/pliny/github.json, or the git mod\'s) — set one with '
                        '`m pliny/token <github_pat>` or the nightly scan is anonymous'}

    def uncron(self) -> dict:
        keep = [ln for ln in self._crontab() if CRON_TAG not in ln]
        removed = len([ln for ln in self._crontab() if CRON_TAG in ln])
        if removed:
            self._write_crontab(keep)
        return {'installed': False, 'removed': removed}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    s = Scanner()
    if '--cron' in argv:
        hour = argv[argv.index('--hour') + 1] if '--hour' in argv else 4
        out = s.cron(hour=hour)
    elif '--uncron' in argv:
        out = s.uncron()
    elif '--status' in argv:
        out = s.status()
    else:
        out = s.run()
        out = {'at': Scanner._iso(out.get('at')), **out}
    print(json.dumps(out, indent=2, default=str), flush=True)
    return 0 if out.get('ok', True) else 1


if __name__ == '__main__':
    sys.exit(main())
