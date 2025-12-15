#!/usr/bin/env python3
"""
PyPM - A native Python process manager similar to PM2
No Docker required - pure Python implementation with Python environment support
"""

import os
import sys
import json
import time
import signal
import psutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import threading
import atexit


class PyPM:
    """
    A native Python process manager similar to PM2.
    Manages processes without Docker, using only Python standard library and psutil.
    Supports multiple Python environments (virtualenv, conda, system python).
    """

    def __init__(self, storage_path: str = "~/.pypm"):
        self.storage_path = Path(storage_path).expanduser()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.processes_file = self.storage_path / "processes.json"
        self.logs_dir = self.storage_path / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        self.pids_dir = self.storage_path / "pids"
        self.pids_dir.mkdir(exist_ok=True)
        self._load_processes()

    def _load_processes(self):
        """Load process registry from disk."""
        if self.processes_file.exists():
            with open(self.processes_file, 'r') as f:
                self.processes = json.load(f)
        else:
            self.processes = {}

    def _save_processes(self):
        """Save process registry to disk."""
        with open(self.processes_file, 'w') as f:
            json.dump(self.processes, f, indent=2)

    def _resolve_python_env(self, python_env: Optional[str] = None) -> str:
        """
        Resolve Python environment path.
        
        Args:
            python_env: Path to Python interpreter or virtualenv
            
        Returns:
            Full path to Python interpreter
        """
        if python_env is None:
            return sys.executable
        
        # Check if it's a direct path to python executable
        if os.path.isfile(python_env) and os.access(python_env, os.X_OK):
            return python_env
        
        # Check if it's a virtualenv directory (raw venv)
        venv_python = os.path.join(python_env, 'bin', 'python')
        if os.path.isfile(venv_python):
            return venv_python
        
        # Check Windows virtualenv
        venv_python_win = os.path.join(python_env, 'Scripts', 'python.exe')
        if os.path.isfile(venv_python_win):
            return venv_python_win
        
        # Default to system python3 or python
        return python_env if '/' in python_env or '\\' in python_env else 'python3'

    def start(self, name: str, script: str, cwd: Optional[str] = None, 
              env: Optional[Dict] = None, interpreter: str = None,
              python_env: Optional[str] = None,
              args: List[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Start a new process.
        
        Args:
            name: Process name
            script: Script or command to run
            cwd: Working directory
            env: Environment variables
            interpreter: Interpreter to use (python3, node, bash, etc.)
            python_env: Python environment (virtualenv path, conda env name, or python executable)
            args: Additional arguments
            
        Returns:
            Dictionary with start status
        """
        if name in self.processes and self._is_running(name):
            return {'status': 'error', 'message': f'Process {name} already running', 'success': False}

        # Resolve Python environment if specified
        if python_env:
            python_executable = self._resolve_python_env(python_env)
            interpreter = python_executable
        elif interpreter is None:
            interpreter = 'python3'

        # Prepare command
        if interpreter.endswith('python') or interpreter.endswith('python3') or 'python' in os.path.basename(interpreter):
            cmd = [interpreter, '-u', script]  # -u for unbuffered output
        elif interpreter == 'bash' or interpreter == 'sh':
            cmd = [interpreter, '-c', script]
        else:
            cmd = [interpreter, script]
        
        if args:
            cmd.extend(args)

        # Prepare environment
        process_env = os.environ.copy()
        if env:
            process_env.update(env)

        # Set working directory
        work_dir = cwd or os.getcwd()

        # Prepare log files
        stdout_log = self.logs_dir / f"{name}.out.log"
        stderr_log = self.logs_dir / f"{name}.err.log"

        try:
            # Start process
            with open(stdout_log, 'a') as out, open(stderr_log, 'a') as err:
                process = subprocess.Popen(
                    cmd,
                    cwd=work_dir,
                    env=process_env,
                    stdout=out,
                    stderr=err,
                    start_new_session=True  # Detach from parent
                )

            # Store process info
            self.processes[name] = {
                'name': name,
                'pid': process.pid,
                'script': script,
                'cwd': work_dir,
                'interpreter': interpreter,
                'python_env': python_env,
                'started_at': datetime.now().isoformat(),
                'restarts': 0,
                'status': 'online',
                'stdout_log': str(stdout_log),
                'stderr_log': str(stderr_log)
            }
            self._save_processes()

            return {
                'status': 'started',
                'name': name,
                'pid': process.pid,
                'python_env': python_env,
                'interpreter': interpreter,
                'success': True
            }

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'success': False
            }

    def stop(self, name: str) -> Dict[str, Any]:
        """Stop a running process."""
        if name not in self.processes:
            return {'status': 'error', 'message': f'Process {name} not found', 'success': False}

        proc_info = self.processes[name]
        pid = proc_info['pid']

        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=10)
            proc_info['status'] = 'stopped'
            self._save_processes()
            return {'status': 'stopped', 'name': name, 'success': True}
        except psutil.NoSuchProcess:
            proc_info['status'] = 'stopped'
            self._save_processes()
            return {'status': 'stopped', 'name': name, 'message': 'Process already stopped', 'success': True}
        except psutil.TimeoutExpired:
            # Force kill if terminate doesn't work
            try:
                process.kill()
                proc_info['status'] = 'stopped'
                self._save_processes()
                return {'status': 'killed', 'name': name, 'success': True}
            except Exception as e:
                return {'status': 'error', 'message': str(e), 'success': False}
        except Exception as e:
            return {'status': 'error', 'message': str(e), 'success': False}

    def restart(self, name: str) -> Dict[str, Any]:
        """Restart a process."""
        if name not in self.processes:
            return {'status': 'error', 'message': f'Process {name} not found', 'success': False}

        proc_info = self.processes[name]
        
        # Stop the process
        stop_result = self.stop(name)
        if not stop_result['success']:
            return stop_result

        # Wait a bit
        time.sleep(1)

        # Restart with original parameters
        proc_info['restarts'] += 1
        self._save_processes()

        return self.start(
            name=name,
            script=proc_info['script'],
            cwd=proc_info['cwd'],
            interpreter=proc_info['interpreter'],
            python_env=proc_info.get('python_env')
        )

    def delete(self, name: str) -> Dict[str, Any]:
        """Delete a process from registry."""
        if name not in self.processes:
            return {'status': 'error', 'message': f'Process {name} not found', 'success': False}

        # Stop if running
        if self._is_running(name):
            self.stop(name)

        # Remove from registry
        del self.processes[name]
        self._save_processes()

        return {'status': 'deleted', 'name': name, 'success': True}

    def list(self) -> List[Dict[str, Any]]:
        """List all processes."""
        result = []
        for name, proc_info in self.processes.items():
            info = proc_info.copy()
            info['running'] = self._is_running(name)
            
            if info['running']:
                try:
                    process = psutil.Process(proc_info['pid'])
                    info['cpu'] = process.cpu_percent(interval=0.1)
                    info['memory'] = process.memory_info().rss / 1024 / 1024  # MB
                    info['uptime'] = time.time() - process.create_time()
                except:
                    info['cpu'] = 0
                    info['memory'] = 0
                    info['uptime'] = 0
            else:
                info['status'] = 'stopped'
                info['cpu'] = 0
                info['memory'] = 0
                info['uptime'] = 0

            result.append(info)
        return result

    def logs(self, name: str, lines: int = 100, follow: bool = False, 
             stderr: bool = False) -> str:
        """Get process logs."""
        if name not in self.processes:
            return f"Process {name} not found"

        proc_info = self.processes[name]
        log_file = proc_info['stderr_log'] if stderr else proc_info['stdout_log']

        if not os.path.exists(log_file):
            return "No logs available"

        if follow:
            # Tail -f equivalent
            os.system(f"tail -f {log_file}")
            return ""
        else:
            # Read last N lines
            try:
                with open(log_file, 'r') as f:
                    all_lines = f.readlines()
                    return ''.join(all_lines[-lines:])
            except Exception as e:
                return f"Error reading logs: {e}"

    def _is_running(self, name: str) -> bool:
        """Check if a process is running."""
        if name not in self.processes:
            return False

        pid = self.processes[name]['pid']
        try:
            process = psutil.Process(pid)
            return process.is_running()
        except psutil.NoSuchProcess:
            return False

    def kill_all(self) -> Dict[str, Any]:
        """Stop all processes."""
        results = []
        for name in list(self.processes.keys()):
            result = self.stop(name)
            results.append(result)
        return {'status': 'all_stopped', 'results': results, 'success': True}

    def save(self) -> Dict[str, Any]:
        """Save current process list (already auto-saved)."""
        self._save_processes()
        return {'status': 'saved', 'success': True}

    def resurrect(self) -> Dict[str, Any]:
        """Restart all previously running processes."""
        results = []
        for name, proc_info in self.processes.items():
            if proc_info.get('status') == 'online':
                result = self.start(
                    name=name,
                    script=proc_info['script'],
                    cwd=proc_info['cwd'],
                    interpreter=proc_info['interpreter'],
                    python_env=proc_info.get('python_env')
                )
                results.append(result)
        return {'status': 'resurrected', 'results': results, 'success': True}

    def describe(self, name: str) -> Dict[str, Any]:
        """Get detailed info about a process."""
        if name not in self.processes:
            return {'status': 'error', 'message': f'Process {name} not found'}

        proc_info = self.processes[name].copy()
        proc_info['running'] = self._is_running(name)

        if proc_info['running']:
            try:
                process = psutil.Process(proc_info['pid'])
                proc_info['cpu_percent'] = process.cpu_percent(interval=0.1)
                proc_info['memory_mb'] = process.memory_info().rss / 1024 / 1024
                proc_info['num_threads'] = process.num_threads()
                proc_info['create_time'] = datetime.fromtimestamp(process.create_time()).isoformat()
            except:
                pass

        return proc_info


def main():
    """CLI interface for PyPM."""
    import argparse
    
    parser = argparse.ArgumentParser(description='PyPM - Native Python Process Manager')
    parser.add_argument('command', choices=['start', 'stop', 'restart', 'delete', 'list', 'logs', 'describe', 'kill-all', 'save', 'resurrect'])
    parser.add_argument('--name', help='Process name')
    parser.add_argument('--script', help='Script to run')
    parser.add_argument('--cwd', help='Working directory')
    parser.add_argument('--interpreter', help='Interpreter to use')
    parser.add_argument('--python-env', help='Python environment (virtualenv path, conda env, or python executable)')
    parser.add_argument('--lines', type=int, default=100, help='Number of log lines')
    parser.add_argument('--follow', '-f', action='store_true', help='Follow logs')
    
    args = parser.parse_args()
    
    pm = PyPM()
    
    if args.command == 'start':
        if not args.name or not args.script:
            print("Error: --name and --script required for start")
            sys.exit(1)
        result = pm.start(args.name, args.script, cwd=args.cwd, interpreter=args.interpreter, python_env=args.python_env)
        print(json.dumps(result, indent=2))
    
    elif args.command == 'stop':
        if not args.name:
            print("Error: --name required")
            sys.exit(1)
        result = pm.stop(args.name)
        print(json.dumps(result, indent=2))
    
    elif args.command == 'restart':
        if not args.name:
            print("Error: --name required")
            sys.exit(1)
        result = pm.restart(args.name)
        print(json.dumps(result, indent=2))
    
    elif args.command == 'delete':
        if not args.name:
            print("Error: --name required")
            sys.exit(1)
        result = pm.delete(args.name)
        print(json.dumps(result, indent=2))
    
    elif args.command == 'list':
        processes = pm.list()
        for proc in processes:
            env_info = f" [{proc.get('python_env', 'system')}]" if proc.get('python_env') else ""
            print(f"{proc['name']:20} {proc['status']:10} PID: {proc['pid']:6} CPU: {proc['cpu']:.1f}% MEM: {proc['memory']:.1f}MB{env_info}")
    
    elif args.command == 'logs':
        if not args.name:
            print("Error: --name required")
            sys.exit(1)
        logs = pm.logs(args.name, lines=args.lines, follow=args.follow)
        print(logs)
    
    elif args.command == 'describe':
        if not args.name:
            print("Error: --name required")
            sys.exit(1)
        info = pm.describe(args.name)
        print(json.dumps(info, indent=2))
    
    elif args.command == 'kill-all':
        result = pm.kill_all()
        print(json.dumps(result, indent=2))
    
    elif args.command == 'save':
        result = pm.save()
        print(json.dumps(result, indent=2))
    
    elif args.command == 'resurrect':
        result = pm.resurrect()
        print(json.dumps(result, indent=2))

    @classmethod
    def test(cls) -> Dict[str, Any]:
        """Test function for PyPM class."""
        try:
            # Create a test instance
            pm = cls(storage_path="~/.pypm_test")
            
            # Test basic functionality
            test_results = {
                "instance_created": True,
                "storage_path": str(pm.storage_path),
                "processes_loaded": isinstance(pm.processes, dict),
                "success": True
            }
            
            return test_results
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

