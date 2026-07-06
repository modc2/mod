"""LocalFS - Content-addressable local filesystem storage"""

from .mod import *

__version__ = "0.1.0"

# Try to import Rust acceleration module
try:
    from . import localfs_rs
    _has_rust = True
except ImportError:
    _has_rust = False
    localfs_rs = None

__all__ = ['localfs_rs', 'mod']
