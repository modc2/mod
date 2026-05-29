"""
dev proxy API — single-port frontend for claude + codex backends.

Endpoints are mounted under both `/` (so prefix-stripping reverse proxies like
Caddy work) and `/api/dev` (so direct browser hits without a proxy work). The
UI is served at both `/` and `/dev`.

GET  /health                       — liveness
GET  /config                       — module config.json
GET  /providers                    — list of providers (claude, codex)
GET  /key/{provider}               — fetch wallet's encrypted key (if set)
POST /key/{provider}               — store the wallet's encrypted key
                                       body: { ciphertext, iv }
                                       header: X-Mod-Signature (wallet sig over challenge)
ANY  /proxy/{provider}/{path}      — proxy request to claude/codex backend
"""

import json
import os
from pathlib import Path
from typing import Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response

CONFIG_PATH = Path("/app/config.json") if Path("/app/config.json").exists() else Path(__file__).resolve().parents[2] / "config.json"
KEY_STORE_DIR = Path(os.environ.get("DEV_KEY_DIR", "/home/node/.mod/dev"))
KEY_STORE_DIR.mkdir(parents=True, exist_ok=True)
KEY_STORE_PATH = KEY_STORE_DIR / "keys.json"

SIGNATURE_CHALLENGE = "mod-key-derivation-v1"

with CONFIG_PATH.open() as f:
    CONFIG = json.load(f)

PROVIDERS = dict(CONFIG.get("providers", {}))
# Env override for upstream API URL — useful when running outside Docker where
# the in-network hostnames (claude:8820, codex:8830) don't resolve.
for _name in list(PROVIDERS):
    _env_url = os.environ.get(f"{_name.upper()}_API")
    if _env_url:
        PROVIDERS[_name] = {**PROVIDERS[_name], "api": _env_url}

OWNER = (CONFIG.get("owner") or "").lower()
UI_PATH = Path(__file__).resolve().parents[1] / "app" / "index.html"

app = FastAPI(title="mod/dev proxy", version=CONFIG.get("version", "0.1.0"))
api = APIRouter()


def _load_store() -> dict:
    if not KEY_STORE_PATH.exists():
        return {}
    return json.loads(KEY_STORE_PATH.read_text() or "{}")


def _save_store(d: dict) -> None:
    KEY_STORE_PATH.write_text(json.dumps(d, indent=2))


def _recover_wallet(signature: Optional[str]) -> str:
    if not signature:
        raise HTTPException(status_code=401, detail="missing X-Mod-Signature")
    try:
        msg = encode_defunct(text=SIGNATURE_CHALLENGE)
        return Account.recover_message(msg, signature=signature).lower()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid signature: {e}")


@api.get("/health")
def health():
    return {"status": "ok", "name": CONFIG.get("name"), "version": CONFIG.get("version")}


@api.get("/config")
def get_config():
    return CONFIG


@api.get("/providers")
def get_providers():
    return {name: {k: v for k, v in p.items() if k != "env_key"} for name, p in PROVIDERS.items()}


@api.get("/key/{provider}")
def get_key(provider: str, x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    if provider not in PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    wallet = _recover_wallet(x_mod_signature)
    entry = _load_store().get(provider, {}).get(wallet)
    if not entry:
        return JSONResponse({"exists": False}, status_code=404)
    return {"exists": True, "ciphertext": entry["ciphertext"], "iv": entry["iv"], "wallet": wallet}


@api.post("/key/{provider}")
async def set_key(provider: str, request: Request, x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    if provider not in PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    wallet = _recover_wallet(x_mod_signature)
    body = await request.json()
    ciphertext = body.get("ciphertext")
    iv = body.get("iv")
    if not ciphertext or not iv:
        raise HTTPException(400, "ciphertext and iv required")
    store = _load_store()
    store.setdefault(provider, {})[wallet] = {"ciphertext": ciphertext, "iv": iv}
    _save_store(store)
    return {"ok": True, "wallet": wallet, "provider": provider}


def _commune_challenge(action: str, name: str) -> str:
    return f"mod-commune-{action}:{name}"


def _recover_wallet_for(action: str, name: str, signature: Optional[str]) -> str:
    if not signature:
        raise HTTPException(status_code=401, detail="missing X-Mod-Signature")
    try:
        msg = encode_defunct(text=_commune_challenge(action, name))
        return Account.recover_message(msg, signature=signature).lower()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid signature: {e}")


def _dev_mod():
    """Lazy-load the dev orchestrator (avoids import-time cost)."""
    import sys
    sys.path.insert(0, "/Users/broski/mod")
    import mod as m
    return m.mod("dev")()


@api.get("/communes")
def list_communes():
    """List all registered communes."""
    return _dev_mod().communes()


@api.get("/communes/{name}")
def get_commune(name: str):
    """Get one commune by name."""
    c = _dev_mod().commune(name)
    if not c:
        raise HTTPException(404, f"commune {name!r} not found")
    return c


@api.post("/communes/{name}")
async def create_commune(name: str, request: Request,
                         x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    """Create or update a commune. Body: {modules: [{name, content_hash}]}.

    The caller signs the challenge `mod-commune-create:{name}` with their
    wallet; that wallet becomes the commune's owner (if new) or must match
    the existing owner (if updating).
    """
    wallet = _recover_wallet_for("create", name, x_mod_signature)
    body = await request.json()
    modules = body.get("modules") or []
    try:
        result = _dev_mod().create_commune(name=name, modules=modules, owner=wallet,
                                           signature=x_mod_signature)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400 if isinstance(e, ValueError) else 403, str(e))
    return result


@api.delete("/communes/{name}")
def delete_commune(name: str,
                   x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    """Delete a commune. Caller signs `mod-commune-delete:{name}`."""
    wallet = _recover_wallet_for("delete", name, x_mod_signature)
    try:
        return _dev_mod().delete_commune(name=name, owner=wallet)
    except PermissionError as e:
        raise HTTPException(403, str(e))


@api.get("/merkle")
def get_merkle():
    """Current deployment merkle (modules + communes)."""
    return _dev_mod().merkle()


# Brand image overrides — kept here (not in config.json) so a config linter
# stripping unknown fields can't break the avatars. iconify CDN.
KNOWN_IMAGES = {
    "claude":  "https://api.iconify.design/simple-icons:anthropic.svg?color=%23ffffff",
    "codex":   "https://api.iconify.design/simple-icons:openai.svg?color=%23ffffff",
    "cursor":  "https://api.iconify.design/simple-icons:cursor.svg?color=%23ffffff",
    "tether":  "https://api.iconify.design/simple-icons:tether.svg?color=%23ffffff",
    "dev":     "https://api.iconify.design/mdi:code-tags.svg?color=%23ffffff",
    "bridge":  "https://api.iconify.design/mdi:bridge.svg?color=%23ffffff",
    "model":   "https://api.iconify.design/mdi:brain.svg?color=%23ffffff",
    "localfs": "https://api.iconify.design/mdi:folder-cog.svg?color=%23ffffff",
}


@api.get("/modules")
def list_modules():
    """List orbit modules with status info for the sidebar.

    Each entry: name, alive (gateway reachable?), kind, port (if known),
    served (in current dev.serve() set?), warnings (config issues).
    """
    import urllib.request as _r
    import urllib.error as _e
    dev = _dev_mod()
    served = (dev._read_pidfile().get("modules") or {})
    orbit = dev._orbit_dir()
    results = []
    for entry in sorted(orbit.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        cfg_path = entry / "config.json"
        if not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            continue
        name = entry.name
        port = cfg.get("port")
        served_info = served.get(name)
        alive = False
        warnings = []
        # Probe directly by port — more reliable than the pidfile (which only
        # tracks what dev.serve() started) and works when modules are run
        # manually or by a different orchestrator.
        if port:
            try:
                with _r.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.3) as r:
                    alive = r.status == 200
            except Exception:
                pass
        if not cfg.get("owner"):
            warnings.append("no owner set")
        if cfg.get("port") is None:
            warnings.append("no port configured")
        results.append({
            "name": name,
            "version": cfg.get("version"),
            "description": cfg.get("description"),
            "icon": cfg.get("icon"),
            "color": cfg.get("color"),
            "image": KNOWN_IMAGES.get(name) or cfg.get("image"),
            "port": port,
            "app_port": cfg.get("app_port"),
            "kind": "fastapi" if name == "dev" else ("rust" if port else "static"),
            "alive": alive,
            "served": bool(served_info),
            "served_pid": served_info.get("pid") if served_info else None,
            "warnings": warnings,
        })
    return results


@api.get("/logs/{module}")
def get_logs(module: str, lines: int = 100, since: int = 0):
    """Tail the last `lines` of /tmp/mod-{module}.log. `since` is a byte offset
    for incremental polling — the response includes the new offset so the
    client can fetch only what's new on the next call.
    """
    log_path = Path(f"/tmp/mod-{module}.log")
    if not log_path.exists():
        return {"module": module, "lines": [], "offset": 0, "exists": False}
    size = log_path.stat().st_size
    with log_path.open("rb") as f:
        if since and since < size:
            f.seek(since)
            chunk = f.read().decode("utf-8", errors="replace")
            new_lines = chunk.splitlines()
        else:
            # Initial load — read the tail.
            tail_bytes = min(size, 64 * 1024)
            f.seek(max(0, size - tail_bytes))
            chunk = f.read().decode("utf-8", errors="replace")
            new_lines = chunk.splitlines()[-lines:]
    return {"module": module, "lines": new_lines, "offset": size, "exists": True, "bytes": size}


ORBIT_DIR = Path("/Users/broski/mod/mod/orbit")


def _detect_runtime(name: str):
    """Return (lang, dispatcher_fn) for an agent — looks for contract files
    in three languages and picks the first match: Rust binary > JS > Python.
    For Rust contracts where the source exists but the binary hasn't been
    built yet, returns a "synthetic" dispatcher that serves manifest only."""
    base = ORBIT_DIR / name
    rust_bin = base / "target" / "release" / f"{name}-contract"
    rust_src = base / "src" / "main.rs"
    if rust_bin.exists() and os.access(rust_bin, os.X_OK):
        return ("rust", lambda cmd, *args: _shell_dispatch([str(rust_bin), cmd, *args]))
    for js_file in (base / "contract.js", base / "src" / "contract.js"):
        if js_file.exists():
            return ("javascript", lambda cmd, *args: _shell_dispatch(["node", str(js_file), cmd, *args]))
    py_file = next((p for p in (base / "contract.py", base / "src" / "mod.py") if p.exists()), None)
    if py_file is not None:
        return ("python", lambda cmd, *args: _python_dispatch(name, py_file, cmd, args))
    # Rust source without binary — synthetic manifest, no call dispatch.
    if rust_src.exists():
        return ("rust", lambda cmd, *args: _rust_synthetic_dispatch(name, rust_src, cmd, args))
    return (None, None)


def _rust_synthetic_dispatch(name: str, rust_src: Path, cmd: str, args: tuple) -> dict:
    """Serve manifest for a Rust contract whose binary hasn't been built yet.
    Returns a hint about how to build."""
    import hashlib as _h
    src = rust_src.read_bytes()
    code_hash = "0x" + _h.sha3_256(src).hexdigest()
    cfg_path = ORBIT_DIR / name / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    if cmd == "manifest":
        return {
            "name": name,
            "lang": "rust",
            "binary": cfg.get("binary", ""),
            "default_model": cfg.get("default_model", ""),
            "env_key": cfg.get("env_key", ""),
            "description": cfg.get("description", ""),
            "icon": cfg.get("icon", ""),
            "color": cfg.get("color", ""),
            "code_hash": code_hash,
            "abi": [],
            "status": "binary not built — run `cd " + str(ORBIT_DIR / name) + " && cargo build --release`",
        }
    if cmd == "abi":
        return []
    return {"error": "binary not built — run cargo build --release in " + str(ORBIT_DIR / name)}


def _shell_dispatch(argv: list) -> dict:
    import subprocess
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout or "no output").strip()}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"raw": r.stdout.strip()}


def _python_dispatch(name: str, py_file: Path, cmd: str, args: tuple) -> dict:
    import importlib.util as iu
    import sys as _sys
    agent_base_dir = str(ORBIT_DIR / "dev" / "src")
    if agent_base_dir not in _sys.path:
        _sys.path.insert(0, agent_base_dir)
    spec = iu.spec_from_file_location(f"_agent_{name}", py_file)
    mod = iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Mod", None)
    if cls is None:
        return {"error": f"no Mod class in {py_file}"}
    # File-based code_hash since dynamically loaded classes lose inspect.getsource.
    try:
        cls._sourceFile = str(py_file)
        # Patch class method to use known file path
        import hashlib as _h
        cls.code_hash = classmethod(lambda c: "0x" + _h.sha3_256(py_file.read_bytes()).hexdigest())
    except Exception:
        pass
    if cmd == "manifest":
        return {
            "name": name, "lang": "python",
            "code_hash": cls.code_hash(),
            "abi": cls.abi() if hasattr(cls, "abi") else [],
            "icon": getattr(cls, "ICON", ""), "color": getattr(cls, "COLOR", ""),
            "binary": getattr(cls, "BINARY", ""), "default_model": getattr(cls, "DEFAULT_MODEL", ""),
            "env_key": getattr(cls, "ENV_KEY", ""), "description": getattr(cls, "DESCRIPTION", ""),
        }
    if cmd == "abi":
        return cls.abi() if hasattr(cls, "abi") else []
    if cmd == "call":
        method, json_args = (args + ("", "{}"))[:2]
        try:
            kwargs = json.loads(json_args) if json_args else {}
        except json.JSONDecodeError:
            kwargs = {}
        inst = cls()
        fn = getattr(inst, method, None)
        if fn is None:
            return {"error": f"no method {method}"}
        return fn(**kwargs) if callable(fn) else {"error": f"{method} not callable"}
    return {"error": f"unknown cmd: {cmd}"}


@api.post("/agent/from-repo")
async def add_agent_from_repo(request: Request,
                               x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    """Clone a GitHub repo and scaffold it as a new agent module.

    Body: { url: "https://github.com/owner/repo", name?: "...", lang?: "python"|"javascript"|"rust" }

    Detects the language from the repo layout (Cargo.toml → Rust,
    package.json → JS, *.py → Python), then writes:
      - orbit/<name>/repo/           — the cloned source
      - orbit/<name>/config.json     — module config with port/owner/icon
      - orbit/<name>/contract.<ext>  — AgentContract stub wrapping the repo

    Requires X-Mod-Signature to bind the new agent's owner to the caller.
    """
    import subprocess, tempfile, shutil, re
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    if not re.match(r"^https?://(www\.)?(github\.com|gitlab\.com|bitbucket\.org)/[^/]+/[^/]+/?$", url.rstrip("/").split("?")[0] + "/"):
        raise HTTPException(400, "url must be a public github/gitlab/bitbucket repo")

    repo_name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1])
    name = (body.get("name") or repo_name).lower()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    if not name or name in ("dev", "core"):
        raise HTTPException(400, f"invalid module name: {name!r}")

    # Resolve owner from the caller's signature (if provided), else fall back
    # to the dev module's configured owner.
    owner = None
    if x_mod_signature:
        try:
            msg = encode_defunct(text=SIGNATURE_CHALLENGE)
            owner = Account.recover_message(msg, signature=x_mod_signature).lower()
        except Exception:
            pass
    owner = owner or (CONFIG.get("owner") or "").lower()

    target = ORBIT_DIR / name
    if target.exists():
        raise HTTPException(409, f"module {name!r} already exists at {target}")

    # Clone shallowly into a temp dir, then move into orbit/.
    with tempfile.TemporaryDirectory() as tmp:
        clone_dst = Path(tmp) / "src"
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(clone_dst)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise HTTPException(500, f"clone failed: {(r.stderr or r.stdout)[-300:]}")

        # Language detection — prefer hint, else inspect files.
        lang = (body.get("lang") or "").lower() or None
        if lang is None:
            if (clone_dst / "Cargo.toml").exists():
                lang = "rust"
            elif (clone_dst / "package.json").exists():
                lang = "javascript"
            elif any((clone_dst / x).exists() for x in ("pyproject.toml", "setup.py", "requirements.txt")) \
                 or list(clone_dst.glob("*.py")):
                lang = "python"
            else:
                lang = "python"  # default

        # Try to guess a CLI binary name from package.json/Cargo.toml/setup.py.
        binary = name
        try:
            if (clone_dst / "package.json").exists():
                pkg = json.loads((clone_dst / "package.json").read_text())
                if isinstance(pkg.get("bin"), dict):
                    binary = next(iter(pkg["bin"]), name)
                elif isinstance(pkg.get("bin"), str):
                    binary = Path(pkg["bin"]).stem
                else:
                    binary = pkg.get("name", name).split("/")[-1]
            elif (clone_dst / "Cargo.toml").exists():
                cargo = (clone_dst / "Cargo.toml").read_text()
                m_match = re.search(r'name\s*=\s*"([^"]+)"', cargo)
                if m_match: binary = m_match.group(1)
        except Exception:
            pass

        target.mkdir(parents=True)
        shutil.move(str(clone_dst), str(target / "repo"))

    # Choose a free port (8870 + offset, avoiding collisions).
    used_ports = set()
    for d in ORBIT_DIR.iterdir():
        cp = d / "config.json"
        if cp.exists():
            try:
                used_ports.add(json.loads(cp.read_text()).get("port"))
            except Exception:
                pass
    port = next(p for p in range(8900, 9000) if p not in used_ports)

    # Write the config.json.
    new_cfg = {
        "name": name,
        "version": "0.1.0",
        "description": f"{name} (cloned from {url})",
        "lang": lang,
        "port": port,
        "app_port": port + 1,
        "owner": owner,
        "icon": name[0].upper(),
        "color": "#" + hex(abs(hash(name)) % 0xFFFFFF)[2:].rjust(6, "0"),
        "binary": binary,
        "default_model": "",
        "env_key": f"{name.upper().replace('-','_')}_API_KEY",
        "source_repo": url,
    }
    (target / "config.json").write_text(json.dumps(new_cfg, indent=4))

    # Write the contract stub in the chosen language.
    stub = _agent_stub_for(lang, name, binary)
    ext = {"python": "contract.py", "javascript": "contract.js", "rust": "src/main.rs"}[lang]
    stub_path = target / ext
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    stub_path.write_text(stub)

    if lang == "rust":
        # Also drop a minimal Cargo.toml so the contract binary can be built.
        (target / "Cargo.toml").write_text(_rust_cargo_toml(name))

    code_cid = _push_code_cid(name)
    return {
        "ok": True,
        "name": name,
        "lang": lang,
        "path": str(target),
        "config": new_cfg,
        "contract": str(stub_path),
        "code_cid": code_cid,
        "owner": owner,
        "build_hint": "cargo build --release" if lang == "rust" else None,
    }


def _agent_stub_for(lang: str, name: str, binary: str) -> str:
    if lang == "python":
        return f'''"""{name} — cloned agent module.

Wraps the cloned repo at ./repo/ as an AgentContract. Edit build_args()
to match the CLI's actual flags.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dev", "src"))
from agent_base import AgentContract, tx, view


class Mod(AgentContract):
    NAME = "{name}"
    BINARY = "{binary}"
    DEFAULT_MODEL = ""
    DESCRIPTION = "{name} agent (auto-scaffolded from repo)"

    def build_args(self, prompt, model, work_dir):
        return [prompt]
'''
    if lang == "javascript":
        return f'''// {name} — cloned agent module.
// Edit buildArgs() to match the CLI's actual flags.

const {{ AgentContract, runCli }} = require("../dev/src/agent_base.js");

class Mod extends AgentContract {{
  static NAME = "{name}";
  static BINARY = "{binary}";
  static DEFAULT_MODEL = "";
  static DESCRIPTION = "{name} agent (auto-scaffolded)";
  static ABI = {{
    info:   {{ kind: "view", ownerOnly: false, doc: "Return info" }},
    submit: {{ kind: "tx",   ownerOnly: false, doc: "Queue a prompt" }},
  }};

  buildArgs(prompt, _model, _workDir) {{
    return [prompt];
  }}
}}

if (require.main === module) runCli(Mod);
module.exports = Mod;
'''
    # rust
    return f'''//! {name} — cloned agent module.
use agent_base::{{run_cli, AbiEntry, Contract}};
use serde_json::{{json, Value}};

struct Mod;
impl Contract for Mod {{
    const NAME: &'static str = "{name}";
    const BINARY: &'static str = "{binary}";
    const DESCRIPTION: &'static str = "{name} agent (auto-scaffolded)";

    fn abi() -> Vec<AbiEntry> {{
        vec![
            AbiEntry {{ name: "info".into(),   kind: "view".into(), owner_only: false, inputs: vec![], doc: "Return info".into() }},
            AbiEntry {{ name: "submit".into(), kind: "tx".into(),   owner_only: false, inputs: vec![], doc: "Queue a prompt".into() }},
        ]
    }}
    fn call(method: &str, args: Value) -> Value {{
        match method {{
            "info" => json!({{ "name": Self::NAME, "binary": Self::BINARY, "lang": "rust" }}),
            "submit" => json!({{ "queued": true, "prompt": args.get("prompt") }}),
            _ => json!({{ "error": format!("unknown method: {{}}", method) }}),
        }}
    }}
}}

fn main() {{ run_cli::<Mod>(); }}
'''


def _rust_cargo_toml(name: str) -> str:
    return f'''[package]
name = "{name}"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "{name}-contract"
path = "src/main.rs"

[dependencies]
agent_base = {{ path = "../dev/src/agent_base_rs" }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
'''


@api.get("/proxy/wallet")
def get_proxy_wallet():
    """Return the funded relayer wallet info — address, balance, network.

    This is the wallet that pays gas for per-module-key registrations.
    Users top it up with the FUND button in the dev UI.
    """
    try:
        import sys as _sys
        if "/Users/broski/mod" not in _sys.path:
            _sys.path.insert(0, "/Users/broski/mod")
        import mod as _m
        chain = _m.mod("chain")()
        addr = chain.account.address
        bal_wei = int(chain.w3.eth.get_balance(addr))
        chain_id = chain.w3.eth.chain_id
        return {
            "address": addr,
            "balance_wei": bal_wei,
            "balance_eth": bal_wei / 1e18,
            "chain_id": chain_id,
            "network": "base-sepolia" if chain_id == 84532 else f"chain {chain_id}",
            "explorer": f"https://sepolia.basescan.org/address/{addr}" if chain_id == 84532 else None,
        }
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@api.get("/agent/{name}/manifest")
def get_agent_manifest(name: str):
    """Universal manifest for an agent in any supported language.

    Includes `code_hash` (sha3 of source) and `code_cid` (content-addressed
    pointer in localfs) so the contract source can be retrieved by anyone.
    """
    lang, dispatch = _detect_runtime(name)
    if dispatch is None:
        raise HTTPException(404, f"no contract found for {name!r}")
    result = dispatch("manifest")
    if isinstance(result, dict):
        if "lang" not in result:
            result["lang"] = lang
        result["code_cid"] = _push_code_cid(name)
        src = _agent_source_path(name)
        result["source_path"] = str(src) if src else None
    return result


@api.get("/agent/{name}/abi")
def get_agent_abi(name: str):
    """Return the agent's ABI — works across Python / JavaScript / Rust."""
    lang, dispatch = _detect_runtime(name)
    if dispatch is None:
        raise HTTPException(404, f"no contract found for {name!r}")
    result = dispatch("abi")
    return {"name": name, "lang": lang, "abi": result if isinstance(result, list) else result.get("abi", [])}


@api.post("/agent/{name}/call/{method}")
async def call_agent_method(name: str, method: str, request: Request):
    """Invoke a method on the agent contract. Body = JSON object of args.
    Works uniformly across Python / JS / Rust contracts.
    """
    lang, dispatch = _detect_runtime(name)
    if dispatch is None:
        raise HTTPException(404, f"no contract found for {name!r}")
    try:
        args = await request.json()
    except Exception:
        args = {}
    result = dispatch("call", method, json.dumps(args))
    return {"name": name, "lang": lang, "method": method, "result": result}


@api.get("/agent/{name}/state")
def get_agent_state(name: str):
    """Return persistent storage + recent events for the agent."""
    state_path = Path(os.path.expanduser(f"~/.mod/{name}/state.json"))
    events_path = Path(os.path.expanduser(f"~/.mod/{name}/events.jsonl"))
    state = {}
    events = []
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            pass
    if events_path.exists():
        with events_path.open() as f:
            tail = f.readlines()[-30:]
        events = []
        for l in tail:
            try:
                events.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    return {"name": name, "state": state, "events": events}


def _agent_source_path(name: str) -> Optional[Path]:
    """Return the canonical source path for an agent's contract code.
    For Rust contracts we return src/main.rs (the source, not the binary)
    so the CID is deterministic across builds.
    """
    base = ORBIT_DIR / name
    for cand in (
        base / "contract.py",
        base / "contract.js",
        base / "src" / "contract.js",
        base / "src" / "main.rs",
        base / "src" / "mod.py",
    ):
        if cand.exists():
            return cand
    return None


def _push_code_cid(name: str) -> Optional[str]:
    """Read the agent's contract source and push it to localfs, returning
    the CID. Idempotent — localfs is content-addressable, so calling this
    repeatedly on unchanged source returns the same CID.
    """
    src = _agent_source_path(name)
    if src is None:
        return None
    try:
        import sys as _sys
        if "/Users/broski/mod" not in _sys.path:
            _sys.path.insert(0, "/Users/broski/mod")
        import mod as _m
        text = src.read_text(encoding="utf-8", errors="replace")
        cid = _m.fn("api/put")(text)
        return cid
    except Exception:
        return None


@api.get("/jobs/{module}")
def get_jobs(module: str, x_mod_signature: Optional[str] = Header(default=None, alias="X-Mod-Signature")):
    """List recent jobs for a module — proxies through with the caller's
    wallet signature. The module's own auth layer enforces authorization.
    For modules without a /jobs endpoint, returns an empty list.
    """
    dev = _dev_mod()
    served = (dev._read_pidfile().get("modules") or {})
    info = served.get(module)
    if not info:
        return {"jobs": [], "reason": "module not served"}
    try:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            headers = {}
            if x_mod_signature:
                headers["X-Mod-Signature"] = x_mod_signature
                headers["Authorization"] = f"Bearer {x_mod_signature}"
            r = client.get(f"{info['url']}/jobs", headers=headers)
            if r.status_code == 200:
                return {"jobs": r.json()}
            return {"jobs": [], "reason": f"upstream HTTP {r.status_code}"}
    except Exception as e:
        return {"jobs": [], "reason": f"{type(e).__name__}: {e}"}


@api.api_route("/proxy/{provider}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(provider: str, path: str, request: Request):
    if provider not in PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    target = PROVIDERS[provider]["api"]
    url = f"{target.rstrip('/')}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}
    body = await request.body()
    async with httpx.AsyncClient(timeout=300) as client:
        upstream = await client.request(request.method, url, headers=headers, content=body, params=dict(request.query_params))
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in {"transfer-encoding", "content-encoding"}}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers)


# Mount the same router at both root (for prefix-stripping Caddy) and /api/dev
# (for direct local hits without a proxy in front).
app.include_router(api)
app.include_router(api, prefix="/api/dev")


def _serve_ui():
    if UI_PATH.exists():
        return FileResponse(UI_PATH)
    raise HTTPException(404, "UI not built")


@app.get("/")
def root():
    return _serve_ui()


@app.get("/dev")
@app.get("/dev/")
def dev_ui():
    return _serve_ui()
