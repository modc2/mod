 # start of file
import os
import pandas as pd
from typing import List, Dict, Union, Optional, Any
import mod as m

import subprocess
import time
import json
from datetime import datetime
import yaml  

print = m.print
class PM:
    """
    A mod for interacting with Docker.
    """

    def __init__(self,  
                mod='mod',
                path='~/.mod/server', 
                network='modnet',
                registry = 'server.namespace',
                store = 'store',
                image = None,
                **kwargs):
        self.mod = mod
        self.image = image or mod
        self.network = network
        self.registry = m.mod(registry)()
        self.store = m.mod(store)(path)


    def forward(self,
                mod : str ='api',
                port : int = None,
                params : dict = None,
                key : str = None,
                image:str=None,
                daemon:bool=True,
                cwd : str = None, # the working directory to run docker-compose in
                volumes : list = None,
                docker_in_docker:bool = False,
                env:Optional[dict]=None,
                call_interval : float = 0.2, # time between calls to check if server is up
                ):
        """
        Runs a mod as a Docker container with port forwarding as a
        """
        self.ensure_docker()
        params = params or {}
        port = port or m.free_port()
        params.update({'port': port, 'key': key or mod, 'remote': False, 'mod': mod})
        dirpath = m.dirpath(mod)
        cmd = f"m serve {self.params2cmd(params)}"
        volumes = volumes or [f'{p}:{self.convert_docker_path(p)}' for p in  [m.lib_path, m.storage_path, dirpath]]
        self.registry.reg(mod, f'http://0.0.0.0:{port}')
        result = self.run(name=mod, 
                          image=image, 
                          port=port, 
                          cmd=cmd , 
                          daemon=daemon, 
                          env=env, 
                          volumes=volumes, 
                          cwd=cwd or dirpath, 
                          docker_in_docker=docker_in_docker,
                          working_dir=self.convert_docker_path(dirpath))
       
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
        self.ensure_docker()
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
        services = compose_config['services']
        image = image or self.ensure_image(name)

        if self.server_exists(name):
            self.kill(name)
        serve_config = {
            'build':{'context':'./'},
            'image': image or f'{name}:{tag}',
            'container_name': name,
            'restart': restart,
            'deploy': {'resources': resources} if resources else {},
            'shm_size': shm_size,
            'ports': services.get(name, {}).get('ports', [])
        }
        ports =  [f'{port}:{port}'] 
        serve_config['ports'] = ports + serve_config['ports'][1:] if len(serve_config['ports']) > 0 else ports

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
            
        # before running we need to make the volumes absolute
        self.make_volumes_absolute(compose_config)
        m.put_yaml(compose_path, compose_config)
        os.system(compose_cmd)

        # # now we want to make them relative again in case we push to git
        self.make_volumes_relative(compose_config)
        m.put_yaml(compose_path, compose_config)

        # sync once you run 
        self.sync()
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
                    rel_host_path = host_path.replace(m.homepath, '~')
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
    

    def server_exists(self, name: str) -> bool:
        exists =  name in self.ps()
        return exists



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

    def update(self):
        return self.namespace(update=True)

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
        self.ensure_docker()
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
        return name in self.ps()
        
    def kill(self, name: str, update=True, prefix: bool = False) -> Dict[str, str]:
        """
        Kill and remove a container. If prefix=True, kill all containers matching the prefix.
        """
        if name == 'all':
            return self.kill_all()
        if prefix:
            return self.kill_prefix(name, update=update)
        if not self.server_exists(name):
            return {'status': 'not_found', 'name': name}
        servers = self.ps(search=name)
        if name in servers:
            result =  {'status': 'not_found', 'name': name}
        try:
            os.system(f'docker kill {name}')
            os.system(f'docker rm {name}')
            if update:
                self.sync()
        except Exception as e:
            print(f'Error killing container {name}: {m.detailed_error(e)}', color='red')
        assert name not in self.ps(), f'Failed to kill container {name}'
        children = self.ps(search=name + '.')
        for child in children:
            print(f'Killing child container {child}')
            self.kill(child, update=update)
        self.registry.dereg(name)
        return result

    def kill_prefix(self, prefix: str, update=True) -> Dict[str, str]:
        """Kill all Docker containers whose name starts with the given prefix."""
        matches = [s for s in self.ps() if s.startswith(prefix)]
        if not matches:
            return {'status': 'no_matches', 'prefix': prefix, 'killed': []}
        killed = []
        errors = []
        for name in matches:
            try:
                self.kill(name, update=False)
                killed.append(name)
            except Exception as e:
                print(f'Error killing {name}: {e}', color='red')
                errors.append(name)
        if update and killed:
            self.sync()
        print(f"Killed {len(killed)}/{len(matches)} containers with prefix '{prefix}'", color='green')
        return {
            'status': 'killed' if not errors else 'partial',
            'prefix': prefix,
            'killed': killed,
            'errors': errors
        }
    


    

    def kill_all(self) -> Dict[str, str]:
        """
        Kill all running containers.
        """
        try:
            for container in self.ps():
                self.kill(container)
            return {'status': 'all_containers_killed'}
        except Exception as e:
            print('fam')
            return {'status': 'error', 'error': str(e), 'servers': self.servers()}
    killall = kill_all

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
                try:

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
                except Exception as e :
                    continue
        if not df:
            return stats
        return m.df(stats)

    def ps(self, search: str = None) -> List[str]:
        """
        List all running Docker containers.
        """
        self.ensure_docker()
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
            if search != None:
                ps = [m for m in ps if search in m]
            return ps
        except Exception as e:
            m.print(f"Error listing containers: {e}", color='red')
            return []
        
    def servers(self, search=None,  **kwargs) -> List[str]:
        return self.ps(search=search)

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
        
    def namespace(self, update=False):
        return self.registry.namespace(update=update)
    
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
        

    def ensure_docker(self, wait_time=30):
        """
        Ensure Docker daemon is running, starting it if needed.
        """
        if self.is_docker_daemon_on():
            return True
        print('Docker is not running. Starting Docker...', color='yellow')
        self.start_docker_daemon(wait_time=wait_time)
        return self.is_docker_daemon_on()

    def start_docker_daemon(self, wait_time=30):
        """
        Start the Docker daemon if it is not already running.
        """
        import sys
        if self.is_docker_daemon_on():
            return "Docker daemon is already running."
        # if macos
        if sys.platform == 'darwin':
            m.cmd('open /Applications/Docker.app')
        elif sys.platform == 'win32':
            m.cmd('Start-Process "C:\\Program Files\\Docker\\Docker\\Docker Desktop')
        elif sys.platform == 'linux':
            m.cmd('sudo systemctl start docker')
        for i in range(wait_time):
            if self.is_docker_daemon_on():
                print('Docker daemon is running.', color='green')
                return "Docker daemon is running."
            m.sleep(1)
        raise RuntimeError("Docker daemon failed to start after {wait_time}s. Please start Docker manually.")

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
        return p.replace('~', '/root').replace(m.homepath, '/root')
        

    # ── the mod protocol sandbox ─────────────────────────────────────────
    # One container that can run ANY mod in the tree. The image is a toolchain
    # (python/node/rust/caddy); the tree itself is bind-mounted, so `m serve x`
    # inside the box runs the same code you are editing on the host.

    sandbox_name = 'mod'
    sandbox_image = 'mod:latest'
    sandbox_band = (50950, 50969)   # host ports compose publishes 1:1

    def sandbox_root(self) -> str:
        """Repo root — where the sandbox Dockerfile and compose file live."""
        return m.abspath(m.lib_path)

    def sh(self, cmd: str, timeout: int = 300, cwd: str = None):
        """Run a shell command, never raise. -> (returncode, output)"""
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
            return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
        except subprocess.TimeoutExpired:
            return 124, f'timeout after {timeout}s: {cmd}'
        except Exception as e:
            return 1, f'{type(e).__name__}: {e}'

    def sandbox_running(self) -> bool:
        code, out = self.sh(f'docker inspect -f "{{{{.State.Running}}}}" {self.sandbox_name}', timeout=30)
        return code == 0 and out.strip().endswith('true')

    def sandbox_image_exists(self, image: str = None) -> bool:
        image = image or self.sandbox_image
        code, _ = self.sh(f'docker image inspect {image}', timeout=60)
        return code == 0

    def status(self, mod: str = None) -> Dict[str, Any]:
        """Where the sandbox stands right now. Safe to call any time."""
        root = self.sandbox_root()
        out = {
            'root': root,
            'image': self.sandbox_image,
            'image_built': self.sandbox_image_exists(),
            'container': self.sandbox_name,
            'running': self.sandbox_running(),
            'daemon': self.is_docker_daemon_on(),
            'dockerfile': os.path.isfile(os.path.join(root, 'Dockerfile')),
            'compose': os.path.isfile(os.path.join(root, 'docker-compose.yml')),
        }
        if out['running']:
            code, tree = self.sh(f'docker exec {self.sandbox_name} python3 -c "import mod; print(mod.__file__)"', timeout=60)
            out['tree_mounted'] = code == 0
            out['tree'] = tree if code == 0 else None
            out['serving'] = self.sandbox_serving()
        if mod:
            out['mod'] = self.ready(mod)
        return out

    def boot(self, rebuild: bool = False, wait: int = 90, no_cache: bool = False) -> Dict[str, Any]:
        """
        Start the mod sandbox: ensure the image, the network and the container,
        then prove the tree is really importable inside it.

        Anything that is not ready comes back as a report with a `fix` field —
        hand that straight to `m docker/modify`.
        """
        self.ensure_docker()
        self.ensure_network()
        root = self.sandbox_root()
        compose = os.path.join(root, 'docker-compose.yml')
        if not os.path.isfile(compose):
            return {'ok': False, 'error': f'no docker-compose.yml at {root}',
                    'fix': f'm docker/modify mod query="write the sandbox docker-compose.yml"'}

        if rebuild or not self.sandbox_image_exists():
            print(f'Building {self.sandbox_image} (this takes a few minutes)...', color='yellow')
            cmd = f'DOCKER_BUILDKIT=0 docker build -t {self.sandbox_image} -f Dockerfile .'
            if no_cache:
                cmd += ' --no-cache'
            code, out = self.sh(cmd, timeout=3600, cwd=root)
            if code != 0:
                return {'ok': False, 'stage': 'build', 'error': out[-4000:],
                        'fix': 'm docker/modify mod query="fix the sandbox image build"'}

        code, out = self.sh(f'docker compose -f {compose} up -d', timeout=600, cwd=root)
        if code != 0:
            return {'ok': False, 'stage': 'up', 'error': out[-4000:],
                    'fix': 'm docker/modify mod query="fix docker-compose.yml so the sandbox starts"'}

        t0 = time.time()
        while time.time() - t0 < wait:
            if self.sandbox_running():
                code, tree = self.sh(
                    f'docker exec {self.sandbox_name} python3 -c "import mod; print(mod.__file__)"', timeout=120)
                if code == 0:
                    return {'ok': True, 'container': self.sandbox_name, 'tree': tree,
                            'enter': f'docker exec -it {self.sandbox_name} bash',
                            'serve': 'm docker/serve <mod>'}
            time.sleep(2)

        return {'ok': False, 'stage': 'health',
                'error': f'sandbox did not become importable within {wait}s',
                'logs': self.logs(self.sandbox_name, tail=40),
                'fix': 'm docker/modify mod query="the sandbox container starts but `import mod` fails inside it"'}

    def shell(self, cmd: str, mod: str = None, timeout: int = 300) -> str:
        """Run a shell command inside the sandbox (cwd = the mod's dir if given)."""
        if not self.sandbox_running():
            boot = self.boot()
            if not boot.get('ok'):
                return json.dumps(boot, indent=2)
        workdir = ''
        if mod:
            workdir = f'-w {self.convert_docker_path(m.dirpath(mod))} '
        code, out = self.sh(
            f'docker exec {workdir}{self.sandbox_name} bash -lc {json.dumps(cmd)}', timeout=timeout)
        return out

    def sandbox_serving(self) -> Dict[str, int]:
        """Mods currently served inside the sandbox -> {mod: port}."""
        code, out = self.sh(
            f"docker exec {self.sandbox_name} bash -lc \"ps -eo args | grep -o 'm serve [^ ]* port=[0-9]*' || true\"",
            timeout=60)
        serving = {}
        if code == 0:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    serving[parts[2]] = int(parts[3].split('=')[1])
        return serving

    def free_band_port(self, mod: str = None) -> int:
        """Pick a host port from the published band that nothing inside is using."""
        lo, hi = self.sandbox_band
        taken = set(self.sandbox_serving().values())
        for port in range(lo, hi + 1):
            if port not in taken:
                return port
        raise RuntimeError(f'sandbox port band {lo}-{hi} is full: {sorted(taken)}')

    def serve(self, mod: str = 'api', port: int = None, wait: int = 60,
              force: bool = False) -> Dict[str, Any]:
        """
        Run ANY mod inside the sandbox and hand back its URL.

        Not ready? You get the readiness report instead of a broken container,
        with the exact `m docker/modify` line that puts the build agent on it.
        """
        report = self.ready(mod)
        if not report['ready'] and not force:
            return report

        boot = self.boot()
        if not boot.get('ok'):
            return boot

        serving = self.sandbox_serving()
        if mod in serving and not force:
            port = serving[mod]
            return {'ok': True, 'mod': mod, 'port': port,
                    'url': f'http://localhost:{port}', 'status': 'already_serving'}

        port = port or self.free_band_port(mod)
        lo, hi = self.sandbox_band
        if not (lo <= port <= hi):
            print(f'port {port} is outside the published band {lo}-{hi}: '
                  f'the mod will run but stay unreachable from the host', color='yellow')

        log = f'/tmp/mod-sandbox-{mod}.log'
        cmd = f'm serve {mod} port={port} remote=0 > {log} 2>&1'
        code, out = self.sh(
            f'docker exec -d {self.sandbox_name} bash -lc {json.dumps(cmd)}', timeout=60)
        if code != 0:
            return {'ok': False, 'mod': mod, 'error': out}

        t0 = time.time()
        while time.time() - t0 < wait:
            if self.port_open(port):
                return {'ok': True, 'mod': mod, 'port': port,
                        'url': f'http://localhost:{port}',
                        'logs': f'm docker/shell "tail -50 {log}"'}
            time.sleep(1)

        tail = self.shell(f'tail -60 {log}')
        return {'ok': False, 'mod': mod, 'port': port,
                'error': f'{mod} did not answer on :{port} within {wait}s',
                'logs': tail,
                'fix': f'm docker/modify {mod}'}

    def unserve(self, mod: str) -> Dict[str, Any]:
        """Stop a mod running inside the sandbox."""
        # `[m]` is the classic self-exclusion: it matches the literal "m" in the
        # target's cmdline, but this shell's own cmdline carries the brackets,
        # so pkill can't kill the process doing the killing.
        self.sh(f"docker exec {self.sandbox_name} bash -lc "
                f"\"pkill -f '[m] serve {mod} ' || true\"", timeout=60)
        serving = self.sandbox_serving()
        return {'ok': mod not in serving, 'mod': mod, 'serving': serving}

    @staticmethod
    def port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.5) -> bool:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0

    # ── readiness ────────────────────────────────────────────────────────

    def ready(self, mod: str = 'mod') -> Dict[str, Any]:
        """
        Can this mod run in the sandbox? Returns a report, never raises.

        Every failing check carries a `fix` string, and the report as a whole
        carries the `m docker/modify` line that hands the whole thing to the
        build agent.
        """
        checks = []
        notes = []

        def check(name, ok, detail='', fix='', warn=False):
            ok = bool(ok)
            entry = {'name': name, 'ok': ok, 'detail': detail}
            if not ok:
                entry['fix'] = fix
            # A warning is something to know, not something to send an agent at.
            (notes if warn else checks).append(entry)
            return ok

        check('docker_daemon', self.is_docker_daemon_on(),
              'docker daemon reachable', 'start docker: sudo systemctl start docker')
        check('sandbox_image', self.sandbox_image_exists(),
              self.sandbox_image, 'm docker/boot rebuild=1')

        path = None
        try:
            path = m.dirpath(mod)
        except Exception as e:
            check('resolves', False, f'{type(e).__name__}: {e}',
                  f'no mod named {mod} in the tree')
        else:
            check('resolves', os.path.isdir(path), path, f'{path} is not a directory')

        if path and os.path.isdir(path):
            cfg_path = os.path.join(path, 'config.json')
            cfg = {}
            if os.path.isfile(cfg_path):
                try:
                    cfg = json.loads(m.get_text(cfg_path))
                    check('config', True, f'{len(cfg.get("fns", []))} fns declared')
                except Exception as e:
                    check('config', False, f'config.json does not parse: {e}',
                          'repair config.json')
            else:
                check('config', False, 'no config.json',
                      'add a config.json with name + fns')

            entries = ['mod.py', 'src', os.path.basename(path), 'package.json', 'Cargo.toml']
            found = [e for e in entries if os.path.exists(os.path.join(path, e))]
            check('entrypoint', bool(found), ', '.join(found) or 'nothing runnable found',
                  'add a mod.py (or src/mod.py) exporting a class')

            try:
                m.mod(mod)
                check('importable', True, 'class resolves')
            except Exception as e:
                check('importable', False, f'{type(e).__name__}: {str(e)[:300]}',
                      'fix the import error in the module')

            # Resolving requirements is a network call. A resolver that says
            # "no" is a real readiness failure; a resolver that never answers
            # is the network's problem, not the module's — that one is a note.
            reqs = os.path.join(path, 'requirements.txt')
            if os.path.isfile(reqs) and self.sandbox_running():
                in_box = self.convert_docker_path(reqs)
                code, _ = self.sh(
                    f'docker exec {self.sandbox_name} bash -lc '
                    f'{json.dumps(f"pip install --dry-run -q -r {in_box}")}', timeout=90)
                if code == 124:
                    check('deps', False, 'dependency resolution timed out — could not tell',
                          '', warn=True)
                else:
                    check('deps', code == 0, 'requirements.txt resolve',
                          f'm docker/shell "pip install -r {in_box}"')

            # The mod's declared port is almost never inside the published band,
            # and that is fine: `serve` runs it on a band port instead. Worth
            # saying out loud, not worth calling the module broken over.
            port = cfg.get('port')
            if port:
                lo, hi = self.sandbox_band
                check('config_port_published', lo <= int(port) <= hi,
                      f'config port {port} is outside the published band {lo}-{hi} — '
                      f'`m docker/serve {mod}` will run it on a band port instead',
                      f'publish {port} in docker-compose.yml to keep the mod on its own port',
                      warn=True)

        failed = [c['name'] for c in checks if not c['ok']]
        return {
            'mod': mod,
            'path': path,
            'ready': not failed,
            'checks': checks,
            'notes': notes,
            'missing': failed,
            'fix': None if not failed else f'm docker/modify {mod}',
        }

    # ── the escape hatch: hand a not-ready mod to the build agent ────────

    def modify(self,
               mod: str = 'mod',
               query: str = None,
               model: str = 'sonnet',
               agent: str = 'build',
               dry_run: bool = False,
               **kwargs) -> Dict[str, Any]:
        """
        Something is not ready — put the build agent on it.

        Turns the readiness report into a precise brief (what failed, where,
        and what "fixed" means) and submits it as a background build job.

        m docker/modify polymarket
        m docker/modify polymarket query="also add a healthcheck to the Dockerfile"
        m docker/modify polymarket dry_run=1     # just show me the brief
        """
        report = self.ready(mod)
        if report['ready'] and not query:
            return {'mod': mod, 'ready': True,
                    'msg': f'{mod} is already sandbox-ready — nothing to modify',
                    'run': f'm docker/serve {mod}'}

        prompt = self.modify_prompt(mod, report, query)
        if dry_run:
            return {'mod': mod, 'report': report, 'prompt': prompt, 'submitted': False}

        errors = {}
        tried = []
        for name in ([agent] if agent else []) + ['build', 'modify']:
            if name in tried:
                continue
            tried.append(name)
            try:
                if name == 'build':
                    job = m.mod('build')().edit_module(module_name=mod, prompt=prompt, model=model, **kwargs)
                else:
                    job = m.mod('modify')().forward(mod=mod, query=prompt, **kwargs)
            except Exception as e:
                errors[name] = f'{type(e).__name__}: {str(e)[:300]}'
                print(f'{name} agent unavailable: {errors[name]}', color='yellow')
                continue

            out = {'mod': mod, 'agent': name, 'submitted': True,
                   'was_missing': report['missing'], 'job': job}
            if errors:
                out['skipped'] = errors
            if name == 'build':
                # A build job is asynchronous — it has not run yet, so there is
                # nothing to verify. Say that instead of implying a fix landed.
                out['status'] = 'queued'
                out['watch'] = 'm build/jobs'
            else:
                # A synchronous agent reports its own success, and agents are
                # optimistic about that. Re-run the checks and report what is
                # actually true now.
                after = self.ready(mod)
                out['status'] = 'fixed' if after['ready'] else 'incomplete'
                out['fixed'] = after['ready']
                out['still_missing'] = after['missing']
                if not after['ready']:
                    out['fix'] = f'm docker/modify {mod}'
            return out

        return {'mod': mod, 'submitted': False, 'report': report,
                'agent_errors': errors, 'prompt': prompt,
                'msg': 'no agent could take the job — the brief above is ready to paste'}

    def modify_prompt(self, mod: str, report: Dict[str, Any] = None, query: str = None) -> str:
        """The brief handed to the build agent. Concrete, path-anchored, testable."""
        report = report or self.ready(mod)
        failed = [c for c in report['checks'] if not c['ok']]
        warnings = [c for c in report.get('notes', []) if not c['ok']]
        lines = [
            f'Make the mod `{mod}` runnable inside the mod protocol docker sandbox.',
            '',
            f'Module path: {report.get("path")}',
            'The sandbox is the `mod` container: the tree is bind-mounted at /root/mod,',
            'and a mod is started inside it with `m serve <mod> port=<port> remote=0`.',
            '',
        ]
        if failed:
            lines.append('Failing readiness checks:')
            for c in failed:
                lines.append(f'  - {c["name"]}: {c["detail"] or "failed"}')
                if c.get('fix'):
                    lines.append(f'      suggested fix: {c["fix"]}')
            lines.append('')
        if warnings and query:
            lines.append('Context (not failures):')
            for c in warnings:
                lines.append(f'  - {c["name"]}: {c["detail"]}')
            lines.append('')
        if query:
            lines += ['Additional instruction from the operator:', f'  {query}', '']
        lines += [
            'Rules:',
            '  - Change only what the failing checks (and the operator instruction) require.',
            '  - Follow the conventions of the neighbouring mods in the tree.',
            '  - Do not weaken a check by deleting it; make the underlying thing true.',
            '',
            'Done means: `m docker/ready ' + mod + '` reports ready, and',
            '`m docker/serve ' + mod + '` answers on its port.',
        ]
        return '\n'.join(lines)

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

    def rm_orphan_containers(self):
        """
        Remove orphan Docker containers that are not managed by this PM.
        """
        managed = set(self.servers())
        all_containers = set(self.ps())
        orphans = all_containers - managed
        for orphan in orphans:
            print(f'Removing orphan container: {orphan}')
            self.kill(orphan)
        return {'status': 'removed_orphans', 'orphans': list(orphans)}
