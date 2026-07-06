"""
Permission system for user-based module access control.

Owner has full root access to everything.
Other users can only access modules in orbit/portal/{their_address}/{mod}
"""

import os
from typing import Optional, List, Set
from pathlib import Path
import mod as m


class PermissionManager:
    """
    Manages access control for modules based on user addresses.

    Rules:
    - Owner (computer owner) has full root access
    - Other users can only access modules in mod/peers/{user_address}/{mod}
    - Users can read/write/execute their own modules
    - Users cannot access modules owned by other users
    """

    def __init__(self, owner_address: Optional[str] = None, mod_root: Optional[str] = None):
        """
        Initialize the permission manager.

        Args:
            owner_address: The address of the owner (has full root access)
            mod_root: The root directory of the mod system (defaults to finding it automatically)
        """
        self.owner_address = owner_address or self._get_owner_address()
        self.mod_root = Path(mod_root) if mod_root else self._find_mod_root()
        self.outer_path = self.mod_root / "mod" / "orbit" / "registry" / "mods"

    def _get_owner_address(self) -> str:
        """
        Get the owner address from configuration or environment.
        This should be set by the system administrator.
        """
        # Try to get from config
        try:
            config = m.get('owner_address')
            if config:
                return config
        except:
            pass

        # Try to get from environment
        owner = os.getenv('MOD_OWNER_ADDRESS')
        if owner:
            return owner

        # Default to the current user's address if available
        try:
            key = m.key()
            return key.address
        except:
            pass

        return None

    def _find_mod_root(self) -> Path:
        """
        Find the mod root directory by looking for the mod package.
        """
        try:
            import mod
            mod_path = Path(mod.__file__).parent.parent
            return mod_path
        except:
            # Fallback to current directory structure
            return Path.cwd()

    def is_owner(self, user_address: str) -> bool:
        """
        Check if the user is the owner of this system.

        Args:
            user_address: The address to check

        Returns:
            True if the user is the owner
        """
        if not self.owner_address:
            return False
        return user_address.lower() == self.owner_address.lower()

    def get_user_path(self, user_address: str) -> Path:
        """
        Get the path to a user's module directory.

        Args:
            user_address: The user's address

        Returns:
            Path to the user's module directory
        """
        return self.outer_path / user_address

    def user_owns_module(self, user_address: str, module_path: str) -> bool:
        """
        Check if a user owns a module based on its path.

        Args:
            user_address: The user's address
            module_path: The path to the module

        Returns:
            True if the user owns the module
        """
        module_path = Path(module_path).resolve()
        user_path = self.get_user_path(user_address).resolve()

        # Check if the module is under the user's directory
        try:
            module_path.relative_to(user_path)
            return True
        except ValueError:
            return False

    def can_access(self, user_address: str, module_path: str, operation: str = 'read') -> bool:
        """
        Check if a user can access a module.

        Args:
            user_address: The user's address
            module_path: The path to the module
            operation: The operation to check ('read', 'write', 'execute')

        Returns:
            True if the user can access the module
        """
        # Owner has full access
        if self.is_owner(user_address):
            return True

        # Check if user owns the module
        return self.user_owns_module(user_address, module_path)

    def list_user_modules(self, user_address: str) -> List[str]:
        """
        List all modules owned by a user.

        Args:
            user_address: The user's address

        Returns:
            List of module names owned by the user
        """
        user_path = self.get_user_path(user_address)

        if not user_path.exists():
            return []

        # List all directories in the user's path
        modules = []
        for item in user_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                modules.append(item.name)

        return sorted(modules)

    def get_accessible_modules(self, user_address: str) -> List[str]:
        """
        Get all modules accessible to a user.

        Args:
            user_address: The user's address

        Returns:
            List of accessible module paths
        """
        # If owner, return all modules
        if self.is_owner(user_address):
            return self._list_all_modules()

        # Otherwise, return only user's modules
        user_modules = self.list_user_modules(user_address)
        user_path = self.get_user_path(user_address)

        return [str(user_path / mod) for mod in user_modules]

    def _list_all_modules(self) -> List[str]:
        """
        List all modules in the system (for owner).

        Returns:
            List of all module paths
        """
        modules = []

        if not self.outer_path.exists():
            return modules

        for user_dir in self.outer_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                for mod_dir in user_dir.iterdir():
                    if mod_dir.is_dir() and not mod_dir.name.startswith('.'):
                        modules.append(str(mod_dir))

        return sorted(modules)

    def create_user_directory(self, user_address: str) -> Path:
        """
        Create a directory for a new user if it doesn't exist.

        Args:
            user_address: The user's address

        Returns:
            Path to the created user directory
        """
        user_path = self.get_user_path(user_address)
        user_path.mkdir(parents=True, exist_ok=True)

        # Create a README for the user
        readme_path = user_path / "README.md"
        if not readme_path.exists():
            readme_content = f"""# Modules for {user_address}

This directory contains all modules owned by user `{user_address}`.

Each subdirectory is a separate module that can be:
- Registered on-chain
- Executed via the MOD API
- Monetized through the BlocTime protocol

## Access Control

- You have full read/write/execute access to modules in this directory
- Other users cannot access or modify your modules
- The system owner has administrative access to all modules

## Creating a New Module

1. Create a new directory: `mkdir my_module`
2. Add your module code: `my_module/mod.py`
3. Register it: `m register my_module`
"""
            readme_path.write_text(readme_content)

        return user_path

    def check_and_raise(self, user_address: str, module_path: str, operation: str = 'read'):
        """
        Check permissions and raise an exception if denied.

        Args:
            user_address: The user's address
            module_path: The path to the module
            operation: The operation to check

        Raises:
            PermissionError: If access is denied
        """
        if not self.can_access(user_address, module_path, operation):
            raise PermissionError(
                f"User {user_address} does not have permission to {operation} module at {module_path}"
            )


def check_permission(user_address: str, module_path: str, operation: str = 'read') -> bool:
    """
    Helper function to check permissions.

    Args:
        user_address: The user's address
        module_path: The path to the module
        operation: The operation to check ('read', 'write', 'execute')

    Returns:
        True if the user has permission
    """
    pm = PermissionManager()
    return pm.can_access(user_address, module_path, operation)


def require_permission(user_address: str, module_path: str, operation: str = 'read'):
    """
    Require permission or raise an exception.

    Args:
        user_address: The user's address
        module_path: The path to the module
        operation: The operation to check

    Raises:
        PermissionError: If access is denied
    """
    pm = PermissionManager()
    pm.check_and_raise(user_address, module_path, operation)
