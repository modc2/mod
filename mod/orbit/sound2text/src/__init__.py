"""sound2text — the parts, each usable on its own.

    audio     containers and sample rates, without a media stack
    vad       where the speech is
    engines   the recognisers, behind one interface
    router    which one to use, and why
    cache     what has been transcribed before
    ledger    what each engine actually did on this machine
    pipeline  the five steps, in order
"""

__all__ = ['audio', 'cache', 'engines', 'keys', 'ledger', 'pipeline', 'router', 'vad']
