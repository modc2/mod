import os
import pandas as pd
from typing import List, Dict, Union, Optional, Any
import mod as m
import subprocess
import json
from datetime import datetime

class PM2:
    """
    A mod for interacting with PM2 process manager.
    Manages server processes with PM2, providing start, stop, restart, and monitoring capabilities.
    """

    def __init__(self,  
                mod='mod',
                path='~/.mod/pm2',
                scripts_path='~/.mod/scripts/serve',
                registry = 'server.namespace',
                **kwargs):
        self.mod = mod
        self.store = m.mod('store')(path)
        self.registry = m.mod(registry)(path)
        self.scripts_path = os.path.expanduser(scripts_path)
        os.makedirs(self.scripts_path, exist_ok=True)

    def forward(self,  
                mod: str = 'api', 
                params: Optional[dict] = None,
                key: Optional[str] = None,
                port: Optional[int] = None,
                cwd: Optional[str] = None,
                env: Optional[dict] = None,
                interpreter: str = 'python3',
                name: Optional[str] = None,
                **kwargs):
        """
        Start a mod as a PM2 process server.
        """
        params = params or {}
        name = name or mod
        
        # Create the Python script that will be executed
        script_path = self.create_script(
            mod=mod, 
            port=port,
            key=key,
            extra_params=params
        )

        print(m.get_text(script_path), 'is the script path')
        
        dirpath = m.dirpath(mod)
        cwd = cwd or dirpath
        self.registry.reg(name, f'http://0.0.0.0:{port}')
        self.kill(name, remove_script=False) if self.exists(name) else None
        self.rm_logs(name)
        return self.start_script(
            name=name, 
            script_path=script_path, 
            cwd=cwd, 
            env=env, 
            interpreter=interpreter
        )
    def rm_logs(self, name: str) -> Dict[str, str]:
        """Remove PM2 log files for a given process name."""
        logs = self.pm2_logs_path(name)
        removed_files = []
        for log_type, log_path in logs.items():
            if os.path.exists(log_path):
                os.remove(log_path)
                removed_files.append(log_path)
                m.print(f"Removed {log_type} log: {log_path}", color='yellow')
            else:
                m.print(f"{log_type} log not found: {log_path}", color='red')
        return {
            'status': 'logs_removed' if removed_files else 'no_logs_found',
            'name': name,
            'removed_files': removed_files
        }
    
    def pm2_logs_path(self, name: str) -> Dict[str, str]:
        """Get the paths to PM2 log files for a given process name."""
        pm2_home = os.path.expanduser(os.environ.get('PM2_HOME', '~/.pm2'))
        logs_dir = os.path.join(pm2_home, 'logs')
        return {
            'out_log': os.path.join(logs_dir, f'{name}-out.log'),
            'error_log': os.path.join(logs_dir, f'{name}-error.log')
        }

    def create_script(self, 
                           mod: str, 
                           port: Optional[int] = None,
                           key: Optional[str] = None,
                           extra_params: Optional[dict] = None) -> str:
        """
        Create a Python script file that starts the server.
        
        Returns:
            Path to the created script file
        """
        script_path = os.path.join(self.scripts_path, f'{mod}_serve.py')
        
        # Build the params for the serve call
        serve_kwargs = ['remote=False']  # Critical: prevent recursion
        
        if port is not None:
            serve_kwargs.append(f'port={port}')
        if key is not None:
            serve_kwargs.append(f'key={repr(key)}')
        if extra_params:
            for k, v in extra_params.items():
                if k not in ['remote', 'daemon', 'd', 'mod']:
                    if isinstance(v, str):
                        serve_kwargs.append(f'{k}={repr(v)}')
                    else:
                        serve_kwargs.append(f'{k}={v}')
        
        kwargs_str = ', '.join(serve_kwargs)
        
        script_content = f'''
"""
Auto-generated serve script
Module: {mod}
Generated: {datetime.now().isoformat()}
"""
import mod as m

if __name__ == "__main__":
    print("Starting server for {mod}...")
    m.serve({repr(mod)}, {kwargs_str})
'''
        
        # Write the script
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        # Make it executable
        os.chmod(script_path, 0o755)
        
        m.print(f"Created serve script: {script_path}", color='green')
        m.print(f"Script content:", color='cyan')
        print(script_content)
        
        return script_path

    def start_script(self, 
                    name: str, 
                    script_path: str, 
                    cwd: str = None, 
                    env: Dict = None, 
                    interpreter: str = 'python3') -> Dict[str, Any]:
        """
        Start a Python script with PM2.
        
        Args:
            name: Process name in PM2
            script_path: Absolute path to the Python script
            cwd: Working directory
            env: Environment variables
            interpreter: Python interpreter to use
            
        Returns:
            Dictionary with start status
        """
        if self.exists(name):
            m.print(f"Process {name} already exists, deleting first...", color='yellow')
            self.kill(name, remove_script=False)
        
        # Verify script exists
        if not os.path.exists(script_path):
            return {
                'status': 'error',
                'name': name,
                'error': f'Script not found: {script_path}',
                'success': False
            }
        
        # Build PM2 command as a list for proper escaping
        cmd_parts = [
            'pm2', 'start',
            script_path,
            '--name', name,
            '--interpreter', interpreter
        ]
        
        if cwd:
            cmd_parts.extend(['--cwd', cwd])
        
        cmd_str = ' '.join(cmd_parts)
        m.print(f"Running: {cmd_str}", color='cyan')
        
        # Use subprocess for better control
        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                env={**os.environ, **(env or {})}
            )
            
            success = result.returncode == 0
            
            if success:
                m.print(f"Successfully started {name}", color='green')
            else:
                m.print(f"Failed to start {name}: {result.stderr}", color='red')
            
            return {
                'status': 'started' if success else 'error',
                'name': name,
                'command': cmd_str,
                'script': script_path,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'success': success
            }
        except Exception as e:
            m.print(f"Exception starting process: {e}", color='red')
            return {
                'status': 'error',
                'name': name,
                'error': str(e),
                'success': False
            }

    start = forward  # Alias start to forward for serving modules

    def stop(self, name: str) -> Dict[str, str]:
        """Stop a PM2 process."""
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        result = subprocess.run(['pm2', 'stop', name], capture_output=True, text=True)
        return {
            'status': 'stopped' if result.returncode == 0 else 'error',
            'name': name,
            'success': result.returncode == 0
        }

    def restart(self, name: str) -> Dict[str, str]:
        """Restart a PM2 process."""
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        result = subprocess.run(['pm2', 'restart', name], capture_output=True, text=True)
        return {
            'status': 'restarted' if result.returncode == 0 else 'error',
            'name': name,
            'success': result.returncode == 0
        }

    def delete(self, name: str) -> Dict[str, str]:
        """Delete a PM2 process."""
        return self.kill(name)
    
    def namespace(self, search: Optional[str] = None, **kwargs) -> Dict[str, str]:
        """Get the namespace of registered servers."""
        return self.registry.namespace(search=search, **kwargs)

    def kill(self, name: str, remove_script: bool = True, prefix: bool = False) -> Dict[str, str]:
        """Kill and remove a PM2 process. If prefix=True, kill all processes matching the prefix."""
        if prefix:
            return self.kill_prefix(name, remove_script=remove_script)
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}

        try:
            result = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            subprocess.run(['pm2', 'delete', name, '--force'], capture_output=True, text=True, timeout=10)
            result = type('Result', (), {'returncode': 0})()
        success = result.returncode == 0
        self.registry.dereg(name)
        # Remove generated scripts
        if remove_script:
            for suffix in ['_serve.py', '_cmd.sh']:
                script_path = os.path.join(self.scripts_path, f'{name}{suffix}')
                if os.path.exists(script_path):
                    os.remove(script_path)
                    m.print(f"Removed script: {script_path}", color='yellow')
        self.rm_logs(name)

        return {
            'status': 'deleted' if success else 'error',
            'name': name,
            'success': success
        }

    def kill_prefix(self, prefix: str, remove_script: bool = True) -> Dict[str, Any]:
        """Kill all PM2 processes whose name starts with the given prefix."""
        matches = [s for s in self.servers() if s.startswith(prefix)]
        if not matches:
            return {'status': 'no_matches', 'prefix': prefix, 'killed': [], 'success': True}
        killed = []
        errors = []
        for name in matches:
            result = self.kill(name, remove_script=remove_script)
            if result.get('success'):
                killed.append(name)
            else:
                errors.append(name)
        m.print(f"Killed {len(killed)}/{len(matches)} processes with prefix '{prefix}'", color='green')
        return {
            'status': 'killed' if not errors else 'partial',
            'prefix': prefix,
            'killed': killed,
            'errors': errors,
            'success': len(errors) == 0
        }

    def kill_all(self, remove_scripts: bool = True) -> Dict[str, str]:
        """Kill all PM2 processes."""
        servers = self.servers() if remove_scripts else []
        
        result = subprocess.run(['pm2', 'delete', 'all'], capture_output=True, text=True)
        success = result.returncode == 0
        
        if remove_scripts:
            for name in servers:
                for suffix in ['_serve.py', '_cmd.sh']:
                    script_path = os.path.join(self.scripts_path, f'{name}{suffix}')
                    if os.path.exists(script_path):
                        os.remove(script_path)
        
        return {
            'status': 'all_processes_killed' if success else 'error',
            'success': success
        }

    def servers(self, search=None, **kwargs) -> List[str]:
        """List all PM2 server processes."""
        servers = self.ps()
        if search is not None:
            servers = [s for s in servers if search in s]
        return sorted(list(set(servers)))

    def exists(self, name: str) -> bool:
        """Check if a PM2 process exists."""
        return name in self.ps()

    def ps(self) -> List[str]:
        """List all running PM2 processes."""
        try:
            result = subprocess.run(
                ['pm2', 'jlist'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return []
            processes = json.loads(result.stdout)
            return [p['name'] for p in processes]
        except Exception as e:
            m.print(f"Error listing processes: {e}", color='red')
            return []

    def ls(self, include_logs: bool = True, log_lines: int = 50) -> List[Dict[str, Any]]:
        """
        List all PM2 processes with their status and optionally their logs.

        Args:
            include_logs: Whether to include recent logs for each process
            log_lines: Number of log lines to include per process

        Returns:
            List of dictionaries containing process info and logs
        """
        try:
            result = subprocess.run(
                ['pm2', 'jlist'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return []

            processes = json.loads(result.stdout)
            process_list = []

            for proc in processes:
                proc_info = {
                    'name': proc.get('name', ''),
                    'pid': proc.get('pid', 0),
                    'status': proc.get('pm2_env', {}).get('status', ''),
                    'cpu': proc.get('monit', {}).get('cpu', 0),
                    'memory': proc.get('monit', {}).get('memory', 0),
                    'uptime': proc.get('pm2_env', {}).get('pm_uptime', 0),
                    'restarts': proc.get('pm2_env', {}).get('restart_time', 0)
                }

                # Include logs if requested
                if include_logs:
                    logs = self.logs(proc_info['name'], lines=log_lines, follow=False)
                    proc_info['logs'] = logs

                process_list.append(proc_info)

            return process_list

        except Exception as e:
            m.print(f"Error listing processes: {e}", color='red')
            return []

    def logs(self, name: str, lines: int = 100, follow: bool = False, f=None, blocking: bool = False) -> Union[str, subprocess.Popen]:
        """
        Get PM2 process logs by reading log files directly.

        Args:
            name: Process name
            lines: Number of lines to show (when not following)
            follow: Whether to follow logs in real-time
            f: Alias for follow parameter
            blocking: Whether to block execution (default False for non-blocking)

        Returns:
            If blocking=False and follow=True: subprocess.Popen object for managing the process
            If blocking=True or follow=False: String output of logs
        """
        follow = f if f is not None else follow

        # PM2 log files location
        pm2_home = os.path.expanduser(os.environ.get('PM2_HOME', '~/.pm2'))
        logs_dir = os.path.join(pm2_home, 'logs')
        out_log = os.path.join(logs_dir, f'{name}-out.log')
        error_log = os.path.join(logs_dir, f'{name}-error.log')

        if follow:
            # Use tail -f to follow both log files
            if blocking:
                # Blocking mode - old behavior
                return os.system(f'tail -f {out_log} {error_log}')
            else:
                # Non-blocking mode - return Popen process
                m.print(f"Starting non-blocking log stream for {name}. Use .terminate() on returned process to stop.", color='cyan')
                process = subprocess.Popen(['tail', '-f', out_log, error_log])
                return process
        else:
            # Read log files directly
            logs_output = []

            # Read error log
            if os.path.exists(error_log):
                try:
                    result = subprocess.run(
                        ['tail', '-n', str(lines), error_log],
                        capture_output=True,
                        text=True
                    )
                    if result.stdout:
                        logs_output.append(f"==> {name} error log <==")
                        logs_output.append(result.stdout)
                except Exception as e:
                    logs_output.append(f"Error reading error log: {e}")

            # Read output log
            if os.path.exists(out_log):
                try:
                    result = subprocess.run(
                        ['tail', '-n', str(lines), out_log],
                        capture_output=True,
                        text=True
                    )
                    if result.stdout:
                        logs_output.append(f"==> {name} output log <==")
                        logs_output.append(result.stdout)
                except Exception as e:
                    logs_output.append(f"Error reading output log: {e}")

            if not logs_output:
                return f"No log files found for {name} in {logs_dir}"

            return '\n'.join(logs_output)

    def stats(self, max_age=60, update=False) -> pd.DataFrame:
        """Get PM2 process statistics."""
        path = 'pm2_stats.json'
        stats = self.store.get(path, [], max_age=max_age, update=update)
        
        if len(stats) == 0 or update:
            try:
                result = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True)
                processes = json.loads(result.stdout)
                stats = []
                
                for proc in processes:
                    row = {
                        'name': proc.get('name', ''),
                        'pid': proc.get('pid', 0),
                        'status': proc.get('pm2_env', {}).get('status', ''),
                        'cpu': proc.get('monit', {}).get('cpu', 0),
                        'memory': proc.get('monit', {}).get('memory', 0),
                        'uptime': proc.get('pm2_env', {}).get('pm_uptime', 0),
                        'restarts': proc.get('pm2_env', {}).get('restart_time', 0)
                    }
                    stats.append(row)
                
                self.store.put(path, stats)
            except Exception as e:
                m.print(f"Error getting stats: {e}", color='red')
                return pd.DataFrame()
        
        return pd.DataFrame(stats)
    
    
    def save(self):
        """Save PM2 process list for resurrection."""
        result = subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'status': 'saved', 'success': result.returncode == 0}

    def resurrect(self):
        """Resurrect previously saved PM2 processes."""
        result = subprocess.run(['pm2', 'resurrect'], capture_output=True, text=True)
        return {'status': 'resurrected', 'success': result.returncode == 0}

    def flush(self, name: str = None):
        """Flush PM2 logs."""
        cmd = ['pm2', 'flush']
        if name:
            cmd.append(name)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return {'status': 'flushed', 'success': result.returncode == 0}

    def monit(self):
        """Open PM2 monitoring dashboard."""
        return os.system('pm2 monit')