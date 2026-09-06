"""default - the memory module an agent gets when it names none

The full layered subsystem: working state for the prompt being built, the
episodic trail of every step, the dialogue that makes a new conversation
pick up where the last one left off, and the durable facts. All of it is
persisted under ~/.mod/agent/memory/ and all of it is retrievable.

Nothing is overridden — this IS the base, named and registered so an agent
can point at it explicitly and so the console has something to list next to
the alternatives.
"""
# the registry injects the base class before exec; the import is the path
# taken when this file is loaded on its own (a test, a REPL)
_Base = globals().get('BaseMemory')
if _Base is None:  # pragma: no cover - registry always injects
    from ..mod import Memory as _Base


class Memory(_Base):
    kind = 'default'
    label = 'Default'
    description = ("Layered persistent memory: working state, the step trail, "
                   "your conversation and durable facts — all retrievable")
