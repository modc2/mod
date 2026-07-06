"""
whitepaper — MOD off-chain Merkle-tree registry whitepaper.

Ships:
  * docs/whitepaper.tex   — LaTeX source
  * src/app/              — Next.js viewer (basePath /whitepaper)
  * Caddyfile             — proxy stanza (:3000 routes /whitepaper and /api/whitepaper)
  * reference impl        — tree_build / tree_root / tree_proof / tree_verify

CLI usage:
  m whitepaper/info
  m whitepaper/build           # compile pdf (requires pdflatex)
  m whitepaper/serve           # api + app behind the proxy
  m whitepaper/tree_root
  m whitepaper/tree_build records='[...]'
  m whitepaper/tree_proof name=agent
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

import mod as m


DIR = Path(__file__).parent
TEX_FILE = DIR / "whitepaper.tex"
APP_DIR = DIR / "src" / "app"
API_DIR = DIR / "src" / "api"
STATE_DIR = Path.home() / ".mod" / "whitepaper"
STATE_DIR.mkdir(parents=True, exist_ok=True)
TREE_FILE = STATE_DIR / "tree.json"
PID_FILE = STATE_DIR / "serve.pids"


def _keccak(b: bytes) -> bytes:
    try:
        from Crypto.Hash import keccak  # pycryptodome
        h = keccak.new(digest_bits=256)
        h.update(b)
        return h.digest()
    except Exception:
        try:
            from eth_hash.auto import keccak as _k
            return _k(b)
        except Exception:
            return hashlib.sha3_256(b).digest()


def _cjson(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _leaf(record: dict) -> bytes:
    return _keccak(_cjson(record))


def _pair(a: bytes, b: bytes) -> bytes:
    return _keccak(a + b) if a < b else _keccak(b + a)


def _build_tree(leaves: list[bytes]) -> tuple[bytes, list[list[bytes]]]:
    if not leaves:
        zero = b"\x00" * 32
        return zero, [[zero]]
    layers = [sorted(leaves)]
    while len(layers[-1]) > 1:
        prev = layers[-1]
        nxt = [_pair(prev[i], prev[i + 1] if i + 1 < len(prev) else prev[i])
               for i in range(0, len(prev), 2)]
        layers.append(nxt)
    return layers[-1][0], layers


def _proof_for(layers: list[list[bytes]], leaf: bytes) -> list[bytes]:
    if leaf not in layers[0]:
        raise KeyError("leaf not in tree")
    idx = layers[0].index(leaf)
    proof: list[bytes] = []
    for layer in layers[:-1]:
        sibling = idx ^ 1
        # Odd-sized layers duplicate the unpaired node during build, so the
        # proof must mirror that by self-pairing here.
        proof.append(layer[sibling] if sibling < len(layer) else layer[idx])
        idx //= 2
    return proof


def _verify(leaf: bytes, proof: list[bytes], root: bytes) -> bool:
    cur = leaf
    for sib in proof:
        cur = _pair(cur, sib)
    return cur == root


class Mod:
    description = "Off-chain Merkle-tree open-source registry whitepaper + reference impl."

    def __init__(self, **kwargs):
        self.dir = DIR
        cfg_path = DIR / "config.json"
        self.config = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        self.api_port = self.config.get("port", 50106)
        self.app_port = self.config.get("app_port", 3106)

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": "whitepaper",
            "description": self.description,
            "tex": str(TEX_FILE),
            "tex_exists": TEX_FILE.exists(),
            "app_dir": str(APP_DIR),
            "ports": {"api": self.api_port, "app": self.app_port},
            "proxy": self.config.get("proxy"),
            "tree_state": str(TREE_FILE) if TREE_FILE.exists() else None,
        }

    # ── PDF build ────────────────────────────────────────────────
    def build(self, engine: str = "pdflatex") -> dict:
        """Compile whitepaper.tex to PDF. Requires a TeX engine on PATH."""
        if not TEX_FILE.exists():
            raise FileNotFoundError(str(TEX_FILE))
        try:
            subprocess.run([engine, "-version"], capture_output=True, check=True)
        except Exception as e:
            return {"ok": False, "error": f"{engine} not available: {e}"}
        out_dir = DIR / "build"
        out_dir.mkdir(exist_ok=True)
        result = subprocess.run(
            [engine, "-interaction=nonstopmode", f"-output-directory={out_dir}", str(TEX_FILE)],
            capture_output=True, text=True, cwd=str(DIR),
        )
        pdf = out_dir / (TEX_FILE.stem + ".pdf")
        return {
            "ok": pdf.exists(),
            "pdf": str(pdf) if pdf.exists() else None,
            "log_tail": result.stdout.splitlines()[-20:],
        }

    # ── Merkle tree reference impl ──────────────────────────────
    def tree_build(self, records: list[dict]) -> dict:
        """Build a Merkle tree over module records. Returns root + manifest path."""
        leaves = [_leaf(r) for r in records]
        root, layers = _build_tree(leaves)
        manifest = {
            "epoch": int(_now_epoch()),
            "root": "0x" + root.hex(),
            "count": len(records),
            "records": records,
            "layers": [[node.hex() for node in layer] for layer in layers],
        }
        TREE_FILE.write_text(json.dumps(manifest))
        return {"root": manifest["root"], "count": manifest["count"], "epoch": manifest["epoch"]}

    def tree_root(self) -> dict:
        if not TREE_FILE.exists():
            return {"root": None, "epoch": None}
        manifest = json.loads(TREE_FILE.read_text())
        return {"root": manifest["root"], "epoch": manifest["epoch"], "count": manifest["count"]}

    def tree_proof(self, name: str) -> dict:
        if not TREE_FILE.exists():
            raise RuntimeError("no tree built — call tree_build first")
        manifest = json.loads(TREE_FILE.read_text())
        target = next((r for r in manifest["records"] if r.get("name") == name), None)
        if target is None:
            raise KeyError(f"no record named {name!r}")
        layers = [[bytes.fromhex(h) for h in layer] for layer in manifest["layers"]]
        proof = _proof_for(layers, _leaf(target))
        return {
            "record": target,
            "leaf": "0x" + _leaf(target).hex(),
            "proof": ["0x" + p.hex() for p in proof],
            "root": manifest["root"],
        }

    def tree_verify(self, leaf: str, proof: list[str], root: str | None = None) -> dict:
        target_root = root or (json.loads(TREE_FILE.read_text())["root"] if TREE_FILE.exists() else None)
        if target_root is None:
            raise RuntimeError("no root available; pass `root=` or build the tree")
        ok = _verify(
            bytes.fromhex(leaf.removeprefix("0x")),
            [bytes.fromhex(p.removeprefix("0x")) for p in proof],
            bytes.fromhex(target_root.removeprefix("0x")),
        )
        return {"ok": ok, "root": target_root}

    # ── Protocol bridge: merkle the live orbit + core ecosystem ──────────
    def _merkle_records(self, scope: str = "orbit") -> list[dict]:
        """Build canonical records for every module on disk.

        Uses the same shape the Rust API emits so on-chain roots match across
        languages: {name, scope, path, port, app_port, schema}.
        """
        records: list[dict] = []
        scopes = ["orbit", "core"] if scope == "all" else [scope]
        for s in scopes:
            root_path = m.paths["orbit"].get(s)
            if not root_path or not os.path.isdir(root_path):
                continue
            for entry in sorted(os.listdir(root_path)):
                if entry.startswith(("_", ".")):
                    continue
                p = os.path.join(root_path, entry)
                if not os.path.isdir(p):
                    continue
                try:
                    cfg = m.config(entry) or {}
                except Exception:
                    cfg = {}
                anchor = next(
                    (a for a in ("agent.py", "mod.py", "block.py", f"{entry}.py")
                     if os.path.exists(os.path.join(p, a))),
                    None,
                )
                if anchor is None and not cfg:
                    continue
                records.append({
                    "name": entry,
                    "scope": s,
                    "path": p,
                    "port": cfg.get("port"),
                    "app_port": cfg.get("app_port"),
                    "schema": cfg.get("schema"),
                })
        return records

    def merkle(self, scope: str = "orbit") -> dict:
        """Build the Merkle tree from the live orbit (and optionally core) ecosystem."""
        return self.tree_build(self._merkle_records(scope))

    def list_mods(self, scope: str = "orbit") -> list[dict]:
        return [
            {"name": r["name"], "scope": r["scope"], "path": r["path"],
             "port": r["port"], "app_port": r["app_port"]}
            for r in self._merkle_records(scope)
        ]

    def mod_info(self, name: str) -> dict:
        path = os.path.join(m.paths["orbit"]["orbit"], name)
        scope = "orbit"
        if not os.path.isdir(path):
            path = os.path.join(m.paths["orbit"]["core"], name)
            scope = "core"
        if not os.path.isdir(path):
            raise KeyError(f"module `{name}` not found in orbit or core")
        return {"name": name, "scope": scope, "path": path, "config": m.config(name) or {}}

    # ── Serve (rust api + next app + caddy) ─────────────────────
    def build_api(self, release: bool = True) -> dict:
        """Compile the Rust API binary."""
        if not (API_DIR / "Cargo.toml").exists():
            raise FileNotFoundError(str(API_DIR / "Cargo.toml"))
        cmd = ["cargo", "build"] + (["--release"] if release else [])
        result = subprocess.run(cmd, cwd=str(API_DIR), capture_output=True, text=True)
        if result.returncode != 0:
            return {"ok": False, "stderr_tail": result.stderr.splitlines()[-30:]}
        target = "release" if release else "debug"
        bin_path = API_DIR / "target" / target / "whitepaper-api"
        return {"ok": bin_path.exists(), "bin": str(bin_path)}

    def _api_binary(self) -> Path:
        for target in ("release", "debug"):
            p = API_DIR / "target" / target / "whitepaper-api"
            if p.exists():
                return p
        raise RuntimeError("Rust API binary not built. Run `m whitepaper/build_api`.")

    def serve(self, app: bool = True, caddy: bool = True) -> dict:
        """Start the Rust API + Next.js app + optional Caddy proxy.

        Picks the first free proxy port from {3000, 3030, 3031, …} and threads
        it through to Caddy as WHITEPAPER_PROXY_PORT, so the canonical :3000
        is used when free and we degrade gracefully otherwise.
        """
        pids: dict[str, int | None] = {"api": None, "app": None, "caddy": None}
        warnings: list[str] = []

        configured_host = (self.config.get("proxy") or {}).get("host", "localhost:3000")
        configured_port = int(configured_host.rsplit(":", 1)[-1])
        proxy_port = _first_free_port([configured_port, 3030, 3031, 3032, 3033, 3034])
        if proxy_port != configured_port:
            owner = _port_owner(configured_port)
            warnings.append(
                f"proxy port {configured_port} busy (pid={owner.get('pid') if owner else '?'} "
                f"{owner.get('name') if owner else ''}); falling back to {proxy_port}"
            )

        for port, label in [
            (self.api_port, f"api ({self.api_port})"),
            (self.app_port, f"app ({self.app_port})"),
        ]:
            owner = _port_owner(port)
            if owner:
                warnings.append(
                    f"port {port} for {label} already in use by pid={owner['pid']} ({owner['name']})"
                )

        log_dir = Path("/tmp/whitepaper")
        log_dir.mkdir(exist_ok=True)

        # Auto-build the Rust binary if missing.
        try:
            api_bin = self._api_binary()
        except RuntimeError:
            self.build_api(release=True)
            api_bin = self._api_binary()

        # Env vars shared by every child so all three know about the proxy.
        proxy_env = {
            "WHITEPAPER_MODULE_DIR": str(DIR),
            "WHITEPAPER_PROXY_PORT": str(proxy_port),
            "WHITEPAPER_API_PORT": str(self.api_port),
            "WHITEPAPER_APP_PORT": str(self.app_port),
            "WHITEPAPER_PROXY_URL": f"http://localhost:{proxy_port}",
            "NEXT_PUBLIC_BASE_PATH": "/whitepaper",
            "NEXT_PUBLIC_API_URL": "/api/whitepaper",
            "WHITEPAPER_API_URL": f"http://localhost:{self.api_port}",
        }

        api_log = open(log_dir / "api.log", "ab")
        api_proc = subprocess.Popen(
            [str(api_bin)],
            cwd=str(DIR),
            stdout=api_log,
            stderr=api_log,
            env={**os.environ, **proxy_env},
        )
        pids["api"] = api_proc.pid

        if app and APP_DIR.exists():
            app_log = open(log_dir / "app.log", "ab")
            app_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(APP_DIR), stdout=app_log, stderr=app_log,
                env={**os.environ, **proxy_env, "PORT": str(self.app_port)},
            )
            pids["app"] = app_proc.pid

        if caddy and (DIR / "Caddyfile").exists():
            caddy_log = open(log_dir / "caddy.log", "ab")
            try:
                caddy_proc = subprocess.Popen(
                    ["caddy", "run", "--config", str(DIR / "Caddyfile"), "--adapter", "caddyfile"],
                    stdout=caddy_log, stderr=caddy_log,
                    env={**os.environ, **proxy_env},
                )
                pids["caddy"] = caddy_proc.pid
            except FileNotFoundError:
                pids["caddy"] = None
                warnings.append("caddy binary not on PATH; proxy not started")

        PID_FILE.write_text(json.dumps({**pids, "proxy_port": proxy_port}))
        return {
            "pids": pids,
            "urls": {
                "app":   f"http://localhost:{proxy_port}/whitepaper",
                "api":   f"http://localhost:{proxy_port}/api/whitepaper",
                "direct_app": f"http://localhost:{self.app_port}/whitepaper",
                "direct_api": f"http://localhost:{self.api_port}",
            },
            "proxy": {**(self.config.get("proxy") or {}), "active_port": proxy_port},
            "api_bin": str(api_bin),
            "warnings": warnings,
        }

    def kill(self) -> dict:
        if not PID_FILE.exists():
            return {"ok": True, "killed": []}
        pids = json.loads(PID_FILE.read_text())
        killed: list[str] = []
        for name, pid in pids.items():
            if pid is None:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(name)
            except ProcessLookupError:
                pass
        PID_FILE.unlink(missing_ok=True)
        return {"ok": True, "killed": killed}


def _now_epoch() -> int:
    import time
    return int(time.time())


def _port_owner(port: int) -> dict | None:
    """Return the process listening on `port`, or None if free. macOS/Linux lsof."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    pid: str | None = None
    name: str | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c"):
            name = line[1:]
    return {"pid": pid, "name": name} if pid else None


def _first_free_port(candidates: list[int]) -> int:
    for p in candidates:
        if _port_owner(p) is None:
            return p
    return candidates[-1]
