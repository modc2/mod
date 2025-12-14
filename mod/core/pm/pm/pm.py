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
    A mod for interacting with Docker.
    """

    def __init__(self,  
                mod='mod',
                path='~/.mod/server', 
                network='modnet',
                image = None,
                **kwargs):
        self.mod = mod
        self.image = image or mod
        self.network = network
        self.store = m.mod('store')(path)

    def forward(self,  
                mod : str ='api', 
                port : Optional[int] = None, 
                params : Optional[dict] = None,
                key : Optional[str] = None,
                image:str=None, 
                daemon:bool=True,
                cwd : Optional[str] = None,
                volumes : Optional[dict] = None,
                docker_in_docker:bool = False,
                env:Optional[dict]=None,
                call_interval : float = 0.2, # time between calls to check if server is up
                ):
        """
        Runs a mod as a Docker container with port forwarding as a 
        """
        params = params or {}
        port = port or m.free_port()
        params.update({'port': port, 'key': key or mod, 'remote': False, 'mod': mod})
        dirpath = m.dirpath(mod)
        volumes = volumes or [f'{p}:{self.convert_docker_path(p)}' for p in  [m.lib_path, m.storage_path, dirpath]]
        result = self.run(name=mod, 
                          image=image, 
                          port=port, 
                          cmd=f"m serve {self.params2cmd(params)}" , 
                          daemon=daemon, 
                          env=env, 
                          volumes=volumes, 
                          cwd=cwd or dirpath, 
                          docker_in_docker=docker_in_docker,
                          working_dir=self.convert_docker_path(dirpath))
        self.namespace(update=True)
        return result

    def up(self, mod='chain', daemon:bool=True):
        """
        Run docker-compose up in the specified path.
        """
        docker_compose_paths = self.compose_files(mod)
        assert len(docker_compose_paths) > 0, f'No docker-compose file found in {mod}'
        cmd = 'docker-compose up'
        path = m.dirpath(mod)
        if daemon:
            cmd += ' -d'
        return os.system('cd ' + path + ' && ' + cmd)
        
    def down(self, mod='chain'):
        """
        Run docker-compose down in the specified path.
        """
        docker_compose_paths = self.compose_files(mod)
        assert len(docker_compose_paths) > 0, f'No docker-compose file found in {mod}'
        cmd = 'docker-compose down'
        path = m.dirpath(mod)
        return os.system('cd ' + path + ' && ' + cmd)

    def get_compose_path(self, name: str):
        dirpath = m.dirpath(name)
        files = [dirpath+'/'+f for f in os.listdir(dirpath) if f.lower() in ['docker-compose.yml', 'docker-compose.yaml']]
        return files[0] if len(files) > 0 else os.path.join(dirpath, 'docker-compose.yml')

    def run(self,
            name : str = "mod",
            image: str = None, # the docker image to use
            cwd: Optional = None, # the working directory to run docker-compose in
            cmd: str = None, entrypoint: str = None, # command to run in the container
            volumes: Dict = None, # volume mappings
            resources: Union[List, str, bool] = None,
            shm_size: str = '100g',
            network: Optional = None,  # 'host', 'bridge', etc.
            port: int = None,
            ports: Union[List, Dict[int, int]] = None,
            daemon: bool = True,
            remote: bool = False,
            env: Optional[Dict] = None,
            working_dir : str = '/app',
            tag = 'latest',
            docker_in_docker = False,
            compose_path: str = None, # the path to the compose file
            restart: str = 'unless-stopped',
            build =  None,
            ) -> Dict:
        """
        Generate and run a Docker container using docker-compose.
        """ 
        network = self.ensure_network(network)
        compose_path = self.get_compose_path(name)
        if not os.path.exists(compose_path):
            m.print(f'Creating new docker-compose file at {compose_path}', color='yellow')
            compose_config = {'version': '3.8', 'services': {}}
        else:
            compose_config = m.get_yaml(compose_path)
        compose_config['networks'] = {
            'default': {
                'external': True,
                'name': network
            }
        }
        image = image or self.ensure_image(name)

        if self.server_exists(name):
            self.kill(name)
        serve_config = {
            'build':{'context':'./'},
            'image': image or f'{name}:{tag}',
            'container_name': name,
            'restart': restart,
            'deploy': {'resources': resources} if resources else {},
            'shm_size': shm_size
        }

        # PORTS
        if port != None:
            ports = {port: port}
        if isinstance(ports, dict):
            serve_config['ports'] = [f"{host_port}:{container_port}" for container_port, host_port in ports.items()] 

        # VOLUMES
        if volumes:
            if isinstance(volumes, dict):
                volumes = [f'{k}:{v}' for k, v in volumes.items()] 
            elif isinstance(volumes, list):
                volumes = volumes
            else:
                volumes = []
            serve_config['volumes'] = volumes
        if docker_in_docker: 
            volumes.append('/var/run/docker.sock:/var/run/docker.sock')
        if env:
            serve_config['environment'] = [f"{k}={v}" for k,v in env.items()] if env else []
    
        serve_config['working_dir'] = working_dir

        if build:
            serve_config.pop('image', None)
        else:
            serve_config.pop('build', None)
        if cmd or entrypoint:
            serve_config['entrypoint'] = f'bash -c "{cmd}"'
        # Write the docker-compose file

        if name in compose_config['services']:
            compose_config['services'][name].update(serve_config)
        else:
            compose_config['services'][name] = serve_config
        cwd = cwd or os.getcwd() 
        compose_cmd =  f'cd {cwd} && docker-compose -f {compose_path} up'
        if daemon:
            compose_cmd += ' -d'   

        self.make_volumes_absolute(compose_config)
        m.put_yaml(compose_path, compose_config)
        os.system(compose_cmd)
        # # now we want to make them relative again in case we push to git
        self.make_volumes_relative(compose_config)
        m.put_yaml(compose_path, compose_config)

        # now we need to make the 

        return {'path': compose_path, 'compose' : compose_config}


    @staticmethod
    def make_volumes_absolute(compose_config):
        """Convert volume paths to absolute paths."""
        for service, config in compose_config.get('services', {}).items():
            volumes = config.get('volumes', [])
            abs_volumes = []
            for vol in volumes:
                if  ':' in vol:
                    host_path, container_path = vol.split(':')
                    abs_host_path = m.abspath(host_path)
                    abs_volumes.append(f"{abs_host_path}:{container_path}")
                else:
                    abs_volumes.append(vol)
            compose_config['services'][service]['volumes'] = abs_volumes
        return compose_config

    @staticmethod
    def make_volumes_relative(compose_config):
        """Convert volume paths to relative paths."""
        for service, config in compose_config.get('services', {}).items():
            volumes = config.get('volumes', [])
            rel_volumes = []
            for vol in volumes:
                if ':' in vol:
                    host_path, container_path = vol.split(':')
                    # remove the home directory part
                    rel_host_path = host_path.replace(m.home_path, '~')
                    rel_volumes.append(f"{rel_host_path}:{container_path}")
                else:
                    rel_volumes.append(vol)
            compose_config['services'][service]['volumes'] = rel_volumes
        return compose_config

    def process_info(self, name):
        """ info of the process, the memory, cpu, etc"""
        stats = self.stats()

        if 'name' in stats.columns:
            info = stats[stats['name'] == name]
            if len(info) > 0:
                return info.iloc[0].to_dict()
        return {}

    def servers(self, search=None, **kwargs):
        servers =  self.ps()
        if search != None:
            servers = [m for m in servers if search in m]
        servers = sorted(list(set(servers)))
        return servers

    def server_exists(self, name):
        return name in self.servers()

    def params2cmd(self, params: Dict[str, Any]) -> str:
        """
        Convert a dictionary of parameters to a command string.
        
        Args:
            params (Dict[str, Any]): Dictionary of parameters.
            
        Returns:
            str: Command string with parameters formatted as key=value pairs.
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

    def dockerfiles(self, mod='mod'):
        """
        List all Dockerfiles in the specified path.
        """
        dockerfiles = []
        path = m.dp(mod)
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower() == 'dockerfile':
                    dockerfiles.append(os.path.join(root, file))
        return dockerfiles

    def compose_paths(self, mod='ipfs'):
        """
        List all docker-compose files in the specified path.
        """
        compose_files = []
        path = m.dp(mod, relative=False)
        for file in os.listdir(path):
            if file.lower() in ['docker-compose.yml', 'docker-compose.yaml']:
                compose_files.append(os.path.join(path, file))
                break
        return compose_files

    def compose_path(self, mod='mod'):
        paths = self.compose_paths(mod)
        return paths[0] if len(paths) > 0 else None

    def compose_config(self, mod='mod'):
     
        return m.get_yaml(self.compose_path(mod))

    def dockerfile(self, mod='mod'):
        path = self.dockerfile_path(mod)
        if path == None:
            return None
        return self.dockerfiles(mod)

    def has_dockerfile(self, mod='mod'):
        """
        Check if a Dockerfile exists in the specified mod path.
        """
        dockerfiles = self.dockerfiles(mod)
        return len(dockerfiles) > 0

    def dockerfile_path(self, mod='mod'):
        """
        Get the path to the Dockerfile in the specified mod.
        """
        dockerfiles = self.dockerfiles(mod)
        # choose the shortest dockerfile path
        if len(dockerfiles) == 0:
            print(f'No Dockerfile found in {mod}')
            return None
        else: 
            print(f'Found {len(dockerfiles)} Dockerfiles in {mod}')
        dockerfiles = sorted(dockerfiles, key=len)
        return dockerfiles[0] if len(dockerfiles) > 0 else None

    def dockerfile(self, mod='mod'):
        dockerfile_path = self.dockerfile_path(mod)
        if dockerfile_path is None:
            return None
        return m.get_text(dockerfile_path)

    def up(self, mod='chain', daemon: bool = True):
        """
        Run docker-compose up in the specified path.
        """
        cmd = 'docker-compose up'
        path = m.dirpath(mod)
        if daemon:
            cmd += ' -d'
        return os.system('cd ' + path + ' && ' + cmd)

    def build(self,
              mod = None,
              tag: Optional[str] = None,
              verbose: bool = True,
              no_cache: bool = False,
              env: Dict[str, str] = {}) -> Dict[str, Any]:
        """
        Build a Docker image from a Dockerfile.
        """
        mod = mod or 'mod'
        path = m.dirpath(mod)
        dockerfile_path = self.dockerfile_path(mod)
        if dockerfile_path is None:
            return self.build()
        
        cmd = f'docker build -t {mod} .'
        if no_cache:
            cmd += ' --no-cache'
        cmd = 'cd ' + path + ' && ' + cmd
        print(cmd)
        return os.system(cmd)

    def enter(self, contianer): 
        cmd = f'docker exec -it {contianer} bash'
        os.system(cmd)

    def exists(self, name: str) -> bool:
        """
        Check if a container exists.
        """
        return name in self.servers()
        
    def kill(self, name: str) -> Dict[str, str]:
        """
        Kill and remove a container.
        """
        if not self.exists(name):
            return {'status': 'not_found', 'name': name}
        try:
            os.system(f'docker kill {name}' )
            os.system(f'docker rm {name}')
            print(f'Killed container --> {name}')
            # remove from namespace cache
            self.namespace(update=True)
            return {'status': 'killed', 'name': name}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def kill_all(self) -> Dict[str, str]:
        """
        Kill all running containers.
        """
        try:
            for container in self.servers():
                self.kill(container)
            return {'status': 'all_containers_killed'}
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'servers': self.servers()}

    def images(self, df: bool = True) -> Union[pd.DataFrame, Any]:
        """
        List all Docker images.
        """
        text = m.cmd('docker images')
        results = []
        cols = []
        forbidden_terms = ['IMAGE', 'WARNING', '<none>']
        for i, line in enumerate(text.split('\n')):
            if 'warning:_this_output_is_designed' in line:
                continue
            if not line.strip():
                continue
            if any([ ft in line for ft in forbidden_terms]):
                continue
            image = line.split(' ')[0]
            results.append(image.split(':')[0])
        return results

    def image_names(self) -> List[str]:
        """
        Get a list of Docker image names.
        """
        images = self.images()
        return [img.split(':')[0] for img in images]

    def image_exists(self, name: str=None) -> bool:
        """
        Check if a Docker image exists.
        """
        name = name or self.mod
        if ':latest' in name:
            name = name.replace(':latest', '')
        return name in self.image_names()
    
    def ensure_image(self, mod='mod') -> str:
        if not self.image_exists(mod):
            dockerfiles = self.dockerfiles(mod)
            if len(dockerfiles) == 0:
                return self.image + ':latest'
            print(f'Image {mod} does not exist. Building...')
            self.build(mod)
        return mod


    def logs(self,
             name: str,
             follow: bool = False, f = None,
             sudo: bool = False,
             verbose: bool = False,
             tail: int = 100,
             head: int = None,
             since: Optional[str] = None) -> str:
        """
        Get container logs with advanced options.
        """
        follow = f if f is not None else follow
        
        cmd = ['docker', 'logs']

        if tail:
            cmd.extend(['--tail', str(tail)])
        if since:
            cmd.extend(['--since', since])
        if follow:
            cmd.append('--follow')

        cmd.append(name)
        cmd = ' '.join(cmd)
        return os.system(cmd) if follow else m.cmd(cmd, verbose=verbose)

    def rm_image(self, name: str) -> str:   
        """
        Remove a Docker image.
        """
        try:
            return m.cmd(f'docker rmi {name} -f')
        except Exception as e:
            return f"Error removing image: {e}"

    def prune(self, all: bool = False) -> str:
        """
        Prune Docker resources.
        """
        cmd = 'docker system prune -f' if all else 'docker container prune -f'
        try:
            return m.cmd(cmd)
        except Exception as e:
            return f"Error pruning: {e}"

    def get_path(self, path: str) -> str:
        """
        Get the path to a Docker-related file.
        """
        return os.path.expanduser(f'~/.mod/pm/{path}')

    def stats(self, max_age=60, update=False, df=False) -> pd.DataFrame:
        """
        Get container resource usage statistics.
        """
        path = 'container_stats.json'
        stats = self.store.get(path, [], max_age=max_age, update=update)
        if len(stats) == 0:
            cmd = f'docker stats --no-stream'
            output = m.cmd(cmd, verbose=False)
            lines = output.split('\n')
            headers = lines[0].split('  ')
            lines = [line.split('   ') for line in lines[1:] if line.strip()]
            lines = [[col.strip().replace(' ', '') for col in line if col.strip()] for line in lines]
            headers = [header.strip().replace(' %', '') for header in headers if header.strip()]
            data = pd.DataFrame(lines, columns=headers)
            stats = []
            for k, v in data.iterrows():
                row = {header: v[header] for header in headers}
                if 'MEM USAGE / LIMIT' in row:
                    mem_usage, mem_limit = row.pop('MEM USAGE / LIMIT').split('/')
                    row['MEM_USAGE'] = mem_usage
                    row['MEM_LIMIT'] = mem_limit
                row['ID'] = row.pop('CONTAINER ID')

                for prefix in ['NET', 'BLOCK']:
                    if f'{prefix} I/O' in row:
                        net_in, net_out = row.pop(f'{prefix} I/O').split('/')
                        row[f'{prefix}_IN'] = net_in
                        row[f'{prefix}_OUT'] = net_out
                
                row = {_k.lower(): _v for _k, _v in row.items()}
                stats.append(row)
                self.store.put(path, stats)
        if not df:
            return stats
        return m.df(stats)

    def ps(self) -> List[str]:
        """
        List all running Docker containers.
        """
        try:
            text = m.cmd('docker ps')
            ps = []
            for i, line in enumerate(text.split('\n')):
                if not line.strip():
                    continue
                if i > 0:
                    parts = line.split()
                    if len(parts) > 0:  # Check if there are any parts in the line
                        ps.append(parts[-1])
            return ps
        except Exception as e:
            m.print(f"Error listing containers: {e}", color='red')
            return []

    def exec(self, name: str, cmd: str, *extra_cmd, **cmd_kwargs) -> str:
        """
        Execute a command in a running Docker container.
        """
        if len(extra_cmd) > 0:
            cmd = ' '.join([cmd] + list(extra_cmd)) + self.params2cmd(cmd_kwargs)
        cmd = f'docker exec {name} bash -c "{cmd}"'
        return os.system(cmd)

    def container_stats(self, max_age=10, update=False, cache_dir="./docker_stats") -> pd.DataFrame:
        """
        Get resource usage statistics for all containers.
        """
        # Create cache directory if it doesn't exist
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "all_containers.json")
        
        # Check if cache exists and is recent enough
        should_update = update
        if not should_update and os.path.exists(cache_file):
            file_age = datetime.now().timestamp() - os.path.getmtime(cache_file)
            should_update = file_age > max_age
        
        if should_update or not os.path.exists(cache_file):
            # Run docker stats command
            cmd = 'docker stats --no-stream'
            try:
                output = subprocess.check_output(cmd, shell=True, text=True)
            except subprocess.CalledProcessError:
                print("Error running docker stats command")
                return pd.DataFrame()
            
            # Parse the output
            lines = output.strip().split('\n')
            if len(lines) <= 1:
                print("No containers running")
                return pd.DataFrame()
            
            # Process headers
            headers = [h.strip() for h in lines[0].split('  ') if h.strip()]
            cleaned_headers = []
            header_indices = []
            
            # Find the position of each header in the line
            current_pos = 0
            for header in headers:
                pos = lines[0].find(header, current_pos)
                if pos != -1:
                    header_indices.append(pos)
                    cleaned_headers.append(header)
                    current_pos = pos + len(header)
            
            # Process data rows
            stats = []
            for line in lines[1:]:
                if not line.strip():
                    continue
                    
                # Extract values based on header positions
                values = []
                for i in range(len(header_indices)):
                    start = header_indices[i]
                    end = header_indices[i+1] if i+1 < len(header_indices) else len(line)
                    values.append(line[start:end].strip())
                
                # Create a dictionary for this row
                row = dict(zip(cleaned_headers, values))
                
                # Process special columns
                if 'MEM USAGE / LIMIT' in row:
                    mem_usage, mem_limit = row.pop('MEM USAGE / LIMIT').split('/')
                    row['MEM_USAGE'] = mem_usage.strip()
                    row['MEM_LIMIT'] = mem_limit.strip()
                
                for prefix in ['NET', 'BLOCK']:
                    if f'{prefix} I/O' in row:
                        io_in, io_out = row.pop(f'{prefix} I/O').split('/')
                        row[f'{prefix}_IN'] = io_in.strip()
                        row[f'{prefix}_OUT'] = io_out.strip()
                
                # Rename ID column
                if 'CONTAINER ID' in row:
                    row['ID'] = row.pop('CONTAINER ID')
                
                # Convert keys to lowercase
                row = {k.lower(): v for k, v in row.items()}
                stats.append(row)
            
            # Save to cache
            with open(cache_file, 'w') as f:
                json.dump(stats, f)
        else:
            # Load from cache
            with open(cache_file, 'r') as f:
                stats = json.load(f)
        
        # Convert to DataFrame
        return pd.DataFrame(stats)

    def sync(self):
        """
        Sync container statistics.
        """
        self.stats(update=1)

    # PM2-like methods for container management
    def start(self, name: str, image: str, **kwargs) -> Dict[str, Any]:
        """
        Start a container (PM2-like interface).
        """
        if self.exists(name):
            return self.restart(name)
        
        return self.run(image=image, name=name, **kwargs)

    def stop(self, name: str) -> Dict[str, str]:
        """
        Stop a container without removing it (PM2-like interface).
        """
        try:
            m.cmd(f'docker stop {name}', verbose=False)
            return {'status': 'stopped', 'name': name}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def restart(self, name: str) -> Dict[str, str]:
        """
        Restart a container (PM2-like interface).
        """
        try:
            m.cmd(f'docker restart {name}', verbose=False)
            return {'status': 'restarted', 'name': name}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def delete(self, name: str) -> Dict[str, str]:
        """
        Remove a container (PM2-like interface).
        """
        return self.kill(name)

    def get_port(self, name: str) -> Dict[int, int]:
        """
        Get the exposed ports of a container as a dictionary.
        """
        # Convert name format if needed
        container_name = name
        
        # Get container inspection data
        try:
            inspect_output = m.cmd(f'docker inspect {container_name}', verbose=False)
            container_info = json.loads(inspect_output)[0]
            
            # Extract port bindings from HostConfig
            port_bindings = container_info.get('HostConfig', {}).get('PortBindings', {})
            
            # Convert port bindings to a simple dict format
            ports_dict = {}
            for container_port, host_configs in port_bindings.items():
                if host_configs:
                    # Extract port number from format like "8080/tcp"
                    container_port_num = int(container_port.split('/')[0])
                    # Get the host port from the first binding
                    host_port = int(host_configs[0]['HostPort'])
                    ports_dict = container_port_num
                    
            return ports_dict
            
        except Exception as e:
            m.print(f"Error getting ports for container {container_name}: {e}", color='red')
            return {}
    
    def networks(self) -> List[str]:
        """
        List all Docker networks.
        """
        text = m.cmd('docker network ls', verbose=False)
        networks = []
        for i, line in enumerate(text.split('\n')):
            if not line.strip():
                continue
            if i > 0:
                parts = line.split()
                if len(parts) > 1:  # Check if there are enough parts in the line
                    networks.append(parts[1])
        return networks

    def add_network(self, name: str='modnet') -> Dict[str, str]:
        """
        Add a Docker network.
        """
        print(f'Adding network {name}')
        if name in self.networks():
            return {'status': 'exists', 'name': name}
        try:
            m.cmd(f'docker network create {name}', verbose=False)
            return {'status': 'created', 'name': name}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def network_exists(self, name: str) -> bool:
        """
        Check if a Docker network exists.
        """
        return name in self.networks()

    def network_info(self, name: str) -> Dict[str, Any]:
        """
        Get information about a Docker network.
        """
        try:
            output = m.cmd(f'docker network inspect {name}', verbose=False)
            info = json.loads(output)
            return info[0] if len(info) > 0 else {}
        except Exception as e:
            m.print(f"Error inspecting network {name}: {e}", color='red')
            return {}
    
    def rm_network(self, name: str) -> Dict[str, str]:
        """
        Remove a Docker network.
        """
        if name not in self.networks():
            return {'status': 'not_found', 'name': name}
        try:
            m.cmd(f'docker network rm {name}', verbose=False)
            return {'status': 'removed', 'name': name}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}
    
    def namespace(self, search=None, max_age=None, update=False, **kwargs) -> dict:
        """
        Get a list of unique namespaces from container names.
        """
        ip = '0.0.0.0'
        path = self.store.get_path('namespace')
        namespace = m.get(path, None, max_age=max_age, update=update)
        if namespace == None :
            containers = self.servers(search=search)
            namespace = {}
            for container in containers:
                port = self.get_port(container)
                namespace[container] =  ip + ':'+  str(port)
            self.store.put(path, namespace)
        if search != None:
            namespace = {k:v for k,v in namespace.items() if search in k}
        return namespace

    def urls(self, search=None, mode='http') -> List[str]:
        return list(self.namespace(search=search).values())

    def start_docker_daemon(self, wait_time=5):
        """
        Start the Docker daemon if it is not already running.
        """
        import sys
        # if macos
        if sys.platform == 'darwin':
            m.cmd('open /Applications/Docker.app')
        elif sys.platform == 'win32':
            m.cmd('Start-Process "C:\\Program Files\\Docker\\Docker\\Docker Desktop')
        elif sys.platform == 'linux':
            m.cmd('systemctl is-active --quiet docker')
        for i in range(wait_time):
            if self.is_docker_daemon_on():
                return "Docker daemon is running."
            m.sleep(1)
        raise RuntimeError("Docker daemon is not running. Please start Docker and try again.")

    def is_docker_daemon_on(self):
        """
        Check if the Docker daemon is running.
        """
        return not("Is the docker daemon running?" in m.cmd('docker info', verbose=False))

    def compose_files(self, mod = 'mod', depth=3) -> List[str]:
        """
        List all docker-compose files in the specified path.
        """
        compose_files = []
        path = m.dp(mod, relative=False)
        print(f'Searching for docker-compose files in {path} with depth {depth}')
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower() in ['docker-compose.yml', 'docker-compose.yaml']:
                    compose_files.append(os.path.join(root, file))
        return compose_files

    def convert_docker_path(self, p):
        """
        Convert a local path to a Docker-compatible path.
        """
        return p.replace('~', '/root').replace(m.home_path, '/root')
        
    # TEST
    def test_network(self, network='modnet'):
        """
        Test if a Docker network exists, and create it if it doesn't.
        """
        if not self.network_exists(network):
            self.add_network(network)
        assert self.network_exists(network), f"Failed to create network {network}"
        return {'status': 'exists', 'name': network}

    def test_server(self, mod='api', port=8000, run_mode='uvicorn'):
        """
        Test running a mod as a Docker container with port forwarding.
        """
        def server_fn(fn: str):
            result = self.forward(mod=mod, port=port, key=mod)
            print(f'Server running at: {result}')
            m.sleep(2)
            print(self.logs(mod, tail=10))
            print('Test complete.')
            self.kill(mod)
            return result

    def ensure_network(self, network: str=None):
        network = network or self.network
        if not self.network_exists(network):
            self.add_network(network)
        assert self.network_exists(network), f"Failed to create network {network}"
        return network
