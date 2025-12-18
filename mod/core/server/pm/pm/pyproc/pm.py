import os
import subprocess
import signal
import psutil
import json
from typing import List, Dict, Union, Optional, Any
from datetime import datetime
import mod as m

class PM:
    """
    Process Manager for Python processes running in the background.
    Similar to Docker PM but manages native Python processes.
    """

    def __init__(self, 
                 mod='mod',
                 path='~/.mod/server/pyproc',
                 **kwargs):
        self.mod = mod
        self.store = m.mod('store')(path)
        self.processes_file = os.path.expanduser(os.path.join(path, 'processes.json'))
        os.makedirs(os.path.expanduser(path), exist_ok=True)

    def forward(self,
                mod: str = 'api',
                port: Optional[int] = None,
                params: Optional[dict] = None,
                key: Optional[str] = None,
                daemon: bool = True,
                cwd: Optional[str] = None,
                env: Optional[dict] = None,
                **kwargs):
        """
        Run a mod as a background Python process.
        """
        params = params or {}
        port = port or m.free_port()
        params.update({'port': port, 'key': key or mod, 'remote': False, 'mod': mod})
        cmd = f"m serve {self.params2cmd(params)}"
        dirpath = m.dirpath(mod)
        cwd = cwd or dirpath
        
        return self.run(name=mod, cmd=cmd, cwd=cwd, env=env, daemon=daemon)

    def run(self,
            name: str = "mod",
            cmd: str = None,
            cwd: Optional[str] = None,
            env: Optional[Dict] = None,
            daemon: bool = True,
            **kwargs) -> Dict:
        """
        Start a background Python process.
        """
        if self.server_exists(name):
            self.kill(name)
        
        cwd = cwd or os.getcwd()
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        
        if daemon:
            # Start process in background
            process = subprocess.Popen(
                cmd,
                shell=True,
                cwd=cwd,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            
            # Store process info
            self._save_process(name, process.pid, cmd, cwd)
            
            return {
                'status': 'started',
                'name': name,
                'pid': process.pid,
                'cmd': cmd
            }
        else:
            result = subprocess.run(cmd, shell=True, cwd=cwd, env=process_env)
            return {'status': 'completed', 'name': name, 'returncode': result.returncode}

    def _save_process(self, name: str, pid: int, cmd: str, cwd: str):
        """Save process information to storage."""
        processes = self._load_processes()
        processes[name] = {
            'pid': pid,
            'cmd': cmd,
            'cwd': cwd,
            'started_at': datetime.now().isoformat()
        }
        with open(self.processes_file, 'w') as f:
            json.dump(processes, f, indent=2)

    def _load_processes(self) -> Dict:
        """Load process information from storage."""
        if os.path.exists(self.processes_file):
            with open(self.processes_file, 'r') as f:
                return json.load(f)
        return {}

    def _remove_process(self, name: str):
        """Remove process from storage."""
        processes = self._load_processes()
        if name in processes:
            del processes[name]
            with open(self.processes_file, 'w') as f:
                json.dump(processes, f, indent=2)

    def servers(self, search=None, **kwargs) -> List[str]:
        """List all running processes."""
        processes = self._load_processes()
        servers = []
        
        for name, info in processes.items():
            if self._is_process_running(info['pid']):
                servers.append(name)
            else:
                # Clean up dead process
                self._remove_process(name)
        
        if search:
            servers = [s for s in servers if search in s]
        
        return sorted(servers)

    def server_exists(self, name: str) -> bool:
        """Check if a server process exists."""
        return name in self.servers()

    def exists(self, name: str) -> bool:
        """Check if a process exists."""
        return self.server_exists(name)

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is running by PID."""
        try:
            return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
        except:
            return False

    def kill(self, name: str) -> Dict[str, str]:
        """Kill a process."""
        processes = self._load_processes()
        
        if name not in processes:
            return {'status': 'not_found', 'name': name}
        
        pid = processes[name]['pid']
        
        try:
            if self._is_process_running(pid):
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=5)
            
            self._remove_process(name)
            return {'status': 'killed', 'name': name}
        except psutil.TimeoutExpired:
            try:
                proc.kill()
                self._remove_process(name)
                return {'status': 'force_killed', 'name': name}
            except Exception as e:
                return {'status': 'error', 'name': name, 'error': str(e)}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def kill_all(self) -> Dict[str, str]:
        """Kill all processes."""
        try:
            for name in self.servers():
                self.kill(name)
            return {'status': 'all_processes_killed'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def stop(self, name: str) -> Dict[str, str]:
        """Stop a process (alias for kill)."""
        return self.kill(name)

    def restart(self, name: str) -> Dict[str, str]:
        """Restart a process."""
        processes = self._load_processes()
        
        if name not in processes:
            return {'status': 'not_found', 'name': name}
        
        info = processes[name]
        self.kill(name)
        
        return self.run(name=name, cmd=info['cmd'], cwd=info['cwd'])

    def logs(self, name: str, tail: int = 100, follow: bool = False, f=None) -> str:
        """Get process logs."""
        # For background processes, logs would need to be redirected to files
        # This is a placeholder implementation
        return f"Logs for {name} (not implemented for pyproc yet)"

    def stats(self, max_age=60, update=False) -> List[Dict]:
        """Get process statistics."""
        processes = self._load_processes()
        stats = []
        
        for name, info in processes.items():
            if self._is_process_running(info['pid']):
                try:
                    proc = psutil.Process(info['pid'])
                    stats.append({
                        'name': name,
                        'pid': info['pid'],
                        'cpu': proc.cpu_percent(interval=0.1),
                        'memory': proc.memory_info().rss / 1024 / 1024,  # MB
                        'status': proc.status(),
                        'started_at': info.get('started_at', '')
                    })
                except:
                    pass
        
        return stats

    def process_info(self, name: str) -> Dict:
        """Get info about a specific process."""
        stats = self.stats()
        for stat in stats:
            if stat['name'] == name:
                return stat
        return {}

    def params2cmd(self, params: Dict[str, Any]) -> str:
        """Convert parameters to command string."""
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
        """Get namespace mapping."""
        processes = self.servers(search=search)
        namespace = {proc: f'pyproc:{proc}' for proc in processes}
        return namespace

    def ps(self) -> List[str]:
        """List running processes."""
        return self.servers()

    def sync(self):
        """Sync process statistics."""
        self.stats(update=True)
        return {'status': 'synced'}
