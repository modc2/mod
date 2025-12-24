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
import argparse
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
              args: List[str] = None, watch: bool = False,
              max_restarts: int = 10, **kwargs) -> Dict[str, Any]:
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
            watch: Watch for file changes and restart
            max_restarts: Maximum number of automatic restarts
            
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
                'restarts': self.processes.get(name, {}).get('restarts', 0),
                'max_restarts': max_restarts,
                'status': 'online',
                'watch': watch,
                'stdout_log': str(stdout_log),
                'stderr_log': str(stderr_log),
                'env': env or {},
                'args': args or []
            }
            self._save_processes()

            # Write PID file
            pid_file = self.pids_dir / f"{name}.pid"
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))

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
            proc_info['stopped_at'] = datetime.now().isoformat()
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
        if not stop_result['success'] and 'already stopped' not in stop_result.get('message', ''):
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
            python_env=proc_info.get('python_env'),
            env=proc_info.get('env'),
            args=proc_info.get('args')
        )

    def delete(self, name: str) -> Dict[str, Any]:
        """Delete a process from registry."""
        if name not in self.processes:
            return {'status': 'error', 'message': f'Process {name} not found', 'success': False}

        # Stop if running
        if self._is_running(name):
            self.stop(name)

        # Remove PID file
        pid_file = self.pids_dir / f"{name}.pid"
        if pid_file.exists():
            pid_file.unlink()

        # Remove from registry
        del self.processes[name]
        self._save_processes()

        return {'status': 'deleted', 'name': name, 'success': True}

    def list(self) -> List[Dict[str, Any]]:
        """List all processes with PM2-style output."""
        result = []
        for name, proc_info in self.processes.items():
            info = proc_info.copy()
            info['running'] = self._is_running(name)
            
            if info['running']:
                try:
                    process = psutil.Process(proc_info['pid'])
                    info['cpu'] = f"{process.cpu_percent(interval=0.1):.1f}%"
                    info['memory'] = f"{process.memory_info().rss / 1024 / 1024:.1f}MB"
                    info['uptime'] = self._format_uptime(time.time() - process.create_time())
                    info['status'] = 'online'
                except:
                    info['cpu'] = '0%'
                    info['memory'] = '0MB'
                    info['uptime'] = '0s'
            else:
                info['status'] = 'stopped'
                info['cpu'] = '0%'
                info['memory'] = '0MB'
                info['uptime'] = '0s'

            result.append(info)
        return result

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime in human readable format."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds/60)}m"
        elif seconds < 86400:
            return f"{int(seconds/3600)}h"
        else:
            return f"{int(seconds/86400)}d"

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

    def flush(self, name: str = None) -> Dict[str, Any]:
        """Flush logs for a process or all processes."""
        if name:
            if name not in self.processes:
                return {'status': 'error', 'message': f'Process {name} not found', 'success': False}
            processes_to_flush = [name]
        else:
            processes_to_flush = list(self.processes.keys())

        for proc_name in processes_to_flush:
            proc_info = self.processes[proc_name]
            for log_file in [proc_info['stdout_log'], proc_info['stderr_log']]:
                if os.path.exists(log_file):
                    open(log_file, 'w').close()

        return {'status': 'flushed', 'processes': processes_to_flush, 'success': True}

    def _is_running(self, name: str) -> bool:
        """Check if a process is running."""
        if name not in self.processes:
            return False

        pid = self.processes[name]['pid']
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
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
        return {'status': 'saved', 'file': str(self.processes_file), 'success': True}

    def resurrect(self) -> Dict[str, Any]:
        """Restart all previously running processes."""
        results = []
        for name, proc_info in self.processes.items():
            if proc_info.get('status') == 'online' and not self._is_running(name):
                result = self.start(
                    name=name,
                    script=proc_info['script'],
                    cwd=proc_info['cwd'],
                    interpreter=proc_info['interpreter'],
                    python_env=proc_info.get('python_env'),
                    env=proc_info.get('env'),
                    args=proc_info.get('args')
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
                proc_info['connections'] = len(process.connections())
                proc_info['open_files'] = len(process.open_files())
            except:
                pass

        return proc_info

    def monit(self, interval: float = 2.0):
        """Real-time monitoring dashboard like PM2 monit."""
        try:
            while True:
                os.system('clear' if os.name != 'nt' else 'cls')
                print("\033[1m" + "="*70 + "\033[0m")
                print("\033[1m PyPM - Process Monitor \033[0m".center(70))
                print("\033[1m" + "="*70 + "\033[0m")
                print()
                self._print_table()
                print()
                print(f"Press Ctrl+C to exit | Refresh: {interval}s")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nExiting monitor...")

    def _print_table(self):
        """Print PM2-style process table."""
        processes = self.list()
        if not processes:
            print("No processes running")
            return

        # Header
        header = f"{'ID':<4} {'Name':<20} {'Status':<10} {'CPU':<8} {'Memory':<10} {'Uptime':<10} {'Restarts':<8}"
        print("\033[1m" + header + "\033[0m")
        print("-" * 70)

        # Rows
        for i, proc in enumerate(processes):
            status_color = "\033[92m" if proc['status'] == 'online' else "\033[91m"
            status = f"{status_color}{proc['status']}\033[0m"
            row = f"{i:<4} {proc['name']:<20} {status:<19} {proc['cpu']:<8} {proc['memory']:<10} {proc['uptime']:<10} {proc.get('restarts', 0):<8}"
            print(row)

    def status(self) -> None:
        """Print status table (alias for list with pretty print)."""
        self._print_table()

    @classmethod
    def test(cls) -> Dict[str, Any]:
        """Test function for PyPM class."""
        try:
            import tempfile
            import shutil
            
            # Create a test instance with temp directory
            test_dir = tempfile.mkdtemp(prefix="pypm_test_")
            pm = cls(storage_path=test_dir)
            
            # Test basic functionality
            test_results = {
                "instance_created": True,
                "storage_path": str(pm.storage_path),
                "storage_exists": pm.storage_path.exists(),
                "logs_dir_exists": pm.logs_dir.exists(),
                "pids_dir_exists": pm.pids_dir.exists(),
                "processes_loaded": isinstance(pm.processes, dict),
                "success": True
            }
            
            # Cleanup
            shutil.rmtree(test_dir, ignore_errors=True)
            
            return test_results
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


def main():
    """CLI entry point - PM2-style interface."""
    parser = argparse.ArgumentParser(
        description='PyPM - Python Process Manager (PM2-like)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pypm start app.py --name myapp
  pypm stop myapp
  pypm restart myapp
  pypm list
  pypm logs myapp
  pypm monit
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Start command
    start_parser = subparsers.add_parser('start', help='Start a process')
    start_parser.add_argument('script', help='Script to run')
    start_parser.add_argument('--name', '-n', required=True, help='Process name')
    start_parser.add_argument('--interpreter', '-i', default='python3', help='Interpreter')
    start_parser.add_argument('--cwd', help='Working directory')
    start_parser.add_argument('--python-env', help='Python environment path')
    start_parser.add_argument('--watch', action='store_true', help='Watch for changes')
    start_parser.add_argument('args', nargs='*', help='Script arguments')
    
    # Stop command
    stop_parser = subparsers.add_parser('stop', help='Stop a process')
    stop_parser.add_argument('name', help='Process name')
    
    # Restart command
    restart_parser = subparsers.add_parser('restart', help='Restart a process')
    restart_parser.add_argument('name', help='Process name')
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a process')
    delete_parser.add_argument('name', help='Process name')
    
    # List command
    subparsers.add_parser('list', aliases=['ls', 'status'], help='List all processes')
    
    # Logs command
    logs_parser = subparsers.add_parser('logs', help='View process logs')
    logs_parser.add_argument('name', help='Process name')
    logs_parser.add_argument('--lines', '-n', type=int, default=100, help='Number of lines')
    logs_parser.add_argument('--follow', '-f', action='store_true', help='Follow logs')
    logs_parser.add_argument('--err', action='store_true', help='Show stderr')
    
    # Flush command
    flush_parser = subparsers.add_parser('flush', help='Flush logs')
    flush_parser.add_argument('name', nargs='?', help='Process name (optional)')
    
    # Describe command
    describe_parser = subparsers.add_parser('describe', aliases=['info'], help='Describe a process')
    describe_parser.add_argument('name', help='Process name')
    
    # Monit command
    monit_parser = subparsers.add_parser('monit', help='Real-time monitoring')
    monit_parser.add_argument('--interval', '-i', type=float, default=2.0, help='Refresh interval')
    
    # Kill all command
    subparsers.add_parser('kill', help='Stop all processes')
    
    # Save command
    subparsers.add_parser('save', help='Save process list')
    
    # Resurrect command
    subparsers.add_parser('resurrect', help='Resurrect saved processes')
    
    args = parser.parse_args()
    pm = PyPM()
    
    if args.command == 'start':
        result = pm.start(
            name=args.name,
            script=args.script,
            interpreter=args.interpreter,
            cwd=args.cwd,
            python_env=args.python_env,
            watch=args.watch,
            args=args.args if args.args else None
        )
        print(json.dumps(result, indent=2))
        
    elif args.command == 'stop':
        result = pm.stop(args.name)
        print(json.dumps(result, indent=2))
        
    elif args.command == 'restart':
        result = pm.restart(args.name)
        print(json.dumps(result, indent=2))
        
    elif args.command == 'delete':
        result = pm.delete(args.name)
        print(json.dumps(result, indent=2))
        
    elif args.command in ['list', 'ls', 'status']:
        pm.status()
        
    elif args.command == 'logs':
        logs = pm.logs(args.name, lines=args.lines, follow=args.follow, stderr=args.err)
        print(logs)
        
    elif args.command == 'flush':
        result = pm.flush(args.name)
        print(json.dumps(result, indent=2))
        
    elif args.command in ['describe', 'info']:
        result = pm.describe(args.name)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.command == 'monit':
        pm.monit(interval=args.interval)
        
    elif args.command == 'kill':
        result = pm.kill_all()
        print(json.dumps(result, indent=2))
        
    elif args.command == 'save':
        result = pm.save()
        print(json.dumps(result, indent=2))
        
    elif args.command == 'resurrect':
        result = pm.resurrect()
        print(json.dumps(result, indent=2))
        
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
