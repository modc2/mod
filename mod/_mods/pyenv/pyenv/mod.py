import subprocess
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any


class BaseMod:
    description = """
    PyEnv - Python Environment Manager
    Manage Python virtual environments and execute commands within them
    """

    def __init__(self, env_path: Optional[str] = None):
        """
        Initialize PyEnv manager
        
        Args:
            env_path: Path to the virtual environment. If None, uses './venv'
        """
        self.env_path = Path(env_path) if env_path else Path('./venv')
        self.python_bin = self._get_python_bin()
        self.pip_bin = self._get_pip_bin()

    def _get_python_bin(self) -> Path:
        """Get the path to the Python binary in the virtual environment"""
        if sys.platform == 'win32':
            return self.env_path / 'Scripts' / 'python.exe'
        return self.env_path / 'bin' / 'python'

    def _get_pip_bin(self) -> Path:
        """Get the path to the pip binary in the virtual environment"""
        if sys.platform == 'win32':
            return self.env_path / 'Scripts' / 'pip.exe'
        return self.env_path / 'bin' / 'pip'

    def create(self, python_version: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a new virtual environment
        
        Args:
            python_version: Specific Python version to use (e.g., 'python3.9')
        
        Returns:
            Dictionary with success status and message
        """
        try:
            python_cmd = python_version if python_version else sys.executable
            subprocess.run([python_cmd, '-m', 'venv', str(self.env_path)], check=True)
            return {'success': True, 'message': f'Environment created at {self.env_path}'}
        except subprocess.CalledProcessError as e:
            return {'success': False, 'message': f'Failed to create environment: {str(e)}'}

    def exists(self) -> bool:
        """Check if the virtual environment exists"""
        return self.env_path.exists() and self.python_bin.exists()

    def delete(self) -> Dict[str, Any]:
        """Delete the virtual environment"""
        try:
            if self.exists():
                import shutil
                shutil.rmtree(self.env_path)
                return {'success': True, 'message': f'Environment deleted: {self.env_path}'}
            return {'success': False, 'message': 'Environment does not exist'}
        except Exception as e:
            return {'success': False, 'message': f'Failed to delete environment: {str(e)}'}

    def run(self, command: List[str], capture_output: bool = True) -> Dict[str, Any]:
        """
        Run a command in the virtual environment
        
        Args:
            command: Command to run as a list of strings
            capture_output: Whether to capture and return output
        
        Returns:
            Dictionary with returncode, stdout, and stderr
        """
        if not self.exists():
            return {'success': False, 'message': 'Environment does not exist', 'returncode': -1}

        try:
            result = subprocess.run(
                command,
                capture_output=capture_output,
                text=True,
                cwd=str(self.env_path.parent),
                env=self._get_env_vars()
            )
            return {
                'success': result.returncode == 0,
                'returncode': result.returncode,
                'stdout': result.stdout if capture_output else '',
                'stderr': result.stderr if capture_output else ''
            }
        except Exception as e:
            return {'success': False, 'message': str(e), 'returncode': -1}

    def run_python(self, script: str, capture_output: bool = True) -> Dict[str, Any]:
        """
        Run a Python script in the virtual environment
        
        Args:
            script: Python code to execute
            capture_output: Whether to capture and return output
        
        Returns:
            Dictionary with execution results
        """
        return self.run([str(self.python_bin), '-c', script], capture_output)

    def install(self, packages: List[str]) -> Dict[str, Any]:
        """
        Install packages using pip in the virtual environment
        
        Args:
            packages: List of package names to install
        
        Returns:
            Dictionary with installation results
        """
        if not self.exists():
            return {'success': False, 'message': 'Environment does not exist'}

        return self.run([str(self.pip_bin), 'install'] + packages)

    def uninstall(self, packages: List[str]) -> Dict[str, Any]:
        """
        Uninstall packages using pip in the virtual environment
        
        Args:
            packages: List of package names to uninstall
        
        Returns:
            Dictionary with uninstallation results
        """
        if not self.exists():
            return {'success': False, 'message': 'Environment does not exist'}

        return self.run([str(self.pip_bin), 'uninstall', '-y'] + packages)

    def list_packages(self) -> Dict[str, Any]:
        """List all installed packages in the virtual environment"""
        if not self.exists():
            return {'success': False, 'message': 'Environment does not exist'}

        return self.run([str(self.pip_bin), 'list'])

    def _get_env_vars(self) -> Dict[str, str]:
        """
        Get environment variables with virtual environment activated
        
        Returns:
            Dictionary of environment variables
        """
        env = os.environ.copy()
        
        # Add virtual environment to PATH
        if sys.platform == 'win32':
            bin_dir = self.env_path / 'Scripts'
        else:
            bin_dir = self.env_path / 'bin'
        
        env['PATH'] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env['VIRTUAL_ENV'] = str(self.env_path)
        
        # Remove PYTHONHOME if set
        env.pop('PYTHONHOME', None)
        
        return env

    def activate_command(self) -> str:
        """Get the command to activate the virtual environment"""
        if sys.platform == 'win32':
            return f"{self.env_path}\\Scripts\\activate.bat"
        return f"source {self.env_path}/bin/activate"
