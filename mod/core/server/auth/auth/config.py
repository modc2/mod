"""
Configuration utilities for the auth and permission system.
"""

import os
import json
from pathlib import Path
from typing import Optional


class AuthConfig:
    """
    Configuration manager for authentication and permissions.
    """

    DEFAULT_CONFIG_PATH = Path.home() / ".mod" / "auth_config.json"

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the config manager.

        Args:
            config_path: Path to the config file (defaults to ~/.mod/auth_config.json)
        """
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """
        Load configuration from file.

        Returns:
            Configuration dictionary
        """
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}")
                return {}
        return {}

    def _save_config(self):
        """
        Save configuration to file.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)

    def get_owner_address(self) -> Optional[str]:
        """
        Get the owner address from config.

        Returns:
            Owner address or None
        """
        # Check environment first
        env_owner = os.getenv('MOD_OWNER_ADDRESS')
        if env_owner:
            return env_owner

        # Check config file
        return self.config.get('owner_address')

    def set_owner_address(self, address: str):
        """
        Set the owner address in config.

        Args:
            address: The owner's address
        """
        self.config['owner_address'] = address
        self._save_config()
        print(f"Owner address set to: {address}")

    def get_max_token_age(self) -> int:
        """
        Get the maximum token age in seconds.

        Returns:
            Maximum age in seconds (default: 86400)
        """
        return self.config.get('max_token_age', 86400)

    def set_max_token_age(self, seconds: int):
        """
        Set the maximum token age.

        Args:
            seconds: Maximum age in seconds
        """
        self.config['max_token_age'] = seconds
        self._save_config()

    def is_strict_mode(self) -> bool:
        """
        Check if strict permission mode is enabled.

        In strict mode, all operations require explicit permission checks.

        Returns:
            True if strict mode is enabled
        """
        return self.config.get('strict_mode', True)

    def set_strict_mode(self, enabled: bool):
        """
        Enable or disable strict permission mode.

        Args:
            enabled: True to enable strict mode
        """
        self.config['strict_mode'] = enabled
        self._save_config()

    def get_all_settings(self) -> dict:
        """
        Get all configuration settings.

        Returns:
            Dictionary of all settings
        """
        return {
            'owner_address': self.get_owner_address(),
            'max_token_age': self.get_max_token_age(),
            'strict_mode': self.is_strict_mode(),
            'config_path': str(self.config_path)
        }


def setup_owner():
    """
    Interactive setup for configuring the owner address.
    """
    print("\n=== MOD Permission System Setup ===\n")

    config = AuthConfig()
    current_owner = config.get_owner_address()

    if current_owner:
        print(f"Current owner address: {current_owner}")
        change = input("Do you want to change it? (y/n): ").lower().strip()
        if change != 'y':
            print("Setup cancelled.")
            return

    print("\nThe owner address has full root access to all modules.")
    print("Other users can only access modules in orbit/portal/{their_address}/\n")

    # Try to get current user's address
    try:
        import mod as m
        key = m.key()
        default_address = key.address
        print(f"Your current address: {default_address}")
        use_current = input("Use this address as owner? (y/n): ").lower().strip()
        if use_current == 'y':
            config.set_owner_address(default_address)
            print("\n✓ Owner address configured successfully!")
            print(f"  Owner: {default_address}")
            return
    except Exception as e:
        print(f"Could not get current address: {e}")

    # Manual input
    print("\nEnter the owner's address manually:")
    address = input("Address: ").strip()

    if not address:
        print("No address provided. Setup cancelled.")
        return

    config.set_owner_address(address)
    print("\n✓ Owner address configured successfully!")
    print(f"  Owner: {address}")

    # Optional: configure other settings
    print("\nWould you like to configure additional settings? (y/n): ")
    if input().lower().strip() == 'y':
        print("\nMaximum token age (seconds, default 86400):")
        try:
            age = int(input("Age: ").strip() or "86400")
            config.set_max_token_age(age)
        except ValueError:
            print("Invalid value, keeping default (86400)")

        print("\nEnable strict permission mode? (y/n, default y):")
        strict = input().lower().strip() != 'n'
        config.set_strict_mode(strict)

    print("\n=== Configuration Summary ===")
    for key, value in config.get_all_settings().items():
        print(f"  {key}: {value}")
    print()


def show_config():
    """
    Display current configuration.
    """
    config = AuthConfig()
    print("\n=== Current Configuration ===")
    for key, value in config.get_all_settings().items():
        print(f"  {key}: {value}")
    print()


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == 'setup':
            setup_owner()
        elif command == 'show':
            show_config()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python config.py [setup|show]")
    else:
        setup_owner()
