import mod as m
import os

class App:
    
    def serve(self, 
            port=3000, 
            api_url = 'http://0.0.0.0:8000',
            mod = 'app', 
            prod =False, dev =None, # if prod is True, dev is False
            api_port=8000, 
            ipfs_port=8001,
             **kwargs):

        prod = bool(prod or (not dev if dev != None else prod))
        if prod: 
            print("Starting in PROD mode")
        else:
            print("Starting in DEV mode")
        m.serve('api', port=api_port) if not m.server_exists('api') else None
        image = f'{mod}:latest'
        cwd = m.dirpath(mod) 
        working_dir = '/app'
        return m.fn('pm/run')(
                    name=mod, 
                    volumes=[f'{cwd}:/app','/app/node_modules', '~/.mod:/root/.mod', '~/mod:/root/mod', '/app/.next'], 
                    cwd=cwd, 
                    image=image,
                    working_dir=working_dir,
                    port=port, 
                    cmd='npm run build && npm run start' if prod else 'npm run dev',
                    env={'NEXT_PUBLIC_API_URL': api_url}, 
                    **kwargs
                    )

    def edit(self, text='in the edit module version have on the right hand side a sidebar of of previous versions which will be  ', *extra_text, **kwargs):
        text += str(m.fn('api/history')()[:2]) + m.code('api/history')  + ' '.join(extra_text) + 'and fix ersions.map is not a functio'
        return m.edit(mod='app', *text, **kwargs)


    def fix(self):
        # i want to know which files have cid as content ids
        api = m.mod('api')()
        def is_cid(file):
            content = m.get_text(file)
            return len(content) == 46 and content.startswith('Q')
        cid_files = [f for f in m.files(m.dp('app'), depth=10) if is_cid(f)]
        for f in cid_files:
            print(f'Fixing file with CID: {f}')
            m.put_text(f, api.get(m.get_text(f)))
        return cid_files
