"""freetoken — a mod wrapper around FreeToken (github.com/FlashML-org/FreeToken).

The split that matters: everything in `client`, `boxes`, `catalog` and
`preflight` is stdlib and runs anywhere, because driving an engine needs no
GPU. Only `install` and `engine` touch the machine, and only when this machine
is the one with the card in it.
"""
__all__ = ['boxes', 'catalog', 'client', 'engine', 'install', 'preflight', 'state']
