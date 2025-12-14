import subprocess
import time
import json
from typing import Optional, Dict, Any


class Ganache:
    """Ganache blockchain manager for local Ethereum development."""
    
    def __init__(self, port: int = 8545, network_id: int = 1337, mnemonic: Optional[str] = None):
        self.port = port
        self.network_id = network_id
        self.mnemonic = mnemonic or "test test test test test test test test test test test junk"
        self.process: Optional[subprocess.Popen] = None
        self.accounts = []
        self.private_keys = []
        
    def setup(self) -> Dict[str, Any]:
        """Start Ganache instance."""
        cmd = [
            "ganache",
            "--port", str(self.port),
            "--networkId", str(self.network_id),
            "--mnemonic", self.mnemonic,
            "--accounts", "10",
            "--defaultBalanceEther", "100"
        ]
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            time.sleep(3)  # Wait for Ganache to start
            
            if self.process.poll() is not None:
                raise RuntimeError("Ganache failed to start")
                
            return {"status": "running", "port": self.port, "network_id": self.network_id}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    def teardown(self) -> Dict[str, Any]:
        """Stop Ganache instance."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            return {"status": "stopped"}
        return {"status": "not_running"}
    
    def get_info(self) -> Dict[str, Any]:
        """Get all Ganache instance information."""
        return {
            "port": self.port,
            "network_id": self.network_id,
            "mnemonic": self.mnemonic,
            "rpc_url": f"http://localhost:{self.port}",
            "status": "running" if self.process and self.process.poll() is None else "stopped",
            "process_id": self.process.pid if self.process else None
        }
    
    def __enter__(self):
        """Context manager entry."""
        self.setup()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.teardown()
