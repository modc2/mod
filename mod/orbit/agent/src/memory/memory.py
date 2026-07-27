"""
memory.py - backwards-compat shim

The Memory class grew up: it now lives in mod.py as a full mod with
working/episodic/semantic layers and its own servable process (:50119).
This shim keeps `from src.memory.memory import Memory` working.
"""
from .mod import Memory  # noqa: F401
