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
            ip = None,
            build=False):

        if not m.server_exists('ipfs'):
            print('Starting IPFS server...')
            m.serve('ipfs', port=ipfs_port)
        if not m.server_exists('api'):
            print('Starting API server...')
            m.serve('api', port=api_port)
        image = f'{mod}:latest'
        cwd = m.dirpath(mod) 
        ip = ip or (m.ip() if public else '0.0.0.0')
        env = {'NEXT_PUBLIC_API_URL': f'http://{ip}:{api_port}'}
        return m.fn('pm/run')(
                    name=mod, 
                    volumes=[f'{cwd}:/app','/app/node_modules'], 
                    cwd=cwd, 
                    image=image,
                    working_dir=f'/{mod}',
                    daemon=remote, 
                    port=port, 
                    # cmd='npm start',
                    env=env, 
                    build=build
                    )

    
    