import os
import subprocess
from pathlib import Path
import json

class SSHKeyManager:
    def __init__(self, key_type= 'ed25519'):
        self.ssh_dir = Path.home() / ".ssh"
        self.ssh_dir.mkdir(mode=0o700, exist_ok=True)
        self.key_type = key_type
        self.prefix = f'id_{key_type}'

    def list_keys(self , include_fingerprint=False):
        """List all existing SSH keys without revealing system information."""
        keys = []
        for key_file in self.ssh_dir.glob("id_*"):
            if not key_file.name.endswith(".pub"):
                pub_file = Path(str(key_file) + ".pub")
                if pub_file.exists():
                    with open(pub_file, 'r') as f:
                        pub_content = f.read().strip()
                        # Remove comment/hostname from public key
                        parts = pub_content.split()
                        sanitized_key = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else pub_content
                        key_type = parts[0].replace("ssh-", "") if len(parts) >= 1 else "unknown"
                        if key_type != self.key_type:
                            continue
                        keys.append({
                            "name": key_file.name,
                            'key_type': key_type,
                            "public_key": sanitized_key,
        
                        })
                        if include_fingerprint:
                            fingerprint = self._get_fingerprint(pub_file)
                            keys[-1]['fingerprint'] = fingerprint
        return keys

    def _get_fingerprint(self, pub_key_path):
        """Get SSH key fingerprint."""
        try:
            result = subprocess.run(
                ["ssh-keygen", "-lf", str(pub_key_path)],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except:
            return "Unable to get fingerprint"

    def generate_key(self, key_name=None, comment=""):
        """Generate SSH key without hostname in comment."""
        if not key_name:
            key_name = f"id_{key_type}"
        
        key
        key_path = self.ssh_dir / f'{self.prefix}_{key_name}'
        
        if key_path.exists():
            print(f"Key {key_name} already exists!")
            return False

        cmd = [
            "ssh-keygen",
            "-t", key_type,
            "-C", comment if comment else "",
            "-f", str(key_path),
            "-N", ""
        ]
        
        subprocess.run(cmd, check=True)
        
        # Sanitize the public key to remove any system info
        pub_path = Path(str(key_path) + ".pub")
        with open(pub_path, 'r') as f:
            content = f.read().strip()
        
        parts = content.split()
        if len(parts) >= 2:
            sanitized = f"{parts[0]} {parts[1]}"
            if comment:
                sanitized += f" {comment}"
            with open(pub_path, 'w') as f:
                f.write(sanitized + "\n")
        
        print(f"[✓] Generated {key_type} key: {key_name}")
        return True

    def remove_key(self, key_name):
        """Remove SSH key pair."""
        key_path = self.ssh_dir / key_name
        pub_path = Path(str(key_path) + ".pub")
        
        removed = []
        if key_path.exists():
            key_path.unlink()
            removed.append(str(key_path))
        if pub_path.exists():
            pub_path.unlink()
            removed.append(str(pub_path))
        
        if removed:
            print(f"[✓] Removed: {', '.join(removed)}")
            return True
        else:
            print(f"[!] Key {key_name} not found")
            return False

    def get_public_key(self, key_name, sanitized=True):
        """Get public key content without system information."""
        pub_path = self.ssh_dir / f"{key_name}.pub"
        if not pub_path.exists():
            return None
        
        with open(pub_path, 'r') as f:
            content = f.read().strip()
        
        if sanitized:
            parts = content.split()
            return f"{parts[0]} {parts[1]}" if len(parts) >= 2 else content
        return content

