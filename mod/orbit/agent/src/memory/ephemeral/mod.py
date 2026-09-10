"""ephemeral - memory that lives for the run and is gone after it

Same layers, same retrieval, nothing written to disk: the trail, the turns
and the facts sit in RAM and die with the process. This is the right memory
for a run that should leave no trace — an arena match, a benchmark, a
sandboxed portal run — where a durable trail is contamination rather than
context: the next match must meet the task cold, not remember solving it.

Retrieval still works inside the run, which is the point. An agent can ask
what it already tried five steps ago without any of it outliving the run.
"""
_Base = globals().get('BaseMemory')
if _Base is None:  # pragma: no cover - registry always injects
    from ..mod import Memory as _Base


class Memory(_Base):
    kind = 'ephemeral'
    label = 'Ephemeral'
    description = ("In-RAM memory: retrievable during the run, written "
                   "nowhere and gone when it ends")

    def __init__(self, dir: str = None, persist: bool = True, session: str = None,
                 **kwargs):
        # `persist` is ignored on purpose — the whole contract of this module
        # is that a caller cannot accidentally turn durability back on
        super().__init__(dir=dir, persist=False, session=session, **kwargs)

    def save(self, path: str = None):
        """Refuses to write. Ephemeral means ephemeral."""
        return False
