import subprocess
import os

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Mod:
    description = """
    qbit — quantum-resistant key-value store with validator consensus.
    Rust API (axum) + Next.js app. Lamport OTS + Merkle signature scheme.
    """

    def forward(self, **kw):
        """Show module info."""
        return {
            "name": "qbit",
            "api": "rust (axum)",
            "app": "next.js",
            "port": 50100,
            "app_port": 50101,
            "crypto": "lamport-ots + merkle-signature-scheme",
            "quantum_resistant": True,
        }

    def serve(self, **kw):
        """Start API and app servers."""
        os.makedirs("/tmp/qbit", exist_ok=True)
        self.serve_api()
        self.serve_app()
        return {"api": "http://localhost:50100", "app": "http://localhost:50101"}

    def serve_api(self, **kw):
        """Build and start the Rust API."""
        os.makedirs("/tmp/qbit", exist_ok=True)
        subprocess.Popen(
            ["cargo", "run", "--release"],
            cwd=DIR,
            stdout=open("/tmp/qbit/api.log", "a"),
            stderr=subprocess.STDOUT,
        )
        return "api starting on :50100"

    def serve_app(self, **kw):
        """Start the Next.js app."""
        os.makedirs("/tmp/qbit", exist_ok=True)
        app_dir = os.path.join(DIR, "app")
        subprocess.Popen(
            ["npx", "next", "dev", "--port", "50101"],
            cwd=app_dir,
            stdout=open("/tmp/qbit/app.log", "a"),
            stderr=subprocess.STDOUT,
        )
        return "app starting on :50101"

    def build(self, **kw):
        """Build the Rust binary."""
        result = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=DIR,
            capture_output=True,
            text=True,
        )
        return result.stdout or result.stderr

    def kill(self, **kw):
        """Stop API and app servers."""
        os.system("pkill -f 'target.*qbit' 2>/dev/null; pkill -f 'next.*50101' 2>/dev/null")
        return "killed"
