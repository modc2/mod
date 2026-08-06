"""liquidai — one interface to every Liquid AI (LFM) model.

Liquid's models are deliberately small: LFM2.5-350M is a 700 MB download, and
the whole family tops out where most people's laptops still cope. That makes
"where does this run" a real question rather than a rhetorical one, and it's
the question this module is built around. Three answers, one catalog, one chat
call:

    BROWSER   the visitor's own tab, transformers.js on WebGPU. The ONNX
              weights come from HuggingFace to the browser; no prompt and no
              token ever reaches this box.
    SERVER    this box, transformers + torch, streamed back over SSE.
    CLOUD     inference.liquid.ai, billed to the caller's own key (kept in
              ~/.mod/liquidai/keys.json, 0600, never in config.json).

The catalog is derived from HuggingFace at runtime, not pinned in a constant —
Liquid ships models faster than a hardcoded list survives. Repos are folded by
format, so LFM2.5-350M, -GGUF, -ONNX and the five -MLX quants are one row that
knows it can run in all three places.

Layout:
    src/
      mod.py       # this file (lifecycle + mod-protocol surface)
      api/         # FastAPI: catalog, key vault, server + cloud runtimes
      app/         # Next.js console (8-bit cabinet, same one copytensor wears)

CLI:
    m liquidai                                # info
    m liquidai/serve                          # api :50460 + console :50461
    m liquidai/models runtime=browser         # what a tab can run
    m liquidai/model LFM2.5-350M              # one model, every format
    m liquidai/runtimes                       # what this box can do right now
    m liquidai/pull LiquidAI/LFM2.5-350M      # weights onto this disk
    m liquidai/chat "why are LFMs small?"     # server-side, streamed
    m liquidai/set_key sk-...                 # cloud BYOK
"""

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import requests
import mod as m

log = logging.getLogger("liquidai.mod")

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
APP_DIR = os.path.join(SRC_DIR, "app")

_AUTH = None


def auth_module():
    """Our api/auth.py, loaded by file path rather than by name.

    `from api import auth` looked fine until another module imported us: any
    caller that already has a package called `api` on sys.modules — the agent
    module does — shadows ours, the import fails, and the caller silently ends
    up sending no token at all (a 403 on /chat with no clue why). Loading the
    file itself can't be shadowed, and leaves the caller's sys.path alone.
    """
    global _AUTH
    if _AUTH is None:
        import importlib.util
        path = os.path.join(SRC_DIR, "api", "auth.py")
        spec = importlib.util.spec_from_file_location("liquidai_auth", path)
        mod_ = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod_)
        _AUTH = mod_
    return _AUTH


class Liquidai(m.Mod):
    """Catalog, run and compare every Liquid AI model — browser, server, cloud."""

    name = "liquidai"
    description = (
        "Interface to every Liquid AI (LFM) model — run one in the visitor's "
        "browser (transformers.js/WebGPU), on this box (transformers), or in "
        "Liquid's cloud on your own key"
    )
    fns = [
        # lifecycle
        "serve", "kill", "status", "logs", "test", "gateway",
        # catalog
        "models", "model", "families", "runtimes",
        # weights on this box
        "local", "pull", "pulls", "load", "unload",
        # inference, one fn per modality
        "chat", "embed",
        # arena
        "games", "play", "board",
        # BYOK
        "keys", "set_key",
        # accounts
        "auth", "disown",
        # default
        "forward",
    ]

    api_port = 50460
    app_port = 50461

    LOG_DIR = os.path.expanduser("~/.mod/liquidai/logs")

    def __init__(self, **kwargs):
        self.api_url = os.environ.get(
            "LIQUIDAI_API_URL", f"http://localhost:{self.api_port}",
        )

    # ── process supervision (pm2 when present, Popen otherwise) ───

    def _pm2_bin(self) -> Optional[str]:
        for path in ("/usr/local/bin/pm2", "/usr/bin/pm2"):
            if os.path.exists(path):
                return path
        try:
            out = subprocess.run(["which", "pm2"], capture_output=True,
                                 text=True, timeout=5)
            return out.stdout.strip() or None
        except Exception:
            return None

    def _pm2_name(self, role: str) -> str:
        return f"liquidai-{role}"

    def _pm2_running(self, role: str) -> bool:
        pm2 = self._pm2_bin()
        if not pm2:
            return False
        try:
            out = subprocess.run([pm2, "jlist"], capture_output=True,
                                 text=True, timeout=15)
            for proc in json.loads(out.stdout or "[]"):
                if proc.get("name") == self._pm2_name(role):
                    return proc.get("pm2_env", {}).get("status") == "online"
        except Exception:
            pass
        return False

    def _pm2_spawn(self, role: str, cmd: List[str], cwd: str,
                   env: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Start `cmd` under pm2, or return None so the caller falls back.

        `--interpreter none` is not optional: pm2 hands anything it doesn't
        recognise to node, which turns a python entrypoint into a crash loop
        that `pm2 start` still reports as a success.
        """
        pm2 = self._pm2_bin()
        if not pm2:
            return None
        name = self._pm2_name(role)
        subprocess.run([pm2, "delete", name], capture_output=True, timeout=30)
        proc = subprocess.run(
            [pm2, "start", cmd[0], "--name", name, "--cwd", cwd,
             "--interpreter", "none", "--"] + cmd[1:],
            cwd=cwd, env={**os.environ, **env},
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            log.warning("pm2 start %s failed: %s", name, proc.stderr.strip())
            return None
        time.sleep(2)
        if not self._pm2_running(role):
            subprocess.run([pm2, "delete", name], capture_output=True, timeout=30)
            return None
        subprocess.run([pm2, "save"], capture_output=True, timeout=30)
        return {"ok": True, "supervisor": "pm2", "pm2_name": name}

    def _pid_file(self, role: str) -> str:
        os.makedirs(self.LOG_DIR, exist_ok=True)
        return os.path.join(self.LOG_DIR, f"{role}.pid")

    def _log_file(self, role: str) -> str:
        os.makedirs(self.LOG_DIR, exist_ok=True)
        return os.path.join(self.LOG_DIR, f"{role}.log")

    def _read_pid(self, role: str) -> Optional[int]:
        try:
            with open(self._pid_file(role)) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return pid
        except Exception:
            return None

    def _spawn(self, role: str, cmd: List[str], cwd: str,
               env: Dict[str, str]) -> Dict[str, Any]:
        log_f = open(self._log_file(role), "ab")
        proc = subprocess.Popen(
            cmd, cwd=cwd, env={**os.environ, **env},
            stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True,
        )
        with open(self._pid_file(role), "w") as f:
            f.write(str(proc.pid))
        return {"ok": True, "supervisor": "popen", "pid": proc.pid,
                "log": self._log_file(role)}

    def _kill_role(self, role: str, port: Optional[int] = None):
        pm2 = self._pm2_bin()
        if pm2:
            subprocess.run([pm2, "delete", self._pm2_name(role)],
                           capture_output=True, timeout=30)
        pid = self._read_pid(role)
        if pid:
            try:
                os.kill(pid, 15)
                time.sleep(0.3)
                os.kill(pid, 9)
            except Exception:
                pass
        try:
            os.remove(self._pid_file(role))
        except Exception:
            pass
        if port is not None:
            subprocess.run(["bash", "-c", f"lsof -ti:{port} | xargs -r kill -9"],
                           capture_output=True, timeout=10)

    # ── serve / kill / status / logs ──────────────────────────────

    def _serve_api(self, port: int) -> Dict[str, Any]:
        self._kill_role("api", port=port)
        # api/app.py imports its siblings relatively, so it has to load as
        # `src.api.app` — uvicorn runs from ROOT_DIR with that on PYTHONPATH.
        cmd = [sys.executable or "python3", "-m", "uvicorn", "src.api.app:app",
               "--host", "0.0.0.0", "--port", str(port)]
        env = {"PORT": str(port), "PYTHONPATH": ROOT_DIR}
        out = (self._pm2_spawn("api", cmd, cwd=ROOT_DIR, env=env)
               or self._spawn("api", cmd, cwd=ROOT_DIR, env=env))
        out["port"] = port
        return out

    def _serve_app(self, port: int, api_port: int) -> Dict[str, Any]:
        if not os.path.isdir(os.path.join(APP_DIR, "node_modules")):
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                           cwd=APP_DIR, capture_output=True, timeout=900)
        self._kill_role("app", port=port)
        env = {"PORT": str(port),
               "NEXT_PUBLIC_API_URL": f"http://localhost:{api_port}"}
        # Always a production build: `next dev` recompiles per request and
        # falls over behind the gateway.
        if not os.path.exists(os.path.join(APP_DIR, ".next", "BUILD_ID")):
            build = subprocess.run(["npm", "run", "build"], cwd=APP_DIR,
                                   env={**os.environ, **env},
                                   capture_output=True, text=True, timeout=1800)
            if build.returncode != 0:
                return {"ok": False, "stage": "build",
                        "error": (build.stderr or build.stdout)[-2000:]}
        cmd = ["npm", "run", "start", "--", "-p", str(port)]
        out = (self._pm2_spawn("app", cmd, cwd=APP_DIR, env=env)
               or self._spawn("app", cmd, cwd=APP_DIR, env=env))
        out["port"] = port
        return out

    def serve(self, api_port: Optional[int] = None,
              app_port: Optional[int] = None, target: str = "all") -> Dict[str, Any]:
        """Start the API and the console."""
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port
        out: Dict[str, Any] = {"ok": True}
        if target in ("api", "all"):
            out["api"] = self._serve_api(api_port)
            out["ok"] = out["ok"] and out["api"].get("ok", False)
            time.sleep(1)
        if target in ("app", "all"):
            out["app"] = self._serve_app(app_port, api_port)
            out["ok"] = out["ok"] and out["app"].get("ok", False)
        self._register_gateway(api_port, app_port)
        out["urls"] = self.gateway()
        return out

    def kill(self, target: str = "all") -> Dict[str, Any]:
        out = {}
        if target in ("api", "all"):
            self._kill_role("api", port=self.api_port)
            out["api"] = "stopped"
        if target in ("app", "all"):
            self._kill_role("app", port=self.app_port)
            out["app"] = "stopped"
        return out

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"module": self.name, "api_url": self.api_url}
        for role in ("api", "app"):
            out[role] = ("running" if (self._pm2_running(role)
                                       or self._read_pid(role)) else "stopped")
        try:
            out["health"] = requests.get(f"{self.api_url}/health", timeout=20).json()
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 50) -> str:
        if self._pm2_running(target):
            proc = subprocess.run(
                [self._pm2_bin(), "logs", self._pm2_name(target),
                 "--lines", str(lines), "--nostream"],
                capture_output=True, text=True, timeout=30)
            if proc.stdout.strip():
                return proc.stdout
        path = self._log_file(target)
        if not os.path.exists(path):
            return f"<no logs: {path}>"
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    # ── gateway ───────────────────────────────────────────────────

    def _detect_gateway_port(self) -> int:
        for port in (3001, 3000, 3002):
            try:
                if requests.get(f"http://localhost:{port}/", timeout=1).status_code < 500:
                    return port
            except Exception:
                continue
        return 3001

    def _register_gateway(self, api_port: int, app_port: int):
        gateway_port = self._detect_gateway_port()
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg["port"] = api_port
            cfg["app_port"] = app_port
            cfg["gateway_port"] = gateway_port
            cfg["urls"] = {
                "api": f"http://localhost:{api_port}",
                "app": f"http://localhost:{app_port}/liquidai",
                "gateway_api": f"http://localhost:{gateway_port}/api/liquidai",
                "gateway_app": f"http://localhost:{gateway_port}/liquidai",
            }
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
                f.write("\n")
        except Exception as e:
            log.warning("config update failed: %s", e)

        # Registered inline, not on a daemon thread: `m liquidai/serve` exits
        # the moment serve() returns, and a daemon thread dies with it — which
        # is how the gateway ended up 404ing a module whose services were up.
        try:
            m.mod("server.namespace")().reg_app(
                name="liquidai",
                address=f"http://localhost:{app_port}",
                api_url=f"http://localhost:{api_port}",
            )
        except Exception as e:
            log.warning("namespace reg_app failed: %s", e)

    def gateway(self) -> Dict[str, Any]:
        port = self._detect_gateway_port()
        return {
            "gateway_port": port,
            "app": f"http://localhost:{port}/liquidai",
            "api": f"http://localhost:{port}/api/liquidai",
            "direct_api": self.api_url,
            "direct_app": f"http://localhost:{self.app_port}/liquidai",
        }

    # ── passthroughs ──────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        """A shell on this box is the operator, so mint itself an owner token.

        The API gates weights and the key vault behind a signed-in owner; the
        CLI proves it belongs by reading ~/.mod/liquidai/server.secret, which
        only the operator can. If the secret isn't readable we just don't send
        a token and the gate answers for itself.
        """
        try:
            return {"Authorization": f"Bearer {auth_module().mint_local()}"}
        except Exception as e:
            log.warning("no local token (%s) — calling %s unauthenticated", e, self.api_url)
            return {}

    @staticmethod
    def _check(r: requests.Response) -> requests.Response:
        """raise_for_status, but keep what the API said.

        Every 4xx here carries a `detail` written for whoever caused it — the
        model this runtime can't load, the key that isn't on file. Bare
        raise_for_status throws that away and leaves the caller holding
        "400 Client Error: Bad Request", which explains nothing and reads
        like the module is broken.
        """
        if r.ok:
            return r
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = (r.text or "").strip()[:400]
        raise requests.HTTPError(
            f"{r.status_code} from {r.request.method} {r.url}"
            + (f": {detail}" if detail else ""), response=r)

    def _get(self, path: str, **params) -> Any:
        r = requests.get(f"{self.api_url}{path}", params=params,
                         headers=self._headers(), timeout=60)
        return self._check(r).json()

    def _post(self, path: str, body: Optional[Dict] = None) -> Any:
        r = requests.post(f"{self.api_url}{path}", json=body or {},
                          headers=self._headers(), timeout=120)
        return self._check(r).json()

    def models(self, runtime: Optional[str] = None, kind: Optional[str] = None,
               family: Optional[str] = None, q: Optional[str] = None,
               limit: int = 100, refresh: bool = False) -> Any:
        """The LFM catalog. runtime=browser|server|edge, kind=text|vision|audio|embed."""
        params = {k: v for k, v in
                  {"runtime": runtime, "kind": kind, "family": family, "q": q,
                   "limit": limit, "refresh": refresh}.items() if v not in (None, False)}
        data = self._get("/models", **params)
        return {
            "count": data["count"],
            "total": data["total"],
            "source": data["source"],
            "models": [
                {"id": mm["id"], "kind": mm["kind"], "params_b": mm["params_b"],
                 "runtimes": mm["runtimes"], "role": mm["role"],
                 "downloads": mm["downloads"]}
                for mm in data["models"]
            ],
        }

    def model(self, model_id: str, refresh: bool = False) -> Any:
        """One model: every format, every quant, and where it can run."""
        return self._get(f"/models/{model_id}", refresh=refresh)

    def families(self) -> Any:
        data = self._get("/models", limit=1)
        return {"families": data["families"], "kinds": data["kinds"],
                "roles": data["roles"], "total": data["total"]}

    def runtimes(self) -> Any:
        """What browser / this box / the cloud can each run right now."""
        return self._get("/runtimes")

    def local(self) -> Any:
        """LFM weights already on this disk."""
        return self._get("/local/models")

    def pull(self, repo: str) -> Any:
        """Download weights (background). `repo` is a full LiquidAI/… id."""
        return self._post("/local/pull", {"repo": repo})

    def pulls(self, repo: Optional[str] = None) -> Any:
        return self._get("/local/pulls", **({"repo": repo} if repo else {}))

    def load(self, repo: str) -> Any:
        """Make `repo` the resident server-side model (evicts the last one)."""
        return self._post("/local/load", {"repo": repo})

    def unload(self) -> Any:
        return self._post("/local/unload")

    def chat(self, prompt: str, model: str = "LiquidAI/LFM2.5-350M",
             runtime: str = "server", system: Optional[str] = None,
             max_tokens: int = 256, temperature: float = 0.3) -> Any:
        """Ask a model. Streams server-side; returns the finished text + stats."""
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        body = {"messages": messages, "model": model, "runtime": runtime,
                "max_tokens": max_tokens, "temperature": temperature}
        r = requests.post(f"{self.api_url}/chat", json=body, stream=True,
                          headers=self._headers(), timeout=900)
        self._check(r)
        text, stats = "", {}
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if event.get("type") == "token":
                text += event["text"]
            elif event.get("type") == "done":
                stats = event
            elif event.get("type") == "error":
                return {"ok": False, "error": event.get("error"), "text": text}
        return {"ok": True, "text": text, **stats}

    def embed(self, texts: str, model: str = "LiquidAI/LFM2.5-Encoder-230M") -> Any:
        """Embed `texts` (one per '|') and score every pair against every other."""
        lines = [t.strip() for t in texts.split("|") if t.strip()]
        out = self._post("/embed", {"model": model, "texts": lines})
        return {"model": model, "dim": out["dim"], "lines": lines,
                "similarity": out["similarity"], "elapsed_sec": out["elapsed_sec"]}

    # ── arena ─────────────────────────────────────────────────────

    def games(self) -> Any:
        """Games models compete at — the four built in, plus anything written here."""
        return {"games": [
            {"id": g["id"], "name": g["name"], "rounds": len(g["rounds"]),
             "builtin": g["builtin"], "blurb": g["blurb"]}
            for g in self._get("/arena/games")["games"]]}

    def play(self, game: str, models: str = "LiquidAI/LFM2.5-350M",
             runtime: str = "server") -> Any:
        """Run models (comma-separated repos) through a game and score it."""
        entrants = [m.strip() for m in models.split(",") if m.strip()]
        out = self._post("/arena/match", {"game": game, "models": entrants,
                                          "runtime": runtime})
        return {"game": game, "results": [
            {"model": r["label"], "score": f"{r['passed']}/{r['total']}",
             "sec_per_round": r["sec_per_round"]} for r in out["results"]]}

    def board(self, game: Optional[str] = None) -> Any:
        """The leaderboard — best run per model per game."""
        return self._get("/arena/leaderboard", **({"game": game} if game else {}))

    def keys(self) -> Any:
        """Which BYOK keys this box holds (masked, always)."""
        return self._get("/keys")

    def set_key(self, key: str, provider: str = "cloud") -> Any:
        """Store a key in ~/.mod/liquidai/keys.json (0600). Pass '' to clear."""
        return self._post("/keys", {"provider": provider, "key": key})

    # ── accounts ──────────────────────────────────────────────────

    def auth(self) -> Any:
        """Who owns this box and who has signed into it."""
        state = self._get("/auth/owner")
        book = auth_module().accounts()
        return {
            **state,
            "accounts": [
                {"address": a["address"], "kind": a["kind"],
                 "logins": a.get("logins", 0),
                 "owner": a["address"] == state.get("address")}
                for a in book.values()
            ],
            "sign_in": ["browser", "evm (MetaMask)", "bittensor (Talisman/SubWallet/PJS)"],
        }

    def disown(self) -> Any:
        """Release the claim so the next account to sign in takes the box."""
        auth_mod = auth_module()
        if os.environ.get("LIQUIDAI_OWNER"):
            return {"ok": False, "error": "owner is pinned by LIQUIDAI_OWNER — unset it first"}
        was = auth_mod.owner()
        try:
            os.remove(auth_mod.OWNER_PATH)
        except FileNotFoundError:
            pass
        return {"ok": True, "was": was, "note": "next sign-in claims this box"}

    # ── test ──────────────────────────────────────────────────────

    def _check_gate(self) -> Dict[str, Any]:
        """A tokenless write must be refused — unless the box is in open mode."""
        r = requests.post(f"{self.api_url}/local/unload", timeout=30)
        open_mode = self._get("/auth/owner").get("open")
        want = 200 if open_mode else 403
        if r.status_code != want:
            raise AssertionError(f"tokenless unload → {r.status_code}, expected {want}")
        return {"status": r.status_code, "open_mode": open_mode}

    def test(self, generate: bool = False) -> Dict[str, Any]:
        """Hit every read endpoint. `generate=1` also runs a real completion."""
        checks = [
            ("health", lambda: self._get("/health")),
            ("models", lambda: self._get("/models", limit=5)),
            ("browser_models", lambda: self._get("/models", runtime="browser", limit=5)),
            ("model_detail", lambda: self._get("/models/LFM2.5-350M")),
            ("runtimes", lambda: self._get("/runtimes")),
            ("local", lambda: self._get("/local/models")),
            ("keys", lambda: self._get("/keys")),
            ("auth_owner", lambda: self._get("/auth/owner")),
            ("auth_me", lambda: self._get("/auth/me")),
            ("arena_games", lambda: self._get("/arena/games")),
            ("arena_board", lambda: self._get("/arena/leaderboard")),
            ("v1_models", lambda: self._get("/v1/models")),
            # The gate is part of the contract: an unauthenticated write has to
            # come back 403, and a test that never checks it would pass on a
            # box whose door is wide open.
            ("gate_closed", self._check_gate),
        ]
        if generate:
            checks.append(("chat", lambda: self.chat(
                "Reply with one word: ok", max_tokens=16)))
            checks.append(("play", lambda: self.play("arithmetic")))
        results: Dict[str, Any] = {}
        for name, fn in checks:
            try:
                results[name] = {"ok": True, "preview": str(fn())[:200]}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}
        passed = sum(1 for v in results.values() if v.get("ok"))
        results["summary"] = f"{passed}/{len(checks)} passed"
        results["ok"] = passed == len(checks)
        return results

    # ── forward ───────────────────────────────────────────────────

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        if fn is None:
            return {
                "module": self.name,
                "description": self.description,
                "fns": self.fns,
                "runtimes": ["browser", "server", "cloud"],
                "api": self.api_url,
                "app": f"http://localhost:{self.app_port}/liquidai",
            }
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


Mod = Liquidai
