import json
import os
import signal
import subprocess
import time
from pathlib import Path


class Mod:
    description = """
    Multi-chain multisig wallet manager — Base (Safe/MetaMask), TAO (Substrate pallet/SubWallet), Solana (Squads/Phantom).
    Rust API (Axum) + Next.js app.
    """

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent.parent
        self.config = config or self._load_config()
        self.name = self.config.get("name", "multisig")
        self.port = int(self.config.get("port", 50100))
        self.app_port = int(self.config.get("app_port", 3100))
        self.log_dir = Path(f"/tmp/{self.name}")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self):
        cfg_path = self.module_dir / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text())
        return {}

    def forward(self):
        """Show module info."""
        return {
            "name": self.name,
            "description": self.description.strip(),
            "port": self.port,
            "app_port": self.app_port,
            "chains": ["base", "tao", "solana"],
            "api": f"http://localhost:{self.port}",
            "app": f"http://localhost:{self.app_port}",
        }

    def health(self):
        """Check API health."""
        import urllib.request
        try:
            url = f"http://localhost:{self.port}/api/health"
            resp = urllib.request.urlopen(url, timeout=3)
            return json.loads(resp.read())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def build(self):
        """Build the Rust API binary."""
        print(f"Building multisig API...")
        result = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=str(self.module_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Build failed:\n{result.stderr}")
            return False
        print("Build complete: target/release/multisig")
        return True

    def serve(self):
        """Start API (Rust) + App (Next.js)."""
        self.kill()
        time.sleep(0.5)

        # Build Rust binary if needed
        binary = self.module_dir / "target" / "release" / "multisig"
        if not binary.exists():
            if not self.build():
                return {"ok": False, "error": "Build failed"}

        # Ensure data dir
        data_dir = Path.home() / ".mod" / "multisig" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Start Rust API
        env = os.environ.copy()
        env["MULTISIG_PORT"] = str(self.port)
        env["MULTISIG_HOST"] = "0.0.0.0"
        env["MULTISIG_DATA_DIR"] = str(data_dir)
        env["RUST_LOG"] = "multisig=info,tower_http=info"

        # Pass RPC URLs from config
        rpc = self.config.get("rpc", {})
        if rpc.get("base"):
            env["BASE_RPC"] = rpc["base"]
        if rpc.get("tao"):
            env["TAO_RPC"] = rpc["tao"]
        if rpc.get("solana"):
            env["SOLANA_RPC"] = rpc["solana"]

        # Chain ID from contracts config
        contracts = self.config.get("contracts", {})
        if contracts.get("base", {}).get("chain_id"):
            env["BASE_CHAIN_ID"] = str(contracts["base"]["chain_id"])

        api_log = open(self.log_dir / "api.log", "w")
        api_proc = subprocess.Popen(
            [str(binary)],
            env=env,
            stdout=api_log,
            stderr=subprocess.STDOUT,
            cwd=str(self.module_dir),
        )
        self._write_pid("api", api_proc.pid)
        print(f"API started on port {self.port} (pid {api_proc.pid})")

        # Wait for API to be ready
        for _ in range(30):
            try:
                import urllib.request
                urllib.request.urlopen(
                    f"http://localhost:{self.port}/api/health", timeout=1
                )
                break
            except Exception:
                time.sleep(0.5)

        # Start Next.js app
        app_dir = self.module_dir / "app"
        if app_dir.exists() and (app_dir / "package.json").exists():
            app_env = os.environ.copy()
            app_env["PORT"] = str(self.app_port)
            app_env["NEXT_PUBLIC_API_URL"] = f"http://localhost:{self.port}"
            app_env["NEXT_PUBLIC_BASE_PATH"] = f"/{self.name}"

            # Install deps if needed
            if not (app_dir / "node_modules").exists():
                print("Installing app dependencies...")
                subprocess.run(
                    ["npm", "install"],
                    cwd=str(app_dir),
                    capture_output=True,
                )

            app_log = open(self.log_dir / "app.log", "w")
            app_proc = subprocess.Popen(
                ["npx", "next", "dev", "-p", str(self.app_port)],
                env=app_env,
                stdout=app_log,
                stderr=subprocess.STDOUT,
                cwd=str(app_dir),
            )
            self._write_pid("app", app_proc.pid)
            print(f"App started on port {self.app_port} (pid {app_proc.pid})")

        # Register with mod namespace
        self._register()

        return {
            "ok": True,
            "api": f"http://localhost:{self.port}",
            "app": f"http://localhost:{self.app_port}",
        }

    def kill(self):
        """Stop API and App processes."""
        for name in ["api", "app"]:
            pid = self._read_pid(name)
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    print(f"Killed {name} (pid {pid})")
                except ProcessLookupError:
                    pass
                self._clear_pid(name)
        return {"ok": True}

    def _write_pid(self, name, pid):
        pid_file = self.log_dir / f"{name}.pid"
        pid_file.write_text(str(pid))

    def _read_pid(self, name):
        pid_file = self.log_dir / f"{name}.pid"
        if pid_file.exists():
            try:
                return int(pid_file.read_text().strip())
            except ValueError:
                return None
        return None

    def _clear_pid(self, name):
        pid_file = self.log_dir / f"{name}.pid"
        if pid_file.exists():
            pid_file.unlink()

    def _register(self):
        """Register with the mod namespace/routy gateway."""
        try:
            import urllib.request
            # Register API
            data = json.dumps({
                "name": self.name,
                "url": f"http://localhost:{self.port}",
                "type": "api",
            }).encode()
            req = urllib.request.Request(
                "http://localhost:3001/_api/register",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)

            # Register App
            data = json.dumps({
                "name": self.name,
                "url": f"http://localhost:{self.app_port}",
                "type": "app",
            }).encode()
            req = urllib.request.Request(
                "http://localhost:3001/_api/register",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass  # routy may not be running
