"""
privacy - module visibility for the fleet: public modules anyone can audit,
private modules that leave the host as ciphertext.

Every module on this host is PUBLIC by default. Public means auditable: any
stranger can list the fleet, walk a module's file tree and read its source
through the audit endpoints, without signing in. That is the point of a
module — you can read what you are about to run.

The owner can flip any module, or the whole fleet at once, to PRIVATE. A
private module drops out of the audit surface and gets SEALED: an encrypted
blob (.modseal) is written into its directory and everything else in that
directory is untracked by git, so the only thing a public push carries is
ciphertext that opens with a key that never leaves ~/.mod.

    Why seal instead of encrypting the files in place?

    Because a module is not an archive, it is a running service. Encrypting
    agent/src/mod.py in place stops the agent from importing it. So the
    plaintext stays on the owner's own disk (theirs already) and the
    *published* form is the blob: `git` sees .modseal and nothing else, and
    a clone with the key runs `restore()` to get the tree back.

    What sealing CANNOT do: unpublish. If plaintext was already pushed, it
    is in the repo's history and on every clone. seal() says so.

Layout — all state off-tree, none of it committed:

    ~/.mod/agent/privacy/visibility.json   default + per-module overrides
    ~/.mod/agent/privacy/master.key        32 random bytes (0600), or a
                                           passphrase-wrapped blob
    ~/.mod/agent/privacy/gitignore/<mod>   the module's own .gitignore,
                                           parked while it is sealed

Usage:
    p = Privacy()
    p.ls()                          # the fleet + each module's visibility
    p.tree('polymarket')            # audit: file list of a public module
    p.read('polymarket', 'mod.py')  # audit: one file
    p.set('polymarket', 'private')  # owner: hide it and seal it
    p.set_all('private')            # owner: the whole fleet
    p.restore('polymarket')         # owner: unpack .modseal in a fresh clone
"""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None


STATE = '~/.mod/agent/privacy'
SEAL_NAME = '.modseal'
MAGIC = b'MODSEAL1'

PUBLIC, PRIVATE = 'public', 'private'

NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')

# Directories that are never source: build output, dependency trees and vcs
# metadata. Walking them would turn a 200-file audit into a 200,000-file one
# and a 2 MB seal into a 2 GB one.
SKIP_DIRS = {
    '.git', '.hg', '.svn', 'node_modules', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.next', '.nuxt', '.turbo', '.cache',
    'target', 'dist', 'build', '.venv', 'venv', 'env', 'vendor',
    '.terraform', 'coverage', '.gradle', '.idea', '.vscode', 'logs',
}

# Files that are secrets wherever they sit. A public module is auditable
# source, not a key dump — these are withheld from the audit API from
# everyone (the owner included: reading a key is what the vault is for) and
# left out of the seal, because a secret that rides along in a published
# blob is a secret one bad key rotation away from being public.
SECRET_RE = re.compile(
    r'(^\.env($|\.)|(^|[._-])secret|(^|[._-])credentials?($|[._-])'
    r'|\.key$|\.pem$|\.p12$|\.pfx$|\.keystore$|^id_(rsa|ed25519)|\.ppk$'
    r'|^owner\.json$|\.key\.enc$)',
    re.IGNORECASE)

MAX_FILE_BYTES = 512 * 1024       # one audited file — source, not a payload
MAX_TREE_FILES = 4000             # one tree listing
MAX_SEAL_BYTES = 64 * 1024 * 1024  # refuse to seal more than this of source

# Text detection for the audit reader: a NUL byte in the first block means
# it is not source and the console has nothing useful to show.
BINARY_PROBE = 8192


class SealError(RuntimeError):
    """Sealing/unsealing failed in a way the caller should see verbatim."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Privacy:
    description = "Module visibility: public modules anyone can audit, private ones sealed as ciphertext"

    # Listing the fleet means a config read per module — 300+ of them, ~4s
    # cold, and this is the first thing the MODULES tab does. The names and
    # descriptions barely move, so they are cached for a few minutes; the
    # visibility registry and the sealed flag are read fresh every time,
    # because those are what a click just changed.
    INDEX_TTL = 300

    def __init__(self, state: str = STATE, mods_root: Optional[str] = None):
        self._dir = Path(os.path.expanduser(state))
        self._mods_root = mods_root
        self._key: Optional[bytes] = None
        self._index: Dict[str, dict] = {}
        self._read = 0.0

    # ── state files ──────────────────────────────────────────────────

    def _ensure_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    @property
    def _vis_path(self) -> Path:
        return self._dir / 'visibility.json'

    @property
    def _key_path(self) -> Path:
        return self._dir / 'master.key'

    def _load_vis(self) -> dict:
        """The visibility registry. Absent file = the shipped default: all public."""
        try:
            with open(self._vis_path) as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError
        except Exception:
            doc = {}
        doc.setdefault('default', PUBLIC)
        if doc['default'] not in (PUBLIC, PRIVATE):
            doc['default'] = PUBLIC
        mods = doc.get('modules')
        doc['modules'] = mods if isinstance(mods, dict) else {}
        return doc

    def _save_vis(self, doc: dict):
        doc['updated'] = int(time.time())
        self._ensure_dir()
        tmp = self._vis_path.with_suffix('.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(doc, f, indent=2)
        tmp.replace(self._vis_path)

    # ── the master key ───────────────────────────────────────────────
    #
    # One 32-byte key for the whole fleet, kept 0600 under ~/.mod — off the
    # module tree, so it is never a candidate for a commit. With a passphrase
    # the file holds only a scrypt-wrapped copy, and unsealing needs the
    # passphrase typed; without one, holding the host disk is holding the key.

    KDF_N, KDF_R, KDF_P = 2 ** 15, 8, 1

    @staticmethod
    def _derive(passphrase: str, salt: bytes) -> bytes:
        # maxmem: OpenSSL caps scrypt at 32 MB by default and n=2^15,r=8 wants
        # ~32 MB exactly, so it has to be raised or the derivation throws
        return hashlib.scrypt(passphrase.encode(), salt=salt,
                              n=Privacy.KDF_N, r=Privacy.KDF_R,
                              p=Privacy.KDF_P, dklen=32,
                              maxmem=128 * 1024 * 1024)

    def key_state(self) -> dict:
        """Whether a key exists and whether a passphrase guards it."""
        try:
            with open(self._key_path) as f:
                blob = json.load(f)
            return {'exists': True, 'passphrase': bool(blob.get('kdf')),
                    'created': blob.get('created')}
        except Exception:
            return {'exists': self._key_path.exists(), 'passphrase': False,
                    'created': None}

    def master_key(self, passphrase: Optional[str] = None,
                   create: bool = True) -> bytes:
        """The fleet key, created on first use."""
        if self._key is not None:
            return self._key
        blob = None
        if self._key_path.exists():
            try:
                with open(self._key_path) as f:
                    blob = json.load(f)
            except Exception as e:
                raise SealError(f"master key unreadable: {e}")
        if blob is None:
            if not create:
                raise SealError("no master key yet — seal something first")
            raw = os.urandom(32)
            self._write_key(raw, passphrase)
            self._key = raw
            return raw
        if blob.get('kdf'):
            if not passphrase:
                raise SealError("this key is passphrase-protected — pass the passphrase")
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            kek = self._derive(passphrase, base64.b64decode(blob['salt']))
            try:
                raw = AESGCM(kek).decrypt(base64.b64decode(blob['nonce']),
                                          base64.b64decode(blob['ct']),
                                          b'agent-privacy-key-v1')
            except Exception:
                raise SealError("wrong passphrase")
        else:
            raw = base64.b64decode(blob['key'])
        self._key = raw
        return raw

    def _write_key(self, raw: bytes, passphrase: Optional[str]):
        self._ensure_dir()
        doc = {'v': 1, 'created': int(time.time())}
        if passphrase:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            salt, nonce = os.urandom(16), os.urandom(12)
            kek = self._derive(passphrase, salt)
            ct = AESGCM(kek).encrypt(nonce, raw, b'agent-privacy-key-v1')
            doc.update(kdf='scrypt', n=self.KDF_N, r=self.KDF_R, p=self.KDF_P,
                       salt=base64.b64encode(salt).decode(),
                       nonce=base64.b64encode(nonce).decode(),
                       ct=base64.b64encode(ct).decode())
        else:
            doc['key'] = base64.b64encode(raw).decode()
        with open(self._key_path, 'w') as f:
            json.dump(doc, f, indent=2)
        try:
            os.chmod(self._key_path, 0o600)
        except Exception:
            pass

    def key_export(self, passphrase: Optional[str] = None) -> str:
        """The key as base64 — what you write down so a clone can decrypt."""
        return base64.b64encode(self.master_key(passphrase, create=False)).decode()

    def key_import(self, key_b64: str, passphrase: Optional[str] = None) -> dict:
        """Install a key exported from another host."""
        raw = base64.b64decode(key_b64)
        if len(raw) != 32:
            raise SealError("a fleet key is 32 bytes (base64)")
        self._write_key(raw, passphrase)
        self._key = raw
        return self.key_state()

    def key_passphrase(self, passphrase: Optional[str],
                       current: Optional[str] = None) -> dict:
        """Add, change or (passphrase=None) drop the passphrase on the key."""
        raw = self.master_key(current, create=False)
        self._write_key(raw, passphrase or None)
        return self.key_state()

    # ── the fleet ────────────────────────────────────────────────────

    @staticmethod
    def _name(name: str) -> str:
        n = (name or '').strip()
        if not NAME_RE.fullmatch(n):
            raise ValueError("module name must be 1-64 chars: letters, digits, _ . -")
        return n

    def modules(self) -> List[str]:
        """Every module on the host, by name."""
        if m is None:
            return []
        return sorted(n for n in m.mods()
                      if isinstance(n, str) and not n.startswith(('_', '.')))

    def index(self, fresh: bool = False) -> Dict[str, dict]:
        """name -> {description, version, dir}, cached for INDEX_TTL."""
        if self._index and not fresh and (time.time() - self._read) < self.INDEX_TTL:
            return self._index
        index = {}
        for name in self.modules():
            try:
                cfg = m.config(name) or {}
            except Exception:
                cfg = {}
            try:
                d = m.dirpath(name)
            except Exception:
                d = None
            index[name] = {'description': (cfg.get('description') or '')[:280],
                           'version': cfg.get('version'),
                           'dir': d}
        self._index, self._read = index, time.time()
        return index

    def dirpath(self, name: str) -> Path:
        """Where a module lives. Raises if the framework does not know it."""
        name = self._name(name)
        if m is None:
            raise SealError("mod framework unavailable")
        p = m.dirpath(name)
        if not p or not os.path.isdir(p):
            raise KeyError(f"module not found: {name}")
        return Path(os.path.realpath(p))

    def visibility(self, name: str) -> str:
        """public / private for one module — its override, else the default."""
        doc = self._load_vis()
        return doc['modules'].get(self._name(name), doc['default'])

    def is_public(self, name: str) -> bool:
        return self.visibility(name) == PUBLIC

    def sealed(self, name: str) -> bool:
        try:
            return (self.dirpath(name) / SEAL_NAME).exists()
        except Exception:
            return False

    def ls(self, q: str = '', include_private: bool = True) -> dict:
        """The fleet with each module's visibility — the audit index.

        Private modules are LISTED but not opened: that a module exists is
        not the secret, its source is. Hiding the name as well would make
        the fleet lie about its own shape.
        """
        doc = self._load_vis()
        q = (q or '').strip().lower()
        out = []
        for name, entry in self.index().items():
            vis = doc['modules'].get(name, doc['default'])
            if vis == PRIVATE and not include_private:
                continue
            desc = entry['description']
            if q and q not in name.lower() and q not in desc.lower():
                continue
            d = entry.get('dir')
            out.append({
                'name': name,
                'visibility': vis,
                'description': desc if vis == PUBLIC else '',
                'version': entry['version'] if vis == PUBLIC else None,
                'sealed': bool(d) and os.path.exists(os.path.join(d, SEAL_NAME)),
            })
        return {'modules': out,
                'default': doc['default'],
                'total': len(out),
                'public': sum(1 for e in out if e['visibility'] == PUBLIC),
                'private': sum(1 for e in out if e['visibility'] == PRIVATE),
                'updated': doc.get('updated')}

    # ── audit (public modules, no sign-in) ───────────────────────────

    def _audit_dir(self, name: str) -> Path:
        """The module directory, or PermissionError if it is not public."""
        name = self._name(name)
        if not self.is_public(name):
            raise PermissionError(f"module '{name}' is private")
        return self.dirpath(name)

    @staticmethod
    def _walk(root: Path, limit: int = MAX_TREE_FILES):
        """Every auditable file under root, relative, sorted, capped."""
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in SKIP_DIRS and not d.startswith('.git'))
            for fn in sorted(filenames):
                if fn == SEAL_NAME or SECRET_RE.search(fn):
                    continue
                full = Path(dirpath) / fn
                if full.is_symlink():
                    continue
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                found.append((str(full.relative_to(root)), size))
                if len(found) >= limit:
                    return found, True
        return sorted(found), False

    def tree(self, name: str) -> dict:
        """The file list of a public module — what an audit starts from."""
        root = self._audit_dir(name)
        files, truncated = self._walk(root)
        return {'module': name, 'visibility': PUBLIC,
                'files': [{'path': p, 'size': s} for p, s in files],
                'count': len(files), 'bytes': sum(s for _, s in files),
                'truncated': truncated}

    def _resolve(self, root: Path, rel: str) -> Path:
        """rel inside root, or ValueError. The traversal guard.

        realpath both sides and compare: '../../etc/passwd', a symlink out of
        the tree and an absolute path all fail the same check.
        """
        rel = (rel or '').strip()
        if not rel or rel.startswith('/') or rel.startswith('~'):
            raise ValueError("path must be relative to the module directory")
        full = Path(os.path.realpath(root / rel))
        if full != root and root not in full.parents:
            raise ValueError("path escapes the module directory")
        if SECRET_RE.search(full.name) or full.name == SEAL_NAME:
            raise PermissionError("that file is withheld from audit")
        for part in full.relative_to(root).parts[:-1]:
            if part in SKIP_DIRS:
                raise PermissionError("that directory is not source")
        return full

    def read(self, name: str, path: str) -> dict:
        """One file out of a public module."""
        root = self._audit_dir(name)
        full = self._resolve(root, path)
        if not full.is_file():
            raise KeyError(f"no such file: {path}")
        size = full.stat().st_size
        head = full.open('rb').read(BINARY_PROBE)
        if b'\0' in head:
            return {'module': name, 'path': path, 'size': size,
                    'binary': True, 'text': None}
        if size > MAX_FILE_BYTES:
            text = full.open('r', errors='replace').read(MAX_FILE_BYTES)
            return {'module': name, 'path': path, 'size': size,
                    'binary': False, 'truncated': True, 'text': text}
        return {'module': name, 'path': path, 'size': size,
                'binary': False, 'truncated': False,
                'text': full.open('r', errors='replace').read()}

    # ── sealing ──────────────────────────────────────────────────────

    def _tar(self, root: Path, name: Optional[str] = None) -> tuple:
        """A deterministic .tar.gz of the module's source. (bytes, files, raw)

        Deterministic — sorted names, zeroed mtime/uid/gid — so re-sealing an
        unchanged module produces the same plaintext hash and we can skip
        rewriting the blob instead of churning the repo on every call.

        The managed .gitignore is ours, not the module's, so it never goes in
        (it would change the hash on the second seal and every seal after);
        the module's own copy, parked in the state dir while it is sealed,
        goes in under that name instead so a restore puts it back.
        """
        files, truncated = self._walk(root, limit=10 ** 7)
        parked = (self._dir / 'gitignore' / name) if name else None
        managed = self._is_managed_gitignore(root / '.gitignore')
        if managed:
            files = [(f, s) for f, s in files if f != '.gitignore']
        total = sum(s for _, s in files)
        if total > MAX_SEAL_BYTES:
            raise SealError(
                f"{total // 1024 // 1024} MB of source is more than the "
                f"{MAX_SEAL_BYTES // 1024 // 1024} MB seal limit — this looks "
                f"like build output that should be in SKIP_DIRS")
        buf = io.BytesIO()
        # gzip stamps the current time into its own header, which would make
        # every seal of an unchanged module a different blob — mtime=0 is the
        # difference between "no diff" and a rewrite on every call
        gz = gzip.GzipFile(filename='', fileobj=buf, mode='wb',
                           compresslevel=9, mtime=0)
        with gz, tarfile.open(fileobj=gz, mode='w|',
                              format=tarfile.GNU_FORMAT) as tar:
            for rel, _ in files:
                full = root / rel
                info = tar.gettarinfo(str(full), arcname=rel)
                info.mtime, info.uid, info.gid = 0, 0, 0
                info.uname = info.gname = ''
                with open(full, 'rb') as f:
                    tar.addfile(info, f)
            if managed and parked is not None and parked.exists():
                blob = parked.read_bytes()
                if blob.strip():
                    info = tarfile.TarInfo('.gitignore')
                    info.size, info.mtime, info.mode = len(blob), 0, 0o644
                    tar.addfile(info, io.BytesIO(blob))
                    files = files + [('.gitignore', len(blob))]
                    total += len(blob)
        raw = buf.getvalue()
        return raw, len(files), total

    def _is_managed_gitignore(self, path: Path) -> bool:
        try:
            return path.read_text(errors='replace').startswith(self.GITIGNORE_HEAD)
        except Exception:
            return False

    def _seal_header(self, path: Path) -> Optional[dict]:
        try:
            with open(path, 'rb') as f:
                if f.read(len(MAGIC) + 1) != MAGIC + b'\n':
                    return None
                n = int.from_bytes(f.read(4), 'big')
                return json.loads(f.read(n).decode())
        except Exception:
            return None

    def seal(self, name: str, passphrase: Optional[str] = None) -> dict:
        """Write the module's encrypted blob and take its plaintext out of git.

        Three things happen, and all three are needed for a push to carry
        ciphertext only:
          1. .modseal        — AES-256-GCM over a deterministic tar of source
          2. .gitignore      — managed: ignore everything but the blob
          3. git rm --cached — .gitignore does not untrack what git already
                               tracks, so the plaintext has to be dropped
                               from the index explicitly
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        name = self._name(name)
        root = self.dirpath(name)
        plain, n_files, n_bytes = self._tar(root, name)
        digest = _sha256(plain)

        seal_path = root / SEAL_NAME
        prev = self._seal_header(seal_path)
        if prev and prev.get('sha256') == digest:
            # unchanged source — rewriting would only churn the diff
            self._park_gitignore(name, root)
            untracked = self._git_untrack(root)
            return {'module': name, 'sealed': True, 'unchanged': True,
                    'files': prev.get('files'), 'bytes': prev.get('bytes'),
                    'untracked': untracked, 'path': str(seal_path)}

        master = self.master_key(passphrase)
        content_key = os.urandom(32)
        nonce, wrap_nonce = os.urandom(12), os.urandom(12)
        ct = AESGCM(content_key).encrypt(nonce, plain, MAGIC)
        wrapped = AESGCM(master).encrypt(wrap_nonce, content_key, MAGIC)

        header = {
            'v': 1, 'module': name, 'alg': 'AES-256-GCM', 'container': 'tar.gz',
            'nonce': base64.b64encode(nonce).decode(),
            'wrap': base64.b64encode(wrapped).decode(),
            'wrap_nonce': base64.b64encode(wrap_nonce).decode(),
            'sha256': digest, 'files': n_files, 'bytes': n_bytes,
            'sealed': int(time.time()),
        }
        hb = json.dumps(header, sort_keys=True).encode()
        tmp = seal_path.with_suffix('.tmp')
        with open(tmp, 'wb') as f:
            f.write(MAGIC + b'\n')
            f.write(len(hb).to_bytes(4, 'big'))
            f.write(hb)
            f.write(ct)
        tmp.replace(seal_path)

        self._park_gitignore(name, root)
        untracked = self._git_untrack(root)
        return {'module': name, 'sealed': True, 'unchanged': False,
                'files': n_files, 'bytes': n_bytes,
                'blob_bytes': seal_path.stat().st_size,
                'untracked': untracked, 'path': str(seal_path),
                'warning': ('sealing hides the module from here on — plaintext '
                            'already pushed stays in the repo history')}

    GITIGNORE_HEAD = '# --- agent/privacy: sealed module ---'

    def _park_gitignore(self, name: str, root: Path):
        """Swap in the managed .gitignore, keeping the module's own copy."""
        gi = root / '.gitignore'
        parked = self._ensure_dir() / 'gitignore'
        parked.mkdir(exist_ok=True)
        keep = parked / name
        if gi.exists():
            text = gi.read_text(errors='replace')
            if not text.startswith(self.GITIGNORE_HEAD) and not keep.exists():
                keep.write_text(text)
        elif not keep.exists():
            keep.write_text('')
        gi.write_text(
            f"{self.GITIGNORE_HEAD}\n"
            f"# This module is private. Only the encrypted blob is published;\n"
            f"# `agent unseal name={name}` puts the tree back under git.\n"
            f"*\n!.gitignore\n!{SEAL_NAME}\n")

    def _restore_gitignore(self, name: str, root: Path):
        gi = root / '.gitignore'
        keep = self._dir / 'gitignore' / name
        original = keep.read_text(errors='replace') if keep.exists() else ''
        if original.strip():
            gi.write_text(original)
        elif gi.exists() and gi.read_text(errors='replace').startswith(self.GITIGNORE_HEAD):
            gi.unlink()
        if keep.exists():
            keep.unlink()

    def _git(self, root: Path, *args: str) -> tuple:
        try:
            r = subprocess.run(('git',) + args, cwd=str(root), capture_output=True,
                               text=True, timeout=120)
            return r.returncode, (r.stdout or '') + (r.stderr or '')
        except Exception as e:
            return 1, str(e)

    def _git_untrack(self, root: Path) -> int:
        """Drop the module's plaintext from the index, keep it on disk.

        Files stay in the working tree (the module has to keep running) —
        this only stops git from carrying them to the remote.
        """
        code, out = self._git(root, 'rev-parse', '--is-inside-work-tree')
        if code != 0 or 'true' not in out:
            return 0
        code, out = self._git(root, 'ls-files', '-z')
        tracked = [p for p in out.split('\0') if p and p not in ('.gitignore', SEAL_NAME)]
        if tracked:
            self._git(root, 'rm', '-r', '--cached', '--quiet', '--', *tracked)
        # stage the two files that replace them, so the next commit publishes
        # ciphertext instead of leaving the blob untracked next to a diff that
        # deletes the module. -f because the managed .gitignore ignores them.
        self._git(root, 'add', '-f', '--', '.gitignore', SEAL_NAME)
        return len(tracked)

    def _git_track(self, root: Path) -> int:
        code, out = self._git(root, 'rev-parse', '--is-inside-work-tree')
        if code != 0 or 'true' not in out:
            return 0
        self._git(root, 'add', '-A', '.')
        code, out = self._git(root, 'ls-files', '-z')
        return len([p for p in out.split('\0') if p])

    def unseal(self, name: str) -> dict:
        """Undo seal(): drop the blob, restore .gitignore, re-track the tree."""
        name = self._name(name)
        root = self.dirpath(name)
        seal_path = root / SEAL_NAME
        had = seal_path.exists()
        if had:
            seal_path.unlink()
        self._restore_gitignore(name, root)
        tracked = self._git_track(root)
        return {'module': name, 'sealed': False, 'blob_removed': had,
                'tracked': tracked}

    def restore(self, name: str, passphrase: Optional[str] = None,
                force: bool = False) -> dict:
        """Unpack .modseal back into the module directory.

        This is the other side of a public push: a clone holds the blob and
        nothing else, and whoever has the key gets the source back.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        name = self._name(name)
        root = self.dirpath(name)
        seal_path = root / SEAL_NAME
        if not seal_path.exists():
            raise SealError(f"{name} has no {SEAL_NAME} to restore")
        with open(seal_path, 'rb') as f:
            if f.read(len(MAGIC) + 1) != MAGIC + b'\n':
                raise SealError("not a modseal blob")
            hb = f.read(int.from_bytes(f.read(4), 'big'))
            header = json.loads(hb.decode())
            ct = f.read()
        master = self.master_key(passphrase, create=False)
        try:
            content_key = AESGCM(master).decrypt(
                base64.b64decode(header['wrap_nonce']),
                base64.b64decode(header['wrap']), MAGIC)
            plain = AESGCM(content_key).decrypt(
                base64.b64decode(header['nonce']), ct, MAGIC)
        except Exception:
            raise SealError("this key does not open that blob")
        if _sha256(plain) != header.get('sha256'):
            raise SealError("blob failed its integrity check")

        # a fresh clone holds the blob and the managed .gitignore and nothing
        # else — anything beyond that is somebody's working tree, so don't
        # overwrite it without being told to
        existing, _ = self._walk(root, limit=10)
        if self._is_managed_gitignore(root / '.gitignore'):
            existing = [e for e in existing if e[0] != '.gitignore']
        if existing and not force:
            raise SealError(
                f"{name} already has source on disk — restore(force=True) to "
                f"overwrite it with the sealed copy")
        with tarfile.open(fileobj=io.BytesIO(plain), mode='r:gz') as tar:
            for member in tar.getmembers():
                # a tar from elsewhere is untrusted input: refuse anything
                # that would land outside the module directory
                target = Path(os.path.realpath(root / member.name))
                if root not in target.parents and target != root:
                    raise SealError(f"blob contains an escaping path: {member.name}")
                if member.issym() or member.islnk():
                    raise SealError(f"blob contains a link: {member.name}")
            tar.extractall(root)
        return {'module': name, 'restored': True,
                'files': header.get('files'), 'bytes': header.get('bytes'),
                'sealed_at': header.get('sealed')}

    # ── the switches ─────────────────────────────────────────────────

    def set(self, name: str, visibility: str,
            passphrase: Optional[str] = None, seal: bool = True) -> dict:
        """Flip one module. private seals it, public unseals it."""
        name = self._name(name)
        visibility = (visibility or '').strip().lower()
        if visibility not in (PUBLIC, PRIVATE):
            raise ValueError("visibility must be 'public' or 'private'")
        self.dirpath(name)                       # 404 before we write state
        doc = self._load_vis()
        if visibility == doc['default']:
            doc['modules'].pop(name, None)       # back to following the default
        else:
            doc['modules'][name] = visibility
        self._save_vis(doc)
        out = {'module': name, 'visibility': visibility}
        if seal:
            out['seal'] = (self.seal(name, passphrase) if visibility == PRIVATE
                           else self.unseal(name))
        return out

    def set_all(self, visibility: str, passphrase: Optional[str] = None,
                seal: bool = True) -> dict:
        """Flip the whole fleet, and the default new modules inherit.

        Sealing 300 modules can fail in 300 ways — a module that is not in a
        git tree, one whose build output blows the size cap — so every module
        is attempted and the failures come back as a list rather than
        aborting the run half-done.
        """
        visibility = (visibility or '').strip().lower()
        if visibility not in (PUBLIC, PRIVATE):
            raise ValueError("visibility must be 'public' or 'private'")
        doc = self._load_vis()
        doc['default'] = visibility
        doc['modules'] = {}                      # no overrides left to honor
        self._save_vis(doc)

        done, failed = [], []
        if seal:
            for name in self.modules():
                try:
                    if visibility == PRIVATE:
                        r = self.seal(name, passphrase)
                    else:
                        r = self.unseal(name)
                    done.append({'module': name, **{k: r.get(k) for k in
                                                    ('files', 'bytes', 'unchanged')}})
                except Exception as e:
                    failed.append({'module': name, 'error': str(e)})
        return {'default': visibility, 'sealed' if visibility == PRIVATE else 'unsealed': len(done),
                'failed': failed, 'modules': len(self.modules())}

    # ── self-test ────────────────────────────────────────────────────

    def test(self) -> dict:
        """Round-trip the crypto and the guards in a throwaway directory."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix='privacy-test-'))
        try:
            state = tmp / 'state'
            p = Privacy(state=str(state))
            mod_dir = tmp / 'demo'
            (mod_dir / 'src').mkdir(parents=True)
            (mod_dir / 'mod.py').write_text('print("hello")\n')
            (mod_dir / 'src' / 'a.py').write_text('A = 1\n')
            (mod_dir / '.env').write_text('SECRET=nope\n')
            (mod_dir / 'node_modules').mkdir()
            (mod_dir / 'node_modules' / 'big.js').write_text('x' * 1000)

            files, _ = p._walk(mod_dir)
            names = {f for f, _ in files}
            assert names == {'mod.py', 'src/a.py'}, names

            # the traversal guard
            for bad in ('../etc/passwd', '/etc/passwd', '.env',
                        'node_modules/big.js'):
                try:
                    p._resolve(mod_dir, bad)
                    raise AssertionError(f'{bad} should not resolve')
                except (ValueError, PermissionError):
                    pass

            # seal -> wipe -> restore
            plain, n, total = p._tar(mod_dir, 'demo')
            assert n == 2 and total > 0
            assert p._tar(mod_dir, 'demo')[0] == plain, 'tar must be deterministic'

            # a full round trip on a throwaway "module": seal it, prove the
            # blob is the only thing git would carry, wipe the tree the way a
            # fresh clone arrives, and open it again with the key
            p.dirpath = lambda name: mod_dir      # type: ignore[assignment]
            p._git(mod_dir, 'init', '-q')
            p._git(mod_dir, 'add', '-A', '.')
            r = p.seal('demo')
            assert r['files'] == 2 and (mod_dir / SEAL_NAME).exists()
            _, tracked = p._git(mod_dir, 'ls-files')
            assert set(tracked.split()) <= {'.gitignore', SEAL_NAME}, tracked
            assert p._seal_header(mod_dir / SEAL_NAME)['sha256'] == _sha256(plain)
            assert p.seal('demo')['unchanged'] is True

            blob = (mod_dir / SEAL_NAME).read_bytes()
            assert b'hello' not in blob and b'A = 1' not in blob

            (mod_dir / 'mod.py').unlink()
            (mod_dir / 'src' / 'a.py').unlink()
            p.restore('demo')
            assert (mod_dir / 'mod.py').read_text() == 'print("hello")\n'
            assert (mod_dir / 'src' / 'a.py').read_text() == 'A = 1\n'

            # a stranger's key opens nothing
            other = Privacy(state=str(tmp / 'state3'))
            other.dirpath = lambda name: mod_dir  # type: ignore[assignment]
            other.master_key()
            try:
                other.restore('demo', force=True)
                raise AssertionError('a foreign key must not open the blob')
            except SealError:
                pass

            p.unseal('demo')
            assert not (mod_dir / SEAL_NAME).exists()

            # a passphrase-wrapped key needs the passphrase
            p2 = Privacy(state=str(tmp / 'state2'))
            p2.master_key('hunter2')
            p2._key = None
            try:
                p2.master_key()
                raise AssertionError('passphrase key opened without one')
            except SealError:
                pass
            p2._key = None
            assert len(p2.master_key('hunter2')) == 32
            return {'ok': True, 'checks': 4}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
