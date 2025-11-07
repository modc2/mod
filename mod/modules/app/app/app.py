import mod as m
import os

class App:


    def serve(self, 
            port=3000, 
            api_port=8000, 
            ipfs_port=8001,
            mod = 'app',
            public=False, 
            remote=True, 
            build=False):

        m.serve('ipfs', port=ipfs_port)
        m.serve('api', port=api_port)
        image = f'{mod}:latest'
        cwd = m.dirpath(mod) 
        ip = m.ip() if public else '0.0.0.0'
        env = {'API_URL': f'http://{ip}:{api_port}'}
        return m.fn('pm/run')(
                    name=mod, 
                    volumes=[f'{cwd}:/app','/app/node_modules'], 
                    cwd=cwd, 
                    image=image,
                    working_dir=f'/{mod}',
                    daemon=remote, 
                    port=port, 
                    env=env, 
                    build=build)

    
    