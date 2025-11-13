import os
import subprocess
import paramiko
from pathlib import Path

class SSHManager:
    def __init__(self, user, host=None, key_type="ed25519", port=22):
        """
        SSHManager handles SSH key generation, uploading, and secure connections.

        :param user: Username (local or remote)
        :param host: Remote hostname or IP (None for local key generation)
        :param key_type: "rsa" or "ed25519"
        :param port: SSH port (default: 22)
        """
        self.user = user
        self.host = host
        self.port = port
        self.key_type = key_type
        self.ssh_dir = Path.home() / ".ssh"
        self.private_key_path = self.ssh_dir / f"id_{key_type}"
        self.public_key_path = Path(str(self.private_key_path) + ".pub")

    # -----------------------
    # 1. Generate SSH keypair
    # -----------------------
    def generate_keypair(self, overwrite=False, comment="generated-by-SSHManager"):
        """Generate an SSH keypair if it doesn't already exist."""
        if self.private_key_path.exists() and not overwrite:
            print(f"[+] Key already exists at {self.private_key_path}")
            return

        self.ssh_dir.mkdir(mode=0o700, exist_ok=True)
        cmd = [
            "ssh-keygen",
            "-t", self.key_type,
            "-C", comment,
            "-f", str(self.private_key_path),
            "-N", "",  # empty passphrase
        ]
        subprocess.run(cmd, check=True)
        print(f"[✓] Generated {self.key_type} key at {self.private_key_path}")

    # ---------------------------------
    # 2. Upload public key to server
    # ---------------------------------
    def upload_key(self, password=None):
        """
        Upload the public key to a remote server's ~/.ssh/authorized_keys
        """
        if not self.host:
            raise ValueError("Host not specified for upload.")
        if not self.public_key_path.exists():
            raise FileNotFoundError("Public key not found. Run generate_keypair() first.")

        # Use ssh-copy-id if available
        cmd = [
            "ssh-copy-id",
            "-i", str(self.public_key_path),
            f"{self.user}@{self.host}"
        ]

        if password:
            # Use paramiko if we need to provide password (non-interactive)
            print("[*] Uploading key via Paramiko...")
            self._upload_key_paramiko(password)
        else:
            subprocess.run(cmd, check=True)
            print(f"[✓] Uploaded key to {self.user}@{self.host}")

    def _upload_key_paramiko(self, password):
        """Fallback method if ssh-copy-id isn't available or password is needed."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.host, username=self.user, password=password)

        pub_key = self.public_key_path.read_text().strip()
        stdin, stdout, stderr = client.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
        client.exec_command(f"echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
        client.close()
        print(f"[✓] Public key added to {self.user}@{self.host}")

    # ------------------------------
    # 3. Connect and run a command
    # ------------------------------
    def run(self, command):
        """Connect to the remote server using the private key and run a command."""
        if not self.host:
            raise ValueError("Host not specified for SSH command.")

        key = paramiko.Ed25519Key.from_private_key_file(self.private_key_path) \
              if self.key_type == "ed25519" else \
              paramiko.RSAKey.from_private_key_file(self.private_key_path)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.host, username=self.user, pkey=key, port=self.port)

        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        client.close()

        if error:
            print(f"[!] Error: {error}")
        return output.strip()

    # -----------------------------------------
    # 4. Disable password authentication (server)
    # -----------------------------------------
    def harden_server(self, password=None):
        """
        Disables password login and root login on the remote server.
        """
        print("[*] Hardening SSH server...")
        cmd = """
        sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config &&
        sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config &&
        sudo systemctl restart sshd || sudo service ssh restart
        """
        output = self.run(cmd)
        print("[✓] Password authentication disabled. SSH key only mode enforced.")
        return output
