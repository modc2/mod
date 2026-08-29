import importlib.util
import os
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))


class Mod:
    """The zcash web app. The module itself lives one level up in zcash/mod.py.

    The protocol resolves the name `zcash` to *this* file rather than to
    ../zcash/mod.py, so `m zcash/<fn>` lands here -- which is why `m
    zcash/token`, `m zcash/status` and every other documented command used to
    fail with AttributeError. Anything this shim does not define is forwarded
    to the real module, so one name reaches both.
    """

    description = "Next.js front end for the zcash module"
    path = _DIR
    _module = None

    @classmethod
    def _core(cls):
        """../zcash/mod.py, by path. `import mod` here is the protocol's own."""
        if cls._module is None:
            path = os.path.join(os.path.dirname(_DIR), 'zcash', 'mod.py')
            spec = importlib.util.spec_from_file_location('zcash_core', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls._module = module
        return cls._module

    def __getattr__(self, name):
        # Only reached for names this class does not define, and never for
        # dunders or privates -- those would drag the real module into
        # copy/pickle machinery that has nothing to do with it.
        if name.startswith('_'):
            raise AttributeError(name)
        if self.__dict__.get('_delegate') is None:
            self.__dict__['_delegate'] = type(self)._core().Mod()
        return getattr(self.__dict__['_delegate'], name)

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {'name': 'zcash-app', 'description': self.description,
                'path': self.path, 'files': os.listdir(self.path)}

    def install(self):
        """Install dependencies."""
        return subprocess.run(['npm', 'install'], cwd=self.path,
                              capture_output=True, text=True).stdout

    def build(self):
        """Build the production bundle."""
        env = dict(os.environ, NEXT_PUBLIC_BASE_PATH=os.environ.get(
            'NEXT_PUBLIC_BASE_PATH', '/zcash'))
        return subprocess.run(['npm', 'run', 'build'], cwd=self.path, env=env,
                              capture_output=True, text=True).stdout
