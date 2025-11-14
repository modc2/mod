import mod as m
import os

class App:
    
    def serve(self, 
            port=3000, 
            api_port=8000, 
            ipfs_port=8001,
            mod = 'app', 
            prod =False, dev =None, # if prod is True, dev is False
            ip = None,
             **kwargs):

        prod = bool(prod or (not dev if dev != None else prod))
        if prod: 
            print("Starting in PROD mode")
        else:
            print("Starting in DEV mode")
        m.serve('ipfs', port=ipfs_port) if not m.server_exists('ipfs') else None
        m.serve('api', port=api_port) if not m.server_exists('api') else None
        image = f'{mod}:latest'
        cwd = m.dirpath(mod) 
        ip = ip or'0.0.0.0'
        env = {'NEXT_PUBLIC_API_URL': f'http://{ip}:{api_port}'}
        cmd = 'npm run build && npm run start' if prod else 'npm run dev'
        volumes = [f'{cwd}:/app','/app/node_modules', '~/.mod:/root/.mod', '~/mod:/root/mod', '/app/.next']
        working_dir = '/app'
        return m.fn('pm/run')(
                    name=mod, 
                    volumes=volumes, 
                    cwd=cwd, 
                    image=image,
                    working_dir=working_dir,
                    port=port, 
                    cmd=cmd,
                    env=env, 
                    **kwargs
                    )

    def edit(self, text='make the chat interface  ', **kwargs):
        text = text + 'given '  + m.code('api') + ' as the model in it has ' + m.code('model.openrouter') + ' mod. '
        return m.edit(mod='app', *text, **kwargs)
    
    