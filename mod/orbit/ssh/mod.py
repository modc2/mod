"""
ssh — server-side SSH key vault.

Private keys live on the server at ~/.mod/ssh/keys/, encrypted at rest in
OpenSSH's own passphrase-protected format (bcrypt KDF via `ssh-keygen -p`).
The vault never stores a password and never persists a plaintext key:
decryption happens per-operation into a 0600 tempfile on /dev/shm (RAM)
that is deleted before the call returns.

Because the at-rest format is standard OpenSSH, every stored key file is
portable — copy it anywhere and use it directly with `ssh -i` plus its
passphrase. No custom crypto to trust.

  add(name, private_key, password)   import a key you already have
  generate(name, password)           mint a new ed25519 key
  keys()                             list metadata (no password needed)
  pubkey(name)                       public key (no password needed)
  verify(name, password)             check a password without exporting
  export(name, password)             decrypted private key (deliberate)
  exec(name, password, host, cmd)    run a command over ssh with a vault key
  passwd(name, old, new)             rotate the encryption password
  remove(name)                       delete a key
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MIN_PASSWORD = 5  # ssh-keygen refuses passphrases shorter than 5 chars
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class Mod:
    description = "SSH key vault — keys stored server-side, encrypted at rest, decrypted per-use with your password"
    path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        self.store_dir = Path(os.environ.get("SSH_STORE_DIR", os.path.expanduser("~/.mod/ssh")))
        self.keys_dir = self.store_dir / "keys"
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.store_dir, 0o700)
        os.chmod(self.keys_dir, 0o700)
        self.index_path = self.store_dir / "index.json"

    # ── helpers ───────────────────────────────────────────────────────

    def _index(self) -> dict:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text() or "{}")
        except Exception:
            return {}

    def _save_index(self, idx: dict) -> None:
        self.index_path.write_text(json.dumps(idx, indent=2))
        os.chmod(self.index_path, 0o600)

    @staticmethod
    def _check_name(name: str) -> str:
        if not NAME_RE.match(name or ""):
            raise ValueError("name must be 1-64 chars of [A-Za-z0-9._-]")
        return name

    @staticmethod
    def _check_password(password: str) -> str:
        if not password or len(password) < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        return password

    def _key_path(self, name: str) -> Path:
        p = self.keys_dir / self._check_name(name)
        return p

    def _require_key(self, name: str) -> Path:
        p = self._key_path(name)
        if not p.exists():
            raise FileNotFoundError(f"no key named {name!r} — see keys()")
        return p

    @staticmethod
    def _tmpdir():
        # /dev/shm keeps transient plaintext off disk; fall back to default tmp
        base = "/dev/shm" if os.path.isdir("/dev/shm") else None
        return tempfile.mkdtemp(prefix="modssh-", dir=base)

    @staticmethod
    def _run(cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)

    def _fingerprint(self, pub_path: Path) -> dict:
        r = self._run(["ssh-keygen", "-lf", str(pub_path)])
        if r.returncode != 0:
            return {}
        parts = r.stdout.split()
        # e.g. "256 SHA256:xxxx comment (ED25519)"
        return {
            "bits": parts[0] if parts else None,
            "fingerprint": parts[1] if len(parts) > 1 else None,
            "type": parts[-1].strip("()") if parts else None,
        }

    def _decrypt_to(self, name: str, password: str, workdir: str) -> str:
        """Copy the stored key into workdir and strip its passphrase.
        Returns the plaintext key path. Caller must delete workdir."""
        src = self._require_key(name)
        dst = os.path.join(workdir, name)
        shutil.copy(src, dst)
        os.chmod(dst, 0o600)
        r = self._run(["ssh-keygen", "-p", "-f", dst, "-P", password, "-N", ""])
        if r.returncode != 0:
            raise PermissionError(f"wrong password for key {name!r}")
        return dst

    def _register(self, name: str, priv: Path, source: str) -> dict:
        os.chmod(priv, 0o600)
        pub = Path(str(priv) + ".pub")
        meta = {
            "name": name,
            "created": int(time.time()),
            "source": source,
            **self._fingerprint(pub),
            "public_key": pub.read_text().strip() if pub.exists() else None,
        }
        idx = self._index()
        idx[name] = meta
        self._save_index(idx)
        return meta

    # ── public ────────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": "ssh",
            "description": self.description,
            "store": str(self.keys_dir),
            "keys": list(self._index().keys()),
            "ops": ["add", "generate", "keys", "pubkey", "verify", "export", "exec", "passwd", "remove"],
        }

    def add(self, name: str, private_key: str, password: str, current_password: str = "") -> dict:
        """Import a private key you already have and encrypt it under `password`.

        If the pasted key is itself passphrase-protected, pass its current
        passphrase as `current_password` so it can be re-encrypted.
        """
        self._check_name(name)
        self._check_password(password)
        if self._key_path(name).exists():
            raise FileExistsError(f"key {name!r} already exists — remove() it first or pick another name")
        work = self._tmpdir()
        try:
            tmp = os.path.join(work, name)
            body = private_key.strip() + "\n"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(body)
            # -o forces the modern OpenSSH format (bcrypt KDF) even for PEM input
            r = self._run(["ssh-keygen", "-p", "-o", "-f", tmp, "-P", current_password, "-N", password])
            if r.returncode != 0:
                err = (r.stderr or r.stdout).strip()
                raise ValueError(f"could not import key: {err}")
            # derive the public key (works now that we know the passphrase)
            rp = self._run(["ssh-keygen", "-y", "-f", tmp, "-P", password])
            if rp.returncode != 0:
                raise ValueError("imported key but could not derive its public key")
            dst = self._key_path(name)
            shutil.move(tmp, dst)
            Path(str(dst) + ".pub").write_text(rp.stdout)
            os.chmod(str(dst) + ".pub", 0o644)
            return self._register(name, dst, source="imported")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def generate(self, name: str, password: str, type: str = "ed25519", comment: str = "") -> dict:
        """Generate a fresh keypair, encrypted under `password` from birth."""
        self._check_name(name)
        self._check_password(password)
        if type not in ("ed25519", "rsa", "ecdsa"):
            raise ValueError("type must be ed25519, rsa or ecdsa")
        if self._key_path(name).exists():
            raise FileExistsError(f"key {name!r} already exists")
        dst = self._key_path(name)
        cmd = ["ssh-keygen", "-t", type, "-f", str(dst), "-N", password, "-C", comment or f"mod-ssh:{name}"]
        if type == "rsa":
            cmd += ["-b", "4096"]
        r = self._run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"ssh-keygen failed: {(r.stderr or r.stdout).strip()}")
        return self._register(name, dst, source="generated")

    def keys(self) -> list:
        """List stored keys — metadata and public keys only, no password needed."""
        return list(self._index().values())

    def pubkey(self, name: str) -> str:
        """Public key line for `name` — paste into authorized_keys."""
        p = Path(str(self._require_key(name)) + ".pub")
        return p.read_text().strip()

    def verify(self, name: str, password: str) -> bool:
        """True if `password` unlocks the stored key. Nothing is exported."""
        r = self._run(["ssh-keygen", "-y", "-f", str(self._require_key(name)), "-P", password])
        return r.returncode == 0

    def export(self, name: str, password: str, encrypted: bool = False) -> dict:
        """Return the private key. Plaintext by default; `encrypted=true`
        returns the at-rest ciphertext (still needs the password to use)."""
        src = self._require_key(name)
        if encrypted:
            if not self.verify(name, password):
                raise PermissionError(f"wrong password for key {name!r}")
            return {"name": name, "private_key": src.read_text(), "encrypted": True}
        work = self._tmpdir()
        try:
            plain = self._decrypt_to(name, password, work)
            return {"name": name, "private_key": Path(plain).read_text(), "encrypted": False,
                    "warning": "plaintext private key — handle accordingly"}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def exec(self, name: str, password: str, host: str, cmd: str,
             user: str = "root", port: int = 22, timeout: int = 30) -> dict:
        """Run `cmd` on `host` over ssh, authenticating with the vault key.
        The key is decrypted into RAM for this call only."""
        work = self._tmpdir()
        try:
            ident = self._decrypt_to(name, password, work)
            r = subprocess.run(
                ["ssh", "-i", ident,
                 "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=accept-new",
                 "-o", f"ConnectTimeout={min(timeout, 30)}",
                 "-p", str(port),
                 f"{user}@{host}", cmd],
                capture_output=True, text=True, timeout=timeout + 15,
            )
            return {"host": host, "cmd": cmd, "code": r.returncode,
                    "stdout": r.stdout, "stderr": r.stderr}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def passwd(self, name: str, old_password: str, new_password: str) -> dict:
        """Re-encrypt the stored key under a new password."""
        self._check_password(new_password)
        src = self._require_key(name)
        r = self._run(["ssh-keygen", "-p", "-o", "-f", str(src), "-P", old_password, "-N", new_password])
        if r.returncode != 0:
            raise PermissionError(f"wrong password for key {name!r}")
        return {"name": name, "rotated": True}

    def remove(self, name: str) -> dict:
        """Delete a stored key and its metadata."""
        src = self._require_key(name)
        src.unlink(missing_ok=True)
        Path(str(src) + ".pub").unlink(missing_ok=True)
        idx = self._index()
        idx.pop(name, None)
        self._save_index(idx)
        return {"name": name, "removed": True}

    def readme(self):
        for n in ["README.md", "readme.md", "README"]:
            p = os.path.join(self.path, n)
            if os.path.exists(p):
                return open(p).read()
        return None
