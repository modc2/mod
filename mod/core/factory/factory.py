import mod as m
import os

class Factory:
    def __init__(self):
        pass
        
    def cpmod(self, from_mod:str = 'dev', to_mod:str = 'dev2', force=True):
        """
        Copy the mod to the git repository
        """
        mods_path = m.get_mods_path()
        from_path = m.dirpath(from_mod)
        if not os.path.exists(from_path):
            raise Exception(f'Mod {from_path} does not exist')
        to_mod=to_mod.replace('.', '/')
        to_path = to_mod
        if to_path.startswith('./') or to_path.startswith('~/') or to_path.startswith('/'):
            to_path = m.abspath(to_mod)
        else:
            to_path = mods_path + '/' + to_mod.replace('.', '/')
        if os.path.exists(to_path):
            if force or input(f'Path {to_path} already exists. Do you want to remove it? (y/n)') == 'y':
                for f in m.files(to_path):
                    print(f'Removing {f}')
                    m.rm(f)
        from_files = m.files(from_path)
        for f in from_files:
            m.cp(f, to_path + '/' + f[len(from_path)+1:])
        # assert os.path.exists(to_path), f'Failed to copy {from_path} to {to_path}'
        m.tree(update=1)
        return { 
                'from': {'mod': from_mod, 'path': from_path, 'cid': m.cid(from_mod)}, 
                'to': {'path': to_path, 'mod': to_mod, 'cid': m.cid(to_mod)},
                } 

    def mvmod(self, from_mod:str = 'dev', to_mod:str = 'dev2'):
        """
        Move the mod to the git repository
        """
        from_path = m.dirpath(from_mod)
        mods_path = m.get_mods_path()
        to_path = mods_path + '/' + to_mod.replace('.', '/')
        for f in m.files(from_path):
            print(f'Moving {f} to {to_path + "/" + f[len(from_path)+1:]}')

        
        m.mv(from_path, to_path)
        m.tree(update=1)
        return {'from': {'mod': from_mod, 'path': from_path}, 'to': {'mod': to_mod, 'path': to_path}}


    def rmmod(self, mod:str = 'dev'):
        """
        Remove the mod from the git repository
        """
        path = self.dirpath(mod)
        assert os.path.exists(path), f'Mod {mod} does not exist'
        self.rm(path)
        self.tree(update=1)
        return {'success': True, 'msg': 'removed mod'}



    def addpath(self, path, name=None, update=True):
        assert os.path.exists(path), f'Path {path} does not exist'
        path = self.abspath(path)
        name = name or path.split('/')[-1]
        dirpath = self.mods_path + '/' + name.replace('.', '/')
        self.cmd(f'cp -r {path} {dirpath}')
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created from path'}

    def addcid(self, name='churn',  cid='QmXUjBQRFa8DbY2GhD1Aq6a44EBYzgejmtwwnYYTfvnFW4'):
        api = c.mod('api')()
        file2text =  api.content(cid, expand=True)
        path = self.mods_path + '/' + name.replace('.', '/')
        for k,v in file2text.items():
            new_path = path + '/' + k
            print(f'Creating {new_path} for mod {name}')
            self.put_text(new_path, v)
        self.tree(update=True)
        assert self.mod_exists , f'Mod {name} not found after creation from cid {cid}'
        return {'name': name, 'path': path, 'msg': 'Mod Created from cid', 'cid': cid}

    exp_mode = True
    def addgit(self,  repo , name=None, update=True):
        """
        make a new mod from a git repo
        """
        name = name or repo.split('/')[-1].replace('.git', '')
        mods_path = self.exp_path if self.exp_mode else self.mods_path
        dirpath = mods_path + '/' + name.replace('.', '/')
        mod_name = dirpath.split('/')[-1]
        self.cmd(f'git clone {repo} {dirpath}')
        files = self.files(dirpath)
        has_python_files = any([f.endswith('.py') for f in files])
        if not has_python_files:
            self.put_text( dirpath + '/'+ mod_name +'.py', self.code('base'))
        self.exp_tree(update=True)
        return self.files(dirpath)

    def addmod(self,  path  , name=None, base='base', update=True, external=True):
        """
        make a new mod
        """
        name = name or path.split('/')[-1]
        mods_path = self.exp_path if external else self.mods_path
        dirpath = mods_path + '/' + name.replace('.', '/')
        mod_name = dirpath.split('/')[-1]
        for k,v in self.content(base).items():
            new_path = dirpath + '/' +  k.replace(base, mod_name)
            print(f'Creating {new_path} for mod {name}')
            self.put_text( new_path, v)
        files = self.files(dirpath)
        self.tree(update=True)
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created', 'base': base, 'cid': self.cid(name)}
