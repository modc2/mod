import json
import os
import subprocess
import urllib.request
import urllib.error


class Mod:
    description = """
    Decentralized authoritative DNS server (Rust + libp2p).
    P2P record sync via Kademlia DHT + GossipSub. No proprietary providers.
    """

    PM2_NAME = "mod-dns"
    PM2_APP = "mod-dns-app"
    BINARY = "mod-dns"

    def __init__(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.rust_dir = os.path.join(self.root, "mod-dns")
        self.app_dir = os.path.join(self.root, "app")
        self.binary_path = os.path.join(self.rust_dir, "target", "release", self.BINARY)
        self.api_port = 5380
        self.app_port = 5382
        self.dns_port = 15353
        self.p2p_port = 5381
        self.api_url = f"http://localhost:{self.api_port}"

    def _run(self, cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, **kw)

    def _api(self, method, path, data=None):
        url = f"{self.api_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                return json.loads(body)
            except Exception:
                return {"error": body, "status": e.code}
        except Exception as e:
            return {"error": str(e)}

    def build(self, release=True):
        """Build the Rust binary."""
        cmd = ["cargo", "build"]
        if release:
            cmd.append("--release")
        result = self._run(cmd, cwd=self.rust_dir, timeout=300)
        if result.returncode != 0:
            return {"status": "error", "stderr": result.stderr}
        return {"status": "ok", "binary": self.binary_path}

    def serve(self, zone="modc2.com", dns_port=None, api_port=None, p2p_port=None,
              bootstrap_peers=None, bind="0.0.0.0", build=True, app=True):
        """Build (if needed) and start the DNS server + app via PM2."""
        if build and not os.path.exists(self.binary_path):
            result = self.build()
            if result.get("status") != "ok":
                return result

        dns_port = dns_port or self.dns_port
        api_port = api_port or self.api_port
        p2p_port = p2p_port or self.p2p_port

        # Kill existing
        self._run(["pm2", "delete", self.PM2_NAME])

        cmd = [
            self.binary_path,
            "--zone", zone,
            "--dns-port", str(dns_port),
            "--api-port", str(api_port),
            "--p2p-port", str(p2p_port),
            "--bind", bind,
        ]
        if bootstrap_peers:
            cmd.extend(["--bootstrap-peers", bootstrap_peers])

        result = self._run([
            "pm2", "start", cmd[0],
            "--name", self.PM2_NAME,
            "--", *cmd[1:],
        ])

        self.api_port = api_port
        self.api_url = f"http://localhost:{api_port}"

        out = {
            "status": "ok" if result.returncode == 0 else "error",
            "pm2": self.PM2_NAME,
            "dns_port": dns_port,
            "api_port": api_port,
            "p2p_port": p2p_port,
            "zone": zone,
        }

        # Start Next.js app
        if app and os.path.isdir(self.app_dir):
            app_result = self.serve_app()
            out["app_port"] = self.app_port
            out["app"] = app_result.get("status", "error")

        return out

    def serve_app(self, port=None):
        """Start the Next.js app via PM2."""
        port = port or self.app_port
        self._run(["pm2", "delete", self.PM2_APP])

        # Install deps if needed
        if not os.path.isdir(os.path.join(self.app_dir, "node_modules")):
            self._run(["npm", "install"], cwd=self.app_dir, timeout=120)

        result = self._run([
            "pm2", "start", "npx",
            "--name", self.PM2_APP,
            "--", "next", "dev", "-p", str(port),
        ], cwd=self.app_dir)

        return {
            "status": "ok" if result.returncode == 0 else "error",
            "pm2": self.PM2_APP,
            "app_port": port,
            "url": f"http://localhost:{port}/dns",
        }

    def kill(self):
        """Stop the DNS server and app."""
        result = self._run(["pm2", "delete", self.PM2_NAME])
        app_result = self._run(["pm2", "delete", self.PM2_APP])
        return {
            "status": "killed" if result.returncode == 0 else "not_running",
            "pm2": self.PM2_NAME,
            "app": "killed" if app_result.returncode == 0 else "not_running",
        }

    def kill_app(self):
        """Stop just the Next.js app."""
        result = self._run(["pm2", "delete", self.PM2_APP])
        return {
            "status": "killed" if result.returncode == 0 else "not_running",
            "pm2": self.PM2_APP,
        }

    def status(self):
        """Check if the DNS server is running."""
        return self._api("GET", "/health")

    def add(self, name, rtype="A", value="", ttl=300, zone="modc2.com"):
        """Add or update a DNS record."""
        return self._api("PUT", f"/zones/{zone}/records", {
            "name": name,
            "rtype": rtype.upper(),
            "value": value,
            "ttl": ttl,
        })

    def remove(self, name, rtype="A", zone="modc2.com"):
        """Delete a DNS record."""
        return self._api("DELETE", f"/zones/{zone}/records/{name}/{rtype.upper()}")

    def get(self, name, rtype="A", zone="modc2.com"):
        """Get a specific DNS record."""
        return self._api("GET", f"/zones/{zone}/records/{name}/{rtype.upper()}")

    def records(self, zone="modc2.com"):
        """List all records in a zone."""
        return self._api("GET", f"/zones/{zone}/records")

    def zones(self):
        """List all zones."""
        return self._api("GET", "/zones")

    def peers(self):
        """List connected P2P peers."""
        return self._api("GET", "/peers")

    def stats(self):
        """Get server stats."""
        return self._api("GET", "/stats")

    def forward(self, name=None, rtype="A", value=None, zone="modc2.com", **kwargs):
        """Default entry point. With args: add record. Without: show stats."""
        if name and value:
            return self.add(name, rtype, value, zone=zone, **kwargs)
        elif name:
            return self.get(name, rtype, zone)
        else:
            return self.stats()
