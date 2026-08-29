"""promptland — store & share prompts under wallet identities.

The API (api/api.py) speaks the build-console auth flow: challenge →
wallet signature → HMAC bearer session. This SDK gives local (CLI/agent)
callers direct access to the same store — filesystem + localfs CIDs —
without a wallet round-trip: local access is trusted as the host operator.
"""

import json
import os
import re
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m


class Mod:
    description = "Prompt library with wallet-signed sessions (browser or local key) and CID sharing via localfs"
    path = os.path.dirname(os.path.abspath(__file__))

    STATE = Path.home() / ".mod" / "promptland"
    PIDFILE = "/tmp/promptland/served.json"
    LOG_DIR = "/tmp/promptland"
    SHARE_TYPE = "promptland/prompt@1"

    def __init__(self):
        with open(os.path.join(self.path, "config.json")) as f:
            self._cfg = json.load(f)

    # ── info ──────────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            "name": self._cfg.get("name", "promptland"),
            "description": self.description,
            "path": self.path,
            "ports": {"api": self._cfg.get("port"), "app": self._cfg.get("app_port")},
            "urls": self._cfg.get("urls", {}),
            "schema": self._cfg.get("schema"),
        }

    def readme(self):
        p = os.path.join(self.path, "README.md")
        return m.get_text(p) if os.path.exists(p) else None

    def health(self):
        import urllib.request

        try:
            with urllib.request.urlopen(
                f"http://localhost:{self._cfg.get('port', 50580)}/health", timeout=3
            ) as r:
                return json.loads(r.read())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── local (operator) identity ─────────────────────────────────────
    # CLI callers act as the claimed owner when one exists, else "local".

    def _identity(self) -> str:
        owner = self.STATE / "owner.json"
        if owner.exists():
            try:
                return json.loads(owner.read_text()).get("owner") or "local"
            except Exception:
                pass
        return "local"

    def _dir(self, addr: Optional[str] = None) -> Path:
        d = self.STATE / "prompts" / (addr or self._identity())
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── prompt store (same files the API serves) ──────────────────────

    def prompts(self, addr: Optional[str] = None) -> List[dict]:
        out = []
        for f in sorted(self._dir(addr).glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except Exception:
                continue
        out.sort(key=lambda p: p.get("updated", 0), reverse=True)
        return out

    def get_prompt(self, id: str, addr: Optional[str] = None) -> dict:
        p = self._dir(addr) / f"{id}.json"
        if not p.exists():
            raise FileNotFoundError(f"prompt {id} not found")
        return json.loads(p.read_text())

    def save_prompt(self, name: str, body: str, description: str = "",
                    tags: Optional[List[str]] = None, id: Optional[str] = None) -> dict:
        now = int(time.time())
        prompt = self.get_prompt(id) if id else {"id": secrets.token_hex(4), "created": now}
        prompt.update({
            "name": name.strip(),
            "description": description.strip(),
            "tags": [t.strip() for t in (tags or []) if t.strip()][:12],
            "body": body,
            "updated": now,
        })
        (self._dir() / f"{prompt['id']}.json").write_text(json.dumps(prompt, indent=2))
        return prompt

    def delete_prompt(self, id: str) -> dict:
        p = self._dir() / f"{id}.json"
        if not p.exists():
            raise FileNotFoundError(f"prompt {id} not found")
        p.unlink()
        return {"deleted": id}

    # ── sharing via localfs CIDs ──────────────────────────────────────

    def _localfs(self):
        return m.mod("localfs")()

    def _shared_index(self) -> List[dict]:
        p = self.STATE / "shared.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def share_prompt(self, id: str) -> dict:
        prompt = self.get_prompt(id)
        addr = self._identity()
        payload = {
            "type": self.SHARE_TYPE,
            "name": prompt["name"],
            "description": prompt.get("description", ""),
            "tags": prompt.get("tags", []),
            "body": prompt["body"],
            "author": addr,
            "shared_at": int(time.time()),
        }
        cid = self._localfs().put(payload, pin=True)
        entries = [e for e in self._shared_index() if e.get("cid") != cid]
        entries.insert(0, {"cid": cid, "name": payload["name"],
                           "description": payload["description"], "tags": payload["tags"],
                           "author": addr, "ts": payload["shared_at"]})
        self.STATE.mkdir(parents=True, exist_ok=True)
        (self.STATE / "shared.json").write_text(json.dumps(entries, indent=2))
        prompt["cid"] = cid
        (self._dir() / f"{id}.json").write_text(json.dumps(prompt, indent=2))
        return {"cid": cid, "prompt": prompt}

    def shared(self) -> List[dict]:
        return self._shared_index()

    def get_shared(self, cid: str) -> dict:
        data = self._localfs().get(cid)
        if isinstance(data, (str, bytes)):
            data = json.loads(data)
        if not isinstance(data, dict) or data.get("type") != self.SHARE_TYPE:
            raise ValueError(f"{cid} does not hold a promptland prompt")
        return data

    def import_prompt(self, cid: str) -> dict:
        data = self.get_shared(cid)
        now = int(time.time())
        prompt = {
            "id": secrets.token_hex(4),
            "name": data["name"],
            "description": data.get("description", ""),
            "tags": data.get("tags", []),
            "body": data["body"],
            "created": now,
            "updated": now,
            "imported_from": cid,
            "original_author": data.get("author"),
        }
        (self._dir() / f"{prompt['id']}.json").write_text(json.dumps(prompt, indent=2))
        return prompt

    # ── serve / kill / status ─────────────────────────────────────────

    def serve(self, api_port: Optional[int] = None, app_port: Optional[int] = None) -> dict:
        api_port = int(api_port or self._cfg.get("port", 50580))
        app_port = int(app_port or self._cfg.get("app_port", 50581))
        log_dir = Path(self.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)

        for port in (api_port, app_port):
            subprocess.run(f"lsof -ti:{port} | xargs -r kill -9", shell=True, capture_output=True)

        env = os.environ.copy()
        env["PROMPTLAND_PORT"] = str(api_port)
        env["PROMPTLAND_APP_PORT"] = str(app_port)

        api_proc = subprocess.Popen(
            ["python3", "api.py"], cwd=os.path.join(self.path, "api"), env=env,
            stdout=open(log_dir / "api.log", "ab"), stderr=subprocess.STDOUT)
        app_proc = subprocess.Popen(
            ["python3", "server.py"], cwd=os.path.join(self.path, "app"), env=env,
            stdout=open(log_dir / "app.log", "ab"), stderr=subprocess.STDOUT)

        state = {
            "api": {"pid": api_proc.pid, "port": api_port, "url": f"http://localhost:{api_port}"},
            "app": {"pid": app_proc.pid, "port": app_port, "url": f"http://localhost:{app_port}"},
            "started_at": int(time.time()),
        }
        Path(self.PIDFILE).parent.mkdir(parents=True, exist_ok=True)
        Path(self.PIDFILE).write_text(json.dumps(state, indent=2))
        return state

    def _pid_alive(self, pid) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def kill(self) -> dict:
        p = Path(self.PIDFILE)
        state = json.loads(p.read_text()) if p.exists() else {}
        stopped = {}
        for name in ("api", "app"):
            pid = (state.get(name) or {}).get("pid")
            if pid and self._pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    stopped[name] = {"pid": pid, "stopped": True}
                except ProcessLookupError:
                    stopped[name] = {"pid": pid, "stopped": False}
            else:
                stopped[name] = {"pid": pid, "stopped": False, "reason": "not alive"}
        p.unlink(missing_ok=True)
        return {"stopped": stopped}

    def status(self) -> dict:
        p = Path(self.PIDFILE)
        state = json.loads(p.read_text()) if p.exists() else {}
        for name in ("api", "app"):
            rec = state.get(name) or {}
            rec["alive"] = self._pid_alive(rec.get("pid"))
            state[name] = rec
        return state

    def test(self) -> bool:
        pid = self.save_prompt("smoke", "hello from promptland", tags=["test"])
        shared = self.share_prompt(pid["id"])
        got = self.get_shared(shared["cid"])
        ok = got["body"] == "hello from promptland"
        self.delete_prompt(pid["id"])
        return ok
