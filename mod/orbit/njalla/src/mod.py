"""
njalla — multi-account interface over https://njal.la/api/1/

Layout:
    src/
      mod.py           # this file
      api/             # Rust axum API (JSON-RPC client + token store + payments)
      app/             # Next.js 14 dashboard

The Python class is a thin orchestrator: it builds the Rust API, runs
both processes under pm2, and forwards mod-protocol calls into the API.
"""

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import requests

import mod as m


SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")
APP_DIR = os.path.join(SRC_DIR, "app")


class Njalla(m.Mod):
    """njal.la interface — proxy multiple accounts, top up balances in crypto."""

    name = "njalla"
    description = (
        "Interface over njal.la: register/manage domains, DNS records, servers, "
        "VPNs across multiple proxied accounts, with crypto-only top-ups."
    )

    api_port = 8920
    app_port = 3920
    pm2_api = "mod-njalla-api"
    pm2_app = "mod-njalla-app"

    def __init__(self, api_url: Optional[str] = None, **_: Any):
        self.api_url = api_url or os.environ.get(
            "NJALLA_API_URL", f"http://localhost:{self.api_port}"
        )

    # ── lifecycle ────────────────────────────────────────────────────

    def build(self, release: bool = True) -> Dict[str, Any]:
        """Compile the Rust API."""
        if not shutil.which("cargo"):
            return {"ok": False, "error": "cargo not on PATH — install rust toolchain"}
        cmd = ["cargo", "build"]
        if release:
            cmd.append("--release")
        proc = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True)
        return {
            "ok": proc.returncode == 0,
            "release": release,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

    def _api_binary(self) -> str:
        rel = os.path.join(API_DIR, "target", "release", "njalla-api")
        dbg = os.path.join(API_DIR, "target", "debug", "njalla-api")
        if os.path.exists(rel):
            return rel
        if os.path.exists(dbg):
            return dbg
        return ""

    def api(self, port: Optional[int] = None, build: bool = True) -> Dict[str, Any]:
        """Start the Rust API under pm2."""
        port = port or self.api_port
        if build:
            r = self.build(release=True)
            if not r["ok"]:
                return r
        binary = self._api_binary()
        if not binary:
            return {"ok": False, "error": "njalla-api binary not found — build failed?"}

        subprocess.run(["pm2", "delete", self.pm2_api], capture_output=True)
        env = os.environ.copy()
        env["PORT"] = str(port)
        env.setdefault(
            "NJALLA_DATA_DIR",
            os.path.join(env.get("HOME", "."), ".njalla"),
        )
        os.makedirs(env["NJALLA_DATA_DIR"], exist_ok=True)
        proc = subprocess.run(
            ["pm2", "start", binary, "--name", self.pm2_api],
            capture_output=True,
            text=True,
            env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "port": port,
            "url": f"http://localhost:{port}",
            "stderr": proc.stderr[-500:],
        }

    def app(self, port: Optional[int] = None) -> Dict[str, Any]:
        """Start the Next.js dashboard under pm2."""
        port = port or self.app_port
        # Install deps if needed.
        if not os.path.exists(os.path.join(APP_DIR, "node_modules")):
            subprocess.run(["npm", "install"], cwd=APP_DIR, capture_output=True)
        # Build if no .next folder.
        if not os.path.exists(os.path.join(APP_DIR, ".next")):
            subprocess.run(["npm", "run", "build"], cwd=APP_DIR, capture_output=True)

        subprocess.run(["pm2", "delete", self.pm2_app], capture_output=True)
        env = os.environ.copy()
        env["PORT"] = str(port)
        env.setdefault("NJALLA_API_URL", f"http://localhost:{self.api_port}")
        proc = subprocess.run(
            [
                "pm2", "start", "npm",
                "--name", self.pm2_app,
                "--cwd", APP_DIR,
                "--", "run", "start",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "port": port,
            "url": f"http://localhost:{port}",
            "stderr": proc.stderr[-500:],
        }

    def serve(self, build: bool = True) -> Dict[str, Any]:
        """Bring up both API and app."""
        a = self.api(build=build)
        b = self.app()
        return {"api": a, "app": b}

    def kill(self) -> Dict[str, Any]:
        for name in (self.pm2_api, self.pm2_app):
            subprocess.run(["pm2", "delete", name], capture_output=True)
        return {"ok": True}

    def status(self) -> Dict[str, Any]:
        try:
            r = requests.get(f"{self.api_url}/health", timeout=2)
            api_up = r.ok
        except Exception:
            api_up = False
        return {
            "api_up": api_up,
            "api_url": self.api_url,
            "app_url": f"http://localhost:{self.app_port}",
        }

    def logs(self, name: Optional[str] = None, lines: int = 100) -> str:
        target = name or self.pm2_api
        r = subprocess.run(
            ["pm2", "logs", target, "--nostream", "--lines", str(lines)],
            capture_output=True,
            text=True,
        )
        return (r.stdout or "") + (r.stderr or "")

    # ── http helpers ─────────────────────────────────────────────────

    def _hdr(self, account: Optional[str]) -> Dict[str, str]:
        return {"X-Njalla-Account": account} if account else {}

    def _get(self, path: str, account: Optional[str] = None, **q) -> Any:
        r = requests.get(f"{self.api_url}{path}", params=q or None, headers=self._hdr(account), timeout=30)
        return _unwrap(r)

    def _post(self, path: str, body: Optional[Dict] = None, account: Optional[str] = None) -> Any:
        r = requests.post(f"{self.api_url}{path}", json=body or {}, headers=self._hdr(account), timeout=60)
        return _unwrap(r)

    def _patch(self, path: str, body: Optional[Dict] = None, account: Optional[str] = None) -> Any:
        r = requests.patch(f"{self.api_url}{path}", json=body or {}, headers=self._hdr(account), timeout=60)
        return _unwrap(r)

    def _delete(self, path: str, account: Optional[str] = None) -> Any:
        r = requests.delete(f"{self.api_url}{path}", headers=self._hdr(account), timeout=30)
        return _unwrap(r)

    # ── account management ──────────────────────────────────────────

    def accounts(self) -> List[Dict[str, Any]]:
        """List proxied njalla accounts (no tokens returned)."""
        return self._get("/accounts")

    def add_account(self, label: str, token: str) -> Dict[str, Any]:
        """Add a proxied account by label and njal.la API token."""
        return self._post("/accounts", {"label": label, "token": token})

    def remove_account(self, id: str) -> Dict[str, Any]:
        return self._delete(f"/accounts/{id}")

    # ── domains ─────────────────────────────────────────────────────

    def list_domains(self, account: Optional[str] = None) -> Any:
        return self._get("/domains", account=account)

    def get_domain(self, domain: str, account: Optional[str] = None) -> Any:
        return self._get(f"/domains/{domain}", account=account)

    def register_domain(self, domain: str, years: int = 1, account: Optional[str] = None) -> Any:
        return self._post("/domains/register", {"domain": domain, "years": years}, account=account)

    def renew_domain(self, domain: str, years: int = 1, account: Optional[str] = None) -> Any:
        return self._post(f"/domains/{domain}/renew", {"years": years}, account=account)

    def find_domains(self, query: str, account: Optional[str] = None) -> Any:
        return self._get("/domains/find", account=account, q=query)

    # ── records ─────────────────────────────────────────────────────

    def list_records(self, domain: str, account: Optional[str] = None) -> Any:
        return self._get(f"/domains/{domain}/records", account=account)

    def add_record(self, domain: str, type: str, name: str, content: str = "",
                   ttl: int = 3600, account: Optional[str] = None, **extra) -> Any:
        body = {"type": type, "name": name, "content": content, "ttl": ttl, **extra}
        return self._post(f"/domains/{domain}/records", body, account=account)

    def edit_record(self, domain: str, id: str, account: Optional[str] = None, **fields) -> Any:
        return self._patch(f"/domains/{domain}/records/{id}", fields, account=account)

    def remove_record(self, domain: str, id: str, account: Optional[str] = None) -> Any:
        return self._delete(f"/domains/{domain}/records/{id}", account=account)

    # ── servers / VPNs ──────────────────────────────────────────────

    def list_servers(self, account: Optional[str] = None) -> Any:
        return self._get("/servers", account=account)

    def add_server(self, type: str, os: str, name: str = "", months: int = 1,
                   account: Optional[str] = None, **extra) -> Any:
        body = {"type": type, "os": os, "name": name, "months": months, **extra}
        return self._post("/servers", body, account=account)

    def renew_server(self, id: str, months: int = 1, account: Optional[str] = None) -> Any:
        return self._post(f"/servers/{id}/renew", {"months": months}, account=account)

    def remove_server(self, id: str, account: Optional[str] = None) -> Any:
        return self._delete(f"/servers/{id}", account=account)

    def list_vpns(self, account: Optional[str] = None) -> Any:
        return self._get("/vpns", account=account)

    def add_vpn(self, name: str, autorenew: bool = False, account: Optional[str] = None) -> Any:
        return self._post("/vpns", {"name": name, "autorenew": autorenew}, account=account)

    def renew_vpn(self, id: str, months: int = 1, account: Optional[str] = None) -> Any:
        return self._post(f"/vpns/{id}/renew", {"months": months}, account=account)

    def remove_vpn(self, id: str, account: Optional[str] = None) -> Any:
        return self._delete(f"/vpns/{id}", account=account)

    # ── wallet / crypto payments ────────────────────────────────────

    def balance(self, account: Optional[str] = None) -> Any:
        return self._get("/wallet/balance", account=account)

    def transactions(self, account: Optional[str] = None) -> Any:
        return self._get("/wallet/transactions", account=account)

    def pay(self, amount: float, currency: str = "bitcoin",
            account: Optional[str] = None) -> Dict[str, Any]:
        """Start a crypto top-up. currency ∈ {bitcoin, litecoin, dash, monero,
        zcash, ethereum, paypal}. Returns a payment record with an address /
        URL the user pays to."""
        return self._post(
            "/wallet/pay",
            {"amount": amount, "currency": currency},
            account=account,
        )

    def payment_status(self, id: str, account: Optional[str] = None) -> Any:
        return self._get(f"/wallet/payment/{id}", account=account)

    # ── default + raw passthrough ───────────────────────────────────

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        """Default mod-protocol entry. With no `fn`, returns a status snapshot.
        With `fn=<method>`, dispatches to the matching attribute on this
        class (e.g. fn='list_domains', account='abc')."""
        if not fn:
            return self.status()
        attr = getattr(self, fn, None)
        if not callable(attr):
            return {"ok": False, "error": f"unknown fn: {fn}"}
        return attr(**kwargs)

    def rpc(self, method: str, params: Optional[Dict] = None,
            account: Optional[str] = None) -> Any:
        """Raw JSON-RPC passthrough to njal.la for any method we haven't
        wrapped explicitly. See https://njal.la/api/ for the full method list."""
        return self._post("/rpc", {"method": method, "params": params or {}}, account=account)


def _unwrap(r: requests.Response) -> Any:
    try:
        body = r.json()
    except Exception:
        body = {"error": r.text}
    if not r.ok:
        if isinstance(body, dict):
            body.setdefault("status", r.status_code)
        return body
    return body


# Keep a Mod alias for the loader's default lookup.
Mod = Njalla
