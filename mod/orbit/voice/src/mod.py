"""voice — fully local audio→text, in the visitor's own browser tab.

The inference never touches this box. The page records (or accepts) audio,
turns it into text with a model running on WebGPU/WASM inside the tab, and
optionally translates the transcript there too. The server's whole job is to
hand out the static app, answer "what can a tab run" (proxied from the
liquidai catalog), and speak MCP.

Engines the app ships, all in-browser:

    WHISPER      transformers.js ASR pipeline (onnx-community whisper
                 tiny/base/small). 40–250 MB, WebGPU with WASM fallback, so
                 it works in every browser. Whisper's native `translate`
                 task gives audio→English directly.
    LFM AUDIO    LiquidAI/LFM2.5-Audio-1.5B-ONNX — the browser-runnable
                 audio model from the liquidai catalog — via onnxruntime-web
                 on WebGPU. Q4 ≈1.9 GB total (encoder + decoder + embedding
                 table). The mel frontend, ChatML prompt and decode loop
                 mirror Liquid4All/onnx-export's reference exactly.
    LFM 350M     LiquidAI/LFM2.5-350M-ONNX (transformers.js) as a
                 translation post-pass: transcript → any target language,
                 still inside the tab.

Why liquidai is a dependency and not a copy: the catalog there is derived
from HuggingFace at runtime and folds repos by weights. /models here is that
catalog filtered to runtime=browser — when Liquid ships a new browser-capable
model it appears here without a code change.

CLI:
    m voice/serve                 # api + app on :50980
    m voice/models kind=audio     # what a tab can transcribe with
    m voice/engines               # the shipped in-browser engines
    m voice/test                  # health + catalog + mcp round-trip

MCP:
    claude mcp add --transport http voice http://localhost:50980/mcp
    claude mcp add voice -- python3 -m src.api.mcp_server   # stdio
"""

import json
import os
import signal
import subprocess
import time
from typing import Any, Dict, Optional

import requests
import mod as m

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)


class Voice(m.Mod):

    def __init__(self):
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        with open(cfg_path) as f:
            self.config = json.load(f)
        self.name = self.config["name"]
        self.description = self.config["description"]
        self.fns = self.config["fns"]
        self.port = int(self.config.get("port", 50980))
        self.api_url = f"http://localhost:{self.port}"
        self.run_dir = os.path.expanduser("~/.mod/voice")
        os.makedirs(self.run_dir, exist_ok=True)

    # ── process supervision (pm2 when present, Popen otherwise) ──────

    def _pm2_bin(self) -> Optional[str]:
        for path in ("/usr/local/bin/pm2", "/usr/bin/pm2"):
            if os.path.exists(path):
                return path
        try:
            out = subprocess.run(["which", "pm2"], capture_output=True,
                                 text=True, timeout=10)
            return out.stdout.strip() or None
        except Exception:
            return None

    def _pm2_running(self) -> bool:
        pm2 = self._pm2_bin()
        if not pm2:
            return False
        try:
            out = subprocess.run([pm2, "jlist"], capture_output=True,
                                 text=True, timeout=30)
            for proc in json.loads(out.stdout or "[]"):
                if proc.get("name") == "voice-api":
                    return proc.get("pm2_env", {}).get("status") == "online"
        except Exception:
            pass
        return False

    def _pid_file(self) -> str:
        return os.path.join(self.run_dir, "api.pid")

    def _log_file(self) -> str:
        return os.path.join(self.run_dir, "api.log")

    def _read_pid(self) -> Optional[int]:
        try:
            with open(self._pid_file()) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return pid
        except Exception:
            return None

    def serve(self, port: Optional[int] = None) -> Dict[str, Any]:
        """Start the API + app (one uvicorn process)."""
        port = port or self.port
        if self._alive():
            return {"ok": True, "already": True, "url": self.api_url}
        cmd = ["python3", "-m", "uvicorn", "src.api.server:app",
               "--host", "0.0.0.0", "--port", str(port)]
        pm2 = self._pm2_bin()
        if pm2:
            subprocess.run([pm2, "delete", "voice-api"],
                           capture_output=True, timeout=30)
            proc = subprocess.run(
                [pm2, "start", cmd[0], "--name", "voice-api",
                 "--cwd", ROOT_DIR, "--interpreter", "none", "--"] + cmd[1:],
                capture_output=True, text=True, timeout=60)
            started = proc.returncode == 0
        else:
            with open(self._log_file(), "ab") as log:
                child = subprocess.Popen(
                    cmd, cwd=ROOT_DIR, stdout=log, stderr=log,
                    start_new_session=True)
            with open(self._pid_file(), "w") as f:
                f.write(str(child.pid))
            started = True
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                requests.get(f"http://localhost:{port}/health", timeout=2)
                return {"ok": True, "url": f"http://localhost:{port}",
                        "app": f"http://localhost:{port}/",
                        "mcp": f"http://localhost:{port}/mcp"}
            except Exception:
                time.sleep(1)
        return {"ok": False, "started": started,
                "hint": f"see {self._log_file()} or pm2 logs voice-api"}

    def kill(self) -> Dict[str, Any]:
        pm2 = self._pm2_bin()
        if pm2:
            subprocess.run([pm2, "delete", "voice-api"],
                           capture_output=True, timeout=30)
        pid = self._read_pid()
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            try:
                os.remove(self._pid_file())
            except OSError:
                pass
        return {"api": "stopped"}

    def _alive(self) -> bool:
        try:
            requests.get(f"{self.api_url}/health", timeout=2)
            return True
        except Exception:
            return False

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"module": self.name, "api_url": self.api_url,
                               "api": "stopped"}
        if self._pm2_running() or self._read_pid() or self._alive():
            out["api"] = "running"
        try:
            out["health"] = requests.get(f"{self.api_url}/health",
                                         timeout=10).json()
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def logs(self, lines: int = 50) -> str:
        pm2 = self._pm2_bin()
        if pm2 and self._pm2_running():
            proc = subprocess.run(
                [pm2, "logs", "voice-api", "--lines", str(lines),
                 "--nostream"],
                capture_output=True, text=True, timeout=30)
            if proc.stdout.strip():
                return proc.stdout
        path = self._log_file()
        if not os.path.exists(path):
            return f"<no logs: {path}>"
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    # ── surface ──────────────────────────────────────────────────────

    def models(self, kind: Optional[str] = None) -> Dict[str, Any]:
        """Browser-runnable models, from the liquidai catalog."""
        params = {"kind": kind} if kind else {}
        return requests.get(f"{self.api_url}/models", params=params,
                            timeout=30).json()

    def engines(self) -> Dict[str, Any]:
        """The in-browser engines the app ships."""
        return requests.get(f"{self.api_url}/engines", timeout=10).json()

    def transcribe(self, path: str, model: Optional[str] = None) -> Any:
        """Server-side convenience path (forwards to liquidai /transcribe).

        The app never uses this — it exists so an MCP client on this box can
        transcribe a file without a browser. Audio leaves the tab-only story
        here and that is the point of keeping it a separate, labelled fn.
        """
        with open(os.path.expanduser(path), "rb") as f:
            files = {"file": (os.path.basename(path), f)}
            data = {"model": model} if model else {}
            r = requests.post(f"{self.api_url}/transcribe", files=files,
                              data=data, timeout=600)
        return r.json()

    def mcp(self) -> Dict[str, Any]:
        """How to connect an MCP client."""
        return {
            "http": f"{self.api_url}/mcp",
            "stdio": "python3 -m src.api.mcp_server (cwd: orbit/voice)",
            "add": f"claude mcp add --transport http voice {self.api_url}/mcp",
        }

    def gateway(self) -> Dict[str, Any]:
        return dict(self.config.get("urls", {}))

    def test(self) -> Dict[str, Any]:
        """Health + catalog + MCP round-trip against the running API."""
        results: Dict[str, Any] = {}
        try:
            results["health"] = requests.get(f"{self.api_url}/health",
                                             timeout=10).json()
        except Exception as e:
            return {"ok": False, "health": {"error": str(e)},
                    "hint": "m voice/serve first"}
        try:
            models = requests.get(f"{self.api_url}/models",
                                  params={"kind": "audio"}, timeout=30).json()
            results["audio_models"] = [x.get("id") for x in
                                       models.get("models", [])]
        except Exception as e:
            results["audio_models"] = {"error": str(e)}
        try:
            init = requests.post(f"{self.api_url}/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18",
                           "capabilities": {},
                           "clientInfo": {"name": "voice-test",
                                          "version": "0"}},
            }, timeout=10).json()
            results["mcp"] = init.get("result", init)
        except Exception as e:
            results["mcp"] = {"error": str(e)}
        results["ok"] = "error" not in str(results.get("mcp", {}))
        return results

    # ── forward ──────────────────────────────────────────────────────

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        if fn is None:
            return {
                "module": self.name,
                "description": self.description,
                "fns": self.fns,
                "api": self.api_url,
                "app": f"{self.api_url}/",
                "mcp": f"{self.api_url}/mcp",
            }
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


Mod = Voice
