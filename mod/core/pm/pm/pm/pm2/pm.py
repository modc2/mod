# start of file
import os
import pandas as pd
from typing import List, Dict, Union, Optional, Any
import mod as m

import subprocess
import json
from datetime import datetime
import yaml

class PM:
    """
    A mod for interacting with PM2 process manager.
    Manages server processes with PM2, providing start, stop, restart, and monitoring capabilities.
    """

    def __init__(self,  
                mod='mod',
                path='~/.mod/server', 
                **kwargs):
        """
        Initialize PM2 manager.
        
        Args:
            mod: Module name
            path: Storage path for PM2 data
        """
        self.mod = mod
        self.store = m.mod('store')(path)

    def forward(self,  
                mod: str = 'api', 
                params: Optional[dict] = None,
                key: Optional[str] = None,
                cwd: Optional[str] = None,
                env: Optional[dict] = None,
                interpreter: str = 'python3',
                name: Optional[str] = None,
                **kwargs):
        """
        Start a mod as a PM2 process server.
        
        Args:
            mod: Module name to serve
            params: Parameters to pass to the server
            key: Server key identifier
            cwd: Working directory
            env: Environment variables
            interpreter: Python interpreter to use
            name: Process name in PM2
            
        Returns:
            Dictionary with server start status
        """
        params = params or {}
        name = name or mod
        params.update({'key': key or mod, 'remote': False, 'mod': mod})
        cmd = f"m serve {self.params2cmd(params)}"
        dirpath = m.dirpath(mod)
        cwd = cwd or dirpath
        
        return self.start(name=name, script=cmd, cwd=cwd, env=env, interpreter=interpreter)

    def start(self, name: str, script: str = None, cwd: str = None, env: Dict = None, interpreter: str = 'python3', **kwargs) -> Dict[str, Any]:
        """
        Start a process with PM2.
        
        Args:
            name: Process name
            script: Script/command to run
            cwd: Working directory
            env: Environment variables
            interpreter: Interpreter to use
            
        Returns:
            Dictionary with start status and details
        """
        if self.exists(name):
            m.print(f"Process {name} already exists, restarting...", color='yellow')
            return self.restart(name)
        
        cmd = ['pm2', 'start']
        
        if script:
            cmd.append(script)
        
        cmd.extend(['--name', name])
        
        if interpreter:
            cmd.extend(['--interpreter', interpreter])
        
        if cwd:
            cmd.extend(['--cwd', cwd])
        
        if env:
            for k, v in env.items():
                cmd.extend(['--env', f'{k}={v}'])
        
        cmd_str = ' '.join(cmd)
        m.print(f"Starting PM2 process: {cmd_str}", color='cyan')
        result = os.system(cmd_str)
        
        return {
            'status': 'started' if result == 0 else 'error',
            'name': name,
            'command': cmd_str,
            'success': result == 0
        }

    def stop(self, name: str) -> Dict[str, str]:
        """
        Stop a PM2 process.
        
        Args:
            name: Process name to stop
            
        Returns:
            Dictionary with stop status
        """
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        try:
            result = os.system(f'pm2 stop {name}')
            return {
                'status': 'stopped' if result == 0 else 'error',
                'name': name,
                'success': result == 0
            }
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e), 'success': False}

    def restart(self, name: str) -> Dict[str, str]:
        """
        Restart a PM2 process.
        
        Args:
            name: Process name to restart
            
        Returns:
            Dictionary with restart status
        """
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        try:
            result = os.system(f'pm2 restart {name}')
            return {
                'status': 'restarted' if result == 0 else 'error',
                'name': name,
                'success': result == 0
            }
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e), 'success': False}

    def delete(self, name: str) -> Dict[str, str]:
        """
        Delete a PM2 process.
        
        Args:
            name: Process name to delete
            
        Returns:
            Dictionary with delete status
        """
        return self.kill(name)

    def kill(self, name: str) -> Dict[str, str]:
        """
        Kill and remove a PM2 process.
        
        Args:
            name: Process name to kill
            
        Returns:
            Dictionary with kill status
        """
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        try:
            result = os.system(f'pm2 delete {name}')
            return {
                'status': 'deleted' if result == 0 else 'error',
                'name': name,
                'success': result == 0
            }
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e), 'success': False}

    def kill_all(self) -> Dict[str, str]:
        """
        Kill all PM2 processes.
        
        Returns:
            Dictionary with kill all status
        """
        try:
            result = os.system('pm2 delete all')
            return {
                'status': 'all_processes_killed' if result == 0 else 'error',
                'success': result == 0
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'success': False}

    def servers(self, search=None, **kwargs):
        """
        List all PM2 server processes.
        
        Args:
            search: Optional search filter for server names
            
        Returns:
            List of server names
        """
        servers = self.ps()
        if search != None:
            servers = [s for s in servers if search in s]
        servers = sorted(list(set(servers)))
        return servers

    def server_exists(self, name):
        """
        Check if a server exists.
        
        Args:
            name: Server name
            
        Returns:
            Boolean indicating existence
        """
        return name in self.servers()

    def exists(self, name: str) -> bool:
        """
        Check if a PM2 process exists.
        
        Args:
            name: Process name
            
        Returns:
            Boolean indicating existence
        """
        return name in self.servers()

    def ps(self) -> List[str]:
        """
        List all running PM2 processes.
        
        Returns:
            List of process names
        """
        try:
            output = m.cmd('pm2 jlist', verbose=False)
            processes = json.loads(output)
            return [p['name'] for p in processes]
        except Exception as e:
            m.print(f"Error listing processes: {e}", color='red')
            return []

    def logs(self, name: str, lines: int = 100, follow: bool = False, f=None) -> str:
        """
        Get PM2 process logs.
        
        Args:
            name: Process name
            lines: Number of log lines to retrieve
            follow: Whether to follow logs in real-time
            f: Alias for follow
            
        Returns:
            Log output string or system call result
        """
        follow = f if f is not None else follow
        cmd = f'pm2 logs {name}'
        
        if lines:
            cmd += f' --lines {lines}'
        
        if follow:
            return os.system(cmd)
        else:
            return m.cmd(cmd, verbose=False)

    def stats(self, max_age=60, update=False) -> pd.DataFrame:
        """
        Get PM2 process statistics.
        
        Args:
            max_age: Maximum age of cached stats in seconds
            update: Force update of stats
            
        Returns:
            DataFrame with process statistics
        """
        path = 'pm2_stats.json'
        stats = self.store.get(path, [], max_age=max_age, update=update)
        
        if len(stats) == 0 or update:
            try:
                output = m.cmd('pm2 jlist', verbose=False)
                processes = json.loads(output)
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

    def process_info(self, name):
        """
        Get info about a specific PM2 process.
        
        Args:
            name: Process name
            
        Returns:
            Dictionary with process information
        """
        stats = self.stats()
        if 'name' in stats.columns:
            info = stats[stats['name'] == name]
            if len(info) > 0:
                return info.iloc[0].to_dict()
        return {}

    def params2cmd(self, params: Dict[str, Any]) -> str:
        """
        Convert a dictionary of parameters to a command string.
        
        Args:
            params: Dictionary of parameters
            
        Returns:
            Command string with key=value pairs
        """
        for k, v in params.items():
            if isinstance(v, bool):
                params[k] = '1' if v else '0'
            elif isinstance(v, list):
                params[k] = ','.join(map(str, v))
            elif isinstance(v, dict):
                params[k] = json.dumps(v)
            elif v is None:
                params[k] = ''
        return ' '.join([f"{k}={v}" for k, v in params.items() if v is not None])

    def namespace(self, search=None, max_age=None, update=False, **kwargs) -> dict:
        """
        Get namespace mapping of PM2 processes.
        
        Args:
            search: Optional search filter
            max_age: Maximum age of cached namespace
            update: Force update of namespace
            
        Returns:
            Dictionary mapping process names to namespaces
        """
        path = self.store.get_path('namespace')
        namespace = m.get(path, None, max_age=max_age, update=update)
        
        if namespace == None:
            processes = self.servers(search=search)
            namespace = {proc: f'pm2:{proc}' for proc in processes}
            self.store.put(path, namespace)
        
        return namespace

    def sync(self):
        """
        Sync PM2 process statistics.
        
        Returns:
            Dictionary with sync status
        """
        self.stats(update=True)
        return {'status': 'synced', 'success': True}

    def save(self):
        """
        Save PM2 process list for resurrection.
        
        Returns:
            Dictionary with save status
        """
        result = os.system('pm2 save')
        return {'status': 'saved', 'success': result == 0}

    def resurrect(self):
        """
        Resurrect previously saved PM2 processes.
        
        Returns:
            Dictionary with resurrect status
        """
        result = os.system('pm2 resurrect')
        return {'status': 'resurrected', 'success': result == 0}

    def flush(self, name: str = None):
        """
        Flush PM2 logs.
        
        Args:
            name: Optional process name to flush logs for
            
        Returns:
            Dictionary with flush status
        """
        if name:
            result = os.system(f'pm2 flush {name}')
        else:
            result = os.system('pm2 flush')
        return {'status': 'flushed', 'success': result == 0}

    def monit(self):
        """
        Open PM2 monitoring dashboard.
        
        Returns:
            System call result
        """
        return os.system('pm2 monit')

    def reload(self, name: str) -> Dict[str, Any]:
        """
        Reload a PM2 process with zero-downtime.
        
        Args:
            name: Process name to reload
            
        Returns:
            Dictionary with reload status
        """
        if not self.exists(name):
            return {'status': 'not_found', 'name': name, 'success': False}
        
        try:
            result = os.system(f'pm2 reload {name}')
            return {
                'status': 'reloaded' if result == 0 else 'error',
                'name': name,
                'success': result == 0
            }
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e), 'success': False}

    def describe(self, name: str) -> Dict[str, Any]:
        """
        Get detailed description of a PM2 process.
        
        Args:
            name: Process name
            
        Returns:
            Dictionary with process details
        """
        try:
            output = m.cmd(f'pm2 describe {name}', verbose=False)
            return {'status': 'success', 'name': name, 'output': output}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def env(self, name: str) -> Dict[str, Any]:
        """
        Get environment variables for a PM2 process.
        
        Args:
            name: Process name
            
        Returns:
            Dictionary with environment variables
        """
        try:
            output = m.cmd('pm2 jlist', verbose=False)
            processes = json.loads(output)
            for proc in processes:
                if proc.get('name') == name:
                    return proc.get('pm2_env', {}).get('env', {})
            return {}
        except Exception as e:
            m.print(f"Error getting env: {e}", color='red')
            return {}
