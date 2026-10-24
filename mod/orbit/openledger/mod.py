"""
openledger — your key sharded across USB sticks instead of a hardware wallet.

Shamir's Secret Sharing over GF(256) splits a key into n shares; any k of them
rebuild it, k-1 reveal nothing. `shard` writes one share per plugged-in USB
drive so the whole key never touches any single disk, and `restore` rebuilds
it from whatever drives are plugged back in. A ledger at ~/.openledger records
which drive got which share — never the key, never a share.

Shares use the same ss1 format as orbit/secretshare (the engine in shamir/ is
a vendored copy), so pieces move freely between the two modules.

CLI:
    m openledger/usbs                                  # which USB drives are plugged in
    m openledger/shard path=./key.txt n=3 k=2 label=main
    m openledger/check                                 # which sets can be rebuilt right now
    m openledger/restore set=ab12cd34 out=./key.txt
    m openledger/sets                                  # the ledger: every set ever sharded
    m openledger/wipe set=ab12cd34                     # remove that set's shares from plugged drives
    m openledger/test                                  # end-to-end self-check on temp dirs

No USBs on this box? Every drive-touching fn takes drives=/path/a,/path/b to
use plain directories instead (that is also how the self-test runs).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mod as m

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

import shamir  # noqa: E402

DRIVE_DIR = 'openledger'          # folder created on each stick
DRIVE_NOTE = """\
This drive holds ONE piece of a key sharded with openledger
(Shamir's Secret Sharing). One piece alone reveals NOTHING about the
key — it takes K pieces plugged into one machine to rebuild it:

    m openledger/restore

Do not rename or edit the .share files.
"""


class Mod:
    description = 'Shard a key into N Shamir shares, one per USB stick; any K plugged back in rebuild it'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {'name': self.config['name'], 'description': self.description,
                'format': f'{shamir.VERSION}.<set>.<k>.<x>.<payload> (secretshare-compatible)',
                'ledger': str(self._ledger_dir() / 'ledger.json'),
                'fns': ['usbs', 'shard', 'check', 'restore', 'sets', 'wipe',
                        'split', 'combine', 'inspect', 'test']}

    # ── drives ───────────────────────────────────────────────────────

    def usbs(self, drives=None) -> dict:
        """Mounted USB (removable) partitions. drives=<dir,dir> substitutes plain dirs."""
        found = self._usb_mounts(drives)
        return {'count': len(found), 'drives': found,
                **({} if found else {'hint': 'plug in a USB stick and mount it, or pass drives=/path/a,/path/b'})}

    def _usb_mounts(self, drives=None) -> list:
        if drives:
            if isinstance(drives, str):
                drives = [d for d in drives.replace(',', ' ').split() if d]
            out = []
            for d in drives:
                p = Path(os.path.expanduser(d)).resolve()
                if not p.is_dir():
                    raise ValueError(f'not a directory: {p}')
                out.append({'mount': str(p), 'drive_label': p.name,
                            'uuid': None, 'serial': None, 'size': None})
            return out
        try:
            r = subprocess.run(
                ['lsblk', '-J', '-o', 'NAME,TYPE,TRAN,RM,HOTPLUG,MOUNTPOINT,LABEL,UUID,SERIAL,SIZE'],
                capture_output=True, text=True, check=True)
            tree = json.loads(r.stdout).get('blockdevices', [])
        except Exception:
            return []
        found = []

        def walk(dev, removable, serial):
            removable = removable or dev.get('tran') == 'usb' or bool(dev.get('rm')) or bool(dev.get('hotplug'))
            serial = dev.get('serial') or serial
            mp = dev.get('mountpoint')
            if removable and mp and mp not in ('/', '/boot', '/boot/efi', '[SWAP]'):
                found.append({'mount': mp, 'drive_label': dev.get('label'),
                              'uuid': dev.get('uuid'), 'serial': serial, 'size': dev.get('size')})
            for c in dev.get('children') or []:
                walk(c, removable, serial)

        for d in tree:
            walk(d, False, None)
        return found

    # ── shard / restore ──────────────────────────────────────────────

    def shard(self, secret=None, path: Optional[str] = None, n: Optional[int] = None,
              k: int = 2, label: str = 'key', drives=None) -> dict:
        """Split a key and write one share per drive — the whole key never touches disk.
        n defaults to the number of drives plugged in; any k of them restore."""
        if path:
            secret = Path(os.path.expanduser(path)).read_bytes()
        if secret is None:
            return {'error': 'pass the secret, or path=<file>'}
        mounts = self._usb_mounts(drives)
        n = int(n) if n else len(mounts)
        k = int(k)
        if n < 2 or k < 2:
            return {'error': 'need n >= 2 and k >= 2 — one stick holding the whole key is just a worse Ledger'}
        if len(mounts) < n:
            return {'error': f'need {n} drives mounted, found {len(mounts)}',
                    'found': [mt['mount'] for mt in mounts],
                    'hint': 'plug in more USB sticks, or pass drives=/path/a,/path/b'}
        shares = shamir.split(secret, n=n, k=k)
        set_id = shares[0].split('.')[1]
        rec = {'set': set_id, 'label': label, 'n': n, 'k': k,
               'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'shares': {}}
        placed = []
        for share, mnt in zip(shares, mounts[:n]):
            x = share.split('.')[3]
            d = Path(mnt['mount']) / DRIVE_DIR
            d.mkdir(parents=True, exist_ok=True)
            f = d / f'{label}-{set_id}-x{x}.share'
            f.write_text(share + '\n')
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass                              # FAT has no modes
            note = d / 'WHAT_IS_THIS.txt'
            if not note.exists():
                note.write_text(DRIVE_NOTE)
            rec['shares'][x] = {k2: mnt.get(k2) for k2 in ('serial', 'uuid', 'drive_label')}
            rec['shares'][x]['mount_at_write'] = mnt['mount']
            placed.append({'x': int(x), 'drive': mnt['mount'], 'file': str(f)})
        try:
            os.sync()                             # flush before anyone yanks a stick
        except OSError:
            pass
        self._ledger_add(rec)
        return {'set': set_id, 'label': label, 'n': n, 'k': k, 'placed': placed,
                'note': f'any {k} of these {n} drives rebuild the key; {k - 1} reveal nothing. '
                        f'Unplug them and store them apart.'}

    def restore(self, set: Optional[str] = None, out: Optional[str] = None,
                drives=None) -> dict:
        """Rebuild a key from the shares on plugged-in drives. out=<file> writes it
        (mode 600) instead of printing it."""
        sets = self._scan(drives)
        if not sets:
            return {'error': 'no openledger shares found on any plugged-in drive',
                    'hint': 'm openledger/usbs to see what is mounted'}
        if set is None:
            ready = [s for s, v in sets.items() if len(v['shares']) >= v['k']]
            if len(ready) == 1:
                set = ready[0]
            else:
                return {'error': 'say which set= to restore',
                        'found': {s: {'label': v['label'], 'have': len(v['shares']), 'need': v['k']}
                                  for s, v in sets.items()}}
        if set not in sets:
            return {'error': f'no shares of set {set} on plugged-in drives',
                    'found': list(sets)}
        v = sets[set]
        if len(v['shares']) < v['k']:
            return {'error': f'set {set} needs {v["k"]} shares, only {len(v["shares"])} plugged in',
                    'have': sorted(v['shares']), 'from': v['files']}
        data = shamir.combine(list(v['shares'].values()))
        res = {'set': set, 'label': v['label'], 'bytes': len(data),
               'from_drives': v['files']}
        if out:
            p = Path(os.path.expanduser(out))
            p.write_bytes(data)
            os.chmod(p, 0o600)
            return {**res, 'out': str(p)}
        try:
            return {**res, 'secret': data.decode()}
        except UnicodeDecodeError:
            import base64
            return {**res, 'secret_b64': base64.b64encode(data).decode()}

    def check(self, drives=None) -> dict:
        """The ledger vs. what is plugged in: which sets could be rebuilt right now."""
        plugged = self._scan(drives)
        ledger = {r['set']: r for r in self._ledger_load()['sets']}
        rows = []
        for s in sorted(ledger | plugged):
            rec, live = ledger.get(s), plugged.get(s)
            have = len(live['shares']) if live else 0
            k = (live or rec)['k']
            rows.append({'set': s, 'label': (live or rec).get('label'),
                         'k': k, 'n': rec['n'] if rec else None,
                         'plugged_in': have, 'restorable': have >= k,
                         'in_ledger': rec is not None})
        return {'sets': rows,
                'restorable_now': [r['set'] for r in rows if r['restorable']]}

    def sets(self) -> dict:
        """The ledger: every set ever sharded, and where its shares went."""
        return self._ledger_load()

    def wipe(self, set: str, drives=None) -> dict:
        """Remove a set's share files from plugged-in drives (overwrite, then delete).
        Flash wear-leveling means overwrite is best-effort — physically destroying
        a retired stick is the only sure erase."""
        removed = []
        for mnt in self._usb_mounts(drives):
            for f in (Path(mnt['mount']) / DRIVE_DIR).glob(f'*-{set}-x*.share'):
                try:
                    f.write_bytes(os.urandom(f.stat().st_size))
                    f.unlink()
                    removed.append(str(f))
                except OSError as e:
                    removed.append(f'{f}: FAILED {e}')
        led = self._ledger_load()
        led['sets'] = [r for r in led['sets'] if r['set'] != set]
        self._ledger_save(led)
        return {'set': set, 'removed': removed, 'ledger_entry_dropped': True,
                'caveat': 'flash wear-leveling can keep stale copies; destroy retired sticks'}

    def _scan(self, drives=None) -> dict:
        """{set: {label, k, shares: {x: share}, files: [..]}} from plugged-in drives."""
        sets = {}
        for mnt in self._usb_mounts(drives):
            for f in sorted((Path(mnt['mount']) / DRIVE_DIR).glob('*.share')):
                for line in f.read_text().split():
                    try:
                        p = shamir.parse(line)
                    except shamir.ShareError:
                        continue
                    v = sets.setdefault(p['set'], {'label': f.name.rsplit('-', 2)[0],
                                                   'k': p['k'], 'shares': {}, 'files': []})
                    v['shares'][p['x']] = line
                    v['files'].append(str(f))
        return sets

    # ── plain share ops (secretshare parity) ─────────────────────────

    def split(self, secret=None, path: Optional[str] = None, n: int = 5, k: int = 3) -> dict:
        """Split to share strings without touching any drive."""
        if path:
            secret = Path(os.path.expanduser(path)).read_bytes()
        if secret is None:
            return {'error': 'pass the secret, or path=<file>'}
        shares = shamir.split(secret, n=int(n), k=int(k))
        return {'n': int(n), 'k': int(k), 'set': shares[0].split('.')[1], 'shares': shares}

    def combine(self, shares=None) -> dict:
        """Rebuild from share strings (list, or one whitespace/comma separated string)."""
        if not shares:
            return {'error': 'pass shares'}
        data = shamir.combine(shares)
        try:
            return {'secret': data.decode(), 'bytes': len(data)}
        except UnicodeDecodeError:
            import base64
            return {'secret_b64': base64.b64encode(data).decode(), 'bytes': len(data)}

    def inspect(self, share: str) -> dict:
        """Set id, threshold and index of a share — reveals nothing of the secret."""
        return shamir.inspect(share)

    # ── ledger ───────────────────────────────────────────────────────

    def _ledger_dir(self) -> Path:
        return Path(os.path.expanduser(os.environ.get('OPENLEDGER_DIR', '~/.openledger')))

    def _ledger_load(self) -> dict:
        f = self._ledger_dir() / 'ledger.json'
        if f.exists():
            return json.loads(f.read_text())
        return {'sets': []}

    def _ledger_save(self, led: dict):
        d = self._ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / 'ledger.json'
        f.write_text(json.dumps(led, indent=2) + '\n')
        os.chmod(f, 0o600)

    def _ledger_add(self, rec: dict):
        led = self._ledger_load()
        led['sets'] = [r for r in led['sets'] if r['set'] != rec['set']] + [rec]
        self._ledger_save(led)

    # ── test ─────────────────────────────────────────────────────────

    def test(self) -> dict:
        """End-to-end on temp dirs standing in for sticks: shard to 3, restore from
        any 2, refuse 1; wipe cleans up; plain split/combine roundtrips."""
        import tempfile
        from itertools import combinations
        secret = os.urandom(48)
        results = {}
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['OPENLEDGER_DIR'] = str(Path(tmp) / 'ledger')
            try:
                sticks = []
                for i in range(3):
                    d = Path(tmp) / f'usb{i}'
                    d.mkdir()
                    sticks.append(str(d))
                r = self.shard(secret=secret, k=2, label='t', drives=','.join(sticks))
                results['sharded'] = 'error' not in r and len(r['placed']) == 3
                set_id = r['set']
                ok = True
                for pair in combinations(sticks, 2):
                    out = self.restore(set=set_id, drives=','.join(pair))
                    import base64
                    ok = ok and base64.b64decode(out.get('secret_b64', '')) == secret
                results['any_2_of_3_restore'] = ok
                results['1_refused'] = 'error' in self.restore(set=set_id, drives=sticks[0])
                results['check'] = self.check(drives=','.join(sticks))['restorable_now'] == [set_id]
                w = self.wipe(set=set_id, drives=','.join(sticks))
                results['wiped'] = len(w['removed']) == 3 and not self._scan(','.join(sticks))
                plain = self.split(secret='hunter2', n=4, k=3)
                results['plain_roundtrip'] = self.combine(plain['shares'][:3])['secret'] == 'hunter2'
            finally:
                del os.environ['OPENLEDGER_DIR']
        results['ok'] = all(v is True for k2, v in results.items() if k2 != 'ok')
        return results

    def readme(self):
        return m.get_text(str(self.module_dir / 'README.md'))
