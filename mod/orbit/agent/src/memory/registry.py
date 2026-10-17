"""
memories - the registry of memory modules an agent can be built with

Memory is a component of the agent, not a fixed part of it: an agent is a
prompt, a model, a toolbox and a memory module, and this is the registry the
last one is chosen from. Same shape as the agent and tool registries — a
directory per module, each with a mod.py holding a Memory class — so adding
one is dropping a folder in beside default/ and ephemeral/.

    default    layered, persistent, retrievable    (what an agent gets unnamed)
    ephemeral  the same, in RAM, gone with the run

A dotted name is passed through to the framework instead, so an agent can
also be built with a memory that lives in another module entirely
(`agent.memory`, or anyone else's mod that speaks the same interface).

Usage:
    mems = Memories()
    mems.ls()                    # ['default', 'ephemeral']
    mems.get('ephemeral')        # the registry card
    mems.make('ephemeral')       # an instance, ready to snap into an agent
    mems.make()                  # the default one
"""
import importlib.util
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mod import Memory as BaseMemory

DEFAULT = 'default'


class Memories:
    """Memory-module registry — discovers backends in memory/<name>/mod.py."""
    description = "Memory registry - the memory modules an agent can be built with"

    def __init__(self, dir: str = None, **kwargs):
        self._dir = Path(dir) if dir else Path(__file__).parent
        self._classes: Dict[str, type] = {}
        # one live instance per module name: memory is state, so two callers
        # asking for 'default' must get the same store, not two views of it
        self._instances: Dict[str, BaseMemory] = {}

    # ── discovery ────────────────────────────────────────────────────

    def ls(self) -> List[str]:
        """Module names, the default first — it is the one being offered."""
        found = sorted(d.name for d in self._dir.iterdir()
                       if d.is_dir() and not d.name.startswith(('_', '.'))
                       and (d / 'mod.py').exists())
        return ([DEFAULT] if DEFAULT in found else []) + \
               [n for n in found if n != DEFAULT]

    def exists(self, name: str) -> bool:
        return name in self.ls()

    def cls(self, name: str) -> type:
        """The Memory class a module defines, compiled from source.

        The base is injected into the module namespace before exec so a
        backend can subclass it without knowing what package it was imported
        under — these files are loaded by path, not by import.
        """
        if name in self._classes:
            return self._classes[name]
        path = self._dir / name / 'mod.py'
        if not path.exists():
            raise KeyError(f"memory module not found: {name}. "
                           f"Available: {self.ls()}")
        spec = importlib.util.spec_from_file_location(f"memory.{name}", str(path))
        module = importlib.util.module_from_spec(spec)
        module.BaseMemory = BaseMemory
        spec.loader.exec_module(module)
        cls = getattr(module, 'Memory', None)
        if cls is None:
            raise AttributeError(f"no Memory class in {name}/mod.py")
        self._classes[name] = cls
        return cls

    def get(self, name: str = None) -> Dict[str, Any]:
        """The registry card for one module — what the console lists."""
        name = name or DEFAULT
        cls = self.cls(name)
        card = cls.describe() if hasattr(cls, 'describe') else {'name': name}
        return {**card, 'name': name, 'default': name == DEFAULT,
                'live': name in self._instances}

    def items(self) -> List[Dict[str, Any]]:
        out = []
        for name in self.ls():
            try:
                out.append(self.get(name))
            except Exception as e:
                out.append({'name': name, 'error': str(e)})
        return out

    # ── instances ────────────────────────────────────────────────────

    def make(self, name: str = None, fresh: bool = False, **kwargs) -> Any:
        """The memory instance for a module name.

        A dotted or slashed name ('agent.memory') is a framework module path
        and is resolved through the fleet — that is how an agent is built with
        a memory that isn't one of ours. Everything else is a local backend.

        Instances are cached per name (memory that is rebuilt per call is
        memory that remembers nothing); `fresh=True` forces a new one, which
        is what a run wants when it must not share working state.
        """
        name = (name or DEFAULT).strip()
        if '.' in name or '/' in name:
            return self._from_fleet(name, **kwargs)
        if not fresh and not kwargs and name in self._instances:
            return self._instances[name]
        instance = self.cls(name)(**kwargs)
        if not fresh and not kwargs:
            self._instances[name] = instance
        return instance

    def _from_fleet(self, path: str, **kwargs):
        """A memory module that lives in another mod, resolved by path.

        Falls back to the default backend rather than failing a run: an agent
        built against a module that has since moved should lose its memory,
        not its ability to answer.
        """
        try:
            import mod as m
            return m.mod(path)(**kwargs)
        except Exception as e:
            print(f"[memories] {path} unavailable ({e}) — using {DEFAULT}")
            return self.make(DEFAULT)

    def name_of(self, instance) -> str:
        """The registry name of a live instance, for status and for the UI."""
        return getattr(instance, 'kind', None) or DEFAULT

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, name: str = None, **kwargs) -> Any:
        """
        forward()                -> list the memory modules
        forward('ephemeral')     -> one module's card
        forward(action='retrieve', name=…, query=…) -> retrieval on one module
        """
        action = kwargs.get('action')
        if action == 'retrieve':
            mem = self.make(kwargs.get('name', name))
            return {'module': self.name_of(mem),
                    'query': kwargs.get('query', ''),
                    'hits': mem.retrieve(kwargs.get('query', ''),
                                         k=int(kwargs.get('k', 5)),
                                         layers=kwargs.get('layers'),
                                         session=kwargs.get('session'),
                                         who=kwargs.get('who'))}
        if name:
            return self.get(name)
        return {'memories': self.items(), 'default': DEFAULT,
                'total': len(self.ls())}

    def test(self) -> Dict[str, Any]:
        assert DEFAULT in self.ls(), self.ls()
        assert self.ls()[0] == DEFAULT      # the default is offered first
        # every module builds, and every one is the same interface
        for name in self.ls():
            mem = self.make(name, fresh=True, persist=False)
            assert hasattr(mem, 'retrieve') and hasattr(mem, 'compile')
            assert self.name_of(mem) == name
        # instances are shared, so a fact stored through one is retrievable
        # through the next caller that asks for the same module
        mem = self.make(DEFAULT)
        assert self.make(DEFAULT) is mem
        # ephemeral writes nothing, whatever it is asked
        eph = self.make('ephemeral', fresh=True)
        assert eph.persist is False and eph.save('/tmp/should-not-exist') is False
        return {'passed': True, 'memories': self.ls()}
