import mod as m
import os

class App:

    def serve(self, 
            port=3000, 
            api_port=8000, 
            ipfs_port=8001,
            mod = 'app',
            prod =False, dev =None, # if prod is True, dev is False
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
        ip = ip or'0.0.0.0'
        env = {'NEXT_PUBLIC_API_URL': f'http://{ip}:{api_port}'}
        cmd = 'npm run build && npm run start' if prod else 'npm run dev'
        volumes = [f'{cwd}:/app','/app/node_modules', '~/.mod:/root/.mod', '~/mod:/root/mod', '/app/.next']
        if dev != None:
            prod = not dev
        if prod: 
            print('Building production app...')
        return m.fn('pm/run')(
                    name=mod, 
                    volumes=volumes, 
                    cwd=cwd, 
                    image=image,
                    working_dir=f'/{mod}',
                    daemon=remote, 
                    port=port, 
                    cmd=cmd,
                    env=env, 
                    build=build
                    )

    
    